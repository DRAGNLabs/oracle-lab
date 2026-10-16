"""Small file helpers: JSON, hashing, required-file checks, and archiving.

`write_json` writes to a temp file and renames it, so a crash never leaves a
half-written file. `digest` is sha256, used to tell when an input has changed.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def require_files(paths: Iterable[Path], label: str) -> None:
    """Fail naming every missing file at once, not just the first one.

    A symlink counts as missing: these are files the benchmark wrote itself.
    """
    missing = [str(path) for path in paths if not path.is_file() or path.is_symlink()]
    if missing:
        raise FileNotFoundError(f"{label} are missing: " + ", ".join(missing))


def utc_stamp() -> str:
    """A filename-safe UTC timestamp, unique enough to name an archive directory."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def archive_files(sources: Iterable[Path], history: Path) -> Path | None:
    """Move paths into a new timestamped directory under `history`.

    Returns where they went, or None when there was nothing to move.
    """
    sources = list(sources)
    if not sources:
        return None
    destination = history / utc_stamp()
    destination.mkdir(parents=True)
    for path in sources:
        shutil.move(path, destination / path.name)
    return destination
