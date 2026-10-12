import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml
from fixtures import completed_judgment, evaluation_result, instance_record

import oracle_bench.judge.run as judge_module
import oracle_bench.run as run_module
from oracle_bench.config import JudgeConfig, RuntimeConfig, load_config
from oracle_bench.evaluation import archive_previous_evaluation
from oracle_bench.harnesses import require_credentials
from oracle_bench.harnesses.codex import parse_trace
from oracle_bench.io import read_json, write_json
from oracle_bench.paths import RunPaths
from oracle_bench.results import EvaluationResult, write_result
from oracle_bench.run import completion_state

SAMPLE = Path(__file__).parents[1] / "configs/smoke.yaml"


def test_reevaluation_archives_results_but_preserves_generated_tests(tmp_path):
    paths = RunPaths.create(tmp_path)
    (paths.evaluation / "buggy").mkdir()
    (paths.evaluation / "buggy" / "tests.json").write_text("old")
    (paths.submission / "manifest.json").write_text("keep")
    archive_previous_evaluation(paths)
    assert not (paths.evaluation / "buggy").exists()
    assert next(paths.evaluation_history.glob("*/buggy/tests.json")).read_text() == "old"
    assert (paths.submission / "manifest.json").read_text() == "keep"


def test_codex_usage_already_counts_cached_prompt_tokens(tmp_path):
    raw = {
        "input_tokens": 103067,
        "cached_input_tokens": 82068,
        "cache_write_input_tokens": 20523,
        "output_tokens": 3728,
        "reasoning_output_tokens": 852,
    }
    path = tmp_path / "trace.jsonl"
    path.write_text("not-json\n" + json.dumps({"type": "turn.completed", "usage": raw}) + "\n")
    result = parse_trace(path)
    assert result["usage"] == {
        "input_tokens": 103067,
        "cached_input_tokens": 82068,
        "output_tokens": 3728,
        "reasoning_tokens": 852,
        "raw_usage": raw,
    }
    assert result["unparsed_trace_lines"] == 1


def test_recovered_stream_error_does_not_mean_the_turn_failed(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        '{"type":"error","message":"reconnecting"}\n'
        '{"type":"turn.completed","usage":{"output_tokens":2}}\n'
    )
    result = parse_trace(path)
    assert result["turn_completed"] and not result["turn_failed"]
    assert result["errors"]


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
    config.output = str(tmp_path / "runs")
    config.task.prompt = str(prompt)
    config.judge = None
    if with_judge:
        rubric = tmp_path / "rubric.md"
        rubric.write_text("# Rubric\n")
        instructions = tmp_path / "instructions.md"
        instructions.write_text("Judge the submission in $root.\n")
        config.judge = JudgeConfig(
            rubric=str(rubric),
            instructions=str(instructions),
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

    @contextmanager
    def client_context():
        yield client

    monkeypatch.setattr(run_module, "require_credentials", Mock())
    monkeypatch.setattr(run_module, "resolve_source", lambda source: (instance, runtime))
    monkeypatch.setattr(run_module, "docker_client", client_context)
    monkeypatch.setattr(run_module, "prepare_image", lambda *args: "example/runtime")
    monkeypatch.setattr(run_module, "check_reference", Mock())
    monkeypatch.setattr(run_module, "generate_submission", Mock(return_value={"empty": False}))
    result = EvaluationResult.model_validate(evaluation_result())
    monkeypatch.setattr(run_module, "evaluate", lambda *args: result)

    # Fake the turn itself, not the stage. judge_stage's own failure handling then
    # runs for real, which is what these tests are checking.
    @contextmanager
    def judge_sandbox(*args, **kwargs):
        yield Mock(helpers="/opt/oracle-bench/container-helpers")

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
    monkeypatch.setattr(run_module, "measure_spend", fake_spend)
    return judge_call


spent = []


@contextmanager
def fake_spend(harnesses, path, on_wait):
    spent.append(([harness.harness for harness in harnesses], path.name))
    yield
    on_wait()


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

    final = read_json(run_dir / "status.json")
    assert final["state"] == "completed"
    assert final["judge_status"] == expected_status
    assert judge_call.call_count == expected_calls


@pytest.mark.parametrize("with_judge,measured", [(False, ["claude"]), (True, ["claude", "claude"])])
def test_run_measures_spend_across_every_model_stage(tmp_path, monkeypatch, with_judge, measured):
    config, runtime, instance = configured_pipeline(tmp_path, with_judge)
    install_pipeline_fakes(monkeypatch, runtime, instance)
    spent.clear()

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

    assert RunPaths.open(run_dir).prompt.read_text() == (
        f"Write to oracle_tests from {runtime.workdir} using {runtime.python}; "
        f"target {instance.test_target}; $HOME remains literal."
    )


@pytest.mark.parametrize("name", ["unit-tests.md", "localized-tests.md", "smoke-tests.md"])
def test_run_renders_each_bundled_agent_prompt(tmp_path, monkeypatch, name):
    config, runtime, instance = configured_pipeline(tmp_path, False)
    config.task.prompt = str(Path(__file__).parents[1] / "prompts/agent" / name)
    install_pipeline_fakes(monkeypatch, runtime, instance)

    run_dir = run_module.run(config)
    prompt = RunPaths.open(run_dir).prompt.read_text()

    assert "oracle_tests" in prompt
    assert runtime.python in prompt


def test_saved_config_points_at_the_run_s_own_rubric_copy(tmp_path, monkeypatch):
    """A later reevaluate or judge must read the bytes this run used, not the repo's."""
    config, runtime, instance = configured_pipeline(tmp_path, True)
    source_rubric = Path(config.judge.rubric)
    install_pipeline_fakes(monkeypatch, runtime, instance)

    run_dir = run_module.run(config)

    paths = RunPaths.open(run_dir)
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
    assert read_json(RunPaths.open(run_dir).judge.judgment)["status"] == "failed"


def test_empty_generation_skips_configured_judge(tmp_path, monkeypatch):
    config, runtime, instance = configured_pipeline(tmp_path, True)
    judge_call = install_pipeline_fakes(monkeypatch, runtime, instance)
    monkeypatch.setattr(run_module, "generate_submission", Mock(return_value={"empty": True}))
    failed = EvaluationResult.model_validate(
        evaluation_result(
            agent={"status": "failed"}, buggy_status="no_tests", golden_status="no_tests"
        )
    )
    monkeypatch.setattr(run_module, "evaluate", lambda *args: failed)

    run_dir = run_module.run(config)

    paths = RunPaths.open(run_dir)
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

    run_dir = next((tmp_path / "runs").iterdir())
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
    config.judge.instructions = str(paths.judge.instructions)
    paths.judge.instructions.write_text("Judge the submission in $root.\n")
    paths.config.write_text(yaml.safe_dump(config.to_dict(), sort_keys=False))
    write_json(paths.runtime, {"image": "example/runtime"})
    write_result(paths.instance, instance)
    write_json(paths.judge.judgment, completed_judgment())
    human_judgment = paths.judge.human.judgments / "rater_1/judgment.json"
    write_json(human_judgment, read_json(paths.judge.judgment))
    client = Mock()

    @contextmanager
    def client_context():
        yield client

    result = EvaluationResult.model_validate(evaluation_result())
    monkeypatch.setattr(run_module, "docker_client", client_context)
    monkeypatch.setattr(run_module, "evaluate", lambda *args: result)
    monkeypatch.setattr(run_module, "report", lambda paths: paths.report)
    judge_call = Mock()
    monkeypatch.setattr(run_module, "judge_stage", judge_call)

    run_module.reevaluate(load_config(paths.config, resolved=True), paths)

    judgment = read_json(paths.judge.judgment)
    assert judgment["status"] == "stale"
    assert judgment["previous_judgment"]["tests_issue"] == "yes"
    assert read_json(human_judgment)["status"] == "stale"
    assert read_json(run_dir / "status.json")["judge_status"] == "stale"
    judge_call.assert_not_called()
