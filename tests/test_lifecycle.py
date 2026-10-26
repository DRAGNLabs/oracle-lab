import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from fixtures import (
    RUN_CONFIG,
    completed_judgment,
    context_of,
    evaluation_result,
    instance_record,
    localized_target,
    submission_manifest,
)

import oracle_lab.judge.generation.run as judge_module
import oracle_lab.preflight as preflight_module
import oracle_lab.run as run_module
from oracle_lab.config import JudgeConfig, RuntimeConfig, load_config
from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.run import EvaluationResult, LocalizedTarget, SubmissionManifest
from oracle_lab.evaluation import archive_previous_evaluation
from oracle_lab.harnesses import require_credentials
from oracle_lab.io import read_json, write_json
from oracle_lab.paths import OutputPaths, RunPaths
from oracle_lab.run import completion_state

SAMPLE = RUN_CONFIG


def test_reevaluation_archives_results_but_preserves_generated_tests(tmp_path):
    paths = RunPaths.create(tmp_path)
    (paths.evaluation / "buggy").mkdir()
    (paths.evaluation / "buggy" / "tests.json").write_text("old")
    (paths.submission / "manifest.json").write_text("keep")
    archive_previous_evaluation(paths)
    assert not (paths.evaluation / "buggy").exists()
    assert next(paths.evaluation_history.glob("*/buggy/tests.json")).read_text() == "old"
    assert (paths.submission / "manifest.json").read_text() == "keep"


@pytest.mark.parametrize(
    "changes,problems",
    [
        ({}, []),
        ({"agent": {"status": "timeout"}}, ["generation timeout"]),
        ({"buggy_status": "no_tests", "golden_status": "no_tests"}, ["no tests collected"]),
        ({"golden_status": "infrastructure_error"}, ["golden evaluation infrastructure_error"]),
        (
            {"submission_compliant": False, "forbidden_changes": ["app.py"]},
            ["diagnostic only: edits outside the generated directory"],
        ),
    ],
)
def test_the_run_state_and_the_report_share_one_list_of_problems(changes, problems):
    result = EvaluationResult.model_validate(evaluation_result(**changes))

    assert result.completion_problems == problems
    assert completion_state(result) == ("completed_with_errors" if problems else "completed")


def test_failed_agent_is_not_reported_as_a_successful_pipeline():
    failed = EvaluationResult.model_validate(
        evaluation_result(
            agent={"status": "failed"}, buggy_status="no_tests", golden_status="no_tests"
        )
    )
    assert completion_state(failed) == "completed_with_errors"
    assert completion_state(EvaluationResult.model_validate(evaluation_result())) == "completed"


def configured_pipeline(tmp_path, with_judge):
    prompt = tmp_path / "prompt.md"
    prompt.write_text("Generate tests")
    config = load_config(SAMPLE)
    config.output = str(tmp_path / "output")
    config.task.prompt = str(prompt)
    config.judge = None
    if with_judge:
        rubric = tmp_path / "rubric.md"
        rubric.write_text("# Rubric\n")
        config.judge = JudgeConfig(
            rubric=str(rubric),
            harness="claude",
            model="judge-model",
            limit={"kind": "wall_seconds", "value": 300},
        )
    runtime = RuntimeConfig(
        image="example/runtime",
        source_roots=["package"],
        import_modules=["package"],
        existing_test_globs=["tests"],
    )
    instance = instance_record(
        golden_patch="diff --git a/a.py b/a.py\n",
        reference_test_patch="diff --git a/test_a.py b/test_a.py\n",
        reference_test_ids=["test_a.py::test_a"],
    )
    return config, runtime, instance


def install_pipeline_fakes(monkeypatch, runtime, instance, judge_effect=None):
    client = Mock()

    client_context = context_of(client)

    monkeypatch.setattr(run_module, "require_credentials", Mock())
    monkeypatch.setattr(run_module, "resolve_source", lambda source: (instance, runtime))
    monkeypatch.setattr(run_module, "docker_client", client_context)
    monkeypatch.setattr(run_module, "prepare_image", lambda *args: "example/runtime")
    target = LocalizedTarget.model_validate(localized_target())
    monkeypatch.setattr(run_module, "localize", Mock(return_value=target))
    monkeypatch.setattr(run_module, "check_reference", Mock())
    manifest = SubmissionManifest.model_validate(submission_manifest())
    monkeypatch.setattr(run_module, "generate_submission", Mock(return_value=manifest))
    monkeypatch.setattr(run_module, "verify_bundle", Mock(return_value=manifest))
    result = EvaluationResult.model_validate(evaluation_result())
    monkeypatch.setattr(run_module, "evaluate", lambda *args: result)

    # Fake the turn itself, not the stage. judge_stage's own failure handling then
    # runs for real, which is what these tests are checking.
    judge_sandbox = context_of(Mock(helpers="/opt/oracle-lab/container-helpers"))

    def judge_effectively(sandbox, request):
        if judge_effect is not None:
            raise judge_effect
        judgment = completed_judgment()
        judgment.pop("status")
        request.artifact_directory.mkdir(parents=True, exist_ok=True)
        (request.artifact_directory / "final.txt").write_text(json.dumps(judgment))
        return {
            "status": "completed",
            "duration_seconds": 1,
            "usage": None,
            "errors": [],
        }

    judge_call = Mock(side_effect=judge_effectively)
    monkeypatch.setattr(judge_module, "open_sandbox", judge_sandbox)
    monkeypatch.setattr(judge_module, "build_judge_workspace", Mock())
    monkeypatch.setattr(judge_module, "run_turn", judge_call)
    monkeypatch.setattr(run_module, "report", lambda paths: paths.report)
    monkeypatch.setattr(run_module, "measure_spend", recording_spend([]))
    return judge_call


def recording_spend(log: list):
    """A stand-in for `measure_spend` that records which harnesses it measured."""

    @contextmanager
    def measure(harnesses, path, on_wait):
        log.append(([harness.harness for harness in harnesses], path.name))
        yield
        on_wait()

    return measure


@pytest.mark.parametrize(
    "with_judge,expected_status,expected_calls",
    [(False, "disabled", 0), (True, "completed", 1)],
)
def test_normal_lifecycle_runs_only_a_configured_judge(
    tmp_path, monkeypatch, with_judge, expected_status, expected_calls
):
    config, runtime, instance = configured_pipeline(tmp_path, with_judge)
    judge_call = install_pipeline_fakes(monkeypatch, runtime, instance)

    run_dir = run_module.run(config)

    assert run_dir.parent == OutputPaths(Path(config.output)).runs
    final = read_json(run_dir / "status.json")
    assert final["state"] == "completed"
    assert final["judge_status"] == expected_status
    assert judge_call.call_count == expected_calls


@pytest.mark.parametrize("with_judge,measured", [(False, ["claude"]), (True, ["claude", "claude"])])
def test_run_measures_spend_across_every_model_stage(tmp_path, monkeypatch, with_judge, measured):
    config, runtime, instance = configured_pipeline(tmp_path, with_judge)
    install_pipeline_fakes(monkeypatch, runtime, instance)
    spent = []
    monkeypatch.setattr(run_module, "measure_spend", recording_spend(spent))

    run_module.run(config)

    assert spent == [(measured, "cost.json")]


def test_run_fills_the_agent_prompt_environment(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, False)
    config.task.scope = "localized"
    Path(config.task.prompt).write_text(
        "Write to ${generated_dir} from ${workdir} using ${project_python}; "
        "target ${test_target}; $$HOME remains literal."
    )
    install_pipeline_fakes(monkeypatch, runtime, instance)

    run_dir = run_module.run(config)

    assert RunPaths(run_dir).prompt.read_text() == (
        f"Write to oracle_tests from {runtime.workdir} using {runtime.python}; "
        f"target {localized_target()['text']}; $HOME remains literal."
    )


def test_a_localized_run_localizes_after_the_build_and_before_the_reference_check(
    tmp_path, monkeypatch
):
    config, runtime, instance = configured_pipeline(tmp_path, False)
    config.task.scope = "localized"
    Path(config.task.prompt).write_text("Test ${test_target}")
    install_pipeline_fakes(monkeypatch, runtime, instance)
    order = []
    monkeypatch.setattr(run_module, "prepare_image", lambda *args: order.append("build") or "img")
    located = run_module.localize
    monkeypatch.setattr(
        run_module, "localize", lambda *args: order.append("localize") or located(*args)
    )

    def check_reference(client, config, image, instance, paths):
        order.append("reference")
        # The prompt exists before anything that can spend money.
        assert paths.prompt.read_text() == "Test " + localized_target()["text"]

    monkeypatch.setattr(run_module, "check_reference", check_reference)

    run_module.run(config)

    assert order == ["build", "localize", "reference"]


def test_a_repository_run_never_localizes(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, False)
    install_pipeline_fakes(monkeypatch, runtime, instance)

    run_dir = run_module.run(config)

    run_module.localize.assert_not_called()
    assert RunPaths(run_dir).prompt.read_text() == "Generate tests"


def test_a_localize_failure_stops_the_run_at_that_stage(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, False)
    config.task.scope = "localized"
    Path(config.task.prompt).write_text("Test ${test_target}")
    install_pipeline_fakes(monkeypatch, runtime, instance)
    failure = ValueError("hunk at line 3 matches nowhere in the buggy file")
    monkeypatch.setattr(run_module, "localize", Mock(side_effect=failure))

    with pytest.raises(ValueError, match="matches nowhere"):
        run_module.run(config)

    (run_dir,) = OutputPaths(Path(config.output)).runs.iterdir()
    final = read_json(run_dir / "status.json")
    assert (final["stage"], final["state"]) == ("localize", "failed")
    assert not RunPaths(run_dir).prompt.exists()
    run_module.check_reference.assert_not_called()


@pytest.mark.parametrize(
    "name,scope",
    [
        ("unit-tests.md", "repository"),
        ("localized-tests.md", "localized"),
        ("smoke-tests.md", "repository"),
    ],
)
def test_run_renders_each_bundled_agent_prompt(tmp_path, monkeypatch, name, scope):
    config, runtime, instance = configured_pipeline(tmp_path, False)
    config.task.prompt = str(Path(__file__).parents[1] / "prompts/agent" / name)
    config.task.scope = scope
    install_pipeline_fakes(monkeypatch, runtime, instance)

    run_dir = run_module.run(config)
    prompt = RunPaths(run_dir).prompt.read_text()

    assert "oracle_tests" in prompt
    assert runtime.python in prompt


def test_run_refuses_a_localized_scope_whose_prompt_hides_the_target(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, False)
    config.task.scope = "localized"
    install_pipeline_fakes(monkeypatch, runtime, instance)

    with pytest.raises(ValueError, match="test_target"):
        run_module.run(config)
    assert not Path(config.output).exists()


def test_saved_config_points_at_the_run_s_own_rubric_copy(tmp_path, monkeypatch):
    """A later reevaluate or judge must read the bytes this run used, not the repo's."""
    config, runtime, instance = configured_pipeline(tmp_path, True)
    source_rubric = Path(config.judge.rubric)
    install_pipeline_fakes(monkeypatch, runtime, instance)

    run_dir = run_module.run(config)

    paths = RunPaths(run_dir)
    assert paths.judge.rubric.read_bytes() == source_rubric.read_bytes()

    # Reload the way `evaluate` and `judge` do, from a different working directory.
    reloaded = load_config(paths.config, resolved=True)
    assert Path(reloaded.judge.rubric) == paths.judge.rubric.resolve()

    # Editing the original afterwards must not change what the run judges against.
    source_rubric.write_text("# Rewritten after the run\n")
    assert paths.judge.rubric.read_text() == "# Rubric\n"


def test_judge_failure_preserves_completed_evaluation(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, True)
    install_pipeline_fakes(monkeypatch, runtime, instance, RuntimeError("judge unavailable"))

    run_dir = run_module.run(config)

    final = read_json(run_dir / "status.json")
    assert final["state"] == "completed"
    assert final["judge_status"] == "failed"
    assert read_json(RunPaths(run_dir).judge.judgment)["status"] == "failed"


def test_empty_generation_skips_configured_judge(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, True)
    judge_call = install_pipeline_fakes(monkeypatch, runtime, instance)
    empty = SubmissionManifest.model_validate(submission_manifest({}))
    monkeypatch.setattr(run_module, "generate_submission", Mock(return_value=empty))
    failed = EvaluationResult.model_validate(
        evaluation_result(
            agent={"status": "failed"}, buggy_status="no_tests", golden_status="no_tests"
        )
    )
    monkeypatch.setattr(run_module, "evaluate", lambda *args: failed)

    run_dir = run_module.run(config)

    paths = RunPaths(run_dir)
    assert read_json(paths.status)["judge_status"] == "skipped"
    assert read_json(paths.judge.judgment) == {
        "status": "skipped",
        "reason": "No generated tests were collected; no judge turn was run.",
    }
    judge_call.assert_not_called()


def test_captured_files_without_collected_tests_skip_judge(tmp_path, monkeypatch):
    """A timeout that leaves only a conftest.py has files but nothing to judge."""
    config, runtime, instance = configured_pipeline(tmp_path, True)
    judge_call = install_pipeline_fakes(monkeypatch, runtime, instance)
    no_tests = EvaluationResult.model_validate(
        evaluation_result(
            agent={"status": "timeout"}, buggy_status="no_tests", golden_status="no_tests"
        )
    )
    monkeypatch.setattr(run_module, "evaluate", lambda *args: no_tests)

    run_dir = run_module.run(config)

    assert read_json(run_dir / "status.json")["judge_status"] == "skipped"
    judge_call.assert_not_called()


def test_timed_out_generation_with_captured_tests_is_judged(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, True)
    judge_call = install_pipeline_fakes(monkeypatch, runtime, instance)
    timed_out = EvaluationResult.model_validate(evaluation_result(agent={"status": "timeout"}))
    monkeypatch.setattr(run_module, "evaluate", lambda *args: timed_out)

    run_dir = run_module.run(config)

    assert read_json(run_dir / "status.json")["judge_status"] == "completed"
    judge_call.assert_called_once()


def test_missing_judge_credential_fails_before_paid_generation(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, True)
    install_pipeline_fakes(monkeypatch, runtime, instance)
    monkeypatch.setattr(run_module, "require_credentials", require_credentials)
    monkeypatch.setenv(config.agent.credential_env, "generation-credential-fixture")
    monkeypatch.delenv(config.judge.credential_env, raising=False)
    generation = Mock()
    evaluation = Mock()
    monkeypatch.setattr(run_module, "generate_submission", generation)
    monkeypatch.setattr(run_module, "evaluate", evaluation)

    with pytest.raises(RuntimeError, match=config.judge.credential_env):
        run_module.run(config)

    generation.assert_not_called()
    evaluation.assert_not_called()


def test_judge_interruption_remains_an_interrupted_judge_stage(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, True)
    install_pipeline_fakes(monkeypatch, runtime, instance, KeyboardInterrupt())

    with pytest.raises(KeyboardInterrupt):
        run_module.run(config)

    (run_dir,) = OutputPaths(Path(config.output)).runs.iterdir()
    status = read_json(run_dir / "status.json")
    assert status["stage"] == "judge"
    assert status["state"] == "interrupted"


def test_reevaluation_marks_existing_judgment_stale_without_calling_a_model(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, True)
    config.runtime = runtime
    run_dir = tmp_path / "saved-run"
    paths = RunPaths.create(run_dir)
    config.judge.rubric = str(paths.judge.rubric)
    paths.judge.rubric.write_text("# Rubric\n")
    paths.config.write_text(yaml.safe_dump(config.model_dump(), sort_keys=False))
    write_json(paths.runtime, {"image": "example/runtime"})
    write_result(paths.instance, instance)
    write_json(paths.judge.judgment, completed_judgment())
    client = Mock()

    client_context = context_of(client)

    result = EvaluationResult.model_validate(evaluation_result())
    monkeypatch.setattr(run_module, "docker_client", client_context)
    monkeypatch.setattr(run_module, "evaluate", lambda *args: result)
    monkeypatch.setattr(run_module, "report", lambda paths: paths.report)
    judge_call = Mock()
    monkeypatch.setattr(run_module, "judge_stage", judge_call)

    run_module.reevaluate(load_config(paths.config, resolved=True), paths)

    judgment = read_json(paths.judge.judgment)
    assert judgment["status"] == "stale"
    assert judgment["previous_judgment"]["tests_issue"]["answer"] == "yes"
    assert read_json(run_dir / "status.json")["judge_status"] == "stale"
    judge_call.assert_not_called()


def test_preflight_checks_the_environment_without_credentials_or_generation(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, True)
    install_pipeline_fakes(monkeypatch, runtime, instance)
    monkeypatch.setattr(run_module, "require_credentials", Mock(side_effect=AssertionError))
    monkeypatch.setattr(preflight_module, "docker_client", context_of(Mock()))
    monkeypatch.setattr(preflight_module, "check_environment", Mock())

    run_dir = preflight_module.preflight(config)

    assert run_dir.parent == OutputPaths(Path(config.output)).preflight
    final = read_json(run_dir / "status.json")
    assert (final["stage"], final["state"]) == ("finished", "passed")
    run_module.check_reference.assert_called_once()
    preflight_module.check_environment.assert_called_once()
    run_module.generate_submission.assert_not_called()


def test_a_preflight_failure_records_the_stage_it_stopped_in(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, False)
    install_pipeline_fakes(monkeypatch, runtime, instance)
    failure = RuntimeError("Environment check test did not pass on both versions")
    monkeypatch.setattr(preflight_module, "docker_client", context_of(Mock()))
    monkeypatch.setattr(preflight_module, "check_environment", Mock(side_effect=failure))

    with pytest.raises(RuntimeError, match="did not pass"):
        preflight_module.preflight(config)

    (run_dir,) = OutputPaths(Path(config.output)).preflight.iterdir()
    final = read_json(run_dir / "status.json")
    assert (final["stage"], final["state"]) == ("environment", "failed")
