"""Turn a SWE-bench instance ID into its record and the settings needed to run it.

`REPOSITORY_LAYOUTS` says where each repo keeps its source and tests. Only the
repos listed there work.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import cast

from oracle_bench.config import RuntimeConfig, SourceConfig
from oracle_bench.io import digest
from oracle_bench.results import InstanceRecord, SourceProvenance

# Image naming/environment conventions are checked against this upstream revision.
UPSTREAM_REVISION = "3947b299c121bb1f45f5094180c0f39fa0c599a0"


REPOSITORY_LAYOUTS = {
    "astropy/astropy": ("astropy", "astropy", ["astropy/**/tests"]),
    "matplotlib/matplotlib": ("lib/matplotlib", "matplotlib", ["lib/matplotlib/tests"]),
    "mwaskom/seaborn": ("seaborn", "seaborn", ["tests"]),
    "pallets/flask": ("src/flask", "flask", ["tests"]),
    "psf/requests": ("requests", "requests", ["test_requests.py", "tests"]),
    "pydata/xarray": ("xarray", "xarray", ["xarray/tests"]),
    "pylint-dev/pylint": ("pylint", "pylint", ["tests"]),
    "pytest-dev/pytest": ("src/_pytest", "_pytest", ["testing"]),
    "scikit-learn/scikit-learn": ("sklearn", "sklearn", ["sklearn/**/tests"]),
    "sphinx-doc/sphinx": ("sphinx", "sphinx", ["tests"]),
}


def image_for(instance_id: str, platform: str = "linux/amd64") -> str:
    architecture = "x86_64" if platform == "linux/amd64" else "arm64"
    image_id = instance_id.lower().replace("__", "_1776_")
    if not re.fullmatch(r"[a-z0-9_.-]+", image_id):
        raise ValueError(f"Cannot derive a SWE-bench image for {instance_id!r}")
    return f"swebench/sweb.eval.{architecture}.{image_id}:latest"


def runtime_for(record: dict) -> RuntimeConfig:
    repo = record["repo"]
    if repo not in REPOSITORY_LAYOUTS:
        raise ValueError(f"No tested SWE-bench runtime profile for repository {repo!r}")
    source_root, import_module, existing_test_globs = REPOSITORY_LAYOUTS[repo]
    uses_python_36 = record["instance_id"] == "scikit-learn__scikit-learn-14710"
    return RuntimeConfig(
        image=image_for(record["instance_id"]),
        source_roots=[source_root],
        import_modules=[import_module],
        existing_test_globs=existing_test_globs,
        # pytest is a src-layout project; its installed distribution may be a
        # different version from the checked-out task revision.
        pythonpath="/testbed/src" if repo == "pytest-dev/pytest" else None,
        # The repository enables pytester through addopts, which the benchmark
        # intentionally clears. Keep this required plugin without inheriting
        # unrelated repository options or default test targets.
        pytest_plugins=["pytester"] if repo == "pytest-dev/pytest" else [],
        # This particular upstream image uses Python 3.6. The normal test
        # toolchain requires a newer interpreter.
        pytest_version="6.2.5" if uses_python_36 else None,
        coverage_version="6.2" if uses_python_36 else None,
        # Official instance images already contain an installed checkout. Patches to
        # Python sources are visible through that editable install; rerunning a generic
        # pip build can require build-only dependencies absent from the final image.
        rebuild=":",
    )


def resolve(config: SourceConfig) -> tuple[InstanceRecord, RuntimeConfig]:
    # Declared up front so the type checker knows both branches give a dict.
    record: dict
    if config.record:
        record = json.loads(Path(config.record).read_text())
    else:
        try:
            from datasets import load_dataset
        except ImportError as exc:
            raise RuntimeError("Install oracle-bench[dataset] or set dataset.record") from exc
        # `load_dataset` can return several shapes, but asking for one named split
        # always gives rows we can loop over. The cast just tells the type checker.
        rows = cast(
            Iterable[dict],
            load_dataset(config.dataset_name, split=config.split, revision=config.dataset_revision),
        )
        matches = [row for row in rows if row["instance_id"] == config.instance]
        if len(matches) != 1:
            raise ValueError(f"Expected one record for {config.instance}, found {len(matches)}")
        record = matches[0]

    for key in ["instance_id", "repo", "base_commit", "patch", "test_patch"]:
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError(f"SWE-bench record is missing {key}")
    if record["instance_id"] != config.instance:
        raise ValueError("Local record does not match configured instance ID")
    if not re.fullmatch(r"[0-9a-f]{40}", record["base_commit"]):
        raise ValueError("base_commit must be immutable")

    # The dataset carries FAIL_TO_PASS as either a list or a JSON-encoded string.
    fail_to_pass = record.get("FAIL_TO_PASS", [])
    if isinstance(fail_to_pass, str):
        fail_to_pass = json.loads(fail_to_pass)
    if not isinstance(fail_to_pass, list) or not all(isinstance(v, str) for v in fail_to_pass):
        raise ValueError("FAIL_TO_PASS must be a list of test IDs")

    runtime = runtime_for(record)
    instance = InstanceRecord(
        instance_id=record["instance_id"],
        repo=record["repo"],
        base_commit=record["base_commit"],
        golden_patch=record["patch"],
        reference_test_patch=record["test_patch"],
        reference_test_ids=fail_to_pass,
        source=SourceProvenance(
            kind=config.kind,
            dataset=config.dataset,
            name=config.dataset_name,
            revision=config.dataset_revision,
            split=config.split,
            upstream_revision=UPSTREAM_REVISION,
            record_sha256=digest(json.dumps(record, sort_keys=True).encode()),
        ),
        original_record=record,
    )
    return instance, runtime
