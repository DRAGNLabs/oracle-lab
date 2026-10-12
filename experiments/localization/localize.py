"""Name the part of a buggy file that a repair changes, by comparing syntax trees.

The target is the existing functions whose syntax tree differs between the
buggy file and the repaired file. A function is a top-level function or a
method, qualified by its classes (`Outer.Inner.method`). Code nested inside a
function belongs to it, because a test cannot call it directly.

Comparing trees ignores comments and formatting, and code the repair adds only
counts through the existing code it changes. When no existing function changes,
the target falls back to the classes whose own body changed (a new method, a
changed attribute or base), and then to the whole file.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)


@dataclass(frozen=True)
class Definition:
    qualname: str
    start: int  # first decorator line, or the `def`/`class` line
    end: int
    shape: str  # what must stay equal for the definition to count as unchanged


@dataclass
class FileChange:
    path: str
    functions: list[Definition]  # existing functions the repair altered or removed
    classes: list[Definition]  # existing classes whose own body changed
    altered: bool  # whether the syntax tree changed at all, beyond comments and formatting
    lines: int  # length of the buggy file


@dataclass
class Target:
    level: str  # "function", "class" or "file"
    names: list[str]  # `path::qualname`, or bare paths for "file"
    lines: int  # buggy-file lines the target spans


def definitions(source: str) -> tuple[dict, dict]:
    """Functions and classes keyed by (qualified name, occurrence).

    The occurrence number tells same-named definitions apart, such as a
    property getter and its setter.
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


def changed(before: dict, after: dict) -> list[Definition]:
    """Definitions in the buggy file that the repair removed or altered."""
    return [
        definition
        for key, definition in before.items()
        if key not in after or after[key].shape != definition.shape
    ]


def compare(path: str, buggy: str, fixed: str) -> FileChange:
    """What the repair changes in one buggy file. `fixed` is empty for a deleted file."""
    functions, classes = definitions(buggy)
    after_functions, after_classes = definitions(fixed) if fixed else ({}, {})
    return FileChange(
        path,
        sorted(changed(functions, after_functions), key=lambda d: d.start),
        sorted(changed(classes, after_classes), key=lambda d: d.start),
        not fixed or ast.dump(ast.parse(buggy)) != ast.dump(ast.parse(fixed)),
        len(buggy.splitlines()),
    )


def localize(files: list[FileChange]) -> Target:
    """The most specific level any changed file reaches: functions, then classes, then files."""
    for level, found in (
        ("function", [(f.path, d) for f in files for d in f.functions]),
        ("class", [(f.path, d) for f in files for d in f.classes]),
    ):
        if found:
            names = list(dict.fromkeys(f"{path}::{d.qualname}" for path, d in found))
            return Target(level, names, sum(d.end - d.start + 1 for _, d in found))
    files = [f for f in files if f.altered] or files
    return Target("file", [f.path for f in files], sum(f.lines for f in files))
