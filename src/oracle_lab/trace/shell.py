"""What a shell command did: split it into sub-commands, then classify each one.

Parsing uses tree-sitter-bash, which handles heredocs, pipelines, and `;`/`&&`/`||`
lists, and tolerates broken input. `sh -c`/`sh -lc` wrappers (every Codex command)
are unwrapped by parsing the quoted script again. A pipeline stays one sub-command,
classified by its first command.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath

import tree_sitter_bash
from tree_sitter import Language, Node, Parser

from oracle_lab.harnesses.steps import EditFile, ReadFile, Search, WriteFile

_PARSER = Parser(Language(tree_sitter_bash.language()))
_SHELLS = {"sh", "bash", "dash", "zsh"}
_PYTHON = re.compile(r"python(\d+(\.\d+)*)?$")
# Leading programs that run the rest of the argv as the real command.
_WRAPPERS = {"timeout", "env", "nice", "time", "stdbuf", "nohup"}
_SEARCHES = {"grep", "egrep", "fgrep", "rg", "ag", "ack"}
# Search flags that take a value as the next word.
_SEARCH_VALUES = {
    "-e", "-f", "-g", "--glob", "-t", "--type", "-T", "--type-not", "-A", "-B", "-C",
    "-m", "--max-count", "--include", "--exclude", "--exclude-dir", "-d", "--max-depth",
}  # fmt: skip
_BLOCKS = {"program", "list", "compound_statement", "subshell"}
_READERS = {"nl", "less", "more", "bat"}
_HISTORY = {"log", "show", "blame"}


@dataclass(frozen=True)
class Command:
    """One element of a shell list, with any wrapper like `timeout 60` or `env A=b` stripped."""

    text: str  # this sub-command's source text
    argv: list[str]  # words after quote removal; argv[0] is the program
    heredoc: str | None = None
    redirect: str | None = None  # the file a `>`, `>>`, or `tee` writes to
    appends: bool = False  # the redirect is `>>` or `tee -a`


@dataclass(frozen=True)
class PatchFile:
    """An `apply_patch` update to an existing file: Codex's hunk text, without line numbers."""

    path: str
    patch: str


@dataclass(frozen=True)
class RunTests:
    command: str


@dataclass(frozen=True)
class RunPython:
    """A Python invocation that is not pytest: `python -c`, `python - <<EOF`, `python x.py`."""

    command: str
    script: str  # the code it ran, when visible in the command


@dataclass(frozen=True)
class RunCommand:
    command: str
    git_history: bool = False  # `git log`, `git show`, `git blame`


ShellAction = (
    ReadFile | Search | WriteFile | EditFile | PatchFile | RunTests | RunPython | RunCommand
)


def split(command: str) -> list[Command]:
    """The sub-commands of a shell command, in order, after unwrapping `sh -c` scripts."""
    tree = _PARSER.parse(command.encode())
    found = []
    for statement, redirects in _statements(tree.root_node):
        parsed = _command(statement, redirects)
        script = _shell_script(parsed.argv)
        found += split(script) if script is not None else [parsed]
    return found


def classify(command: Command) -> list[ShellAction]:
    """What one sub-command did. Usually one action; `apply_patch` gives one per file.

    `sed -n 'a,bp'`, `head`, `tail`, `cat`, and `awk 'NR>=a && NR<=b'` are reads;
    `grep`/`rg` are searches; `cat > f <<EOF`, `tee`, and `echo > f` are writes;
    `sed -i` and appends are edits with unknown strings.
    """
    argv, text = command.argv, command.text
    if not argv:
        return [RunCommand(text)]
    program, args = PurePosixPath(argv[0]).name, argv[1:]
    if program == "apply_patch":
        patch = command.heredoc if command.heredoc is not None else " ".join(args)
        return _apply_patch(patch) or [RunCommand(text)]
    if _PYTHON.match(program):
        return [_python(command, args)]
    if program in {"pytest", "py.test"}:
        return [_pytest(text, args)]
    if command.redirect is not None and program in {"cat", "tee", "echo", "printf"}:
        return [_written(command, args, program)]
    if program == "cat":
        return [ReadFile(path) for path in _operands(args)] or [RunCommand(text)]
    if program == "sed":
        return _sed(args) or [RunCommand(text)]
    if program in {"head", "tail"}:
        return _head_tail(program, args) or [RunCommand(text)]
    if program == "awk":
        return _awk(args) or [RunCommand(text)]
    if program in _READERS:
        return [ReadFile(path) for path in _operands(args)] or [RunCommand(text)]
    if program in _SEARCHES:
        return _search(args) or [RunCommand(text)]
    if program == "git":
        return [_git(text, args)]
    return [RunCommand(text)]


def _statements(node: Node, redirects: tuple[Node, ...] = ()) -> Iterator[tuple[Node, tuple]]:
    """Each simple statement of a list, subshell, or block, in order. Pipelines stay whole.

    tree-sitter wraps a whole list in a redirected statement when its last command
    has a heredoc or redirect (`cd x && python - <<EOF`); those redirects belong to
    the list's last statement. It also parses `a && b 2>&1 | tail` as a pipeline
    around the list; the list's statements are what ran.
    """
    if node.type in _BLOCKS:
        children = [child for child in node.named_children if child.type != "comment"]
        for index, child in enumerate(children):
            yield from _statements(child, redirects if index == len(children) - 1 else ())
    elif (
        node.type == "redirected_statement"
        and node.named_children
        and node.named_children[0].type in _BLOCKS
    ):
        yield from _statements(node.named_children[0], tuple(node.children[1:]) + redirects)
    elif node.type == "pipeline" and node.named_children and _has_list(node.named_children[0]):
        yield from _statements(node.named_children[0], redirects)
    elif node.type != "comment":
        yield node, redirects


def _has_list(node: Node) -> bool:
    if node.type == "redirected_statement" and node.named_children:
        node = node.named_children[0]
    return node.type in _BLOCKS


def _command(statement: Node, redirects: tuple[Node, ...]) -> Command:
    """A statement as a Command: its first simple command, plus redirects and heredoc."""
    text = _text(statement)
    redirect, appends, heredoc = None, False, None
    for child in redirects:
        if child.type == "heredoc_redirect":
            heredoc = _heredoc(child)
            text = f"{text} <<\n{heredoc}"
        elif child.type == "file_redirect" and redirect is None:
            redirect, appends = _file_redirect(child) or (None, False)
    node = statement
    while node.type in {"pipeline", "redirected_statement", "negated_command"}:
        if node.type == "pipeline":
            tee = _tee_target(node)
            if tee is not None:
                redirect, appends = tee
        if node.type == "redirected_statement":
            for child in node.children[1:]:
                if child.type == "heredoc_redirect":
                    heredoc = _heredoc(child)
                elif child.type == "file_redirect" and redirect is None:
                    redirect, appends = _file_redirect(child) or (None, False)
        if not node.named_children:
            break
        node = node.named_children[0]
    if node.type != "command":
        return Command(text, [], heredoc, redirect, appends)
    argv = []
    for child in node.named_children:
        if child.type == "command_name":
            argv.append(_word(child.named_children[0]) if child.named_children else _text(child))
        elif child.type == "file_redirect" and redirect is None:
            redirect, appends = _file_redirect(child) or (None, False)
        elif child.type == "heredoc_redirect":
            heredoc = _heredoc(child)
        elif argv and child.type not in {"variable_assignment", "herestring_redirect"}:
            argv.append(_word(child))
    return Command(text, _unwrapped(argv), heredoc, redirect, appends)


def _unwrapped(argv: list[str]) -> list[str]:
    """Drop leading wrappers like `timeout 60`, `env A=b`, or `nice -n 5`."""
    while argv and PurePosixPath(argv[0]).name in _WRAPPERS:
        wrapper, rest = PurePosixPath(argv[0]).name, argv[1:]
        while rest and (rest[0].startswith("-") or "=" in rest[0]):
            rest = rest[2:] if wrapper == "nice" and rest[0] == "-n" else rest[1:]
        if wrapper == "timeout" and rest:
            rest = rest[1:]  # the duration
        argv = rest
    return argv


def _shell_script(argv: list[str]) -> str | None:
    """The script of `sh -c script` / `bash -lc script`, or None for anything else."""
    if not argv or PurePosixPath(argv[0]).name not in _SHELLS:
        return None
    for index, word in enumerate(argv[1:-1], start=1):
        if re.fullmatch(r"-[a-z]*c[a-z]*", word):
            return argv[index + 1]
    return None


def _tee_target(pipeline: Node) -> tuple[str, bool] | None:
    for child in pipeline.named_children:
        if child.type == "command":
            words = [_word(part) for part in child.named_children]
            if words and words[0] == "tee":
                files = [word for word in words[1:] if not word.startswith("-")]
                if files:
                    return files[0], "-a" in words or "--append" in words
    return None


def _file_redirect(node: Node) -> tuple[str, bool] | None:
    """The target of a stdout redirect (`>`, `>>`, `&>`), or None for other descriptors."""
    if any(child.type == "file_descriptor" for child in node.children):
        return None
    operator = node.children[0].type if node.children else ""
    if operator not in {">", ">>", "&>", "&>>", ">|"}:
        return None
    target = node.child_by_field_name("destination") or (
        node.named_children[-1] if node.named_children else None
    )
    if target is None:
        return None
    path = _word(target)
    return None if path.startswith("/dev/") else (path, operator.endswith(">>"))


def _heredoc(node: Node) -> str:
    body = next((child for child in node.children if child.type == "heredoc_body"), None)
    return _text(body) if body is not None else ""


def _text(node: Node) -> str:
    return (node.text or b"").decode(errors="replace")


def _word(node: Node) -> str:
    """A word's value after quote removal. Expansions are kept as written."""
    text = _text(node)
    if node.type == "raw_string":
        return text[1:-1]
    if node.type == "string":
        return re.sub(r'\\([\\"$`\n])', r"\1", text[1:-1])
    if node.type == "ansi_c_string":
        return text[2:-1]
    if node.type == "concatenation":
        return (
            "".join(_word(child) for child in node.named_children) if node.named_children else text
        )
    if node.type == "word":
        return re.sub(r"\\(.)", r"\1", text)
    return text


def _operands(args: list[str]) -> list[str]:
    return [arg for arg in args if not arg.startswith("-")]


def _python(command: Command, args: list[str]) -> ShellAction:
    if "-m" in args:
        module = args[args.index("-m") + 1] if args.index("-m") + 1 < len(args) else ""
        return _pytest(command.text, args) if module == "pytest" else RunCommand(command.text)
    if "-c" in args and args.index("-c") + 1 < len(args):
        return RunPython(command.text, args[args.index("-c") + 1])
    if command.heredoc is None and not _operands(args):
        return RunCommand(command.text)  # `python --version` and the like run no code
    return RunPython(command.text, command.heredoc or "")


def _pytest(text: str, args: list[str]) -> ShellAction:
    """A pytest run, unless it only asks pytest about itself."""
    if any(arg in {"--version", "-V", "--help", "-h"} for arg in args):
        return RunCommand(text)
    return RunTests(text)


def _written(command: Command, args: list[str], program: str) -> ShellAction:
    path = command.redirect or ""
    if command.appends:
        return EditFile(path, None, None)
    if program in {"cat", "tee"} and command.heredoc is not None:
        return WriteFile(path, command.heredoc)
    if program == "echo" and not any(arg.startswith("-") for arg in args):
        return WriteFile(path, " ".join(args) + "\n")
    return EditFile(path, None, None)


def _apply_patch(patch: str) -> list[ShellAction]:
    """One action per file in Codex's `*** Begin Patch` format."""
    actions: list[ShellAction] = []
    current: tuple[str, str, list[str]] | None = None  # (operation, path, lines)

    def close() -> None:
        if current is None:
            return
        operation, path, lines = current
        if operation == "Add":
            actions.append(WriteFile(path, "".join(line[1:] + "\n" for line in lines)))
        elif operation == "Update":
            actions.append(PatchFile(path, "\n".join(lines) + "\n"))
        else:
            actions.append(EditFile(path, None, None))

    for line in patch.splitlines():
        header = re.match(r"\*\*\* (Add|Update|Delete) File: (.+)", line)
        if header:
            close()
            current = (header.group(1), header.group(2).strip(), [])
        elif line.startswith("*** End Patch"):
            close()
            current = None
        elif line.startswith("*** Move to: ") and current is not None:
            current = (current[0], line.removeprefix("*** Move to: ").strip(), current[2])
        elif current is not None and not line.startswith("*** "):
            current[2].append(line)
    close()
    return actions


def _sed(args: list[str]) -> list[ShellAction]:
    in_place = any(arg == "-i" or arg.startswith(("-i", "--in-place")) for arg in args)
    quiet = any(arg in {"-n", "--quiet", "--silent"} or re.fullmatch(r"-[a-zA-Z]*n[a-zA-Z]*", arg)
                for arg in args)  # fmt: skip
    scripts, files, rest = [], [], iter(args)
    for arg in rest:
        if arg in {"-e", "--expression"}:
            scripts.append(next(rest, ""))
        elif not arg.startswith("-"):
            files.append(arg)
    if not scripts and files:
        scripts.append(files.pop(0))
    if not files:
        return []
    if in_place:
        return [EditFile(path, None, None) for path in files]
    if not quiet:
        return [ReadFile(path) for path in files]
    ranges = []
    for script in scripts:
        for part in re.split(r"[;\n]", script):
            match = re.fullmatch(r"\s*(\d+)(?:,(\d+|\$))?\s*p\s*", part)
            if match is None:
                return [ReadFile(path) for path in files]  # some other script: lines unknown
            start = int(match.group(1))
            end = match.group(2)
            ranges.append((start, None if end == "$" else int(end) if end else start))
    return [ReadFile(path, start, end) for path in files for start, end in ranges]


def _head_tail(program: str, args: list[str]) -> list[ShellAction]:
    count, files, rest = 10, [], iter(args)
    for arg in rest:
        if arg in {"-n", "--lines"}:
            value = next(rest, "10")
            count = int(value.lstrip("+-")) if value.lstrip("+-").isdigit() else count
        elif re.fullmatch(r"-n?\d+", arg) or arg.startswith("--lines="):
            count = int(re.sub(r"\D", "", arg))
        elif arg in {"-c", "--bytes"}:
            next(rest, None)
        elif not arg.startswith("-"):
            files.append(arg)
    if program == "tail":
        return [ReadFile(path) for path in files]  # the last lines; their numbers are unknown
    return [ReadFile(path, 1, count) for path in files]


def _awk(args: list[str]) -> list[ShellAction]:
    operands = _operands(args)
    if len(operands) < 2:
        return []
    program, files = operands[0], operands[1:]
    low = re.search(r"NR\s*>=?\s*(\d+)", program)
    high = re.search(r"NR\s*<=?\s*(\d+)", program)
    exact = re.fullmatch(r"\s*NR\s*==\s*(\d+)\s*", program)
    if exact:
        line = int(exact.group(1))
        return [ReadFile(path, line, line) for path in files]
    if low or high:
        start = int(low.group(1)) if low else 1
        end = int(high.group(1)) if high else None
        return [ReadFile(path, start, end) for path in files]
    return []


def _search(args: list[str]) -> list[ShellAction]:
    if "--files" in args:  # lists files; nothing is searched
        return []
    pattern, paths, rest = None, [], iter(args)
    for arg in rest:
        if arg in {"-e", "--regexp"}:
            pattern = next(rest, "")
        elif arg in _SEARCH_VALUES:
            next(rest, None)
        elif arg.startswith("-"):
            continue
        elif pattern is None:
            pattern = arg
        else:
            paths.append(arg)
    if pattern is None:
        return []
    return [Search(pattern, path) for path in paths] or [Search(pattern)]


def _git(text: str, args: list[str]) -> ShellAction:
    rest = iter(args)
    for arg in rest:
        if arg in {"-C", "-c", "--git-dir", "--work-tree"}:
            next(rest, None)
        elif not arg.startswith("-"):
            if arg == "grep":
                search = _search(list(rest))
                return search[0] if search else RunCommand(text)
            return RunCommand(text, git_history=arg in _HISTORY)
    return RunCommand(text)
