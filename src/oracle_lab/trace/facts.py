"""Facts about events: which files they touch and where the golden patch changed them.

Everything here is mechanical. Nothing judges whether an event was a mistake.
"""

from __future__ import annotations

from dataclasses import dataclass

from oracle_lab.trace.events import (
    DefectOverlap,
    Event,
    FileCategory,
    PytestSummary,
    TestFileHistory,
)


@dataclass(frozen=True)
class Repository:
    """Where paths in a trace resolve: the checkout root and the generated test directory."""

    workdir: str  # e.g. /testbed
    generated_dir: str  # repository-relative, e.g. oracle_tests

    def relative(self, path: str) -> str | None:
        """The path relative to the repository root, or None when it is outside."""
        raise NotImplementedError

    def category(self, path: str) -> FileCategory:
        raise NotImplementedError


@dataclass
class PatchedFile:
    path: str
    hunks: list[tuple[int, int]]  # buggy-side (start, end) of each hunk, 1-based inclusive
    changed: set[int]  # buggy-side lines the patch removes or changes


class PatchedRegions:
    """Where the golden patch changes the buggy repository, by file and buggy-side line."""

    def __init__(self, files: list[PatchedFile], names: list[str]):
        self.files = {patched.path: patched for patched in files}
        self.names = names  # functions and classes defined in or around patched hunks

    @classmethod
    def from_patch(cls, patch: str, target_names: list[str]) -> PatchedRegions:
        """Read hunks with `localization.patches.parse_patch`.

        Names come from hunk headers (`@@ ... @@ def f`), `def`/`class` lines inside
        hunks, and the localized target's names when the run has one.
        """
        raise NotImplementedError

    def overlap(self, path: str, lines: tuple[int, int] | None) -> DefectOverlap:
        """How a touch relates to the patch. Unknown or whole-file lines overlap any hunk."""
        raise NotImplementedError

    def changed_lines(self, path: str, lines: tuple[int, int] | None) -> set[int]:
        """The changed lines a range covers, for counting how many the agent was shown."""
        raise NotImplementedError

    def names_in(self, text: str) -> list[str]:
        """Patched names that the text mentions as whole words."""
        raise NotImplementedError


def grep_hits(output: str, path: str | None) -> list[tuple[str, int]]:
    """(path, line) of each hit in grep/rg output, from `path:N:` or, for one file, `N:`."""
    raise NotImplementedError


def parse_pytest(output: str) -> PytestSummary | None:
    """Pytest's final counts and FAILED ids, or None when the output has no summary line."""
    raise NotImplementedError


def test_histories(
    events: list[Event], submitted: dict[str, str], outcomes: dict[str, str] | None
) -> list[TestFileHistory]:
    """Each generated test file's versions, rebuilt from the edit events in order.

    `submitted` is the final content by path, from `submission/files/`. `outcomes` is
    each test id's matrix cell, attached to the version that introduced the test.
    """
    raise NotImplementedError
