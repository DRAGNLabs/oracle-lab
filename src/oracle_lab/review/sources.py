"""The repository files a rater browses, read from Git without a container.

The upstream repository is fetched once per base commit into a shallow bare cache.
Each view the judge saw is rebuilt there as a Git tree: the buggy view is the base
commit plus the submission, and the golden view also has the repair applied. Files
are then read straight from Git objects, so nothing is checked out.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from oracle_lab.localization.patches import parse_patch
from oracle_lab.results import InstanceRecord
from oracle_lab.review.targets import ReviewTarget, SubmittedFile

# What changed a file: the repair, the reference-test patch, or the submission.
Touch = Literal["repair", "reference_tests", "submission"]
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")


class SourceError(RuntimeError):
    """The repository could not be fetched, or a view could not be built."""


class SourceView:
    """One version of the repository, as the judge saw it."""

    def __init__(self, git_dir: Path, tree: str, touched: dict[str, tuple[Touch, ...]]):
        self._git_dir = git_dir
        self._blobs = _list_blobs(git_dir, tree)
        self._touched = touched

    def tree(self) -> dict[str, tuple[Touch, ...]]:
        """Every file path, with what touched it."""
        return {path: self._touched.get(path, ()) for path in sorted(self._blobs)}

    def read(self, path: str) -> bytes:
        """One file's contents. Only paths in this view's own listing can be read."""
        if path not in self._blobs:
            raise LookupError(f"No such file in this view: {path}")
        return _git(self._git_dir, "cat-file", "blob", self._blobs[path])


def open_views(
    target: ReviewTarget, *, remote: str | None = None, cache: Path | None = None
) -> dict[str, SourceView]:
    """Fetch the target's base commit if needed, then build each view its judge read."""
    instance = target.instance
    git_dir = fetch(instance.repo, instance.base_commit, remote=remote, cache=cache)
    submitted = target.submitted_files()
    touched = _touched(instance, submitted)
    views = {}
    for view in target.views:
        repair = instance.golden_patch if view == "golden" else None
        tree = _build_tree(git_dir, instance.base_commit, repair, submitted)
        views[view] = SourceView(git_dir, tree, touched)
    return views


def fetch(
    repo: str, base_commit: str, *, remote: str | None = None, cache: Path | None = None
) -> Path:
    """A bare repository holding `base_commit`, fetched shallowly on first use.

    `remote` defaults to the repository on GitHub. The commit is kept under its own
    ref, so it stays reachable and is fetched only once.
    """
    if not REPO.fullmatch(repo) or {".", ".."} & set(repo.split("/")):
        raise SourceError(f"Not an owner/name repository: {repo}")
    git_dir = (cache or _cache_root()) / repo
    if not (git_dir / "HEAD").is_file():
        git_dir.mkdir(parents=True, exist_ok=True)
        _git(git_dir, "init", "--bare", "--quiet")
        # Views are written as unreferenced trees; never collect them mid-review.
        _git(git_dir, "config", "gc.auto", "0")

    ref = f"refs/oracle-lab/{base_commit}"
    resolve = ("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
    try:
        fetched = _git_line(git_dir, *resolve)
    except SourceError:
        # Not cached yet.
        url = remote or f"https://github.com/{repo}"
        _git(git_dir, "fetch", "--quiet", "--depth", "1", "--no-tags", url, f"{base_commit}:{ref}")
        fetched = _git_line(git_dir, *resolve)
    if fetched != base_commit:
        raise SourceError(f"Fetched {fetched} from {repo}, not {base_commit}")
    return git_dir


def _cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "oracle-lab/repos"


def _touched(
    instance: InstanceRecord, submitted: dict[str, SubmittedFile]
) -> dict[str, tuple[Touch, ...]]:
    """What touched each path, for every path something touched."""
    patches: list[tuple[Touch, str]] = [
        ("repair", instance.golden_patch),
        ("reference_tests", instance.reference_test_patch),
    ]
    touched: dict[str, list[Touch]] = {}
    for touch, patch in patches:
        for diff in parse_patch(patch):
            for path in diff.paths:
                touched.setdefault(path, []).append(touch)
    for name in submitted:
        touched.setdefault(name, []).append("submission")
    return {path: tuple(touches) for path, touches in touched.items()}


def _build_tree(
    git_dir: Path, base_commit: str, repair: str | None, submitted: dict[str, SubmittedFile]
) -> str:
    """Write one view as a Git tree using a throwaway index, and return its ID.

    The order matches the judge workspace: the repair first, then the submission.
    """
    with TemporaryDirectory(prefix="oracle-review-index-") as temporary:
        env = {"GIT_INDEX_FILE": str(Path(temporary) / "index")}
        _git(git_dir, "read-tree", base_commit, env=env)
        if repair is not None:
            _git(git_dir, "apply", "--cached", "-", input=repair.encode(), env=env)
        entries = []
        for name, file in submitted.items():
            blob = _git_line(git_dir, "hash-object", "-w", "--stdin", input=file.content)
            mode = "100755" if file.mode & 0o111 else "100644"
            entries.append(f"{mode} {blob}\t{name}\n")
        if entries:
            index_info = "".join(entries).encode()
            _git(git_dir, "update-index", "--add", "--index-info", input=index_info, env=env)
        return _git_line(git_dir, "write-tree", env=env)


def _list_blobs(git_dir: Path, tree: str) -> dict[str, str]:
    """Every regular file and symlink in a tree, by path. Submodules have no content here."""
    blobs = {}
    for entry in _git(git_dir, "ls-tree", "-r", "-z", tree).decode().split("\0"):
        if not entry:
            continue
        meta, path = entry.split("\t", 1)
        _, kind, object_id = meta.split(" ")
        if kind == "blob":
            blobs[path] = object_id
    return blobs


def _git(
    git_dir: Path, *args: str, input: bytes | None = None, env: dict[str, str] | None = None
) -> bytes:
    # Never prompt for credentials: a missing repository fails instead of hanging.
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env or {})}
    result = subprocess.run(
        ["git", "--git-dir", str(git_dir), *args],
        input=input,
        capture_output=True,
        env=environment,
        check=False,
    )
    if result.returncode != 0:
        message = result.stderr.decode(errors="replace").strip()
        raise SourceError(f"git {args[0]} failed: {message}")
    return result.stdout


def _git_line(
    git_dir: Path, *args: str, input: bytes | None = None, env: dict[str, str] | None = None
) -> str:
    """A Git command whose output is one line, such as an object ID."""
    return _git(git_dir, *args, input=input, env=env).decode().strip()
