"""Independent cross-check: attribute each changed diff line to the function holding it.

This maps the diff's buggy-file line numbers onto the buggy file's function
spans. It shares no logic with `localize.py`, which compares syntax trees, so
agreement between the two is evidence that both read the patch correctly.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from patches import FileDiff, locate_hunks

NEW_DEFINITION = ("def ", "async def ", "class ", "@")


@dataclass(frozen=True)
class Unit:
    qualname: str
    start: int  # first decorator line, or the `def` line
    end: int
    col: int


@dataclass
class FileTarget:
    units: list[str]  # changed functions, in file order
    class_level: list[str]  # classes whose body changed outside any method
    module_level: bool  # a change outside every class and function


def units(tree: ast.Module) -> tuple[list[Unit], list[Unit]]:
    """Every function or method, and every class, with exact line spans."""
    functions: list[Unit] = []
    classes: list[Unit] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = min([child.lineno, *(d.lineno for d in child.decorator_list)])
                unit = Unit(prefix + child.name, start, child.end_lineno or start, child.col_offset)
                if isinstance(child, ast.ClassDef):
                    classes.append(unit)
                    walk(child, unit.qualname + ".")
                else:
                    functions.append(unit)
            else:
                walk(child, prefix)

    walk(tree, "")
    return functions, classes


def _containing(units_: list[Unit], line: int) -> Unit | None:
    found = [unit for unit in units_ if unit.start <= line <= unit.end]
    return max(found, key=lambda unit: unit.start) if found else None


def attribute_lines(source: str, diff: FileDiff) -> FileTarget:
    """Attribute every changed line of one file to the function that holds it."""
    functions, classes = units(ast.parse(source))
    old = source.splitlines()
    named: dict[str, Unit] = {}
    class_level: dict[str, None] = {}
    module_level = False

    def credit(line: int, indent: int | None = None) -> None:
        nonlocal module_level
        function = _containing(functions, line)
        if function and (indent is None or function.col < indent):
            named[function.qualname] = function
            return
        owner = _containing(classes, line)
        if owner and (indent is None or owner.col < indent):
            class_level[owner.qualname] = None
        else:
            module_level = True

    for hunk, position in zip(diff.hunks, locate_hunks(source, diff)):
        new_definition_indent: int | None = None
        for line in hunk.lines:
            tag, text = line[:1], line[1:]
            if tag in (" ", ""):
                new_definition_indent = None
                position += 1
                continue
            if tag == "-":
                new_definition_indent = None
                if text.strip():
                    credit(position)
                position += 1
                continue
            if tag != "+" or not text.strip():
                continue
            # Added code: where does it land in the buggy file?
            indent = len(text) - len(text.lstrip())
            if new_definition_indent is not None and indent > new_definition_indent:
                continue  # the body of a definition the repair adds
            if text.lstrip().startswith(NEW_DEFINITION):
                new_definition_indent = indent
                continue  # a new definition is not existing code
            new_definition_indent = None
            credit_insertion(position, indent, old, functions, classes, credit)

    ordered = sorted(named.values(), key=lambda unit: unit.start)
    return FileTarget([unit.qualname for unit in ordered], list(class_level), module_level)


def credit_insertion(position, indent, old, functions, classes, credit) -> None:
    """Credit an added statement to the definition whose body it extends.

    The statement is inserted before old line `position`. It belongs to the
    nearest non-blank line above it when that line's definition is less indented
    than the statement. Otherwise it may open the body of the next definition,
    but only when the next line is inside that body, not its `def` line.
    """
    above = position - 1
    while above >= 1 and not old[above - 1].strip():
        above -= 1
    if above >= 1:
        function = _containing(functions, above)
        if function and function.col < indent:
            credit(above, indent)
            return
    below = position
    while below <= len(old) and not old[below - 1].strip():
        below += 1
    if below <= len(old):
        function = _containing(functions, below)
        if function and function.start < below and function.col < indent:
            credit(below, indent)
            return
    credit(above if above >= 1 else below, indent)
