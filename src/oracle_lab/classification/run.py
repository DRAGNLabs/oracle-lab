"""Standalone execution of one SWE-bench task-classification turn."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path

import yaml
from docker import DockerClient
from docker.errors import DockerException

from oracle_lab.classification.contracts import (
    ClassificationResult,
    SavedClassification,
    WorkspaceSpec,
    parse_classification,
    read_saved_classification,
)
from oracle_lab.config import ClassificationConfig
from oracle_lab.container.images import prepare_image
from oracle_lab.container.sandbox import Profile, Sandbox, docker_client, open_sandbox
from oracle_lab.harnesses import provenance, run_turn, turn_request
from oracle_lab.harnesses.launch import require_credential
from oracle_lab.harnesses.retry import with_retries
from oracle_lab.instance import resolve_source
from oracle_lab.io import archive_files, digest, utc_stamp, write_json
from oracle_lab.results import (
    ExposedArtifact,
    InstanceRecord,
    failed_turn,
    last_error_message,
    write_result,
    write_status,
)
from oracle_lab.spend import measure_spend

CLASSIFICATION_ROOT = "/oracle-classification"


def classify(config: ClassificationConfig) -> Path:
    """Resolve, prepare, and classify one problem in a new immutable attempt."""
    require_credential(config.classifier)
    instance, config.runtime = resolve_source(config.source)
    attempt_id = utc_stamp() + "-" + uuid.uuid4().hex[:8]
    attempt = Path(config.output) / instance.instance_id / attempt_id
    for directory in (attempt / "inputs", attempt / "image-build", attempt / "agent"):
        directory.mkdir(parents=True, exist_ok=True)
    stage = "resolve"
    try:
        write_status(attempt, stage)
        rubric = attempt / "inputs/rubric.md"
        rubric.write_bytes(Path(config.rubric).read_bytes())
        config.rubric = str(rubric.resolve())
        (attempt / "inputs/config.resolved.yaml").write_text(
            yaml.safe_dump(config.model_dump(), sort_keys=False)
        )
        write_result(attempt / "inputs/instance.json", instance)

        with docker_client() as client:
            stage = "build"
            write_status(attempt, stage)
            image = prepare_image(client, config, attempt / "image-build")
            write_json(attempt / "image-build/runtime.json", {"image": image})

            stage = "classify"
            write_status(attempt, stage)
            with measure_spend(
                [config.classifier], attempt / "cost.json", lambda: write_status(attempt, "cost")
            ):
                result = _classification_stage(client, config, attempt, image, instance)

        state = "completed" if result.status == "completed" else "completed_with_errors"
        write_status(attempt, "finished", state, classification_status=result.status)
    except KeyboardInterrupt:
        write_status(attempt, stage, "interrupted")
        raise
    except Exception as error:
        write_status(attempt, stage, "failed", error=str(error))
        raise
    return attempt


def _classification_stage(
    client: DockerClient,
    config: ClassificationConfig,
    attempt: Path,
    image: str,
    instance: InstanceRecord,
) -> ClassificationResult:
    request = turn_request(
        config,
        config.classifier,
        attempt / "inputs/rubric.md",
        config.require_runtime().workdir,
        attempt / "agent",
    )
    classification = attempt / "classification.json"
    result_path = attempt / "agent/result.json"
    raw = attempt / "classification.raw.txt"
    final = attempt / "agent/final.txt"

    def one_turn(will_retry: Callable[[dict], bool]) -> tuple[dict, ClassificationResult | None]:
        log = attempt / "workspace.log"
        with open_sandbox(client, image, config, Profile.CLASSIFICATION, log) as sandbox:
            build_classification_workspace(sandbox, config, attempt, image, instance)
            harness_result = run_turn(sandbox, request)
            if will_retry(harness_result):
                return harness_result, None
            result = _classify(harness_result, attempt)
            # Save before container cleanup so a late cleanup failure cannot replace a
            # valid model response with an annotation failure.
            _save_classification(attempt, instance, result)
        return harness_result, result

    try:
        result = with_retries(
            one_turn, lambda: archive_files([attempt / "agent"], attempt / "agent-history")
        )
        assert result is not None
    except (RuntimeError, OSError, ValueError, DockerException) as error:
        if classification.is_file():
            return read_saved_classification(classification).result
        if not result_path.is_file():
            write_json(
                result_path,
                {
                    "status": "failed",
                    "duration_seconds": 0,
                    "usage": None,
                    "errors": [{"message": str(error)}],
                    **provenance(request),
                },
            )
        if not raw.is_file():
            raw.write_text(final.read_text(errors="replace") if final.is_file() else "")
        result = failed_turn(str(error))
        _save_classification(attempt, instance, result)
    return result


def _classify(harness_result: dict, attempt: Path) -> ClassificationResult:
    """Save the raw response and turn it into a classification or a failure."""
    final = attempt / "agent/final.txt"
    raw = final.read_text(errors="replace") if final.is_file() else ""
    (attempt / "classification.raw.txt").write_text(raw)
    if harness_result["status"] == "completed":
        return parse_classification(raw)
    reason = last_error_message(harness_result["errors"]) or (
        f"Classifier harness ended with status {harness_result['status']}"
    )
    return failed_turn(reason, timed_out=harness_result["status"] == "timeout")


def _save_classification(
    attempt: Path,
    instance: InstanceRecord,
    result: ClassificationResult,
) -> None:
    saved = SavedClassification(
        instance_id=instance.instance_id,
        base_commit=instance.base_commit,
        source_record_sha256=instance.source.record_sha256,
        rubric_sha256=digest((attempt / "inputs/rubric.md").read_bytes()),
        result=result,
    )
    write_json(attempt / "classification.json", saved.model_dump(mode="json"))


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

    evidence = [
        (attempt / "inputs/instance.json", CLASSIFICATION_ROOT + "/instance.json"),
        (attempt / "inputs/rubric.md", CLASSIFICATION_ROOT + "/rubric.md"),
    ]
    artifacts = []
    for source, destination in evidence:
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
