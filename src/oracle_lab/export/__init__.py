"""Export an output root as a dataset: flat Parquet tables beside the raw files.

`tables` reads runs, classification attempts, and their reviews through the
persisted contracts; `writer` writes the dataset directory; `hub` uploads it.
"""

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from oracle_lab.export.hub import require_token, upload_dataset
from oracle_lab.export.writer import export_dataset


def export(root: Path, out: Path | None, repo: str | None) -> str:
    """Write the dataset to `out`, upload it to `repo`, or both. Returns where it went."""
    if out is None and repo is None:
        raise ValueError("Give --out, --repo, or both")
    if repo is not None:
        require_token()
    with TemporaryDirectory(prefix="oracle-lab-export-") as staging:
        directory = out or Path(staging)
        manifest = export_dataset(root, directory)
        for skipped in manifest.skipped:
            print(f"Skipped {skipped.path}: {skipped.error}", file=sys.stderr)
        print(f"{len(manifest.runs)} runs, {len(manifest.attempts)} classification attempts")
        return upload_dataset(directory, repo) if repo else str(directory)
