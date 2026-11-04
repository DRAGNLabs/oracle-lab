import json
import re

import pytest
from fixtures import (
    added_failure_mode,
    failure_modes,
    instance_record,
    judge_attempt,
    localized_target,
    out_of_scope_classification,
    usage,
)
from fixtures import completed_judgment as fixture_completed_judgment

from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.run import MATRIX_CELLS
from oracle_lab.io import write_json
from oracle_lab.paths import AttemptPaths, OutputPaths, RunPaths
from oracle_lab.report import report


@pytest.mark.parametrize(
    ("scope", "target", "expected_scope"),
    [
        ("repository", None, "target: whole repository"),
        (
            "localized",
            localized_target(),
            "target: `package/core.py`: `Client.send` (fix scope: function);",
        ),
        (
            "localized",
            localized_target(
                files=[
                    {"path": "a.py", "functions": [], "classes": ["A"], "lines": 3},
                    {"path": "b.py", "functions": [], "classes": ["B", "C"], "lines": 9},
                ],
                text="- `a.py`: class `A`\n- `b.py`: classes `B`, `C`",
                scope="module",
            ),
            "target: `a.py`: class `A`; `b.py`: classes `B`, `C` (fix scope: module);",
        ),
    ],
)
def test_report_renders_run_metadata(tmp_path, scope, target, expected_scope):
    results = report_results(
        task_scope=scope,
        test_target=target,
        existing_tests="keep",
        agent={"status": "completed"},
        failure_kinds={
            "buggy": {"assertion": 2, "exception": 1, "unknown": 0},
            "golden": {"assertion": 0, "exception": 1, "unknown": 0},
        },
    )
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(results))
    (paths.ground_truth / "issue.md").write_text("issue")
    (paths.ground_truth / "fix.patch").write_text("patch")
    text = report(paths).read_text()
    assert "existing tests visible" in text
    assert expected_scope in text
    assert "- Ground truth: [issue](ground-truth/issue.md), [fix](ground-truth/fix.patch)" in text
    assert "| buggy | 2 | 1 | 0 |" in text
    assert "| Judge | — | disabled |" in text
    assert "| Cost | OpenRouter key spend, generation and judge | — |" in text


@pytest.mark.parametrize(
    "cost,expected",
    [
        (
            {"status": "measured", "cost_usd": 0.0378, "waited_seconds": 70},
            "$0.0378",
        ),
        (
            {"status": "unsettled", "cost_usd": 0.0378, "waited_seconds": 180},
            "$0.0378 (unsettled after 180 s)",
        ),
        (
            {"status": "unavailable", "error": "HTTP Error 503"},
            "unavailable: HTTP Error 503",
        ),
        (
            {"status": "not_measured"},
            "not measured: a model stage does not use OpenRouter",
        ),
    ],
)
def test_report_renders_run_cost(tmp_path, cost, expected):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results()))
    write_json(paths.cost, cost)

    text = report(paths).read_text()

    assert f"| Cost | OpenRouter key spend, generation and judge | {expected} |" in text
    assert "[cost](cost.json)" in text


def report_results(**changes):
    """A complete evaluation result; tests override only what they exercise."""
    results = {
        "instance_id": "test-instance",
        "artifact_sha256": "a" * 64,
        "submission_compliant": True,
        "forbidden_changes": [],
        "task_scope": "repository",
        "test_target": None,
        "existing_tests": "hide_all",
        "agent": {
            "status": "completed",
            "duration_seconds": 4,
            "usage": usage(input_tokens=12),
        },
        "buggy_status": "completed",
        "golden_status": "completed",
        "matrix": {key: {"count": 0, "test_ids": []} for key in MATRIX_CELLS},
        "other_outcomes": [],
        "tests": [],
        "failure_kinds": {
            "buggy": {"assertion": 0, "exception": 0, "unknown": 0},
            "golden": {"assertion": 0, "exception": 0, "unknown": 0},
        },
    }
    results.update(changes)
    return results


def completed_judgment(**changes):
    return fixture_completed_judgment(
        tests_issue={
            "answer": "yes",
            "justification": "The generated assertion reaches the issue.",
        },
        attempt_detail={
            "answer": "correct_assertion",
            "justification": "It distinguishes both revisions.",
        },
        **changes,
    )


def test_report_renders_completed_judge_with_provenance_and_supporting_files(tmp_path):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results()))
    judgment = completed_judgment(
        failure_modes=failure_modes(changed_test_to_pass=[23, 24]),
        added_failure_modes=[added_failure_mode()],
    )
    paths.judge.judgment.write_text(json.dumps(judgment))
    write_targets = [
        paths.judge.judgment_raw,
        paths.judge.prompt,
        paths.judge.rubric,
        paths.judge.workspace_spec,
        paths.judge.agent / "trace.jsonl",
        paths.judge.agent / "stderr.log",
    ]
    for path in write_targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("evidence")
    write_json(
        paths.judge.result,
        judge_attempt(
            harness="claude",
            provider="anthropic",
            model="judge-model",
            harness_version="2.1.263",
            duration_seconds=3,
            usage=usage(input_tokens=20),
        ),
    )

    text = report(paths).read_text()

    assert (
        "| Judge | claude 2.1.263, judge-model, anthropic, 60 s limit | completed, tests issue: yes |"
        in text
    )
    assert "| yes | correct_assertion | — | no |" in text
    assert "`tests_issue`:\n\n> The generated assertion reaches the issue." in text
    assert "`attempt_detail`:\n\n> It distinguishes both revisions." in text
    assert "**Changed test to pass**, steps 23, 24:" in text
    assert "**Tested only defaults (judge's own:" in text
    assert "Absent: Never saw the bug;" in text
    assert "| Generate | 4 s | 12 | 0 | 20 |" in text
    assert "| Judge | 3 s | 20 | 0 | 20 |" in text
    assert "[workspace](judge/workspace-spec.json)" in text
    for target in re.findall(r"\[[^]]+\]\(([^)]+)\)", text):
        assert (tmp_path / target).is_file(), target


@pytest.mark.parametrize(
    "judgment,expected",
    [
        (
            {"status": "invalid_output", "error": "bad JSON"},
            "invalid_output: bad JSON",
        ),
        (
            {"status": "failed", "error": "provider failed"},
            "failed: provider failed",
        ),
        (
            {"status": "timed_out", "error": "deadline"},
            "timed_out: deadline",
        ),
        (
            {"status": "skipped", "reason": "generation failed"},
            "Skipped: generation failed",
        ),
        (
            {
                "status": "stale",
                "reason": "Evaluation changed.",
                "previous_judgment": completed_judgment(),
            },
            "Previous tests-issue answer: `yes`.",
        ),
    ],
)
def test_report_renders_noncompleted_judge_states(tmp_path, judgment, expected):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results()))
    paths.judge.judgment.write_text(json.dumps(judgment))

    text = report(paths).read_text()

    assert f"| Judge | — | {judgment['status']}" in text
    assert expected in text


def test_report_distinguishes_configured_missing_judgment(tmp_path):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results()))
    paths.judge.rubric.write_text("rubric")

    text = report(paths).read_text()

    assert "| Judge | — | missing |" in text
    assert "no judgment is saved" in text


def test_report_renders_a_run_without_judge_configuration(tmp_path):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results()))

    text = report(paths).read_text()

    assert "| Judge | — | disabled |" in text
    assert "| Cost | OpenRouter key spend, generation and judge | — |" in text


def test_report_derives_diagnostic_status_from_submission_compliance(tmp_path):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(
        json.dumps(
            report_results(
                submission_compliant=False,
                forbidden_changes=["src/application.py"],
            )
        )
    )

    text = report(paths).read_text()

    assert "diagnostic only: edits outside the generated directory" in text
    assert "- `src/application.py`" in text


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({}, "**completed**\n"),
        (
            {"agent": {"status": "timeout", "duration_seconds": 60}},
            "**completed with errors**: generation timeout\n",
        ),
        (
            {
                "agent": {"status": "timeout", "duration_seconds": 60},
                "buggy_status": "no_tests",
                "golden_status": "no_tests",
            },
            "**completed with errors**: generation timeout; no tests collected\n",
        ),
        (
            {"golden_status": "infrastructure_error"},
            "**completed with errors**: golden evaluation infrastructure_error\n",
        ),
    ],
)
def test_report_title_states_why_the_run_has_errors(tmp_path, changes, expected):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results(**changes)))

    text = report(paths).read_text()

    assert expected in text
    assert "## Pipeline" in text and "## Test outcomes" in text and "## Files" in text
    assert "Limits of this exploratory run" not in text


def test_report_names_files_that_failed_to_collect(tmp_path):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results()))
    error = {"nodeid": "oracle_tests/test_broken.py", "details": "ImportError"}
    write_json(
        paths.evaluation / "golden/tests.json",
        {"status": "completed", "collection_errors": [error]},
    )

    text = report(paths).read_text()

    assert "**completed**\n" in text
    assert "Files that failed to collect: golden 1." in text


def test_report_does_not_regenerate_the_generation_transcript(tmp_path):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results()))
    session = paths.generation / "session.log"
    session.write_text("frozen generation transcript\n")
    (paths.generation / "trace.jsonl").write_text('{"type": "newer-event"}\n')

    report(paths)

    assert session.read_text() == "frozen generation transcript\n"


def test_report_links_the_saved_target(tmp_path):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results()))
    write_json(paths.target, localized_target())

    assert "[target](inputs/target.json)" in report(paths).read_text()


def test_report_finds_the_classification_in_the_run_s_output_root(tmp_path):
    output = OutputPaths(tmp_path / "output")
    paths = RunPaths.create(output.runs / "run-1")
    paths.results.write_text(json.dumps(report_results()))
    instance = instance_record()
    write_result(paths.instance, instance)
    attempt = AttemptPaths(output.classifications / instance.instance_id / "20260101T000000Z-a")
    write_json(
        attempt.judge.classification,
        {
            "instance_id": instance.instance_id,
            "base_commit": instance.base_commit,
            "source_record_sha256": instance.source.record_sha256,
            "rubric_sha256": "e" * 64,
            "result": {"status": "completed", **out_of_scope_classification()},
        },
    )

    text = report(paths).read_text()

    assert "Task nature: `out_of_scope`." in text
    assert "classifications/owner__project-1/20260101T000000Z-a/judge/classification.json" in text


def test_report_renders_central_task_classification_and_provenance(tmp_path, monkeypatch):
    paths = RunPaths.create(tmp_path / "run")
    paths.results.write_text(json.dumps(report_results()))
    attempt = AttemptPaths.create(tmp_path / "classifications/test-instance/attempt")
    evidence = [
        attempt.judge.classification,
        attempt.judge.classification_raw,
        attempt.judge.rubric,
        attempt.judge.agent / "trace.jsonl",
    ]
    for path in evidence:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("evidence")
    write_json(
        attempt.judge.result,
        {
            "status": "completed",
            "duration_seconds": 2,
            "usage": usage(input_tokens=10),
            "errors": [],
            "harness": "codex",
            "provider": "openrouter",
            "model": "classifier-model",
            "harness_version": "0.153.4",
            "limit": {"kind": "wall_seconds", "value": 90},
        },
    )
    answers = {
        "task_nature": "behavioral_bug",
        "defect_mechanisms": ["control_logic", "data_state"],
        "primary_assertion_target": "return_value_or_status",
        "required_test_setup": "sequence",
        "oracle_kind": "derived",
        "oracle_source": "analogous_code",
        "required_test_scope": "component",
        "benchmark_quality": "usable",
    }
    classification = {
        "status": "completed",
        **{
            key: {
                "answer": answer,
                "justification": f"A nearby implementation establishes the {key} answer.",
            }
            for key, answer in answers.items()
        },
    }
    monkeypatch.setattr(
        "oracle_lab.report._classification", lambda run_paths: (classification, attempt)
    )

    text = report(paths).read_text()

    assert "## Task classification" in text
    assert "| Defect mechanisms | `control_logic, data_state` |" in text
    assert "| Oracle source | `analogous_code` |" in text
    assert "> A nearby implementation establishes the oracle_source answer." in text
    assert "Classifier: codex 0.153.4, classifier-model, openrouter, 90 s limit." in text
    assert f"[result]({attempt.judge.classification})" in text
    assert f"[rubric]({attempt.judge.rubric})" in text
    assert f"[trace]({attempt.judge.agent / 'trace.jsonl'})" in text


@pytest.mark.parametrize(
    ("classification", "expected"),
    [
        (
            {"status": "completed", **out_of_scope_classification()},
            "Task nature: `out_of_scope`.",
        ),
        (
            {"status": "invalid_artifact", "error": "latest attempt is incomplete"},
            "invalid_artifact: latest attempt is incomplete",
        ),
    ],
)
def test_report_renders_out_of_scope_and_unsuccessful_classifications(
    tmp_path, monkeypatch, classification, expected
):
    paths = RunPaths.create(tmp_path)
    paths.results.write_text(json.dumps(report_results()))
    monkeypatch.setattr(
        "oracle_lab.report._classification", lambda run_paths: (classification, None)
    )

    text = report(paths).read_text()

    assert expected in text
