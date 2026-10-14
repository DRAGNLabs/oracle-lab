from pathlib import Path

import pytest
import yaml
from fixtures import completed_judgment, evaluation_result, judge_attempt

from oracle_lab.batch import load_batch_config, run_batch
from oracle_lab.batch.summary import _aggregate_judgments
from oracle_lab.io import read_json, write_json
from oracle_lab.paths import RunPaths


def write_run_config(path: Path, instance: str) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "source": {"instance": instance},
                "agent": {"model": "test-model"},
                "task": {"prompt": "prompt.md"},
                "output": "runs",
            }
        )
    )


def write_batch_config(path: Path, jobs: list[str]) -> None:
    path.write_text(
        yaml.safe_dump(
            {
                "name": "test-batch",
                "jobs": [{"config": job} for job in jobs],
                "output": "batches",
            }
        )
    )


def fake_result(
    run_dir: Path,
    instance: str,
    detected: bool = False,
    *,
    compliant: bool = True,
    matrix_overrides: dict[str, int] | None = None,
) -> None:
    paths = RunPaths.create(run_dir)
    matrix = {
        "pass_on_both": {"count": 2, "test_ids": []},
        "fail_on_buggy_pass_on_golden": {
            "count": int(detected),
            "test_ids": ["test_detects_bug"] if detected else [],
        },
        "pass_on_buggy_fail_on_golden": {"count": 0, "test_ids": []},
        "fail_on_both": {"count": 0, "test_ids": []},
    }
    for key, count in (matrix_overrides or {}).items():
        matrix[key] = {"count": count, "test_ids": [f"test_{key}"] if count else []}
    write_json(
        paths.results,
        evaluation_result(
            instance_id=instance,
            submission_compliant=compliant,
            forbidden_changes=[] if compliant else ["package/core.py"],
            matrix=matrix,
        ),
    )
    write_json(run_dir / "status.json", {"state": "completed"})


def fake_judgment(
    run_dir: Path,
    *,
    status: str = "completed",
    detail: str = "correct_assertion",
    reason: str = "other_target_behavior",
    answer: str = "yes",
) -> None:
    paths = RunPaths.open(run_dir)
    paths.judge.root.mkdir(parents=True, exist_ok=True)
    paths.judge.rubric.write_text("rubric")
    if status == "completed":
        judgment = completed_judgment(
            tests_issue=answer,
            attempt_detail=detail if answer == "yes" else None,
            no_attempt_reason=reason if answer == "no" else None,
        )
    elif status == "stale":
        judgment = {
            "status": "stale",
            "reason": "Evaluation changed.",
            "previous_judgment": {
                "status": "failed",
                "error": "old failure",
            },
        }
    else:
        judgment = {"status": status, "error": f"{status} fixture"}
    write_json(paths.judge.judgment, judgment)
    write_json(paths.judge.result, judge_attempt(status=status))


def test_batch_paths_are_relative_and_all_jobs_validate(tmp_path):
    (tmp_path / "prompt.md").write_text("Generate tests")
    write_run_config(tmp_path / "one.yaml", "owner__one-1")
    write_run_config(tmp_path / "two.yaml", "owner__two-2")
    manifest = tmp_path / "batch.yaml"
    write_batch_config(manifest, ["one.yaml", "two.yaml"])

    config, paths = load_batch_config(manifest)

    assert config.output == str(tmp_path / "batches")
    assert paths == [tmp_path / "one.yaml", tmp_path / "two.yaml"]


def test_batch_continues_after_failure_and_aggregates_results(tmp_path):
    (tmp_path / "prompt.md").write_text("Generate tests")
    write_run_config(tmp_path / "one.yaml", "owner__one-1")
    write_run_config(tmp_path / "two.yaml", "owner__two-2")
    manifest = tmp_path / "batch.yaml"
    write_batch_config(manifest, ["one.yaml", "two.yaml"])
    calls = []

    def run_one(config):
        calls.append(config.source.instance)
        if config.source.instance == "owner__one-1":
            raise RuntimeError("setup failed")
        run_dir = tmp_path / "runs" / config.source.instance
        fake_result(run_dir, config.source.instance, detected=True)
        return run_dir

    batch_dir = run_batch(manifest, run_one=run_one)
    summary = read_json(batch_dir / "summary.json")

    assert calls == ["owner__one-1", "owner__two-2"]
    assert summary["attempted"] == 2
    assert summary["completed_with_results"] == 1
    assert summary["detected"] == 1
    assert summary["completed_evaluations"] == 1
    assert summary["matrix_detection_rate"] == 1.0
    assert "detection_rate_attempted" not in summary
    assert read_json(batch_dir / "status.json")["state"] == "completed_with_errors"
    report = (batch_dir / "report.md").read_text()
    assert "| Instance | Run ID | State |" in report
    assert "| `owner__two-2` | completed |" in report
    assert "| — | failed |" in report


def test_a_non_compliant_run_stays_in_the_denominator_but_is_never_detected(tmp_path):
    (tmp_path / "prompt.md").write_text("Generate tests")
    write_run_config(tmp_path / "one.yaml", "owner__one-1")
    write_run_config(tmp_path / "two.yaml", "owner__two-2")
    manifest = tmp_path / "batch.yaml"
    write_batch_config(manifest, ["one.yaml", "two.yaml"])

    def run_one(config):
        run_dir = tmp_path / "runs" / config.source.instance
        # Both runs have a fail-on-buggy, pass-on-golden test; only one is compliant.
        compliant = config.source.instance == "owner__one-1"
        fake_result(run_dir, config.source.instance, detected=True, compliant=compliant)
        return run_dir

    summary = read_json(run_batch(manifest, run_one=run_one) / "summary.json")

    assert summary["completed_evaluations"] == 2
    assert summary["detected"] == 1
    assert summary["matrix_detection_rate"] == 0.5
    detected = {job["instance_id"]: job["detected"] for job in summary["jobs"]}
    assert detected == {"owner__one-1": True, "owner__two-2": False}


def test_resume_skips_completed_jobs_and_retries_failed_job(tmp_path):
    (tmp_path / "prompt.md").write_text("Generate tests")
    write_run_config(tmp_path / "one.yaml", "owner__one-1")
    write_run_config(tmp_path / "two.yaml", "owner__two-2")
    manifest = tmp_path / "batch.yaml"
    write_batch_config(manifest, ["one.yaml", "two.yaml"])

    def first_run(config):
        if config.source.instance == "owner__two-2":
            raise RuntimeError("temporary failure")
        run_dir = tmp_path / "runs" / config.source.instance
        fake_result(run_dir, config.source.instance)
        return run_dir

    batch_dir = run_batch(manifest, run_one=first_run)
    resumed = []

    def resumed_run(config):
        resumed.append(config.source.instance)
        run_dir = tmp_path / "runs" / config.source.instance
        fake_result(run_dir, config.source.instance, detected=True)
        return run_dir

    assert run_batch(batch_dir, resume=True, run_one=resumed_run) == batch_dir
    assert resumed == ["owner__two-2"]
    assert read_json(batch_dir / "status.json")["state"] == "completed"


def test_batch_aggregates_judge_labels_disagreements_and_costs(tmp_path):
    (tmp_path / "prompt.md").write_text("Generate tests")
    instances = ["unrelated-detected", "relevant-fail-both", "invalid", "stale", "disabled"]
    for instance in instances:
        write_run_config(tmp_path / f"{instance}.yaml", instance)
    manifest = tmp_path / "batch.yaml"
    write_batch_config(manifest, [f"{instance}.yaml" for instance in instances])

    costs = {"unrelated-detected": 0.3, "relevant-fail-both": 0.2, "disabled": 0.05}

    def run_one(config):
        instance = config.source.instance
        run_dir = tmp_path / "runs" / instance
        if instance == "unrelated-detected":
            fake_result(run_dir, instance, detected=True)
            fake_judgment(
                run_dir,
                reason="unrelated_behavior",
                answer="no",
            )
        elif instance == "relevant-fail-both":
            fake_result(run_dir, instance, matrix_overrides={"fail_on_both": 1})
            fake_judgment(
                run_dir,
                detail="no_issue_assertion",
                answer="yes",
            )
        elif instance == "invalid":
            fake_result(run_dir, instance)
            fake_judgment(run_dir, status="invalid_output")
            write_json(run_dir / "cost.json", {"status": "unavailable", "error": "HTTP 503"})
        elif instance == "stale":
            fake_result(run_dir, instance)
            fake_judgment(run_dir, status="stale")
        else:
            fake_result(run_dir, instance)
        if instance in costs:
            write_json(run_dir / "cost.json", {"status": "measured", "cost_usd": costs[instance]})
        return run_dir

    batch_dir = run_batch(manifest, run_one=run_one)
    summary = read_json(batch_dir / "summary.json")
    judge = summary["judge"]

    assert summary["completed_evaluations"] == 5
    assert summary["matrix_detection_rate"] == 0.2
    assert summary["judge_tests_issue_yes_rate"] == 0.5
    assert summary["total_cost_usd"] == pytest.approx(0.55)
    assert summary["costed_runs"] == 3
    assert [job["cost_status"] for job in summary["jobs"]] == [
        "measured",
        "measured",
        "unavailable",
        None,
        "measured",
    ]
    assert judge["valid_judgments"] == 2
    assert judge["status_counts"] == {
        "completed": 2,
        "disabled": 1,
        "invalid_output": 1,
        "stale": 1,
    }
    assert judge["matrix_detection_by_tests_issue"]["detected"] == {"no": 1}
    assert judge["attempt_detail_by_matrix_cell_presence"]["fail_on_both"]["present"] == {
        "no_issue_assertion": 1
    }
    assert judge["label_counts"]["no_attempt_reason"] == {"null": 1, "unrelated_behavior": 1}
    assert summary["jobs"][0]["judge"]["model"] == "judge-model"
    assert summary["jobs"][0]["judge"]["usage"]["input_tokens"] == 100

    report = (batch_dir / "report.md").read_text()
    assert "Judge says tests attempt the issue" in report


def test_failed_judgment_does_not_make_completed_batch_job_resumable(tmp_path):
    (tmp_path / "prompt.md").write_text("Generate tests")
    write_run_config(tmp_path / "one.yaml", "owner__one-1")
    manifest = tmp_path / "batch.yaml"
    write_batch_config(manifest, ["one.yaml"])

    def first_run(config):
        run_dir = tmp_path / "runs" / config.source.instance
        fake_result(run_dir, config.source.instance)
        fake_judgment(run_dir, status="failed")
        return run_dir

    batch_dir = run_batch(manifest, run_one=first_run)
    rerun = []
    run_batch(batch_dir, resume=True, run_one=lambda config: rerun.append(config))

    assert rerun == []
    assert read_json(batch_dir / "summary.json")["judge"]["status_counts"] == {"failed": 1}


def test_every_judge_label_is_counted_independently():
    labels = {
        "no_attempt_reason": [
            "missed_trigger",
            "missed_outcome",
            "other_target_behavior",
            "unrelated_behavior",
            "no_real_tests",
            None,
        ],
        "tests_issue": ["yes", "no", "unsure"],
        "attempt_detail": [
            "correct_assertion",
            "wrong_assertion",
            "no_issue_assertion",
            "missing_required_condition",
            "test_invalid_or_incomplete",
            None,
        ],
        "cheating": [
            "no",
            "external_repository_lookup",
            "hidden_evidence_access",
            "other_circumvention",
        ],
    }
    jobs = []
    for index in range(max(map(len, labels.values()))):
        judge = {
            "status": "completed",
            **{facet: values[index % len(values)] for facet, values in labels.items()},
        }
        jobs.append({"detected": index % 2 == 0, "matrix": {}, "judge": judge})

    aggregation = _aggregate_judgments(jobs)

    for facet, values in labels.items():
        assert set(aggregation["label_counts"][facet]) == {
            value if value is not None else "null" for value in values
        }


def test_a_failing_summary_does_not_hide_an_interrupted_job(tmp_path, monkeypatch):
    (tmp_path / "prompt.md").write_text("Generate tests")
    write_run_config(tmp_path / "one.yaml", "owner__one-1")
    manifest = tmp_path / "batch.yaml"
    write_batch_config(manifest, ["one.yaml"])

    def broken_summary(*args):
        raise ValueError("saved run does not validate")

    def interrupted(config):
        raise KeyboardInterrupt

    monkeypatch.setattr("oracle_lab.batch.write_summary", broken_summary)

    with pytest.raises(KeyboardInterrupt) as error:
        run_batch(manifest, run_one=interrupted)

    assert error.value.__notes__ == ["The batch summary could not be updated."]
    (batch_dir,) = (tmp_path / "batches").iterdir()
    assert read_json(batch_dir / "status.json") == {"state": "interrupted"}
