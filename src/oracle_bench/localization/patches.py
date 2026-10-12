"""Read a unified diff and apply it to the buggy file it was written against."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


@dataclass
class Hunk:
    old_start: int
    old_length: int
    lines: list[str] = field(default_factory=list)


@dataclass
class FileDiff:
    old_path: str | None  # None when the repair creates the file
    new_path: str | None  # None when the repair deletes the file
    hunks: list[Hunk] = field(default_factory=list)


def parse_patch(patch: str) -> list[FileDiff]:
    """Split a unified diff into files and hunks, counting lines to find hunk ends."""
    files: list[FileDiff] = []
    old_left = new_left = 0
    for line in patch.splitlines():
        if old_left or new_left:
            hunk = files[-1].hunks[-1]
            hunk.lines.append(line)
            tag = line[:1]
            if tag in (" ", ""):
                old_left, new_left = old_left - 1, new_left - 1
            elif tag == "-":
                old_left -= 1
            elif tag == "+":
                new_left -= 1
            continue
        if line.startswith("diff --git "):
            files.append(FileDiff(None, None))
        elif line.startswith("--- ") and files:
            files[-1].old_path = _diff_path(line[4:], "a/")
        elif line.startswith("+++ ") and files:
            files[-1].new_path = _diff_path(line[4:], "b/")
        elif (match := HUNK.match(line)) and files:
            old_length = int(match.group(2) or 1)
            files[-1].hunks.append(Hunk(int(match.group(1)), old_length))
            old_left, new_left = old_length, int(match.group(4) or 1)
    return files


def _diff_path(text: str, prefix: str) -> str | None:
    path = text.split("\t")[0]
    if path == "/dev/null":
        return None
    return path[len(prefix) :] if path.startswith(prefix) else path


def _first_old_line(hunk: Hunk) -> int:
    # A hunk that removes nothing is written as `-N,0`: it inserts after line N.
    return hunk.old_start if hunk.old_length else hunk.old_start + 1


def locate_hunks(source: str, diff: FileDiff) -> list[int]:
    """The real first old line of each hunk.

    A hunk's context and removed lines must match the buggy file exactly. Like
    `patch`, a hunk whose header is off may still apply at a nearby offset; the
    nearest exact match wins. A hunk that matches nowhere is an error.
    """
    old = [line.rstrip("\r") for line in source.splitlines()]
    starts = []
    floor = 0
    for hunk in diff.hunks:
        expected = [line[1:].rstrip("\r") for line in hunk.lines if line[:1] in (" ", "", "-")]
        stated = _first_old_line(hunk) - 1
        for offset in sorted(range(-len(old), len(old) + 1), key=abs):
            start = stated + offset
            if start >= floor and old[start : start + len(expected)] == expected:
                break
        else:
            raise ValueError(f"hunk at line {stated + 1} matches nowhere in the buggy file")
        starts.append(start + 1)
        floor = start + len(expected)
    return starts


def apply_patch(source: str, diff: FileDiff) -> str:
    """Rebuild the fixed file from the located hunks."""
    old = source.splitlines()
    out: list[str] = []
    position = 0
    for hunk, first in zip(diff.hunks, locate_hunks(source, diff)):
        out.extend(old[position : first - 1])
        position = first - 1
        for line in hunk.lines:
            tag, text = line[:1], line[1:]
            if tag in (" ", ""):
                out.append(old[position])
                position += 1
            elif tag == "-":
                position += 1
            elif tag == "+":
                out.append(text)
    out.extend(old[position:])
    return "\n".join(out) + "\n"
