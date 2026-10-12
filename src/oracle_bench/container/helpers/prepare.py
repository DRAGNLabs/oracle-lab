"""Finalize a disposable repository workspace after rebuilding (Python 3.6+)."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

TEST_MODULE_PATTERNS = ("test_*.py", "*_test.py", "tests.py")


def git(*args: str) -> None:
    subprocess.run(["git", *args], check=True)


def remove_existing_tests(root: Path, patterns: list) -> None:
    """Remove matched files, and the pytest-collected modules inside matched directories.

    Everything else in a test directory stays, because the project may import it.
    """
    paths = {path for pattern in patterns for path in root.glob(pattern)}
    test_modules = set()
    for path in paths:
        if path.is_symlink() or path.is_file():
            test_modules.add(path)
        elif path.is_dir():
            for module_pattern in TEST_MODULE_PATTERNS:
                test_modules.update(path.rglob(module_pattern))
    for path in sorted(test_modules):
        path.unlink()


def replace_git_history(root: Path) -> None:
    """Replace the source repository metadata with a new, object-free repository.

    An orphan branch is insufficient because the original commits and blobs remain
    recoverable from the object database. Removing ``.git`` ensures the prepared
    snapshot is the only history available to the generation agent.
    """
    metadata = root / ".git"
    if metadata.is_dir():
        shutil.rmtree(metadata)
    else:
        metadata.unlink()
    git("-C", str(root), "init", "--quiet")


def prepare(settings: dict) -> None:
    root = Path(settings["workdir"])
    os.chdir(root)
    if settings["hide"]:
        remove_existing_tests(root, settings["existing_test_globs"])
    # Refuse to repurpose an existing directory: only new files are submissions.
    (root / settings["generated_dir"]).mkdir(parents=True, exist_ok=False)
    if settings["sanitize_history"]:
        replace_git_history(root)
    git("config", "user.email", "oracle-bench@localhost")
    git("config", "user.name", "OracleBench")
    git("config", "--global", "--add", "safe.directory", str(root))
    git("add", "-A")
    git("commit", "--quiet", "--allow-empty", "-m", "Oracle Bench workspace")
    subprocess.run(["chown", "-R", "10001:10001", str(root)], check=True)


if __name__ == "__main__":
    prepare(json.loads(Path(sys.argv[1]).read_text()))
