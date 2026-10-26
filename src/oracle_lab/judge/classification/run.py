"""Standalone execution of one SWE-bench task-classification turn."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml
from docker import DockerClient
from docker.errors import DockerException

from oracle_lab.config import ClassificationConfig
from oracle_lab.container.images import prepare_image
from oracle_lab.container.sandbox import Profile, docker_client, open_sandbox
from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.classification import (
    ClassificationResult,
    SavedClassification,
    parse_classification,
    read_saved_classification,
)
from oracle_lab.contracts.run import InstanceRecord, write_status
from oracle_lab.contracts.turn import failed_turn, turn_failure
from oracle_lab.harnesses import record_turn_error, run_turn, save_raw_answer, turn_request
from oracle_lab.harnesses.launch import require_credential
from oracle_lab.harnesses.retry import with_retries
from oracle_lab.harnesses.spend import measure_spend
from oracle_lab.instance import resolve_source
from oracle_lab.io import archive_files, digest, write_json
from oracle_lab.judge.classification.workspace import build_classification_workspace
from oracle_lab.paths import AttemptPaths, ClassifierPaths, OutputPaths


def classify(config: ClassificationConfig) -> Path:
    """Resolve, prepare, and classify one problem in a new immutable attempt."""
    require_credential(config.classifier)
    instance, config.runtime = resolve_source(config.source)
    attempt = OutputPaths(Path(config.output)).new_attempt(instance.instance_id)
    stage = "resolve"
    try:
        write_status(attempt.root, stage)
        attempt.judge.rubric.write_bytes(Path(config.rubric).read_bytes())
        config.rubric = str(attempt.judge.rubric.resolve())
        attempt.config.write_text(yaml.safe_dump(config.model_dump(), sort_keys=False))
        write_result(attempt.instance, instance)

        with docker_client() as client:
            stage = "build"
            write_status(attempt.root, stage)
            image = prepare_image(client, config, attempt.build)
            write_json(attempt.runtime, {"image": image})

            stage = "classify"
            write_status(attempt.root, stage)
            with measure_spend(
                [config.classifier], attempt.cost, lambda: write_status(attempt.root, "cost")
            ):
                result = _classification_stage(client, config, attempt, image, instance)

        state = "completed" if result.status == "completed" else "completed_with_errors"
        write_status(attempt.root, "finished", state, classification_status=result.status)
    except KeyboardInterrupt:
        write_status(attempt.root, stage, "interrupted")
        raise
    except Exception as error:
        write_status(attempt.root, stage, "failed", error=str(error))
        raise
    return attempt.root


def _classification_stage(
    client: DockerClient,
    config: ClassificationConfig,
    attempt: AttemptPaths,
    image: str,
    instance: InstanceRecord,
) -> ClassificationResult:
    judge = attempt.judge
    request = turn_request(
        config,
        config.classifier,
        judge.rubric,
        config.require_runtime().workdir,
        judge.agent,
    )

    def one_turn(will_retry: Callable[[dict], bool]) -> tuple[dict, ClassificationResult | None]:
        with open_sandbox(client, image, config, Profile.CLASSIFICATION, judge.log) as sandbox:
            build_classification_workspace(sandbox, config, attempt, image, instance)
            harness_result = run_turn(sandbox, request)
            if will_retry(harness_result):
                return harness_result, None
            result = _classify(harness_result, judge)
            # Save before container cleanup so a late cleanup failure cannot replace a
            # valid model response with an annotation failure.
            _save_classification(judge, instance, result)
        return harness_result, result

    try:
        result = with_retries(one_turn, lambda: archive_files([judge.agent], judge.agent_history))
        assert result is not None
    except (RuntimeError, OSError, ValueError, DockerException) as error:
        # A classification already saved is kept; a late failure such as cleanup
        # doesn't undo it.
        record_turn_error(judge, judge.classification_raw, request, error)
        if judge.classification.is_file():
            return read_saved_classification(judge.classification).result
        result = failed_turn(str(error))
        _save_classification(judge, instance, result)
    return result


def _classify(harness_result: dict, judge: ClassifierPaths) -> ClassificationResult:
    """Save the raw response and turn it into a classification or a failure."""
    raw = save_raw_answer(judge, judge.classification_raw)
    return turn_failure(harness_result, "Classifier") or parse_classification(raw)


def _save_classification(
    judge: ClassifierPaths,
    instance: InstanceRecord,
    result: ClassificationResult,
) -> None:
    saved = SavedClassification(
        instance_id=instance.instance_id,
        base_commit=instance.base_commit,
        source_record_sha256=instance.source.record_sha256,
        rubric_sha256=digest(judge.rubric.read_bytes()),
        result=result,
    )
    write_json(judge.classification, saved.model_dump(mode="json"))
