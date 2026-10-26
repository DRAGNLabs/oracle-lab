"""Turn one saved run into the `RunTrace` the page shows."""

from __future__ import annotations

import json
import re
from dataclasses import asdict

from oracle_lab.config import load_config
from oracle_lab.contracts.run import (
    LocalizedTarget,
    read_evaluation_result,
    read_instance_record,
    read_localized_target,
)
from oracle_lab.harnesses import read_steps, reasoning_kind
from oracle_lab.harnesses.steps import (
    Action,
    EditFile,
    OtherTool,
    ReadFile,
    Search,
    Shell,
    Step,
    WriteFile,
)
from oracle_lab.paths import RunPaths
from oracle_lab.trace.events import (
    ActionType,
    DefectOverlap,
    DefectSummary,
    Event,
    FileCategory,
    RunTrace,
    Touch,
)
from oracle_lab.trace.facts import (
    Checkout,
    PatchedRegions,
    generated_files,
    grep_hits,
    parse_pytest,
)
from oracle_lab.trace.shell import (
    PatchFile,
    RunCommand,
    RunPython,
    RunTests,
    ShellAction,
    classify,
    split,
)

_TEST_CATEGORIES = {FileCategory.TEST, FileCategory.GENERATED_TEST}
_PREVIEW = 100


def build_run_trace(paths: RunPaths) -> RunTrace:
    """Read the run's trace, golden patch, target, submission, and outcomes, and annotate.

    Reads the run with the strict contracts: the resolved `RunConfig`, the
    `EvaluationResult` when the run was evaluated, and the localized target.
    """
    config = load_config(paths.config, resolved=True)
    checkout = Checkout(config.require_runtime().workdir, config.task.generated_dir)
    steps = read_steps(config.agent.harness, paths.generation / "trace.jsonl")
    target = read_localized_target(paths.target) if paths.target.exists() else None
    target_names = (
        [name for file in target.files for name in [*file.functions, *file.classes]]
        if target
        else []
    )
    regions = PatchedRegions.from_patch(
        read_instance_record(paths.instance).golden_patch, target_names
    )
    events = to_events(steps, checkout, regions)
    outcomes = None
    if paths.results.exists():
        outcomes = {
            outcome.test_id: outcome.cell.value
            if outcome.cell
            else f"{outcome.buggy} / {outcome.golden}"
            for outcome in read_evaluation_result(paths.results).tests
        }
    submitted_root = paths.submission / "files"
    submitted = {
        file.relative_to(submitted_root).as_posix(): file.read_text(errors="replace")
        for file in sorted(submitted_root.rglob("*"))
        if file.is_file()
    }
    return RunTrace(
        reasoning=reasoning_kind(config.agent.harness),
        events=events,
        generated_files=generated_files(events, submitted, outcomes),
        defect=_defect_summary(events, regions, target),
        outcomes_available=outcomes is not None,
    )


def to_events(steps: list[Step], checkout: Checkout, regions: PatchedRegions) -> list[Event]:
    """One event per step, or per sub-command for chained shell commands."""
    events: list[Event] = []
    scripts: dict[str, str] = {}  # files the agent wrote, by path as written, for `python x.py`
    for index, step in enumerate(steps):
        common = {
            "step": index,
            "trace_line": step.line,
            "output": step.output,
            "exit_code": step.exit_code,
            "timestamp": step.timestamp,
        }
        if step.kind in {"reasoning", "message"}:
            action = ActionType.REASONING if step.kind == "reasoning" else ActionType.MESSAGE
            events.append(
                Event(
                    part=0, action=action, description=_preview(step.text), text=step.text, **common
                )
            )
            continue
        if step.failed:
            name = _tool_name(step.action)
            events.append(
                Event(
                    part=0,
                    action=ActionType.FAILED_CALL,
                    description=f"Failed {name}: {_preview(step.output)}",
                    command=_input_text(step.action),
                    **common,
                )
            )
            continue
        if isinstance(step.action, Shell):
            actions: list[tuple[str, Action | ShellAction]] = [
                (command.text, action)
                for command in split(step.action.command)
                for action in classify(command)
            ] or [(step.action.command, RunCommand(step.action.command))]
        else:
            actions = [(_input_text(step.action), step.action)]
        for part, (text, action) in enumerate(actions):
            event = _event(action, text, part, common, checkout, regions, scripts)
            events.append(event)
    return events


def _event(
    action: Action | ShellAction,
    text: str,
    part: int,
    common: dict,
    checkout: Checkout,
    regions: PatchedRegions,
    scripts: dict[str, str],
) -> Event:
    output = common["output"]

    def touch(path: str, lines: tuple[int, int] | None, *, read: bool) -> Touch:
        relative = checkout.relative(path) or path
        overlap = regions.overlap(relative, lines)
        if not read and overlap is DefectOverlap.LINES:
            overlap = DefectOverlap.FILE  # where an edit or hitless search lands is unknown
        return Touch(relative, checkout.category(path), lines, overlap)

    def event(kind: ActionType, description: str, **fields) -> Event:
        return Event(
            part=part, action=kind, description=description, command=text, **common, **fields
        )

    if isinstance(action, ReadFile):
        lines = _range(action.start, action.end)
        shown = f" L{lines[0]}–{lines[1] if lines[1] < _END else 'end'}" if lines else ""
        relative = checkout.relative(action.path) or action.path
        return event(
            ActionType.READ,
            f"Read {relative}{shown}",
            touches=[touch(action.path, lines, read=True)],
        )
    if isinstance(action, Search):
        touches = _search_touches(action, output, checkout, touch)
        where = f" in {checkout.relative(action.path) or action.path}" if action.path else ""
        files = len({item.path for item in touches})
        return event(
            ActionType.SEARCH,
            f"Search {_preview(action.pattern, 50)!r}{where} → {files} file(s)",
            touches=touches,
        )
    if isinstance(action, WriteFile | EditFile | PatchFile):
        target = touch(action.path, None, read=False)
        if isinstance(action, WriteFile):
            scripts[action.path] = action.content
        written = _written_text(action)
        if target.category in _TEST_CATEGORIES:
            kind = ActionType.EDIT_TEST
        elif target.category is FileCategory.OUTSIDE:
            kind = ActionType.RUN_OTHER  # a scratch file outside the checkout
        else:
            kind = ActionType.EDIT_SOURCE
        return event(
            kind,
            f"{_verb(action)} {target.path} {_size(action)}",
            touches=[target],
            change=action,
            patched_names=regions.names_in(written) if kind is ActionType.EDIT_TEST else [],
        )
    if isinstance(action, RunTests):
        summary = parse_pytest(output)
        return event(
            ActionType.RUN_TESTS, f"{_preview(text, 60)} → {_counts(summary)}", pytest=summary
        )
    if isinstance(action, RunPython):
        script = action.script or _script_file(action.command, scripts)
        first = next((line for line in script.splitlines() if line.strip()), "")
        return event(
            ActionType.PROBE_CODE,
            f"Python: {_preview(first or text, 70)}",
            text=script,
            patched_names=regions.names_in(script or action.command),
        )
    if isinstance(action, RunCommand):
        return event(ActionType.RUN_OTHER, _preview(text, 90), git_history=action.git_history)
    assert isinstance(action, OtherTool | Shell)
    return event(ActionType.RUN_OTHER, f"{_tool_name(action)}: {_preview(_input_text(action), 70)}")


_END = 10**9  # a range's end when it runs to the end of the file


def _range(start: int | None, end: int | None) -> tuple[int, int] | None:
    if start is None and end is None:
        return None
    return (start or 1, end if end is not None else _END)


def _search_touches(action: Search, output: str, checkout: Checkout, touch) -> list[Touch]:
    """One touch per run of consecutive hit lines, or the searched file when there are none."""
    scope = checkout.relative(action.path) if action.path else None
    lines: dict[str, list[int]] = {}
    for path, number in grep_hits(output, action.path):
        relative = checkout.relative(path) or path
        if scope and scope != "." and not (relative == scope or relative.startswith(scope + "/")):
            continue  # another sub-command's output, shared by a chained command
        lines.setdefault(path, []).append(number)
    touches = []
    for path, numbers in lines.items():
        numbers = sorted(set(numbers))
        start = previous = numbers[0]
        for number in numbers[1:] + [None]:
            if number is not None and number == previous + 1:
                previous = number
                continue
            touches.append(touch(path, (start, previous), read=True))
            if number is not None:
                start = previous = number
    if not touches and action.path and "." in action.path.rsplit("/", 1)[-1]:
        touches.append(touch(action.path, None, read=False))
    return touches


def _defect_summary(
    events: list[Event], regions: PatchedRegions, target: LocalizedTarget | None
) -> DefectSummary:
    first_touch = None
    shown: set[tuple[str, int]] = set()
    for event in events:
        for item in event.touches:
            if item.overlap is DefectOverlap.LINES and first_touch is None:
                first_touch = event.step
            if event.action in {ActionType.READ, ActionType.SEARCH}:
                shown |= {
                    (item.path, number)
                    for number in regions.shown_lines(item.path, item.lines, event.output)
                }
    return DefectSummary(
        first_touch=first_touch,
        lines_shown=len(shown),
        lines_total=regions.lines_total,
        hunks={path: patched.hunks for path, patched in regions.files.items()},
        names=regions.names,
        target_read=_target_read(events, target) if target else None,
    )


def _target_read(events: list[Event], target: LocalizedTarget) -> bool:
    """Whether a read or search of a target file showed a target name's definition."""
    for file in target.files:
        names = [name.split(".")[-1] for name in [*file.functions, *file.classes]]
        pattern = re.compile(rf"\b(?:def|class)\s+(?:{'|'.join(map(re.escape, names))})\b")
        for event in events:
            if event.action in {ActionType.READ, ActionType.SEARCH} and any(
                item.path == file.path for item in event.touches
            ):
                if names and pattern.search(event.output):
                    return True
    return False


def _script_file(command: str, scripts: dict[str, str]) -> str:
    """The content of a script the agent wrote earlier and now runs with `python x.py`."""
    for word in command.split():
        if word.endswith(".py") and word in scripts:
            return scripts[word]
    return ""


def _written_text(action: WriteFile | EditFile | PatchFile) -> str:
    if isinstance(action, WriteFile):
        return action.content
    if isinstance(action, EditFile):
        return action.new or ""
    return action.patch


def _verb(action: WriteFile | EditFile | PatchFile) -> str:
    return {WriteFile: "Write", EditFile: "Edit", PatchFile: "Patch"}[type(action)]


def _size(action: WriteFile | EditFile | PatchFile) -> str:
    if isinstance(action, WriteFile):
        return f"({len(action.content.splitlines())} lines)"
    if isinstance(action, EditFile):
        if action.old is None or action.new is None:
            return "(shell edit; result not recorded)"
        return f"(+{len(action.new.splitlines())}/−{len(action.old.splitlines())})"
    lines = action.patch.splitlines()
    added = sum(1 for line in lines if line.startswith("+"))
    removed = sum(1 for line in lines if line.startswith("-"))
    return f"(+{added}/−{removed})"


def _counts(summary) -> str:
    if summary is None:
        return "no pytest summary"
    if not summary.counts:
        return "no tests ran" if not summary.failed else f"{len(summary.failed)} failed"
    return ", ".join(f"{count} {word}" for word, count in summary.counts.items())


def _tool_name(action: Action | None) -> str:
    if isinstance(action, OtherTool):
        return action.name
    return type(action).__name__ if action is not None else "call"


def _input_text(action: Action | None) -> str:
    if isinstance(action, Shell):
        return action.command
    if action is None:
        return ""
    return json.dumps(asdict(action), indent=2, ensure_ascii=False)


def _preview(text: str, width: int = _PREVIEW) -> str:
    line = " ".join(text.split())
    return line if len(line) <= width else line[: width - 1] + "…"
