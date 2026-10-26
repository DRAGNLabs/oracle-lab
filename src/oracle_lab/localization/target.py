"""Choose a localized run's target by comparing the buggy and repaired syntax trees.

The target names the existing functions whose syntax tree the repair changes,
and the classes whose own code (attributes, bases, decorators) it changes. A
function is a top-level function or a method, qualified by its classes
(`Outer.Inner.method`). Code nested inside a function belongs to it, because a
test cannot call it directly.

Comparing trees ignores comments, formatting, and docstrings, and code the
repair adds only counts through the existing code it changes. When neither
exists, the target falls back to the classes that gained or lost methods, and
then to the changed files. Module-level code is never named.

`fix_scope` says where the repair's changes live: inside existing functions, in a
class's own code, or in module-level code.
"""

from __future__ import annotations

import ast
import warnings
from collections import Counter
from dataclasses import dataclass

from oracle_lab.contracts.run import FixScope, LocalizedTarget, TargetFile

FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)
SCOPES: tuple[FixScope, ...] = ("function", "class", "module")  # narrowest first


@dataclass(frozen=True)
class Definition:
    qualname: str
    start: int  # first decorator line, or the `def`/`class` line
    end: int
    shape: str  # what must stay equal for the definition to count as unchanged
    code: str = ""  # a class's own code without its method names; empty for functions

    @property
    def lines(self) -> int:
        return self.end - self.start + 1


@dataclass
class FileChange:
    """What the repair changes in one existing buggy file."""

    path: str
    functions: list[Definition]  # existing functions the repair altered or removed
    classes: list[Definition]  # existing classes whose own body changed
    class_code: list[Definition]  # of those, classes whose attributes, bases, or decorators changed
    module_code: bool  # whether module-level code other than imports and definitions changed
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

    def add(table: list, node: ast.AST, name: str, shape: str, code: str = "") -> None:
        start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
        table.append(Definition(name, start, node.end_lineno or start, shape, code))

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, FUNCTION):
                add(functions, child, prefix + child.name, ast.dump(child))
            elif isinstance(child, ast.ClassDef):
                shape, code = _class_shape(child), _class_code(child)
                add(classes, child, prefix + child.name, shape, code)
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


def _class_code(node: ast.ClassDef) -> str:
    """A class's attributes, bases, and decorators: what no method name accounts for."""
    own = [
        [ast.dump(part) for part in [*node.bases, *node.keywords, *node.decorator_list]],
        [ast.dump(c) for c in node.body if not isinstance(c, (*FUNCTION, ast.ClassDef))],
    ]
    return repr(own)


def _module_code(tree: ast.Module) -> list[str]:
    """Module-level statements other than definitions and imports."""
    return [
        ast.dump(node)
        for node in tree.body
        if not isinstance(node, (*FUNCTION, ast.ClassDef)) and not _imports_only(node)
    ]


def _imports_only(node: ast.stmt) -> bool:
    """An import, or an `if` or `try` that only imports, such as a `TYPE_CHECKING` block."""
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return True
    if isinstance(node, (ast.If, ast.Try)):
        blocks = [node.body, node.orelse]
        if isinstance(node, ast.Try):
            blocks += [node.finalbody, *(handler.body for handler in node.handlers)]
        statements = [child for block in blocks for child in block]
        return all(_imports_only(child) or isinstance(child, ast.Pass) for child in statements)
    return False


def _changed(
    before: list[Definition], after: list[Definition], code_only: bool = False
) -> list[Definition]:
    """Definitions in the buggy file that the repair removed or altered, in file order.

    A buggy definition is unchanged when the repaired file still has a same-named
    definition with the same shape, or with the same `code` when `code_only`. Each
    repaired definition vouches for one buggy one, so a same-named definition the
    repair adds, such as an `@overload` stub, never makes an untouched one look
    changed.
    """
    remaining = Counter((d.qualname, d.code if code_only else d.shape) for d in after)
    found = []
    for definition in before:
        key = (definition.qualname, definition.code if code_only else definition.shape)
        if remaining[key]:
            remaining[key] -= 1
        else:
            found.append(definition)
    return found


def compare(path: str, buggy: str, fixed: str | None) -> FileChange:
    """Compare one buggy file with its repaired version. `fixed` is None for a deleted file."""
    functions, classes = definitions(buggy)
    after_functions, after_classes = definitions(fixed) if fixed is not None else ([], [])
    before_tree = parse(buggy)
    after_tree = parse(fixed) if fixed is not None else ast.Module(body=[], type_ignores=[])
    return FileChange(
        path,
        _changed(functions, after_functions),
        _changed(classes, after_classes),
        _changed(classes, after_classes, code_only=True),
        _module_code(before_tree) != _module_code(after_tree),
        fixed is None or ast.dump(before_tree) != ast.dump(after_tree),
        len(buggy.splitlines()),
    )


def choose_target(files: list[FileChange]) -> LocalizedTarget:
    """Name the changed functions and the classes whose own code changed.

    When neither exists, name the classes that only gained or lost methods, and
    failing that, the changed files.
    """
    if not files:
        raise ValueError("The golden patch changes no existing production Python file")
    entries = [_entry(f.path, f.functions, f.class_code) for f in files]
    if not any(entries):
        entries = [_entry(f.path, [], f.classes) for f in files]
    entries = [entry for entry in entries if entry]
    if not entries:
        # Files whose tree changed; all of them when every change was cosmetic.
        changed = [f for f in files if f.altered] or files
        entries = [
            TargetFile(path=f.path, functions=[], classes=[], lines=f.lines) for f in changed
        ]
    return LocalizedTarget(files=entries, text=render(entries), scope=fix_scope(files))


def _entry(path: str, functions: list[Definition], classes: list[Definition]) -> TargetFile | None:
    """One file's named definitions, or None when it has none."""
    if not functions and not classes:
        return None
    return TargetFile(
        path=path,
        functions=list(dict.fromkeys(d.qualname for d in functions)),
        classes=list(dict.fromkeys(d.qualname for d in classes)),
        lines=sum(d.lines for d in [*functions, *classes]),
    )


def fix_scope(files: list[FileChange]) -> FixScope:
    """Where the repair's changes live, as the broadest of `function`, `class`, and `module`.

    A changed or removed function or method is `function`. A changed or removed
    class attribute, base, or decorator is `class`. Changed module-level code is
    `module`. Comments, formatting, docstrings, imports, and new definitions do not
    count, unless new definitions are the whole repair: then the scope is `class`
    when they went into an existing class, and `module` otherwise.
    """
    found: set[FixScope] = set()
    for change in files:
        if change.functions:
            found.add("function")
        if change.class_code:
            found.add("class")
        if change.module_code:
            found.add("module")
    if not found:
        return "class" if any(change.classes for change in files) else "module"
    return max(found, key=SCOPES.index)


def render(files: list[TargetFile]) -> str:
    """One line per file, for the prompt's `${test_target}`: functions, then classes."""
    lines = []
    for entry in files:
        names = [f"`{name}`" for name in entry.functions]
        if entry.classes:
            kind = "class" if len(entry.classes) == 1 else "classes"
            names.append(f"{kind} " + ", ".join(f"`{name}`" for name in entry.classes))
        lines.append(
            f"- `{entry.path}`: {', '.join(names)}" if names else f"- `{entry.path}` (whole file)"
        )
    return "\n".join(lines)
