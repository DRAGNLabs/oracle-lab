"""The harness-neutral steps an agent took, read back from a saved trace.

Each adapter maps its own tool names and argument keys onto these actions, so
callers never see a harness's event format. Shell commands stay as `Shell`; what
they did is worked out by `oracle_lab.trace.shell`, the same way for every harness.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class ReadFile:
    path: str
    # 1-based, inclusive. None means from the start or to the end of the file.
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class Search:
    pattern: str
    path: str | None = None


@dataclass(frozen=True)
class WriteFile:
    """A write of a file's whole content."""

    path: str
    content: str


@dataclass(frozen=True)
class EditFile:
    """A replacement inside a file. Both strings are None when the CLI does not say."""

    path: str
    old: str | None
    new: str | None


@dataclass(frozen=True)
class Shell:
    command: str


@dataclass(frozen=True)
class OtherTool:
    """A tool the adapter does not map, kept with its raw input."""

    name: str
    input: dict


Action = ReadFile | Search | WriteFile | EditFile | Shell | OtherTool


@dataclass(frozen=True)
class Step:
    """One tool call, reasoning passage, or message, in trace order.

    `line` is the 1-based line of `trace.jsonl` it came from, so the page can show
    the raw event. A tool call the harness aborted or rejected has `failed` set.
    """

    kind: Literal["tool", "reasoning", "message"]
    line: int
    action: Action | None = None
    output: str = ""
    exit_code: int | None = None
    failed: bool = False
    text: str = ""
    timestamp: str | None = None


def numbered_events(path: Path) -> list[tuple[int, dict]]:
    """A trace's JSON object events with their 1-based line numbers. Other lines are skipped."""
    events = []
    if not path.exists():
        return events
    for number, line in enumerate(path.read_text(errors="replace").splitlines(), start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append((number, event))
    return events
