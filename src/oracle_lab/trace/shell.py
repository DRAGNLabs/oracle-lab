"""What a shell command did: split it into sub-commands, then classify each one.

Parsing uses tree-sitter-bash, which handles heredocs, pipelines, and `;`/`&&`/`||`
lists, and tolerates broken input. `sh -c`/`sh -lc` wrappers (every Codex command)
are unwrapped by parsing the quoted script again. A pipeline stays one sub-command,
classified by its first command.
"""

from __future__ import annotations

from dataclasses import dataclass

from oracle_lab.harnesses.steps import EditFile, ReadFile, Search, WriteFile


@dataclass(frozen=True)
class Command:
    """One element of a shell list, with any prefix like `cd x &&` or `env A=b` already split off."""

    text: str  # this sub-command's source text
    argv: list[str]  # words after quote removal; argv[0] is the program
    heredoc: str | None = None
    redirect: str | None = None  # the file a `>`, `>>`, or `tee` writes to


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
    git_history: bool  # `git log`, `git show`, `git blame`, `git diff <rev>`


ShellAction = (
    ReadFile | Search | WriteFile | EditFile | PatchFile | RunTests | RunPython | RunCommand
)


def split(command: str) -> list[Command]:
    """The sub-commands of a shell command, in order, after unwrapping `sh -c` scripts."""
    raise NotImplementedError


def classify(command: Command) -> list[ShellAction]:
    """What one sub-command did. Usually one action; `apply_patch` gives one per file.

    `sed -n 'a,bp'`, `head`, `tail`, and `cat` are reads; `grep`/`rg` are searches;
    `cat > f <<EOF` and `tee` are writes; `sed -i` is an edit with unknown strings.
    """
    raise NotImplementedError
