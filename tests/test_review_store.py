"""A review is saved beside the output it rates, and only its own rater reads it back."""

import json

import pytest
from fixtures import classified_attempt, judged_run

from oracle_lab.io import digest, read_json, write_json
from oracle_lab.review.store import read_review, save_review
from oracle_lab.review.targets import classification_target, judge_target

RATINGS = {"tests_issue": "correct", "attempt_detail": "defensible", "cheating": "correct"}


def submitted(**changes) -> str:
    return json.dumps({"ratings": {**RATINGS, **changes}})


def test_a_review_is_saved_with_its_provenance(tmp_path):
    target = judge_target(judged_run(tmp_path))

    save_review(target, "rater-1", submitted())

    directory = tmp_path / "judge/reviews/rater-1"
    assert read_json(directory / "review.json") == {"ratings": RATINGS}
    assert read_json(directory / "provenance.json") == {
        "stage": "judge",
        "rater": "rater-1",
        "reviewed_output": "judge/judgment.json",
        "reviewed_output_sha256": digest((tmp_path / "judge/judgment.json").read_bytes()),
        "judge_harness": "codex",
        "judge_model": "judge-model",
        "stage_rubric_sha256": digest(b"# Judge rubric\n"),
        "workspace_spec_sha256": digest((tmp_path / "judge/workspace-spec.json").read_bytes()),
    }


def test_a_classification_review_names_the_classifier(tmp_path):
    target = classification_target(classified_attempt(tmp_path))
    ratings = {key: "correct" for key in target.answered_questions()}

    save_review(target, "rater-1", json.dumps({"ratings": ratings}))

    provenance = read_json(tmp_path / "reviews/rater-1/provenance.json")
    assert provenance["stage"] == "classification"
    assert provenance["reviewed_output"] == "classification.json"
    assert provenance["judge_model"] == "classifier-model"


def test_saving_again_moves_the_earlier_review_to_history(tmp_path):
    target = judge_target(judged_run(tmp_path))
    save_review(target, "rater-1", submitted())

    save_review(target, "rater-1", submitted(cheating="incorrect"))

    assert read_review(target, "rater-1").ratings["cheating"] == "incorrect"
    (earlier,) = (tmp_path / "judge/reviews-history/rater-1").iterdir()
    assert read_json(earlier / "review.json") == {"ratings": RATINGS}
    assert (earlier / "provenance.json").is_file()


def test_an_incomplete_review_is_refused_and_nothing_is_written(tmp_path):
    target = judge_target(judged_run(tmp_path))
    partial = json.dumps({"ratings": {"tests_issue": "correct"}})

    with pytest.raises(ValueError, match="answered questions"):
        save_review(target, "rater-1", partial)

    assert not (tmp_path / "judge/reviews").exists()


def test_a_rater_reads_only_their_own_review(tmp_path):
    target = judge_target(judged_run(tmp_path))
    write_json(tmp_path / "judge/reviews/other/review.json", {"ratings": RATINGS})

    assert read_review(target, "rater-1") is None
    assert read_review(target, "other").ratings == RATINGS


@pytest.mark.parametrize("rater", ["", "../other", "a/b", "-dash", "x" * 65])
def test_a_rater_id_cannot_name_another_directory(tmp_path, rater):
    target = judge_target(judged_run(tmp_path))

    with pytest.raises(ValueError, match="Rater IDs"):
        save_review(target, rater, submitted())
