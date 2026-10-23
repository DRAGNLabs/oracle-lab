"""Turn a SWE-bench instance ID into its record and the settings needed to run it.

`REPOSITORY_LAYOUTS` says where each repo keeps its source and tests. Only the
repos listed there work.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

from oracle_lab.config import RuntimeConfig, SourceConfig
from oracle_lab.io import digest
from oracle_lab.results import InstanceRecord, SourceProvenance

# Image naming/environment conventions are checked against this upstream revision.
UPSTREAM_REVISION = "3947b299c121bb1f45f5094180c0f39fa0c599a0"


@dataclass(frozen=True)
class RepositoryLayout:
    """Where one repository keeps its code and tests, and what its tests need."""

    source_root: str
    import_module: str
    existing_tests: tuple[str, ...]
    pythonpath: str | None = None
    pytest_plugins: tuple[str, ...] = ()


REPOSITORY_LAYOUTS = {
    "astropy/astropy": RepositoryLayout("astropy", "astropy", ("astropy/**/tests",)),
    "matplotlib/matplotlib": RepositoryLayout(
        "lib/matplotlib", "matplotlib", ("lib/matplotlib/tests",)
    ),
    "mwaskom/seaborn": RepositoryLayout("seaborn", "seaborn", ("tests",)),
    "pallets/flask": RepositoryLayout("src/flask", "flask", ("tests",)),
    "psf/requests": RepositoryLayout("requests", "requests", ("test_requests.py", "tests")),
    "pydata/xarray": RepositoryLayout("xarray", "xarray", ("xarray/tests",)),
    "pylint-dev/pylint": RepositoryLayout("pylint", "pylint", ("tests",)),
    "pytest-dev/pytest": RepositoryLayout(
        "src/_pytest",
        "_pytest",
        ("testing",),
        # A src-layout project: its installed distribution may be a different
        # version from the checked-out task revision.
        pythonpath="/testbed/src",
        # The repository enables pytester through addopts, which the benchmark
        # clears. Keep this required plugin without the other repository options.
        pytest_plugins=("pytester",),
    ),
    "scikit-learn/scikit-learn": RepositoryLayout("sklearn", "sklearn", ("sklearn/**/tests",)),
    "sphinx-doc/sphinx": RepositoryLayout("sphinx", "sphinx", ("tests",)),
}


# Instances whose image cannot run the default pytest.
OLDER_PYTEST_VERSIONS = {
    # This image uses Python 3.6.
    "scikit-learn__scikit-learn-14710": "6.2.5",
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
    layout = REPOSITORY_LAYOUTS[repo]
    return RuntimeConfig(
        image=image_for(record["instance_id"]),
        source_roots=[layout.source_root],
        import_modules=[layout.import_module],
        existing_test_globs=list(layout.existing_tests),
        pythonpath=layout.pythonpath,
        pytest_plugins=list(layout.pytest_plugins),
        pytest_version=OLDER_PYTEST_VERSIONS.get(record["instance_id"]),
    )


def resolve(config: SourceConfig) -> tuple[InstanceRecord, RuntimeConfig]:
    record = load_record(config)
    instance = instance_from(record, config, UPSTREAM_REVISION)
    return instance, runtime_for(record)


def load_record(config: SourceConfig) -> dict:
    """Read the configured local record, or else the matching row of the dataset.

    Reads the split's one data file at the pinned revision. Hugging Face caches
    the download, so only the first run fetches it.
    """
    if config.record:
        return json.loads(Path(config.record).read_text())
    path = hf_hub_download(
        config.dataset_name,
        f"data/{config.split}-00000-of-00001.parquet",
        repo_type="dataset",
        revision=config.revision,
    )
    rows = pq.read_table(path, filters=[("instance_id", "==", config.instance)]).to_pylist()
    if len(rows) != 1:
        raise ValueError(f"Expected one record for {config.instance}, found {len(rows)}")
    return rows[0]


def test_ids(record: dict, field: str) -> list[str]:
    """A record's test ID field, such as FAIL_TO_PASS: a list, or a JSON-encoded one."""
    value = record.get(field, [])
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{field} must be a list of test IDs")
    return value


def instance_from(record: dict, config: SourceConfig, upstream_revision: str) -> InstanceRecord:
    """Check a SWE-bench-format record and turn it into an instance."""
    for key in ["instance_id", "repo", "base_commit", "patch", "test_patch"]:
        if not isinstance(record.get(key), str) or not record[key]:
            raise ValueError(f"SWE-bench record is missing {key}")
    if record["instance_id"] != config.instance:
        raise ValueError("Local record does not match configured instance ID")
    if not re.fullmatch(r"[0-9a-f]{40}", record["base_commit"]):
        raise ValueError("base_commit must be immutable")

    fail_to_pass = test_ids(record, "FAIL_TO_PASS")

    return InstanceRecord(
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
            revision=config.revision,
            split=config.split,
            upstream_revision=upstream_revision,
            record_sha256=digest(json.dumps(record, sort_keys=True).encode()),
        ),
        original_record=record,
    )
