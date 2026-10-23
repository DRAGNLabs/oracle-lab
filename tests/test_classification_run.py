import json
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fixtures import (
    context_of,
    in_scope_classification,
    instance_record,
    out_of_scope_classification,
    usage,
)

from oracle_lab.classification.contracts import OutOfScopeClassification
from oracle_lab.classification.run import _classification_stage, classify
from oracle_lab.classification.workspace import (
    CLASSIFICATION_ROOT,
    build_classification_workspace,
)
from oracle_lab.config import ClassificationConfig
from oracle_lab.io import read_json, write_json
from oracle_lab.paths import AttemptPaths
from oracle_lab.results import write_result


class RecordingSandbox:
    def __init__(self):
        self.commands = []
        self.uploads = []

    def run(self, argv, **kwargs):
        self.commands.append((argv, kwargs))

    def upload(self, source, destination):
        self.uploads.append((source, destination))


def classification_config() -> ClassificationConfig:
    return ClassificationConfig.model_validate(
        {
            "source": {"instance": "owner__project-1"},
            "classifier": {"model": "classifier-model"},
            "rubric": "rubric.md",
            "output": "output",
            "runtime": {
                "image": "example/image:latest",
                "source_roots": ["package"],
                "import_modules": ["package"],
                "existing_test_globs": ["tests"],
            },
        }
    )


def test_workspace_resets_base_checkout_and_exposes_only_declared_inputs(tmp_path):
    attempt = AttemptPaths.create(tmp_path)
    instance = instance_record()
    write_result(attempt.instance, instance)
    attempt.judge.rubric.write_text("rubric")
    sandbox = RecordingSandbox()

    spec = build_classification_workspace(
        sandbox, classification_config(), attempt, "sha256:image", instance
    )

    assert sandbox.commands[0][0] == [
        "git",
        "-C",
        "/testbed",
        "reset",
        "--hard",
        instance.base_commit,
    ]
    assert [destination for _, destination in sandbox.uploads] == [
        CLASSIFICATION_ROOT + "/instance.json",
        CLASSIFICATION_ROOT + "/rubric.md",
    ]
    assert {artifact.source for artifact in spec.artifacts} == {
        "inputs/instance.json",
        "judge/rubric.md",
    }
    assert attempt.judge.workspace_spec.is_file()


VALID_CLASSIFICATION = json.dumps(in_scope_classification())


def prepared_attempt(tmp_path):
    attempt = AttemptPaths.create(tmp_path)
    instance = instance_record()
    write_result(attempt.instance, instance)
    attempt.judge.rubric.write_text("# Classification rubric\n")
    return attempt, instance


def install_stage_fakes(monkeypatch, final=VALID_CLASSIFICATION, status="completed"):
    sandbox = Mock()

    sandbox_context = context_of(sandbox)

    def turn(_sandbox, request):
        assert request.prompt == request.artifact_directory.parent / "rubric.md"
        request.artifact_directory.mkdir(parents=True, exist_ok=True)
        (request.artifact_directory / "final.txt").write_text(final)
        result = {
            "status": status,
            "duration_seconds": 2.5,
            "usage": usage(),
            "errors": [] if status == "completed" else [{"message": "harness failed"}],
            "harness": "codex",
            "provider": "openrouter",
            "model": "classifier-model",
            "harness_version": "0.153.4",
            "limit": {"kind": "wall_seconds", "value": 900},
        }
        write_json(request.artifact_directory / "result.json", result)
        return result

    monkeypatch.setattr("oracle_lab.classification.run.open_sandbox", sandbox_context)
    monkeypatch.setattr("oracle_lab.classification.run.build_classification_workspace", Mock())
    monkeypatch.setattr("oracle_lab.classification.run.run_turn", turn)
    return sandbox, turn


def test_stage_saves_raw_response_normalized_result_and_provenance(tmp_path, monkeypatch):
    attempt, instance = prepared_attempt(tmp_path)
    install_stage_fakes(monkeypatch)

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == "completed"
    assert (attempt.judge.classification_raw).read_text() == VALID_CLASSIFICATION
    saved = read_json(attempt.judge.classification)
    assert saved["instance_id"] == instance.instance_id
    assert saved["base_commit"] == instance.base_commit
    assert saved["source_record_sha256"] == instance.source.record_sha256
    assert saved["result"]["defect_mechanisms"] == ["validation_checking"]
    assert read_json(attempt.judge.result)["model"] == "classifier-model"


def test_invalid_model_output_is_preserved_without_retry(tmp_path, monkeypatch):
    attempt, instance = prepared_attempt(tmp_path)
    _, fake_turn = install_stage_fakes(monkeypatch, final="explanation before JSON")
    turn = Mock(wraps=fake_turn)
    monkeypatch.setattr("oracle_lab.classification.run.run_turn", turn)

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == "invalid_output"
    assert (attempt.judge.classification_raw).read_text() == "explanation before JSON"
    assert read_json(attempt.judge.classification)["result"]["status"] == "invalid_output"
    assert turn.call_count == 1


def test_a_transient_failure_is_retried_and_its_attempt_archived(tmp_path, monkeypatch):
    attempt, instance = prepared_attempt(tmp_path)
    _, turn = install_stage_fakes(monkeypatch)
    calls = []

    def flaky_turn(sandbox, request):
        result = turn(sandbox, request)
        calls.append(result)
        if len(calls) == 1:
            result = {
                **result,
                "status": "failed",
                "errors": [{"message": "503 Service Unavailable"}],
            }
            write_json(request.artifact_directory / "result.json", result)
        return result

    monkeypatch.setattr("oracle_lab.classification.run.run_turn", flaky_turn)
    waits = []
    monkeypatch.setattr("oracle_lab.harnesses.retry.sleep", waits.append)

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == "completed"
    assert waits == [30]
    (archived,) = attempt.judge.agent_history.iterdir()
    assert read_json(archived / "agent/result.json")["status"] == "failed"
    assert read_json(attempt.judge.result)["status"] == "completed"


@pytest.mark.parametrize(
    ("harness_status", "classification_status"),
    [("failed", "failed"), ("timeout", "timed_out")],
)
def test_harness_failure_has_explicit_saved_status(
    tmp_path, monkeypatch, harness_status, classification_status
):
    attempt, instance = prepared_attempt(tmp_path)
    install_stage_fakes(monkeypatch, status=harness_status)

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == classification_status
    assert result.error == "harness failed"
    assert read_json(attempt.judge.classification)["result"]["status"] == (classification_status)


def test_workspace_or_transport_failure_is_saved(tmp_path, monkeypatch):
    attempt, instance = prepared_attempt(tmp_path)
    install_stage_fakes(monkeypatch)
    monkeypatch.setattr(
        "oracle_lab.classification.run.build_classification_workspace",
        Mock(side_effect=RuntimeError("workspace failed")),
    )

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == "failed"
    assert result.error == "workspace failed"
    assert read_json(attempt.judge.result)["errors"][-1]["message"] == ("workspace failed")
    assert (attempt.judge.classification_raw).read_text() == ""


def test_late_container_cleanup_failure_keeps_completed_classification(tmp_path, monkeypatch):
    attempt, instance = prepared_attempt(tmp_path)
    install_stage_fakes(monkeypatch)

    @contextmanager
    def failing_cleanup(*args, **kwargs):
        yield Mock()
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr("oracle_lab.classification.run.open_sandbox", failing_cleanup)

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == "completed"
    assert read_json(attempt.judge.classification)["result"]["status"] == "completed"


def test_classify_resolves_builds_and_records_attempt_lifecycle(tmp_path, monkeypatch):
    rubric = tmp_path / "rubric.md"
    rubric.write_text("# Rubric\n")
    config = classification_config()
    config.rubric = str(rubric)
    config.output = str(tmp_path / "output")
    instance = instance_record()
    stages = []

    client_context = context_of(Mock())

    monkeypatch.setenv("OPENROUTER_API_KEY", "credential-fixture")
    monkeypatch.setattr(
        "oracle_lab.classification.run.resolve_source",
        lambda source: (instance, config.require_runtime()),
    )
    monkeypatch.setattr("oracle_lab.classification.run.docker_client", client_context)
    monkeypatch.setattr(
        "oracle_lab.classification.run.prepare_image",
        lambda client, resolved, paths: "sha256:image",
    )

    def stage(client, resolved, paths, image, resolved_instance):
        stages.append((image, resolved_instance.instance_id))
        return OutOfScopeClassification.model_validate(out_of_scope_classification())

    monkeypatch.setattr("oracle_lab.classification.run._classification_stage", stage)
    spent = []

    @contextmanager
    def measure_spend(harnesses, path, on_wait):
        spent.append(([harness.harness for harness in harnesses], path.name))
        yield

    monkeypatch.setattr("oracle_lab.classification.run.measure_spend", measure_spend)

    root = classify(config)

    attempt = AttemptPaths(root)
    assert root.parent == tmp_path / "output/classifications" / instance.instance_id
    assert stages == [("sha256:image", instance.instance_id)]
    assert spent == [([config.classifier.harness], "cost.json")]
    assert attempt.instance.is_file()
    assert attempt.judge.rubric.read_text() == "# Rubric\n"
    assert read_json(attempt.runtime) == {"image": "sha256:image"}
    assert read_json(attempt.status)["state"] == "completed"


def test_missing_credential_fails_before_resolving_or_starting_docker(tmp_path, monkeypatch):
    config = classification_config()
    config.output = str(tmp_path / "output")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    resolve = Mock()
    docker = Mock()
    monkeypatch.setattr("oracle_lab.classification.run.resolve_source", resolve)
    monkeypatch.setattr("oracle_lab.classification.run.docker_client", docker)

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        classify(config)

    resolve.assert_not_called()
    docker.assert_not_called()


def test_build_failure_records_failed_stage_in_created_attempt(tmp_path, monkeypatch):
    rubric = tmp_path / "rubric.md"
    rubric.write_text("# Rubric\n")
    config = classification_config()
    config.rubric = str(rubric)
    config.output = str(tmp_path / "output")
    instance = instance_record()

    client_context = context_of(Mock())

    monkeypatch.setenv("OPENROUTER_API_KEY", "credential-fixture")
    monkeypatch.setattr(
        "oracle_lab.classification.run.resolve_source",
        lambda source: (instance, config.require_runtime()),
    )
    monkeypatch.setattr("oracle_lab.classification.run.docker_client", client_context)
    monkeypatch.setattr(
        "oracle_lab.classification.run.prepare_image",
        Mock(side_effect=RuntimeError("image build failed")),
    )

    with pytest.raises(RuntimeError, match="image build failed"):
        classify(config)

    attempts = list((tmp_path / "output/classifications" / instance.instance_id).iterdir())
    assert len(attempts) == 1
    status = read_json(attempts[0] / "status.json")
    assert status["stage"] == "build"
    assert status["state"] == "failed"
    assert status["error"] == "image build failed"
    assert status["updated_at"]
