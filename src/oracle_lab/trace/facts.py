"""Facts about events: which files they touch and where the golden patch changed them.

Everything here is mechanical. Nothing judges whether an event was a mistake.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from oracle_lab.harnesses.steps import EditFile, WriteFile
from oracle_lab.localization import is_test_path
from oracle_lab.localization.patches import parse_patch
from oracle_lab.trace.events import (
    ActionType,
    DefectOverlap,
    Event,
    FileCategory,
    FileVersion,
    GeneratedFile,
    PytestSummary,
)
from oracle_lab.trace.shell import PatchFile

_CHANGELOG = ("HISTORY", "CHANGES", "CHANGELOG", "NEWS", "RELEASE")
_CONFIG_SUFFIXES = {".toml", ".cfg", ".ini", ".yaml", ".yml", ".json"}
_DEFINITION = re.compile(r"^\s*(?:async\s+)?(?:def|class)\s+(\w+)")
_SKIP_MARK = re.compile(r"pytest\.(?:mark\.(?:skip|skipif|xfail)\b|skip\(|xfail\()")
_PYTEST_COUNT = re.compile(
    r"(\d+) (passed|failed|errors?|skipped|xfailed|xpassed|deselected|warnings?)\b"
)


@dataclass(frozen=True)
class Repository:
    """Where paths in a trace resolve: the checkout root and the generated test directory."""

    workdir: str  # e.g. /testbed
    generated_dir: str  # repository-relative, e.g. oracle_tests

    def relative(self, path: str) -> str | None:
        """The path relative to the repository root, or None when it is outside."""
        pure = PurePosixPath(path)
        root = PurePosixPath(self.workdir)
        if pure.is_absolute():
            if pure != root and root not in pure.parents:
                return None
            pure = pure.relative_to(root)
        if ".." in pure.parts:
            return None
        return pure.as_posix()

    def category(self, path: str) -> FileCategory:
        relative = self.relative(path)
        if relative is None:
            return FileCategory.OUTSIDE
        pure = PurePosixPath(relative)
        generated = PurePosixPath(self.generated_dir)
        if pure == generated or generated in pure.parents:
            return FileCategory.GENERATED_TEST
        if is_test_path(pure) or pure.name == "conftest.py":
            return FileCategory.TEST
        if pure.name.upper().startswith(_CHANGELOG) or "changelog" in pure.parts:
            return FileCategory.CHANGELOG
        if pure.parts[:1] in {("doc",), ("docs",)} or pure.suffix in {".rst", ".md"}:
            return FileCategory.DOCS
        if pure.name.upper().startswith("README"):
            return FileCategory.DOCS
        if pure.suffix in _CONFIG_SUFFIXES or pure.name in {"setup.py", "Makefile"}:
            return FileCategory.CONFIG
        return FileCategory.SOURCE


@dataclass
class PatchedFile:
    path: str
    hunks: list[tuple[int, int]]  # buggy-side (start, end) of each hunk, 1-based inclusive
    # Buggy-side lines the patch removes, plus the lines around a pure insertion, with
    # their text. Blank lines are left out.
    changed: dict[int, str]


class PatchedRegions:
    """Where the golden patch changes the buggy repository, by file and buggy-side line."""

    def __init__(self, files: list[PatchedFile], names: list[str]):
        self.files = {patched.path: patched for patched in files}
        self.names = names  # functions and classes defined in or around patched hunks

    @classmethod
    def from_patch(cls, patch: str, target_names: list[str]) -> PatchedRegions:
        """Read hunks with `localization.patches.parse_patch`.

        Names come from hunk headers (`@@ ... @@ class C`), `def`/`class` lines inside
        hunks, and the localized target's names when the run has one. A method under
        a known class is named `C.m`, so a common method name like `run` only counts
        alongside its class.
        """
        files = []
        for diff in parse_patch(patch):
            if diff.old_path is None:
                continue
            hunks, changed = [], {}
            for hunk in diff.hunks:
                hunks.append((hunk.old_start, hunk.old_start + max(hunk.old_length, 1) - 1))
                changed.update(_changed_lines(hunk.old_start, hunk.lines))
            files.append(PatchedFile(diff.old_path, hunks, changed))
        found = _patched_names(patch) + target_names
        names = [name for name in dict.fromkeys(found) if not name.startswith("__")]
        return cls(files, names)

    def overlap(self, path: str, lines: tuple[int, int] | None) -> DefectOverlap:
        """How a read relates to the patch. Whole-file reads (None) overlap any hunk."""
        patched = self.files.get(path)
        if patched is None:
            return DefectOverlap.NONE
        if lines is None or any(
            start <= lines[1] and lines[0] <= end for start, end in patched.hunks
        ):
            return DefectOverlap.LINES
        return DefectOverlap.FILE

    def shown_lines(self, path: str, lines: tuple[int, int] | None, output: str) -> set[int]:
        """Changed lines inside the range whose text appears in the output the agent saw.

        Requiring the text keeps truncated output from counting, so totals are a lower bound.
        """
        patched = self.files.get(path)
        if patched is None:
            return set()
        return {
            number
            for number, text in patched.changed.items()
            if (lines is None or lines[0] <= number <= lines[1]) and text.strip() in output
        }

    @property
    def lines_total(self) -> int:
        return sum(len(patched.changed) for patched in self.files.values())

    def names_in(self, text: str) -> list[str]:
        """Patched names that the text mentions as whole words; `C.m` needs both `C` and `m`."""
        return [
            name
            for name in self.names
            if all(re.search(rf"\b{re.escape(part)}\b", text) for part in name.split("."))
        ]


def _patched_names(patch: str) -> list[str]:
    """Functions and classes a patch's hunks sit in or define, methods as `Class.method`."""
    found = []
    enclosing = None  # the class the current line is inside, when the patch shows it
    for line in patch.splitlines():
        if line.startswith("@@"):
            line = line.split("@@")[-1].removeprefix(" ")
            enclosing = None
        elif line[:1] in {" ", "-"}:
            line = line[1:]
        else:
            continue
        match = _DEFINITION.match(line)
        if match is None:
            continue
        indented = line[:1].isspace()
        if line.lstrip().startswith("class") and not indented:
            enclosing = match.group(1)
            found.append(enclosing)
        elif indented and enclosing:
            found.append(f"{enclosing}.{match.group(1)}")
        else:
            if not indented:
                enclosing = None
            found.append(match.group(1))
    return found


def _changed_lines(start: int, lines: list[str]) -> dict[int, str]:
    """Removed lines, and the context lines either side of each pure insertion."""
    old_side: dict[int, str] = {}
    removed: set[int] = set()
    insert_points: list[int] = []  # the buggy line an insertion comes after
    number = start
    for line in lines:
        tag = line[:1]
        if tag in {" ", ""}:
            old_side[number] = line[1:]
            number += 1
        elif tag == "-":
            old_side[number] = line[1:]
            removed.add(number)
            number += 1
        elif tag == "+" and number - 1 not in removed:
            insert_points.append(number - 1)
    chosen = set(removed)
    for point in insert_points:
        chosen |= {point, point + 1}
    return {
        number: old_side[number]
        for number in sorted(chosen)
        if number in old_side and old_side[number].strip()
    }


def grep_hits(output: str, path: str | None) -> list[tuple[str, int]]:
    """(path, line) of each hit or context line in grep/rg output.

    Reads `path:N:` and `path-N-`, plain `N:` for a single searched file, and
    OpenCode's `path:` heading followed by `  Line N:` lines.
    """
    hits = []
    heading = None
    for line in output.splitlines():
        if line.endswith(":") and not line.startswith(" ") and "/" in line:
            heading = line[:-1]
            continue
        opencode = re.match(r"\s+Line (\d+):", line)
        if opencode and heading:
            hits.append((heading, int(opencode.group(1))))
            continue
        prefixed = re.match(r"^([^\s:]+?)([:-])(\d+)\2", line)
        if prefixed and not prefixed.group(1).isdigit():
            hits.append((prefixed.group(1), int(prefixed.group(3))))
            continue
        bare = re.match(r"^(\d+)[:-]", line)
        if bare and path:
            hits.append((path, int(bare.group(1))))
    return hits


def parse_pytest(output: str) -> PytestSummary | None:
    """Pytest's final counts and FAILED/ERROR ids, or None when the output has neither."""
    counts: dict[str, int] | None = None
    for line in output.splitlines():
        found = _PYTEST_COUNT.findall(line)
        if found and (re.search(r"\bin [\d.]+s", line) or line.startswith("=")):
            counts = {_count_word(word): int(number) for number, word in found}
        elif "no tests ran" in line:
            counts = {}
    failed = re.findall(r"^(?:FAILED|ERROR) (\S+)", output, re.MULTILINE)
    if counts is None and not failed:
        return None
    return PytestSummary(counts or {}, failed)


def _count_word(word: str) -> str:
    return {"error": "errors", "warning": "warnings"}.get(word, word)


def generated_files(
    events: list[Event], submitted: dict[str, str], outcomes: dict[str, str] | None
) -> list[GeneratedFile]:
    """Each generated test file's versions, rebuilt from the edit events in order.

    `submitted` is the final content by path, from `submission/files/`. `outcomes` is
    each test id's matrix cell, attached to the version that introduced the test.
    Edit facts (failures before an edit, added skip/xfail marks, failed tests absent
    from the submission) come from the same versions and runs.
    """
    histories: dict[str, GeneratedFile] = {}
    content: dict[str, str | None] = {}
    for event in events:
        if event.change is None or not event.touches:
            continue
        touch = event.touches[0]
        if touch.category is not FileCategory.GENERATED_TEST:
            continue
        before = content.get(touch.path)
        after, diff, unknown = _apply(before, event.change)
        content[touch.path] = after
        history = histories.setdefault(touch.path, GeneratedFile(touch.path, [], None))
        history.versions.append(
            FileVersion(event.step, event.part, diff, after, unknown, runs_after=[])
        )
    for path in submitted:
        histories.setdefault(path, GeneratedFile(path, [], None))
    runs = [event for event in events if event.action is ActionType.RUN_TESTS and event.pytest]
    for history in histories.values():
        _attach_runs(history, runs)
        _attach_edit_facts(history, runs, submitted.get(history.path))
        final = history.versions[-1].content if history.versions else None
        if final is not None and history.path in submitted:
            history.matches_submission = final == submitted[history.path]
    if outcomes:
        _attach_outcomes(histories, outcomes)
    return sorted(histories.values(), key=lambda history: history.path)


def _apply(
    before: str | None, change: WriteFile | EditFile | PatchFile
) -> tuple[str | None, str, str | None]:
    """The file after a change (None when it cannot be rebuilt), the change's diff, and
    why the file is unknown, as `FileVersion.unknown` names it."""
    if isinstance(change, WriteFile):
        return change.content, _diff(before or "", change.content), None
    if isinstance(change, EditFile):
        if change.old is None or change.new is None:
            return None, "", "shell"
        if before is None:
            return None, _diff(change.old, change.new), "no_base"
        if change.old not in before:
            return None, _diff(change.old, change.new), "mismatch"
        after = before.replace(change.old, change.new, 1)
        return after, _diff(before, after), None
    if before is None:
        return None, change.patch, "no_base"
    after = _apply_codex_patch(before, change.patch)
    if after is None:
        return None, change.patch, "mismatch"
    return after, _diff(before, after), None


def _apply_codex_patch(before: str, patch: str) -> str | None:
    """Apply `apply_patch` hunks by matching their context, as Codex does, or None."""
    lines = before.splitlines()
    position = 0
    hunks: list[list[str]] = []
    for line in patch.splitlines():
        if line.startswith("@@"):
            hunks.append([])
        elif hunks:
            hunks[-1].append(line)
        else:
            hunks.append([line])
    for hunk in hunks:
        old = [line[1:] for line in hunk if line[:1] in {" ", "-", ""}]
        new = [line[1:] for line in hunk if line[:1] in {" ", "+", ""}]
        if line_end := _find(lines, old, position):
            start = line_end - len(old)
            lines[start:line_end] = new
            position = start + len(new)
        else:
            return None
    return "\n".join(lines) + ("\n" if before.endswith("\n") or not before else "")


def _find(lines: list[str], block: list[str], start: int) -> int | None:
    """The index just past the first match of block at or after start."""
    if not block:
        return None
    for index in range(start, len(lines) - len(block) + 1):
        if lines[index : index + len(block)] == block:
            return index + len(block)
    for index in range(start, len(lines) - len(block) + 1):  # Codex also ignores trailing spaces
        if [line.rstrip() for line in lines[index : index + len(block)]] == [
            line.rstrip() for line in block
        ]:
            return index + len(block)
    return None


def _diff(before: str, after: str) -> str:
    lines = difflib.unified_diff(before.splitlines(), after.splitlines(), lineterm="", n=3)
    return "\n".join(list(lines)[2:])  # without the empty ---/+++ file header


def _attach_runs(history: GeneratedFile, runs: list[Event]) -> None:
    """Each version gets the pytest runs between it and the file's next version."""
    for index, version in enumerate(history.versions):
        start = (version.step, version.part)
        following = history.versions[index + 1] if index + 1 < len(history.versions) else None
        end = (following.step, following.part) if following else None
        version.runs_after = [
            (run.step, run.pytest)
            for run in runs
            if (run.step, run.part) > start
            and (end is None or (run.step, run.part) < end)
            and run.pytest is not None
        ]


def _attach_edit_facts(history: GeneratedFile, runs: list[Event], submitted: str | None) -> None:
    for index, version in enumerate(history.versions):
        version.skip_marks_added = [
            line[1:].strip()
            for line in version.diff.splitlines()
            if line.startswith("+") and _SKIP_MARK.search(line)
        ]
        if index == 0:
            continue
        failing = [
            summary
            for _, summary in history.versions[index - 1].runs_after
            if summary.failed or summary.counts.get("failed") or summary.counts.get("errors")
        ]
        version.follows_failed_run = bool(failing)
        version.follows_failures = list(
            dict.fromkeys(
                test_id
                for summary in failing
                for test_id in summary.failed
                if _in_file(test_id, history.path)
            )
        )
    if submitted is not None:
        failed = dict.fromkeys(
            test_id
            for run in runs
            if run.pytest is not None
            for test_id in run.pytest.failed
            if _in_file(test_id, history.path)
        )
        history.failed_then_absent = [
            test_id for test_id in failed if not _defines(submitted, _test_name(test_id))
        ]


def _in_file(test_id: str, path: str) -> bool:
    """Whether a pytest id names a test in this file, whatever directory pytest ran from."""
    file = test_id.split("::")[0]
    return file == path or path.endswith("/" + file)


def _test_name(test_id: str) -> str:
    """The function name in a pytest id, without class or parameters."""
    return test_id.split("::")[-1].split("[")[0]


def _defines(text: str, name: str) -> bool:
    """Whether file content, or a diff's lines, define the named function."""
    pattern = rf"^[+ ]?\s*(?:async\s+)?def {re.escape(name)}\b"
    return re.search(pattern, text, re.MULTILINE) is not None


def _attach_outcomes(histories: dict[str, GeneratedFile], outcomes: dict[str, str]) -> None:
    """Attach each test's outcome to the first version whose text defines it.

    A test no version visibly defines goes on the file's last version.
    """
    for test_id, cell in outcomes.items():
        history = histories.get(test_id.split("::")[0])
        if history is None or not history.versions:
            continue
        name = _test_name(test_id)
        version = next(
            (
                version
                for version in history.versions
                if _defines(version.content or version.diff, name)
            ),
            history.versions[-1],
        )
        version.outcomes[test_id] = cell
