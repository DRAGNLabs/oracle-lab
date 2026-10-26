import json
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
import yaml
from docker.errors import DockerException, ImageNotFound
from fixtures import (
    RUN_CONFIG,
    context_of,
    evaluation_result,
    instance_record,
    judgment_labels,
    run_config,
    run_status,
    usage,
    write_submission,
)

from oracle_lab.config import JudgeConfig, load_config
from oracle_lab.contracts.base import write_result
from oracle_lab.harnesses import claude
from oracle_lab.io import read_json, write_json
from oracle_lab.judge.generation.run import judge as run_judge
from oracle_lab.paths import RunPaths

SAMPLE = RUN_CONFIG
VALID_JUDGMENT = json.dumps(judgment_labels(attempt_detail="missing_required_condition"), indent=2)


def judge(paths):
    """Invoke the standalone stage with the saved run's resolved configuration."""
    return run_judge(load_config(paths.config, resolved=True), paths)


def saved_run(tmp_path):
    paths = RunPaths.create(tmp_path)
    config = run_config("package", image="example/runtime")
    config.judge = JudgeConfig(
        rubric=str(paths.judge.rubric),
        harness="claude",
        # Set on purpose. The default is openrouter, and these tests want to
        # exercise the anthropic credential path.
        provider="anthropic",
        model="judge-model",
        limit={"kind": "wall_seconds", "value": 300},
    )
    paths.config.write_text(yaml.safe_dump(config.model_dump(), sort_keys=False))
    paths.judge.rubric.write_text("# Frozen rubric\n\nChoose labels.\n")
    write_json(paths.runtime, {"image": "example/runtime"})
    write_json(paths.status, run_status(judge_status="missing"))
    write_result(paths.instance, instance_record())
    write_json(paths.results, evaluation_result())
    write_submission(paths)
    return paths


def install_fakes(monkeypatch, final_text=VALID_JUDGMENT, status="completed"):
    client = Mock()

    client_context = context_of(client)

    sandbox = Mock()

    sandbox_context = context_of(sandbox)

    def claude_turn(_sandbox, request):
        assert request.working_directory == "/oracle-judge"
        assert request.harness.limit.kind == "wall_seconds"
        assert request.harness.timeout_seconds == 300
        assert request.harness.multi_agent is False
        request.artifact_directory.mkdir(parents=True, exist_ok=True)
        (request.artifact_directory / "final.txt").write_text(final_text)
        return {
            "status": status,
            "duration_seconds": 2.5,
            "usage": usage(),
            "errors": [] if status == "completed" else [{"message": "harness failed"}],
        }

    monkeypatch.setattr("oracle_lab.judge.generation.run.docker_client", client_context)
    monkeypatch.setattr("oracle_lab.judge.generation.run.open_sandbox", sandbox_context)
    monkeypatch.setattr("oracle_lab.judge.generation.run.build_judge_workspace", Mock())
    monkeypatch.setattr("oracle_lab.harnesses.claude.run_turn", claude_turn)
    return client


def test_standalone_judge_saves_prompt_raw_output_provenance_and_labels(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    install_fakes(monkeypatch)

    output = judge(paths)

    assert output.status == "completed"
    assert paths.judge.judgment_raw.read_text() == VALID_JUDGMENT
    judgment = read_json(paths.judge.judgment)
    assert judgment["status"] == "completed"
    assert judgment["tests_issue"]["answer"] == "yes"
    result = read_json(paths.judge.result)
    assert result["model"] == "judge-model"
    assert result["harness"] == "claude"
    prompt = paths.judge.prompt.read_text()
    assert prompt == paths.judge.rubric.read_text()
    assert read_json(paths.status)["judge_status"] == "completed"


def test_invalid_model_output_is_saved_without_a_retry(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    install_fakes(monkeypatch, final_text="explanation before JSON")
    turn = Mock(wraps=claude.run_turn)
    monkeypatch.setattr("oracle_lab.harnesses.claude.run_turn", turn)

    judge(paths)

    assert read_json(paths.judge.judgment)["status"] == "invalid_output"
    assert paths.judge.judgment_raw.read_text() == "explanation before JSON"
    assert turn.call_count == 1


def test_transient_harness_failure_is_archived_and_retried(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    install_fakes(monkeypatch)
    attempts = 0

    def transient_then_complete(_sandbox, request):
        nonlocal attempts
        attempts += 1
        request.artifact_directory.mkdir(parents=True, exist_ok=True)
        (request.artifact_directory / "trace.jsonl").write_text(f"attempt {attempts}\n")
        if attempts == 1:
            return {
                "status": "failed",
                "duration_seconds": 1,
                "usage": None,
                "errors": [{"message": "429 Too Many Requests"}],
            }
        (request.artifact_directory / "final.txt").write_text(VALID_JUDGMENT)
        return {
            "status": "completed",
            "duration_seconds": 2,
            "usage": usage(),
            "errors": [],
        }

    monkeypatch.setattr("oracle_lab.harnesses.claude.run_turn", transient_then_complete)
    delay = Mock()
    monkeypatch.setattr("oracle_lab.harnesses.retry.sleep", delay)

    output = judge(paths)

    assert output.status == "completed"
    assert attempts == 2
    delay.assert_called_once_with(30)
    archived = list(paths.judge.agent_history.iterdir())
    assert len(archived) == 1
    assert (archived[0] / "agent/trace.jsonl").read_text() == "attempt 1\n"


def test_transient_harness_failure_stops_after_three_retries(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    install_fakes(monkeypatch)

    def always_rate_limited(_sandbox, request):
        request.artifact_directory.mkdir(parents=True, exist_ok=True)
        (request.artifact_directory / "trace.jsonl").write_text("rate limited\n")
        return {
            "status": "failed",
            "duration_seconds": 1,
            "usage": None,
            "errors": [{"message": "429 Too Many Requests"}],
        }

    turn = Mock(side_effect=always_rate_limited)
    monkeypatch.setattr("oracle_lab.harnesses.claude.run_turn", turn)
    delay = Mock()
    monkeypatch.setattr("oracle_lab.harnesses.retry.sleep", delay)

    output = judge(paths)

    assert output.status == "failed"
    assert turn.call_count == 4
    assert [call.args[0] for call in delay.call_args_list] == [30, 90, 180]
    assert len(list(paths.judge.agent_history.iterdir())) == 3


@pytest.mark.parametrize(
    "harness_status,judgment_status",
    [("failed", "failed"), ("timeout", "timed_out")],
)
def test_harness_failure_produces_explicit_judgment_status(
    tmp_path, monkeypatch, harness_status, judgment_status
):
    paths = saved_run(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    install_fakes(monkeypatch, status=harness_status)

    judge(paths)

    judgment = read_json(paths.judge.judgment)
    assert judgment["status"] == judgment_status
    assert judgment["error"] == "harness failed"


def test_rerun_archives_complete_previous_attempt(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    paths.judge.judgment.write_text('{"old": true}\n')
    paths.judge.agent.mkdir()
    (paths.judge.agent / "trace.jsonl").write_text("old trace\n")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    install_fakes(monkeypatch)

    judge(paths)

    archives = list(paths.judge.history.iterdir())
    assert len(archives) == 1
    assert read_json(archives[0] / "judgment.json") == {"old": True}
    assert (archives[0] / "agent/trace.jsonl").read_text() == "old trace\n"
    assert paths.judge.rubric.read_text() == "# Frozen rubric\n\nChoose labels.\n"


def test_missing_credential_fails_before_docker(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    docker = Mock()
    monkeypatch.setattr("oracle_lab.judge.generation.run.docker_client", docker)

    with pytest.raises(RuntimeError, match="CLAUDE_CODE_OAUTH_TOKEN"):
        judge(paths)

    docker.assert_not_called()


def test_missing_evaluation_results_fail_before_docker(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    paths.results.unlink()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    docker = Mock()
    monkeypatch.setattr("oracle_lab.judge.generation.run.docker_client", docker)

    with pytest.raises(FileNotFoundError, match="results.json"):
        judge(paths)

    docker.assert_not_called()


def test_unexpected_harness_error_is_saved_as_failure(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    install_fakes(monkeypatch)
    turn = Mock(side_effect=RuntimeError("transport broke"))
    monkeypatch.setattr("oracle_lab.harnesses.claude.run_turn", turn)

    judge(paths)

    assert read_json(paths.judge.judgment) == {
        "status": "failed",
        "error": "transport broke",
    }
    # The stage records the attempt itself when the harness never got far enough to.
    attempt = read_json(paths.judge.result)
    assert attempt["status"] == "failed"
    assert attempt["model"] == "judge-model"
    assert attempt["errors"][-1]["message"] == "transport broke"
    assert turn.call_count == 1


@pytest.mark.parametrize(
    "error", [RuntimeError("workspace failed"), DockerException("daemon refused the container")]
)
def test_workspace_failure_is_recorded_by_standalone_judge(tmp_path, monkeypatch, error):
    paths = saved_run(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    install_fakes(monkeypatch)
    monkeypatch.setattr(
        "oracle_lab.judge.generation.run.build_judge_workspace", Mock(side_effect=error)
    )

    judge(paths)

    assert read_json(paths.judge.judgment)["status"] == "failed"
    assert read_json(paths.judge.judgment)["error"] == str(error)
    assert read_json(paths.status)["judge_status"] == "failed"


def test_late_failure_keeps_the_judgment_the_attempt_already_produced(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    install_fakes(monkeypatch)

    @contextmanager
    def failing_cleanup(*args, **kwargs):
        yield Mock()
        raise RuntimeError("Could not remove benchmark container")

    monkeypatch.setattr("oracle_lab.judge.generation.run.open_sandbox", failing_cleanup)

    judge(paths)

    judgment = read_json(paths.judge.judgment)
    assert judgment["status"] == "completed"
    assert judgment["tests_issue"]["answer"] == "yes"
    attempt = read_json(paths.judge.result)
    assert attempt["errors"][-1]["message"] == "Could not remove benchmark container"


def test_missing_runtime_image_does_not_archive_previous_attempt(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    paths.judge.judgment.write_text('{"old": true}\n')
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    client = Mock()
    client.images.get.side_effect = ImageNotFound("missing")

    client_context = context_of(client)

    monkeypatch.setattr("oracle_lab.judge.generation.run.docker_client", client_context)

    with pytest.raises(RuntimeError, match="runtime image is missing"):
        judge(paths)

    assert read_json(paths.judge.judgment) == {"old": True}


def test_a_tampered_submission_is_rejected_before_docker(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    (paths.submission / "files/oracle_tests/test_generated.py").write_text("tampered")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    docker = Mock()
    monkeypatch.setattr("oracle_lab.judge.generation.run.docker_client", docker)

    with pytest.raises(ValueError, match="checksum mismatch"):
        judge(paths)

    docker.assert_not_called()


def test_empty_submission_is_rejected_before_docker(tmp_path, monkeypatch):
    paths = saved_run(tmp_path)
    manifest = read_json(paths.submission / "manifest.json")
    manifest["empty"] = True
    write_json(paths.submission / "manifest.json", manifest)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")
    docker = Mock()
    monkeypatch.setattr("oracle_lab.judge.generation.run.docker_client", docker)

    with pytest.raises(ValueError, match="no generated tests"):
        judge(paths)

    docker.assert_not_called()
