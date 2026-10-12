"""Snapshot/export helper run inside a container; no third-party dependencies."""

import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

IGNORED_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}


def snapshot(root: Path) -> dict:
    result = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        # Include symlink directories as entries without traversing their targets.
        links = [d for d in dirs if (Path(directory) / d).is_symlink()]
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and d not in links]
        for filename in files + links:
            path = Path(directory) / filename
            relative = path.relative_to(root).as_posix()
            if filename.endswith((".pyc", ".pyo")):
                continue
            mode = path.lstat().st_mode
            if path.is_symlink():
                data = os.readlink(path).encode()
                kind = "symlink"
            elif stat.S_ISREG(mode):
                data = path.read_bytes()
                kind = "file"
            else:
                data = b""
                kind = "special"
            result[relative] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "kind": kind,
                "mode": stat.S_IMODE(mode),
                "size": len(data),
            }
    return result


def main() -> None:
    operation, root_arg, output_arg = sys.argv[1:4]
    root, output = Path(root_arg), Path(output_arg)
    output.parent.mkdir(parents=True, exist_ok=True)
    if operation == "snapshot":
        output.write_text(json.dumps(snapshot(root), sort_keys=True))
    elif operation == "diff":
        baseline = sys.argv[4]
        with output.open("wb") as stream:
            subprocess.run(
                ["git", "diff", "--binary", baseline, "--", "."],
                cwd=root,
                stdout=stream,
                check=True,
            )
            untracked = subprocess.check_output(
                ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root
            )
            for name in untracked.decode().split("\0"):
                if name:
                    subprocess.run(
                        ["git", "diff", "--no-index", "--binary", "--", "/dev/null", name],
                        cwd=root,
                        stdout=stream,
                        check=False,
                    )
    else:
        raise ValueError("Unknown operation")


if __name__ == "__main__":
    main()
