"""Oracle Lab's old diff-text target heuristic, frozen for comparison.

Copied unchanged from `src/oracle_lab/instance/swebench.py` at the commit before
`4a85d45`, which removed it. `evaluate.py` scores it against the AST method.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath

DIFF_FILE = re.compile(r"^\+\+\+ b/(.+)$")
PYTHON_SCOPE = re.compile(r"^([ ]*)(?:async[ ]+def|def|class)[ ]+([A-Za-z_]\w*)")
HUNK_HEADER = re.compile(r"^@@ .+ @@(?: (.*))?$")


def localized_test_target(patch: str, source_roots: list[str]) -> str:
    """Derive an agent-safe code location without exposing repair semantics.

    Each changed line is attributed to the definition enclosing it in the hunk's
    context or removed lines. Added lines only close scopes, so a name introduced
    by the repair cannot leak. The hunk header, which names the nearest definition
    above the hunk rather than the edited one, is only a fallback.
    """
    roots = tuple(PurePosixPath(root) for root in source_roots)
    targets: dict[str, set[str]] = {}
    current: str | None = None
    header: str | None = None
    header_is_class = False
    scopes: list[tuple[int, str]] = []
    scope_seen = False

    for line in patch.splitlines():
        match = DIFF_FILE.match(line)
        if match:
            candidate = PurePosixPath(match.group(1))
            in_source = any(root == candidate or root in candidate.parents for root in roots)
            current = str(candidate) if in_source else None
            if current and not _is_test_path(candidate):
                targets.setdefault(current, set())
            else:
                current = None
            continue
        hunk = HUNK_HEADER.match(line)
        if hunk:
            scope = PYTHON_SCOPE.match(hunk.group(1) or "")
            header = scope.group(2) if scope else None
            header_is_class = bool(scope) and scope.group(0).lstrip().startswith("class")
            scopes = []
            scope_seen = False
            continue
        if current is None or line.startswith(("+++", "---")) or line[:1] not in " +-":
            continue
        content = line[1:]
        if not content.strip():
            continue
        indent = len(content) - len(content.lstrip(" "))
        scope = PYTHON_SCOPE.match(content)
        # A re-added definition, such as a changed signature, stays in the same scope.
        same = bool(scope) and (indent, scope.group(2)) in scopes
        # Any line closes the scopes it dedents past; only existing code opens one.
        scopes = [
            (depth, name) for depth, name in scopes if depth < indent or (same and depth == indent)
        ]
        if scope and not line.startswith("+") and not same:
            scopes.append((indent, scope.group(2)))
            scope_seen = True
        if line.startswith(("+", "-")):
            names = [name for _, name in scopes]
            # An indented definition right below a class header is that class's member.
            if names and header_is_class and scopes[0][0] > 0:
                names.insert(0, header)
            # The header lies above the hunk, so it no longer applies once the hunk
            # shows its own definitions.
            symbol = ".".join(names) if names else None if scope_seen else header
            if symbol:
                targets[current].add(symbol)

    if not targets:
        raise ValueError("Golden patch does not modify a Python production source path")
    rendered = []
    for path, symbols in targets.items():
        if symbols:
            rendered.append(f"{path} ({', '.join(sorted(symbols))})")
        else:
            rendered.append(path)
    return ", ".join(rendered)


def _is_test_path(path: PurePosixPath) -> bool:
    return "tests" in path.parts or path.name.startswith(("test_", "tests_"))
