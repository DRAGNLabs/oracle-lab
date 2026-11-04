"""Turn a SWE-rebench V2 instance ID into its record and the settings needed to run it.

V2 records use the SWE-bench format and name their own prepared image. Every
Python image clones the repository to `/<repository name>` and installs it in
editable mode with the official Python image's interpreter. The runtime image
build checks both paths. `REPOSITORY_LAYOUTS` says where each repo keeps its
source and tests. Only the repos listed there work.
"""

from __future__ import annotations

from oracle_lab.config import RuntimeConfig, SourceConfig
from oracle_lab.contracts.run import InstanceRecord
from oracle_lab.instance.swebench import RepositoryLayout, instance_from, load_record

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
    # The dataset's model-generated labels. A feature's reference test fails on the
    # old code only because the feature did not exist yet, so keep bug fixes only.
    categories = record.get("meta", {}).get("llm_metadata", {}).get("pr_categories") or []
    if not categories or not all(c.endswith("_bug") for c in categories):
        raise ValueError(f"SWE-rebench V2 record is not labeled as a bug fix only: {categories}")
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
    )
