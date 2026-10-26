"""Agreement between human raters, and how often they found the LLM judges right."""

import pytest
from fixtures import (
    added_failure_mode,
    classified_attempt,
    completed_judgment,
    judged_run,
    judgment_ratings,
)

from oracle_lab.contracts.review import AgreementReport
from oracle_lab.io import read_json, write_json
from oracle_lab.review.agreement import agreement, compute_agreement, krippendorff_alpha

RATINGS = ("agree", "disagree")


def test_alpha_matches_krippendorffs_worked_example():
    # Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability": four
    # observers, twelve units, values 1-5, with missing data. Ordinal alpha = .815.
    units = [
        [1, 1, 1], [2, 2, 3, 2], [3, 3, 3, 3], [3, 3, 3, 3], [2, 2, 2, 2], [1, 2, 3, 4],
        [4, 4, 4, 4], [1, 1, 2, 1], [2, 2, 2, 2], [5, 5, 5], [1, 1], [3],
    ]  # fmt: skip

    assert krippendorff_alpha(units, [1, 2, 3, 4, 5]) == pytest.approx(0.8154, abs=1e-4)


def test_perfect_agreement_gives_one():
    units = [["agree", "agree"], ["disagree", "disagree"], ["agree", "agree"]]

    assert krippendorff_alpha(units, RATINGS) == 1


def test_alpha_is_undefined_without_variation_or_pairs():
    assert krippendorff_alpha([["agree", "agree"], ["agree", "agree"]], RATINGS) is None
    assert krippendorff_alpha([["agree"], ["disagree"]], RATINGS) is None


def review(root, rater, ratings, other=()):
    write_json(
        root / rater / "review.json",
        {
            "ratings": ratings,
            "other_failure_modes": [
                {"name": name, "description": "Seen.", "steps": [1]} for name in other
            ],
        },
    )


@pytest.fixture
def data(tmp_path):
    """Two judged runs and a classification, reviewed by two raters, one of whom skipped one.

    Run A's judge added a failure mode of its own.
    """
    added = completed_judgment(added_failure_modes=[added_failure_mode()])
    run_a = judged_run(tmp_path / "runs/run-a", added).judge.reviews
    run_b = judged_run(tmp_path / "runs/run-b").judge.reviews
    judged_run(tmp_path / "runs/run-c", {"status": "failed", "error": "Crashed."})
    attempt = classified_attempt(tmp_path / "classifications/owner__project-1/attempt-1")
    review(
        run_a, "alice", judgment_ratings(added_failure_modes__0="agree"), other=["Gave up early"]
    )
    review(
        run_a,
        "bob",
        judgment_ratings(tests_issue="disagree", added_failure_modes__0="disagree"),
        other=["Gave up early"],
    )
    review(run_b, "alice", judgment_ratings(failure_modes__never_saw_bug="disagree"))
    review(run_b, "bob", judgment_ratings())
    review(attempt.judge.reviews, "alice", {"task_nature": "disagree"})
    return tmp_path


def test_each_question_reports_judge_quality_and_rater_agreement(data):
    report = compute_agreement([data])

    assert report.raters == ["alice", "bob"]
    judge = report.stages["judge"]
    assert (judge.targets, judge.reviews) == (2, 4)
    tests_issue = judge.questions["tests_issue"]
    assert tests_issue.label == "Do the generated tests attempt to test the issue?"
    assert tests_issue.ratings == {"agree": 3, "disagree": 1}
    assert tests_issue.agree_share == 0.75
    assert (tests_issue.rater_pairs, tests_issue.pairwise_agreement) == (2, 0.5)
    assert tests_issue.alpha is not None
    # Everyone agreed and nobody varied: raw agreement is perfect, alpha undefined.
    cheating = judge.questions["cheating"]
    assert (cheating.pairwise_agreement, cheating.alpha) == (1.0, None)
    assert "no_attempt_reason" not in judge.questions


def test_each_start_label_is_a_question_and_added_labels_are_pooled(data):
    report = compute_agreement([data])

    questions = report.stages["judge"].questions
    never = questions["failure_modes.never_saw_bug"]
    assert (never.label, never.ratings) == ("Never saw the bug", {"agree": 3, "disagree": 1})
    pooled = questions["added_failure_modes"]
    assert (pooled.label, pooled.targets, pooled.ratings) == (
        "Failure modes the judge added",
        1,
        {"agree": 1, "disagree": 1},
    )
    [added] = report.added_labels
    assert (added.name, added.ratings) == ("Tested only defaults", {"agree": 1, "disagree": 1})
    assert report.rater_labels == {"Gave up early": 2}


def test_one_rater_gives_judge_quality_but_no_agreement(data):
    task_nature = compute_agreement([data]).stages["classification"].questions["task_nature"]

    assert task_nature.agree_share == 0
    assert (task_nature.rater_pairs, task_nature.pairwise_agreement, task_nature.alpha) == (
        0,
        None,
        None,
    )


def test_missing_and_unreviewable_targets_are_listed(data):
    report = compute_agreement([data])

    attempt = (data / "classifications/owner__project-1/attempt-1").as_posix()
    assert report.missing == {attempt: ["bob"]}
    assert list(report.unreviewable) == [(data / "runs/run-c").as_posix()]


def test_agreement_is_saved_as_json_and_markdown(data, tmp_path):
    directory = agreement([data], tmp_path / "out")

    report = AgreementReport.model_validate(read_json(directory / "agreement.json"))
    assert report.stages["judge"].questions["tests_issue"].rater_pairs == 2
    text = (directory / "agreement.md").read_text()
    assert "## Generation judgments" in text
    assert "| Do the generated tests attempt to test the issue? | 2 | 3 | 1 | 75% |" in text
    assert "| Tested only defaults | 1 | 1 |" in text
    assert "| Gave up early | 2 |" in text
    assert "## Missing reviews" in text and "| bob |" in text


def test_a_set_without_reviews_says_so(tmp_path):
    judged_run(tmp_path / "runs/run-a")

    text = (agreement([tmp_path], tmp_path / "out") / "agreement.md").read_text()

    assert "1 target, 0 reviews." in text
    assert "| Question |" not in text
    assert "No reviews yet." in text
