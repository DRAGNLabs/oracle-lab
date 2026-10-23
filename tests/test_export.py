"""An output root becomes a dataset: flat tables beside the raw files, then the Hub."""

import json
from pathlib import Path

import pyarrow.parquet as pq
import pytest
import yaml
from fixtures import (
    TRACE_TEST_ID,
    added_failure_mode,
    classified_attempt,
    completed_judgment,
    failure_modes,
    judged_run,
    judgment_ratings,
    out_of_scope_classification,
    run_config,
    traced_run,
)

from oracle_lab.cli import main
from oracle_lab.export.contracts import TABLES
from oracle_lab.export.hub import upload_dataset
from oracle_lab.export.tables import collect
from oracle_lab.export.writer import export_dataset, schema
from oracle_lab.io import read_json, write_json
from oracle_lab.judge.contracts import FAILURE_MODES
from oracle_lab.paths import OutputPaths, RunPaths
from oracle_lab.review.store import save_review
from oracle_lab.review.targets import ClassificationTarget, JudgmentTarget

RUN_ID = "20260101T000000Z-aaaaaaaa"
ATTEMPT = "owner__project-1/20260101T000000Z-bbbbbbbb"
FINISHED = {"stage": "finished", "state": "completed", "updated_at": "2026-01-01T00:00:00+00:00"}


def finished(paths: RunPaths) -> RunPaths:
    write_json(paths.status, FINISHED)
    return paths


def reviewed_run(root: Path) -> RunPaths:
    """A finished, traced run whose judgment found one start-list mode and added one,
    reviewed by one rater who added a mode of their own."""
    paths = finished(traced_run(OutputPaths(root).runs / RUN_ID))
    judgment = completed_judgment(
        failure_modes=failure_modes(never_saw_bug=[1]), added_failure_modes=[added_failure_mode()]
    )
    write_json(paths.judge.judgment, judgment)
    ratings = judgment_ratings(added_failure_modes__0="disagree")
    other = {"name": "Probed only", "description": "It probed and stopped.", "steps": [2]}
    review = {"ratings": ratings, "other_failure_modes": [other]}
    save_review(JudgmentTarget.load(paths.root), "alice", json.dumps(review))
    return paths


def rows(collected, table: str) -> list[dict]:
    return [row.model_dump() for row in collected.tables[table]]


def test_a_reviewed_run_fills_every_run_table(tmp_path):
    reviewed_run(tmp_path)

    collected = collect(tmp_path)

    (run,) = rows(collected, "runs")
    assert (run["run_id"], run["instance_id"], run["state"]) == (
        RUN_ID,
        "owner__project-1",
        "completed",
    )
    assert run["judge_status"] == "completed"
    assert run["test_files"] == 1
    (test,) = rows(collected, "tests")
    assert (test["test_id"], test["cell"]) == (TRACE_TEST_ID, "pass_on_buggy_fail_on_golden")
    (judgment,) = rows(collected, "judgments")
    assert (judgment["tests_issue"], judgment["judge_model"]) == ("yes", "judge-model")
    assert judgment["no_attempt_reason"] is None
    assert judgment["no_attempt_reason_justification"] is None

    modes = {row["item"]: row for row in rows(collected, "failure_modes")}
    assert len(modes) == len(FAILURE_MODES) + 1
    assert modes["failure_modes.never_saw_bug"]["present"] is True
    assert modes["failure_modes.never_saw_bug"]["steps"] == [1]
    assert modes["failure_modes.flagged_bug"]["present"] is False
    assert modes["added_failure_modes.0"]["source"] == "judge_added"

    reviews = {row["item"]: row for row in rows(collected, "reviews")}
    assert set(reviews) <= set(modes) | {"tests_issue", "attempt_detail", "cheating"}
    assert reviews["added_failure_modes.0"]["rating"] == "disagree"
    assert reviews["tests_issue"]["target_id"] == RUN_ID
    (added,) = rows(collected, "rater_failure_modes")
    assert (added["rater"], added["name"], added["steps"]) == ("alice", "Probed only", [2])

    steps = rows(collected, "trace_steps")
    assert steps and all(row["run_id"] == RUN_ID for row in steps)
    assert {row["action"] for row in steps} >= {"run_tests"}


def test_a_run_that_stopped_early_keeps_its_unwritten_columns_null(tmp_path):
    paths = RunPaths.create(OutputPaths(tmp_path).runs / RUN_ID)
    paths.config.write_text(yaml.safe_dump(run_config().model_dump(mode="json")))
    write_json(paths.status, {"stage": "build", "state": "failed", "error": "no image"})

    collected = collect(tmp_path)

    (run,) = rows(collected, "runs")
    assert (run["stage"], run["state"], run["error"]) == ("build", "failed", "no image")
    assert run["instance_id"] == run_config().source.instance
    assert run["generation_status"] is None and run["detected"] is None
    assert run["judge_status"] == "disabled"
    assert not any(collected.tables[name] for name in TABLES if name != "runs")


def test_an_unreadable_run_is_skipped_and_named_not_half_exported(tmp_path):
    paths = finished(judged_run(OutputPaths(tmp_path).runs / RUN_ID))
    write_json(paths.judge.judgment, {"status": "completed"})

    collected = collect(tmp_path)

    assert not any(collected.tables.values())
    (skipped,) = collected.skipped
    assert skipped.path == f"runs/{RUN_ID}"
    assert "tests_issue" in skipped.error


def test_classification_attempts_export_their_answer_and_reviews(tmp_path):
    classifications = OutputPaths(tmp_path).classifications
    attempt = classified_attempt(classifications / ATTEMPT)
    target = ClassificationTarget.load(attempt.root)
    ratings = {key: "agree" for key in target.rating_keys()}
    save_review(target, "bob", json.dumps({"ratings": ratings}))
    out_of_scope = {"status": "completed", **out_of_scope_classification()}
    classified_attempt(classifications / "owner__project-1/20260102T000000Z-cccccccc", out_of_scope)
    (classifications / "owner__project-1/20260103T000000Z-dddddddd").mkdir()

    collected = collect(tmp_path)

    in_scope, narrow = rows(collected, "classifications")
    assert in_scope["defect_mechanisms"] == ["validation_checking"]
    assert in_scope["classifier_model"] == "classifier-model"
    assert (narrow["task_nature"], narrow["defect_mechanisms"]) == ("out_of_scope", None)
    assert narrow["task_nature_justification"] == "This task adds a new feature."
    assert {row["rater"] for row in rows(collected, "reviews")} == {"bob"}
    assert len(rows(collected, "reviews")) == len(ratings)
    (skipped,) = collected.skipped
    assert skipped.error == "The attempt has no classification"


def test_an_empty_output_root_is_refused(tmp_path):
    with pytest.raises(ValueError, match="No runs or classification attempts"):
        collect(tmp_path)


def test_the_dataset_directory_holds_tables_raw_files_manifest_and_card(tmp_path):
    root = tmp_path / "output"
    reviewed_run(root)
    out = tmp_path / "dataset"

    manifest = export_dataset(root, out)

    assert manifest.runs == [RUN_ID]
    assert manifest.rows["classifications"] == 0
    for name, model in TABLES.items():
        table = pq.read_table(out / "data" / f"{name}.parquet")
        assert table.schema.equals(schema(model))
        assert table.num_rows == manifest.rows[name]
    assert read_json(out / "export-manifest.json") == manifest.model_dump(mode="json")
    assert (out / "raw/runs" / RUN_ID / "judge/reviews/alice/review.json").is_file()
    card = (out / "README.md").read_text()
    _, header, body = card.split("---\n", 2)
    assert yaml.safe_load(header)["configs"] == [
        {"config_name": name, "data_files": f"data/{name}.parquet"} for name in TABLES
    ]
    assert "- `reviews` (" in body

    with pytest.raises(ValueError, match="new or empty"):
        export_dataset(root, out)


class FakeHub:
    """Records what an upload asks of the Hub, holding `remote` files before it."""

    def __init__(self, remote: list[str]):
        self.remote = remote
        self.calls = []

    def create_repo(self, repo, **options):
        self.calls.append(("create_repo", repo, options))

    def upload_large_folder(self, **options):
        self.calls.append(("upload", options["repo_id"], options["folder_path"]))

    def list_repo_files(self, repo, **options):
        return self.remote

    def create_commit(self, repo, operations, **options):
        self.calls.append(("delete", repo, sorted(op.path_in_repo for op in operations)))


def test_an_upload_makes_a_private_repository_hold_exactly_the_export(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "token-fixture")
    (tmp_path / "data").mkdir()
    (tmp_path / "data/runs.parquet").write_bytes(b"")
    (tmp_path / "README.md").write_text("card")
    hub = FakeHub([".gitattributes", "README.md", "data/runs.parquet", "data/old.parquet"])

    url = upload_dataset(tmp_path, "lab/results", api=hub)

    assert url == "https://huggingface.co/datasets/lab/results"
    assert hub.calls == [
        ("create_repo", "lab/results", {"repo_type": "dataset", "private": True, "exist_ok": True}),
        ("upload", "lab/results", tmp_path),
        ("delete", "lab/results", ["data/old.parquet"]),
    ]


def test_an_upload_without_a_token_fails_before_any_work(tmp_path, monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env here
    reviewed_run(tmp_path / "output")

    assert main(["export", "output", "--repo", "lab/results", "--out", "dataset"]) == 1
    assert not (tmp_path / "dataset").exists()


def test_export_needs_a_destination(tmp_path, capsys):
    assert main(["export", str(tmp_path)]) == 1
    assert "--out, --repo, or both" in capsys.readouterr().err
