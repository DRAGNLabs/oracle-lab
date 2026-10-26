"""Evaluation execution preserves binary evidence and marks incomplete output."""

from unittest.mock import Mock

import pytest
from fixtures import instance_record, localized_target, run_config, submission_manifest

import oracle_lab.evaluation as evaluation
from oracle_lab.container.sandbox import CommandResult
from oracle_lab.contracts.run import (
    SubmissionManifest,
    VersionResult,
    read_evaluation_result,
    read_localized_target,
)
from oracle_lab.evaluation import run_tests
from oracle_lab.io import read_json, write_json
from oracle_lab.paths import RunPaths


@pytest.fixture
def config():
    value = run_config("app", image="example/image")
    return value


def test_completed_runner_output_becomes_the_version_result(config, tmp_path):
    sandbox = Mock(helpers="/helpers")
    sandbox.run.return_value = CommandResult(0, 1)
    evidence = {"status": "completed", "tests": {"test_example": {"outcome": "fail"}}}
    sandbox.download.side_effect = lambda *args, **kwargs: write_json(
        tmp_path / "tests.json", evidence
    )
    result = run_tests(sandbox, config, ["oracle_tests"], tmp_path)
    assert result == VersionResult.model_validate(evidence)


def test_runner_receives_repository_pytest_plugins(config, tmp_path):
    config.runtime.pytest_plugins = ["pytester"]
    sandbox = Mock(helpers="/helpers")
    sandbox.run.return_value = CommandResult(0, 1)
    sandbox.download.side_effect = lambda *args, **kwargs: write_json(
        tmp_path / "tests.json", {"status": "no_tests", "tests": {}}
    )

    run_tests(sandbox, config, ["oracle_tests"], tmp_path)

    assert read_json(tmp_path / "runner.json")["pytest_plugins"] == ["pytester"]


@pytest.mark.parametrize("timed_out,status", [(True, "timeout"), (False, "runner_error")])
def test_missing_results_never_count_as_success(config, tmp_path, timed_out, status):
    sandbox = Mock(helpers="/helpers")
    sandbox.run.return_value = CommandResult(124 if timed_out else 0, 1, timed_out)
    result = run_tests(sandbox, config, ["oracle_tests"], tmp_path)
    assert result.status == status
    sandbox.download.assert_called_once()


def test_unreadable_runner_output_becomes_an_explicit_incomplete_result(config, tmp_path):
    sandbox = Mock(helpers="/helpers")
    sandbox.run.return_value = CommandResult(0, 1)
    sandbox.download.side_effect = lambda *args, **kwargs: (tmp_path / "tests.json").write_text(
        '{"status": "completed", "tests": {"test_example": "not an object"}}'
    )

    result = run_tests(sandbox, config, ["oracle_tests"], tmp_path)

    assert result.status == "runner_error"
    assert "Unreadable runner output" in result.error
    # The runner's own file is kept beside the normalized one.
    assert "not an object" in (tmp_path / "tests.raw.json").read_text()


def test_runner_interruption_survives_failed_partial_collection(config, tmp_path):
    sandbox = Mock(helpers="/helpers")
    sandbox.run.side_effect = KeyboardInterrupt()
    sandbox.download.side_effect = RuntimeError("copy failed")
    with pytest.raises(KeyboardInterrupt):
        run_tests(sandbox, config, ["oracle_tests"], tmp_path)


def test_collection_failure_after_successful_execution_propagates(config, tmp_path):
    sandbox = Mock(helpers="/helpers")
    sandbox.run.return_value = CommandResult(0, 1)
    sandbox.download.side_effect = RuntimeError("copy failed")
    with pytest.raises(RuntimeError, match="copy failed"):
        run_tests(sandbox, config, ["oracle_tests"], tmp_path)


@pytest.mark.parametrize("scope", ["localized", "repository"])
def test_evaluation_records_the_target_the_agent_was_shown(config, tmp_path, monkeypatch, scope):
    config.task.scope = scope
    paths = RunPaths.create(tmp_path)
    write_json(paths.target, localized_target())
    write_json(paths.generation / "result.json", {"status": "completed"})
    manifest = SubmissionManifest.model_validate(submission_manifest())
    monkeypatch.setattr(evaluation, "verify_bundle", lambda paths: manifest)
    monkeypatch.setattr(evaluation, "archive_previous_evaluation", lambda paths: None)
    completed = VersionResult(status="completed", tests={})
    monkeypatch.setattr(evaluation, "run_submission", lambda *args: completed)

    evaluation.evaluate(Mock(), config, "image", instance_record(), paths)

    saved = read_evaluation_result(paths.results).test_target
    assert saved == (read_localized_target(paths.target) if scope == "localized" else None)
