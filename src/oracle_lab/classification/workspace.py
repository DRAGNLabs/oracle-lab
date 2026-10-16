"""Construct the privileged workspace the task classifier reads."""

from __future__ import annotations

from pathlib import Path

from oracle_lab.classification.contracts import WorkspaceSpec
from oracle_lab.config import ClassificationConfig
from oracle_lab.container.sandbox import Sandbox
from oracle_lab.io import digest, write_json
from oracle_lab.results import ExposedArtifact, InstanceRecord

CLASSIFICATION_ROOT = "/oracle-classification"


def build_classification_workspace(
    sandbox: Sandbox,
    config: ClassificationConfig,
    attempt: Path,
    image: str,
    instance: InstanceRecord,
) -> WorkspaceSpec:
    """Reset the checkout and expose exactly the private evidence named here."""
    runtime = config.require_runtime()
    sandbox.run(
        ["git", "-C", runtime.workdir, "reset", "--hard", instance.base_commit],
        timeout=config.limits.setup_seconds,
    )
    sandbox.run(["mkdir", "-p", CLASSIFICATION_ROOT])

    artifacts = []
    for source, destination in classification_evidence(attempt):
        sandbox.upload(source, destination)
        artifacts.append(
            ExposedArtifact(
                source=source.relative_to(attempt).as_posix(),
                destination=destination,
                sha256=digest(source.read_bytes()),
            )
        )
    sandbox.run(["chmod", "-R", "a+rX", CLASSIFICATION_ROOT])

    spec = WorkspaceSpec(image=image, base_commit=instance.base_commit, artifacts=artifacts)
    write_json(attempt / "workspace-spec.json", spec.model_dump())
    return spec


def classification_evidence(attempt: Path) -> list[tuple[Path, str]]:
    """Every file the classifier may read besides the checkout, and where it lands.

    Human review shows the same rows.
    """
    return [
        (attempt / "inputs/instance.json", CLASSIFICATION_ROOT + "/instance.json"),
        (attempt / "inputs/rubric.md", CLASSIFICATION_ROOT + "/rubric.md"),
    ]
