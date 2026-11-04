"""Resolve one benchmark instance: its source record, runtime, and ground truth.

This is the run's first stage. Everything here is derived from the dataset
record; the private evidence it writes never reaches the generation agent.
"""

from oracle_lab.config import RuntimeConfig, SourceConfig
from oracle_lab.contracts.run import InstanceRecord
from oracle_lab.instance import swebench, swerebench_v2
from oracle_lab.paths import RunPaths


def resolve_source(config: SourceConfig) -> tuple[InstanceRecord, RuntimeConfig]:
    if config.kind == "swebench":
        return swebench.resolve(config)
    if config.kind == "swerebench_v2":
        return swerebench_v2.resolve(config)
    raise ValueError(f"Unsupported source adapter {config.kind!r}")


def write_private_instance_artifacts(paths: RunPaths, instance: InstanceRecord) -> None:
    """Materialize the private evidence consumed by reports and judge workspaces.

    The issue becomes a readable Markdown document. The golden patch is preserved
    byte-for-byte as text so it remains the exact repair supplied by the dataset.
    Generation never receives either file.
    """
    issue = instance.original_record.get("problem_statement", "").rstrip()
    issue_document = f"# {instance.instance_id}\n\n{issue}\n"
    (paths.ground_truth / "issue.md").write_text(issue_document)

    (paths.ground_truth / "fix.patch").write_text(instance.golden_patch)
