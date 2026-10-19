"""What the page shows for one run: typed, annotated events and test file histories.

These are built on request and never persisted, so they are plain dataclasses
rather than artifact contracts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from oracle_lab.harnesses.steps import EditFile, WriteFile
from oracle_lab.trace.shell import PatchFile


class ActionType(StrEnum):
    READ = "read"
    SEARCH = "search"
    EDIT_TEST = "edit_test"
    EDIT_SOURCE = "edit_source"
    RUN_TESTS = "run_tests"
    PROBE_CODE = "probe_code"  # a Python invocation that is not pytest
    RUN_OTHER = "run_other"
    REASONING = "reasoning"
    MESSAGE = "message"
    FAILED_CALL = "failed_call"  # aborted or rejected by the harness, not a nonzero exit


class FileCategory(StrEnum):
    SOURCE = "source"
    TEST = "test"
    GENERATED_TEST = "generated_test"
    DOCS = "docs"
    CHANGELOG = "changelog"
    CONFIG = "config"
    OUTSIDE = "outside"  # outside the repository root


class DefectOverlap(StrEnum):
    LINES = "lines"  # overlaps a patched hunk's buggy-side lines
    FILE = "file"  # in a patched file, but not on its patched lines
    NONE = "none"


@dataclass
class Touch:
    """One file an event read, searched, or changed."""

    path: str  # repository-relative, or as written when outside the repository
    category: FileCategory
    # 1-based, inclusive buggy-side lines; None is the whole file or unknown lines.
    lines: tuple[int, int] | None
    overlap: DefectOverlap


@dataclass
class PytestSummary:
    """Counts by outcome word (`passed`, `failed`, ...) and the ids pytest printed as FAILED."""

    counts: dict[str, int]
    failed: list[str]


@dataclass
class Event:
    """One row of the timeline.

    `step` is the index of the harness step; `part` numbers the sub-commands of a
    chained shell command, which share that step's output.
    """

    step: int
    part: int
    action: ActionType
    trace_line: int
    touches: list[Touch] = field(default_factory=list)
    command: str = ""
    output: str = ""
    exit_code: int | None = None
    text: str = ""  # reasoning or message text
    timestamp: str | None = None
    git_history: bool = False
    # Names defined in patched hunks that a probe or test edit mentions.
    patched_names: list[str] = field(default_factory=list)
    pytest: PytestSummary | None = None
    # The change an edit event made, for rebuilding test file versions.
    change: WriteFile | EditFile | PatchFile | None = None

    @property
    def description(self) -> str:
        """A one-line description built from the fields above, never by an LLM.

        E.g. `Read requests/models.py L220–400` or `pytest oracle_tests -q → 47 passed, 1 failed`.
        """
        raise NotImplementedError


@dataclass
class TestFileVersion:
    """One version of a generated test file and what happened since the previous one."""

    step: int
    diff: str
    content: str | None  # None when only the edit is known
    partial: bool
    runs_before: list[tuple[int, PytestSummary]]  # (step, summary) since the previous version
    # Final outcome by test id, for tests this version introduced. Hidden on the page by default.
    outcomes: dict[str, str] = field(default_factory=dict)


@dataclass
class TestFileHistory:
    path: str
    versions: list[TestFileVersion]
    matches_submission: bool | None  # None when the final content is unknown


@dataclass
class DefectSummary:
    first_touch: int | None  # step of the first LINES overlap
    lines_shown: int  # changed buggy-side lines that appeared in output; a lower bound
    lines_total: int
    names: list[str]
    target_read: bool | None  # None for runs without a localized target


@dataclass
class RunSummary:
    """A run as the picker lists it."""

    run_id: str
    instance: str
    harness: str
    model: str


@dataclass
class RunTrace:
    run: RunSummary
    reasoning: str  # "raw" or "summary"
    events: list[Event]
    test_files: list[TestFileHistory]
    defect: DefectSummary
    outcomes_available: bool

    def to_json(self) -> dict:
        """The page's view of the run, event descriptions included."""
        raise NotImplementedError
