import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
from fixtures import instance_record, usage

from oracle_bench.config import ClassificationConfig
from oracle_bench.io import read_json, write_json
from oracle_bench.repo_classification.contracts import OutOfScopeClassification
from oracle_bench.repo_classification.run import (
    CLASSIFICATION_ROOT,
    _classification_stage,
    build_classification_workspace,
    classify,
)
from oracle_bench.results import write_result


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
            "runtime": {
                "image": "example/image:latest",
                "source_roots": ["package"],
                "import_modules": ["package"],
                "existing_test_globs": ["tests"],
            },
        }
    )


def test_workspace_resets_base_checkout_and_exposes_only_declared_inputs(tmp_path):
    (tmp_path / "inputs").mkdir()
    instance = instance_record()
    write_result(tmp_path / "inputs/instance.json", instance)
    (tmp_path / "inputs/rubric.md").write_text("rubric")
    sandbox = RecordingSandbox()

    spec = build_classification_workspace(
        sandbox, classification_config(), tmp_path, "sha256:image", instance
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
        "inputs/rubric.md",
    }
    assert (tmp_path / "workspace-spec.json").is_file()


VALID_CLASSIFICATION = json.dumps(
    {
        "task_nature": "behavioral_bug",
        "defect_mechanisms": ["control_logic"],
        "primary_assertion_target": "return_value_or_status",
        "required_test_setup": "special_input",
        "code_only_oracle_availability": "repository_pattern",
        "required_test_scope": "component",
        "benchmark_quality": "usable",
        "rationale": "A sibling implementation establishes the expected result.",
    }
)


def prepared_attempt(tmp_path):
    (tmp_path / "inputs").mkdir()
    (tmp_path / "agent").mkdir()
    instance = instance_record()
    write_result(tmp_path / "inputs/instance.json", instance)
    (tmp_path / "inputs/rubric.md").write_text("# Classification rubric\n")
    return tmp_path, instance


def install_stage_fakes(monkeypatch, final=VALID_CLASSIFICATION, status="completed"):
    sandbox = Mock()

    @contextmanager
    def sandbox_context(*args, **kwargs):
        yield sandbox

    def turn(_sandbox, request):
        assert request.prompt == request.artifact_directory.parent / "inputs/rubric.md"
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

    monkeypatch.setattr("oracle_bench.repo_classification.run.open_sandbox", sandbox_context)
    monkeypatch.setattr(
        "oracle_bench.repo_classification.run.build_classification_workspace", Mock()
    )
    monkeypatch.setattr("oracle_bench.repo_classification.run.run_turn", turn)
    return sandbox, turn


def test_stage_saves_raw_response_normalized_result_and_provenance(tmp_path, monkeypatch):
    attempt, instance = prepared_attempt(tmp_path)
    install_stage_fakes(monkeypatch)

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == "completed"
    assert (attempt / "classification.raw.txt").read_text() == VALID_CLASSIFICATION
    saved = read_json(attempt / "classification.json")
    assert saved["instance_id"] == instance.instance_id
    assert saved["base_commit"] == instance.base_commit
    assert saved["source_record_sha256"] == instance.source.record_sha256
    assert saved["result"]["defect_mechanisms"] == ["control_logic"]
    assert read_json(attempt / "agent/result.json")["model"] == "classifier-model"


def test_invalid_model_output_is_preserved_without_retry(tmp_path, monkeypatch):
    attempt, instance = prepared_attempt(tmp_path)
    _, fake_turn = install_stage_fakes(monkeypatch, final="explanation before JSON")
    turn = Mock(wraps=fake_turn)
    monkeypatch.setattr("oracle_bench.repo_classification.run.run_turn", turn)

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == "invalid_output"
    assert (attempt / "classification.raw.txt").read_text() == "explanation before JSON"
    assert read_json(attempt / "classification.json")["result"]["status"] == "invalid_output"
    assert turn.call_count == 1


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
    assert read_json(attempt / "classification.json")["result"]["status"] == (classification_status)


def test_workspace_or_transport_failure_is_saved(tmp_path, monkeypatch):
    attempt, instance = prepared_attempt(tmp_path)
    install_stage_fakes(monkeypatch)
    monkeypatch.setattr(
        "oracle_bench.repo_classification.run.build_classification_workspace",
        Mock(side_effect=RuntimeError("workspace failed")),
    )

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == "failed"
    assert result.error == "workspace failed"
    assert read_json(attempt / "agent/result.json")["errors"][-1]["message"] == ("workspace failed")
    assert (attempt / "classification.raw.txt").read_text() == ""


def test_late_container_cleanup_failure_keeps_completed_classification(tmp_path, monkeypatch):
    attempt, instance = prepared_attempt(tmp_path)
    install_stage_fakes(monkeypatch)

    @contextmanager
    def failing_cleanup(*args, **kwargs):
        yield Mock()
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr("oracle_bench.repo_classification.run.open_sandbox", failing_cleanup)

    result = _classification_stage(
        Mock(), classification_config(), attempt, "sha256:image", instance
    )

    assert result.status == "completed"
    assert read_json(attempt / "classification.json")["result"]["status"] == "completed"


def test_classify_resolves_builds_and_records_attempt_lifecycle(tmp_path, monkeypatch):
    rubric = tmp_path / "rubric.md"
    rubric.write_text("# Rubric\n")
    config = classification_config()
    config.rubric = str(rubric)
    config.output = str(tmp_path / "classifications")
    instance = instance_record()
    stages = []

    @contextmanager
    def client_context():
        yield Mock()

    monkeypatch.setenv("OPENROUTER_API_KEY", "credential-fixture")
    monkeypatch.setattr(
        "oracle_bench.repo_classification.run.resolve_source",
        lambda source: (instance, config.require_runtime()),
    )
    monkeypatch.setattr("oracle_bench.repo_classification.run.docker_client", client_context)
    monkeypatch.setattr(
        "oracle_bench.repo_classification.run.prepare_image",
        lambda client, resolved, paths: "sha256:image",
    )

    def stage(client, resolved, paths, image, resolved_instance):
        stages.append((image, resolved_instance.instance_id))
        return OutOfScopeClassification(task_nature="out_of_scope", rationale="Feature request.")

    monkeypatch.setattr("oracle_bench.repo_classification.run._classification_stage", stage)
    spent = []

    @contextmanager
    def measure_spend(harnesses, path, on_wait):
        spent.append(([harness.harness for harness in harnesses], path.name))
        yield

    monkeypatch.setattr("oracle_bench.repo_classification.run.measure_spend", measure_spend)

    attempt = classify(config)

    assert stages == [("sha256:image", instance.instance_id)]
    assert spent == [([config.classifier.harness], "cost.json")]
    assert (attempt / "inputs/instance.json").is_file()
    assert (attempt / "inputs/rubric.md").read_text() == "# Rubric\n"
    assert read_json(attempt / "image-build/runtime.json") == {"image": "sha256:image"}
    assert read_json(attempt / "status.json")["state"] == "completed"


def test_missing_credential_fails_before_resolving_or_starting_docker(tmp_path, monkeypatch):
    config = classification_config()
    config.output = str(tmp_path / "classifications")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    resolve = Mock()
    docker = Mock()
    monkeypatch.setattr("oracle_bench.repo_classification.run.resolve_source", resolve)
    monkeypatch.setattr("oracle_bench.repo_classification.run.docker_client", docker)

    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        classify(config)

    resolve.assert_not_called()
    docker.assert_not_called()


def test_build_failure_records_failed_stage_in_created_attempt(tmp_path, monkeypatch):
    rubric = tmp_path / "rubric.md"
    rubric.write_text("# Rubric\n")
    config = classification_config()
    config.rubric = str(rubric)
    config.output = str(tmp_path / "classifications")
    instance = instance_record()

    @contextmanager
    def client_context():
        yield Mock()

    monkeypatch.setenv("OPENROUTER_API_KEY", "credential-fixture")
    monkeypatch.setattr(
        "oracle_bench.repo_classification.run.resolve_source",
        lambda source: (instance, config.require_runtime()),
    )
    monkeypatch.setattr("oracle_bench.repo_classification.run.docker_client", client_context)
    monkeypatch.setattr(
        "oracle_bench.repo_classification.run.prepare_image",
        Mock(side_effect=RuntimeError("image build failed")),
    )

    with pytest.raises(RuntimeError, match="image build failed"):
        classify(config)

    attempts = list((Path(config.output) / instance.instance_id).iterdir())
    assert len(attempts) == 1
    status = read_json(attempts[0] / "status.json")
    assert status["stage"] == "build"
    assert status["state"] == "failed"
    assert status["error"] == "image build failed"
    assert status["updated_at"]
