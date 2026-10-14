"""Turn a SWE-rebench V2 instance ID into its record and the settings needed to run it.

V2 records use the SWE-bench format and name their own prepared image. Every
Python image clones the repository to `/<repository name>` and installs it in
editable mode with the official Python image's interpreter. The runtime image
build checks both paths. `REPOSITORY_LAYOUTS` says where each repo keeps its
source and tests. Only the repos listed there work.
"""

from __future__ import annotations

import json
from pathlib import Path

from oracle_bench.config import RuntimeConfig, SourceConfig
from oracle_bench.instance.swebench import RepositoryLayout, instance_from
from oracle_bench.results import InstanceRecord

# The dataset's only data file at the pinned revision. It holds the one split.
DATA_FILE = "data/train-00000-of-00001.parquet"

REPOSITORY_LAYOUTS = {
    "wtforms/wtforms": RepositoryLayout("src/wtforms", "wtforms", ("tests",)),
}


def resolve(config: SourceConfig) -> tuple[InstanceRecord, RuntimeConfig]:
    record = load_record(config)
    # The record names its image, so the pinned dataset revision is also the
    # revision the image conventions come from.
    instance = instance_from(record, config, config.revision)
    # Some V2 test IDs are cut off at the first space inside a test parameter,
    # such as `test_length[a-longer`. pytest cannot select those.
    truncated = [i for i in instance.reference_test_ids if i.count("[") != i.count("]")]
    if truncated:
        raise ValueError(f"SWE-rebench V2 record has truncated test IDs: {truncated}")
    return instance, runtime_for(record)


def load_record(config: SourceConfig) -> dict:
    """Read the configured local record, or else the matching row of the dataset file.

    Hugging Face caches the downloaded file, so only the first run downloads it.
    """
    if config.record:
        return json.loads(Path(config.record).read_text())
    try:
        import pyarrow.parquet as pq
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError("Install oracle-bench[dataset] or set source.record") from exc
    path = hf_hub_download(
        config.dataset_name, DATA_FILE, repo_type="dataset", revision=config.revision
    )
    rows = pq.read_table(path, filters=[("instance_id", "==", config.instance)]).to_pylist()
    if len(rows) != 1:
        raise ValueError(f"Expected one record for {config.instance}, found {len(rows)}")
    return rows[0]


def runtime_for(record: dict) -> RuntimeConfig:
    repo = record["repo"]
    if repo not in REPOSITORY_LAYOUTS:
        raise ValueError(f"No tested SWE-rebench V2 runtime profile for repository {repo!r}")
    if record.get("language") != "python":
        raise ValueError(f"SWE-rebench V2 record is not a Python task: {record.get('language')!r}")
    if not isinstance(record.get("image_name"), str) or not record["image_name"]:
        raise ValueError("SWE-rebench V2 record is missing image_name")
    layout = REPOSITORY_LAYOUTS[repo]
    return RuntimeConfig(
        image=record["image_name"],
        source_roots=[layout.source_root],
        import_modules=[layout.import_module],
        existing_test_globs=list(layout.existing_tests),
        workdir="/" + repo.split("/")[1],
        python="/usr/local/bin/python",
        pythonpath=layout.pythonpath,
        pytest_plugins=list(layout.pytest_plugins),
        # The image already contains an editable install of the checkout.
        rebuild=":",
    )
