"""Choose a localized run's target by comparing the buggy and repaired syntax trees.

The target is the existing functions whose syntax tree the repair changes. A
function is a top-level function or a method, qualified by its classes
(`Outer.Inner.method`). Code nested inside a function belongs to it, because a
test cannot call it directly.

Comparing trees ignores comments, formatting, and docstrings, and code the
repair adds only counts through the existing code it changes. When no existing function changes,
the target falls back to the classes whose own body changed (a new method, a
changed attribute or base), and then to the changed files.
"""

from __future__ import annotations

import ast
import warnings
from collections import Counter
from dataclasses import dataclass
from typing import Literal

from oracle_lab.contracts.run import LocalizedTarget, TargetFile

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


def parse(source: str) -> ast.Module:
    """The syntax tree without docstrings, which, like comments, change no behavior.

    Raises `SyntaxError` when the source does not parse.
    """
    with warnings.catch_warnings():
        # Repository code often has invalid escapes, which Python only warns about.
        warnings.simplefilter("ignore", SyntaxWarning)
        tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, *FUNCTION)) and _has_docstring(node):
            node.body = node.body[1:]
    return tree


def _has_docstring(node: ast.Module | ast.ClassDef | ast.FunctionDef) -> bool:
    first = node.body[0] if node.body else None
    return (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    )


def definitions(source: str) -> tuple[list[Definition], list[Definition]]:
    """Every function and class in the source, in file order.

    Same-named definitions, such as a property getter and its setter, each get
    their own entry. Raises `SyntaxError` when the source does not parse.
    """
    functions: list[Definition] = []
    classes: list[Definition] = []

    def add(table: list, node: ast.AST, name: str, shape: str) -> None:
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        table.append(Definition(name, start, node.end_lineno or start, shape))

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNCTION):
                add(functions, child, prefix + child.name, ast.dump(child))
            elif isinstance(child, ast.ClassDef):
                add(classes, child, prefix + child.name, _class_shape(child))
                walk(child, prefix + child.name + ".")
            else:
                walk(child, prefix)

    walk(parse(source), "")
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


def _changed(before: list[Definition], after: list[Definition]) -> list[Definition]:
    """Definitions in the buggy file that the repair removed or altered, in file order.

    A buggy definition is unchanged when the repaired file still has a same-named
    definition with the same shape. Each repaired definition vouches for one buggy
    one, so a same-named definition the repair adds, such as an `@overload` stub,
    never makes an untouched one look changed.
    """
    remaining = Counter((d.qualname, d.shape) for d in after)
    found = []
    for definition in before:
        key = (definition.qualname, definition.shape)
        if remaining[key]:
            remaining[key] -= 1
        else:
            found.append(definition)
    return found


def compare(path: str, buggy: str, fixed: str | None) -> FileChange:
    """Compare one buggy file with its repaired version. `fixed` is None for a deleted file."""
    functions, classes = definitions(buggy)
    after_functions, after_classes = definitions(fixed) if fixed is not None else ([], [])
    altered = fixed is None or ast.dump(parse(buggy)) != ast.dump(parse(fixed))
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
