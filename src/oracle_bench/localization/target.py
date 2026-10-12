"""Choose a localized run's target by comparing the buggy and repaired syntax trees.

The target is the existing functions whose syntax tree the repair changes. A
function is a top-level function or a method, qualified by its classes
(`Outer.Inner.method`). Code nested inside a function belongs to it, because a
test cannot call it directly.

Comparing trees ignores comments and formatting, and code the repair adds only
counts through the existing code it changes. When no existing function changes,
the target falls back to the classes whose own body changed (a new method, a
changed attribute or base), and then to the changed files.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from typing import Literal

from oracle_bench.results import LocalizedTarget, TargetFile

FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Definition:
    qualname: str
    start: int  # first decorator line, or the `def`/`class` line
    end: int
    shape: str  # what must stay equal for the definition to count as unchanged

    @property
    def lines(self) -> int:
        return self.end - self.start + 1


@dataclass
class FileChange:
    """What the repair changes in one existing buggy file."""

    path: str
    functions: list[Definition]  # existing functions the repair altered or removed
    classes: list[Definition]  # existing classes whose own body changed
    altered: bool  # whether the syntax tree changed beyond comments and formatting
    lines: int  # length of the buggy file


def definitions(source: str) -> tuple[dict, dict]:
    """Functions and classes keyed by (qualified name, occurrence).

    The occurrence number tells same-named definitions apart, such as a property
    getter and its setter. Raises `SyntaxError` when the source does not parse.
    """
    functions: dict[tuple[str, int], Definition] = {}
    classes: dict[tuple[str, int], Definition] = {}

    def add(table: dict, node: ast.AST, name: str, shape: str) -> None:
        occurrence = 1 + sum(1 for key in table if key[0] == name)
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        table[(name, occurrence)] = Definition(name, start, node.end_lineno or start, shape)

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNCTION):
                add(functions, child, prefix + child.name, ast.dump(child))
            elif isinstance(child, ast.ClassDef):
                add(classes, child, prefix + child.name, _class_shape(child))
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)

    walk(ast.parse(source), "")
    return functions, classes


def _class_shape(node: ast.ClassDef) -> str:
    """A class's own code: everything except the bodies of its methods and inner classes."""
    own = [
        node.name,
        [ast.dump(part) for part in [*node.bases, *node.keywords, *node.decorator_list]],
        [
            f"def {child.name}" if isinstance(child, (*FUNCTION, ast.ClassDef)) else ast.dump(child)
            for child in node.body
        ],
    ]
    return repr(own)


def _changed(before: dict, after: dict) -> list[Definition]:
    """Definitions in the buggy file that the repair removed or altered, in file order."""
    found = [d for key, d in before.items() if key not in after or after[key].shape != d.shape]
    return sorted(found, key=lambda d: d.start)


def compare(path: str, buggy: str, fixed: str | None) -> FileChange:
    """Compare one buggy file with its repaired version. `fixed` is None for a deleted file."""
    functions, classes = definitions(buggy)
    after_functions, after_classes = definitions(fixed) if fixed is not None else ({}, {})
    altered = fixed is None or ast.dump(ast.parse(buggy)) != ast.dump(ast.parse(fixed))
    return FileChange(
        path,
        _changed(functions, after_functions),
        _changed(classes, after_classes),
        altered,
        len(buggy.splitlines()),
    )


def choose_target(files: list[FileChange]) -> LocalizedTarget:
    """The most specific level any changed file reaches: functions, then classes, then files."""
    if not files:
        raise ValueError("The golden patch changes no existing production Python file")
    levels: list[tuple[Literal["function", "class"], list[tuple[str, list[Definition]]]]] = [
        ("function", [(f.path, f.functions) for f in files]),
        ("class", [(f.path, f.classes) for f in files]),
    ]
    for level, found in levels:
        entries = [
            TargetFile(
                path=path,
                names=list(dict.fromkeys(d.qualname for d in defs)),
                lines=sum(d.lines for d in defs),
            )
            for path, defs in found
            if defs
        ]
        if entries:
            return LocalizedTarget(level=level, files=entries, text=render(level, entries))
    # Files whose tree changed; all of them when every change was cosmetic.
    changed = [f for f in files if f.altered] or files
    entries = [TargetFile(path=f.path, names=[], lines=f.lines) for f in changed]
    return LocalizedTarget(level="file", files=entries, text=render("file", entries))


def render(level: str, files: list[TargetFile]) -> str:
    """One line per file, for the prompt's `${test_target}`."""
    lines = []
    for entry in files:
        names = ", ".join(f"`{name}`" for name in entry.names)
        if level == "function":
            lines.append(f"- `{entry.path}`: {names}")
        elif level == "class":
            kind = "class" if len(entry.names) == 1 else "classes"
            lines.append(f"- `{entry.path}`: {kind} {names}")
        else:
            lines.append(f"- `{entry.path}` (whole file)")
    return "\n".join(lines)
