"""Build and verify the two judge repository views (Python 3.6+).

Runs inside the disposable judge container under the repository's own Python,
so it uses only the standard library. The host never trusts its own copy of the
submission: `verify` rehashes what the container actually holds.
"""

import hashlib
import json
import shutil
import sys
from pathlib import Path


def copy_views(source: str, views: list) -> None:
    """Replace both views with plain copies when Git worktrees are unavailable."""
    for view in views:
        shutil.rmtree(view, ignore_errors=True)
    for view in views:
        shutil.copytree(source, view, symlinks=True)


def verify_submission(expected: dict, views: list) -> dict:
    """Hash every manifested file in each view, refusing anything that escapes it."""
    observed = {}
    for view in views:
        root = Path(view).resolve()
        observed[view] = {}
        for name in expected:
            path = root / name
            if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
                raise ValueError("Unsafe or missing generated test: " + str(path))
            observed[view][name] = hashlib.sha256(path.read_bytes()).hexdigest()
    return observed


def main() -> None:
    operation = sys.argv[1]
    if operation == "copy-views":
        copy_views(sys.argv[2], sys.argv[3:])
    elif operation == "verify":
        settings = json.loads(Path(sys.argv[2]).read_text())
        observed = verify_submission(settings["expected"], settings["views"])
        Path(sys.argv[3]).write_text(json.dumps(observed, sort_keys=True))
    else:
        raise ValueError("Unknown operation")


if __name__ == "__main__":
    main()
