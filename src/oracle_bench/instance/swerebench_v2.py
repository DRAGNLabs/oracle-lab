"""Turn a SWE-rebench V2 instance ID into its record and the settings needed to run it.

V2 records use the SWE-bench format and name their own prepared image. Every
Python image clones the repository to `/<repository name>` and installs it in
editable mode with the official Python image's interpreter. The runtime image
build checks both paths. `REPOSITORY_LAYOUTS` says where each repo keeps its
source and tests. Only the repos listed there work.
"""

from __future__ import annotations

from oracle_bench.config import RuntimeConfig, SourceConfig
from oracle_bench.instance.swebench import RepositoryLayout, instance_from, load_record
from oracle_bench.results import InstanceRecord

REPOSITORY_LAYOUTS = {
    "wtforms/wtforms": RepositoryLayout("src/wtforms", "wtforms", ("tests",)),
}


def resolve(config: SourceConfig) -> tuple[InstanceRecord, RuntimeConfig]:
    record = load_record(config)
    # The record names its image, so the pinned dataset revision is also the
    # revision the image conventions come from.
    instance = instance_from(record, config, config.revision)
    return instance, runtime_for(record)


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
