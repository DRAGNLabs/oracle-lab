"""Write an output root as a dataset directory: Parquet tables, raw files, and a card."""

from __future__ import annotations

import inspect
import shutil
import subprocess
import types
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import Literal, Union, get_args, get_origin

import pyarrow as pa
import pyarrow.parquet as pq
import yaml

from oracle_lab.export.contracts import TABLES, ExportManifest
from oracle_lab.export.tables import Collected, collect
from oracle_lab.io import write_json
from oracle_lab.paths import OutputPaths
from oracle_lab.results import ArtifactModel

DATASET_CARD = Path(__file__).resolve().parent / "templates/dataset-card.md"
_SCALARS = {str: pa.string(), int: pa.int64(), float: pa.float64(), bool: pa.bool_()}


def export_dataset(root: Path, out: Path) -> ExportManifest:
    """Write every run and classification attempt under `root` to the new directory `out`."""
    if out.exists() and any(out.iterdir()):
        raise ValueError(f"The export directory must be new or empty: {out}")
    collected = collect(root)
    for name, model in TABLES.items():
        path = out / "data" / f"{name}.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [row.model_dump(mode="json") for row in collected.tables[name]]
        pq.write_table(pa.Table.from_pylist(rows, schema=schema(model)), path)
    _copy_raw(root, out, collected)

    manifest = ExportManifest(
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        source=str(root.resolve()),
        oracle_lab_version=_version(),
        runs=[run.root.name for run in collected.runs],
        attempts=[f"{a.root.parent.name}/{a.root.name}" for a in collected.attempts],
        skipped=collected.skipped,
        rows={name: len(rows) for name, rows in collected.tables.items()},
    )
    write_json(out / "export-manifest.json", manifest.model_dump(mode="json"))
    (out / "README.md").write_text(dataset_card(manifest))
    return manifest


def schema(model: type[ArtifactModel]) -> pa.Schema:
    """A table's Parquet schema, from its row model's fields."""
    return pa.schema(
        pa.field(name, *_arrow_type(info.annotation)) for name, info in model.model_fields.items()
    )


def dataset_card(manifest: ExportManifest) -> str:
    """The Hugging Face dataset card: which file holds each table, and what each one is."""
    configs = [{"config_name": name, "data_files": f"data/{name}.parquet"} for name in TABLES]
    tables = "\n".join(
        f"- `{name}` ({manifest.rows[name]} rows): {_summary(model)}"
        for name, model in TABLES.items()
    )
    return Template(DATASET_CARD.read_text()).substitute(
        header=yaml.safe_dump({"configs": configs}, sort_keys=False),
        tables=tables,
        created_at=manifest.created_at,
        version=manifest.oracle_lab_version or "(unknown version)",
        runs=len(manifest.runs),
        attempts=len(manifest.attempts),
    )


def _arrow_type(annotation: object) -> tuple[pa.DataType, bool]:
    """The Arrow type of a row field, and whether it is nullable."""
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        (inner,) = [arg for arg in get_args(annotation) if arg is not type(None)]
        return _arrow_type(inner)[0], True
    if origin is list:
        return pa.list_(_arrow_type(get_args(annotation)[0])[0]), False
    if origin is Literal:
        return pa.string(), False
    return _SCALARS[annotation], False


def _summary(model: type[ArtifactModel]) -> str:
    return " ".join(inspect.cleandoc(model.__doc__ or "").split())


def _copy_raw(root: Path, out: Path, collected: Collected) -> None:
    source, raw = OutputPaths(root), OutputPaths(out / "raw")
    for run in collected.runs:
        shutil.copytree(run.root, raw.runs / run.root.name, symlinks=True)
    for attempt in collected.attempts:
        destination = raw.classifications / attempt.root.relative_to(source.classifications)
        shutil.copytree(attempt.root, destination, symlinks=True)


def _version() -> str | None:
    """This checkout's commit, marked dirty when it has uncommitted changes."""
    try:
        return subprocess.run(
            ["git", "describe", "--always", "--dirty", "--abbrev=12"],
            cwd=Path(__file__).parent,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None
