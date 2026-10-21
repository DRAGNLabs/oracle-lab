"""What the page shows for one run: typed, annotated events and test file histories.

These are built on request and never persisted, so they are plain dataclasses
rather than artifact contracts.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
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
    # One line built mechanically from the action, e.g. `Read requests/models.py L220–400`.
    description: str
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


@dataclass
class FileVersion:
    """One version of a generated test file and what happened since the previous one."""

    step: int
    part: int  # the edit's sub-command, so a run chained after it in the same step follows it
    diff: str
    content: str | None  # None when only the edit is known
    partial: bool
    runs_after: list[tuple[int, PytestSummary]]  # (step, summary) before the next version
    # Final outcome by test id, for tests this version introduced. Hidden on the page by default.
    outcomes: dict[str, str] = field(default_factory=dict)
    # Edit facts, also hidden by default. A run between the previous version and this one
    # reported failures or errors; the FAILED ids among them that are in this file.
    follows_failed_run: bool = False
    follows_failures: list[str] = field(default_factory=list)
    skip_marks_added: list[str] = field(default_factory=list)  # added skip/xfail lines


@dataclass
class GeneratedFile:
    path: str
    versions: list[FileVersion]
    matches_submission: bool | None  # None when the final content is unknown
    # Ids in a FAILED line of some run whose test the submitted file no longer defines.
    failed_then_absent: list[str] = field(default_factory=list)


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
    generated_files: list[GeneratedFile]
    defect: DefectSummary
    outcomes_available: bool

    def to_json(self) -> dict:
        """The page's view of the run. Edit contents stay out; test versions carry them."""
        data = asdict(self)
        for event in data["events"]:
            del event["change"]
        return data
