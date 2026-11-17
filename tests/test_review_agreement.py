"""Agreement between human raters, and how often they found the LLM judges right."""

import pytest
from fixtures import (
    added_failure_mode,
    classified_attempt,
    completed_judgment,
    judged_run,
    judgment_ratings,
    review_of,
    run_config,
)

from oracle_lab.contracts.review import AgreementReport
from oracle_lab.io import read_json, write_json
from oracle_lab.review.agreement import (
    agreement,
    clopper_pearson,
    compute_agreement,
    krippendorff_alpha,
    verdict,
)

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


def test_a_strict_majority_of_raters_decides_an_item():
    assert verdict(["agree", "agree", "agree", "disagree"]) == "correct"
    assert verdict(["agree", "disagree", "disagree", "disagree"]) == "wrong"
    assert verdict(["agree", "agree", "disagree", "disagree"]) == "disputed"
    assert verdict(["agree", "agree", "disagree"]) == "correct"
    assert verdict(["disagree"]) == "wrong"


def test_interval_matches_the_exact_binomial_bounds():
    # 30 of 30: the lower bound solves p**30 = 0.025.
    assert clopper_pearson(30, 30) == pytest.approx((0.025 ** (1 / 30), 1.0))
    # Published Clopper-Pearson values for 5 of 10 and 0 of 10.
    assert clopper_pearson(5, 10) == pytest.approx((0.1871, 0.8129), abs=1e-4)
    assert clopper_pearson(0, 10) == pytest.approx((0.0, 0.3085), abs=1e-4)


def review(root, rater, ratings):
    write_json(root / rater / "review.json", review_of(ratings))


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
    review(run_a, "alice", judgment_ratings(added_failure_modes__0="agree"))
    review(
        run_a, "bob", judgment_ratings(tests_issue="disagree", added_failure_modes__0="disagree")
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


def test_majority_vote_decides_each_item_and_leaves_disputes_out_of_accuracy(data):
    tests_issue = compute_agreement([data]).stages["judge"].questions["tests_issue"]

    # Run A split one to one, run B agreed.
    vote = tests_issue.majority
    assert (vote.correct, vote.wrong, vote.disputed, vote.accuracy) == (1, 0, 1, 1.0)
    assert (vote.interval_low, vote.interval_high) == (pytest.approx(0.025), 1.0)


@pytest.fixture
def harnesses(tmp_path):
    """Three runs, two Claude and one Codex, each rated by three raters."""
    codex = run_config()
    codex.agent = codex.agent.model_copy(update={"harness": "codex"})
    runs = {
        "claude-1": judged_run(tmp_path / "runs/claude-1").judge.reviews,
        "claude-2": judged_run(tmp_path / "runs/claude-2").judge.reviews,
        "codex-1": judged_run(tmp_path / "runs/codex-1", config=codex).judge.reviews,
    }
    for rater in ("alice", "bob", "carol"):
        review(runs["claude-1"], rater, judgment_ratings())
        # Most raters think the judge missed this failure mode on claude-2.
        wrong = "disagree" if rater != "carol" else "agree"
        review(runs["claude-2"], rater, judgment_ratings(failure_modes__never_saw_bug=wrong))
        review(runs["codex-1"], rater, judgment_ratings())
    return tmp_path


def test_whole_items_are_wrong_when_any_question_is(harnesses):
    judge = compute_agreement([harnesses]).stages["judge"]

    assert (judge.items.correct, judge.items.wrong, judge.items.disputed) == (2, 1, 0)
    assert judge.questions["tests_issue"].majority.accuracy == 1.0
    never = judge.questions["failure_modes.never_saw_bug"].majority
    assert (never.correct, never.wrong) == (2, 1)


def test_accuracy_is_broken_down_by_harness(harnesses):
    by_harness = compute_agreement([harnesses]).stages["judge"].by_harness

    assert list(by_harness) == ["claude", "codex"]
    claude = by_harness["claude"]
    assert (claude.items.correct, claude.items.wrong) == (1, 1)
    never = claude.questions["failure_modes.never_saw_bug"]
    assert (never.correct, never.wrong, never.accuracy) == (1, 1, 0.5)
    assert by_harness["codex"].items.accuracy == 1.0
    text = (agreement([harnesses], harnesses / "out") / "agreement.md").read_text()
    assert "| Question | claude | codex |" in text
    assert "| Never saw the bug | 1/2 | 1/1 |" in text
    assert "| **Whole item** | 1/2 | 1/1 |" in text


def test_classifications_have_no_harness_breakdown(data):
    assert compute_agreement([data]).stages["classification"].by_harness == {}


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
    assert (
        "| Do the generated tests attempt to test the issue? | 2 | 1 | 0 | 1 | 100.0% "
        "| 2.5%–100.0% | 75.0% |"
    ) in text
    assert "| Do the generated tests attempt to test the issue? | 2 | 50.0% |" in text
    assert "| **Whole item** | 2 | 0 | 0 | 2 | — | — | |" in text
    assert "| Tested only defaults | 1 | 1 |" in text
    assert "## Missing reviews" in text and "| bob |" in text


def test_a_set_without_reviews_says_so(tmp_path):
    judged_run(tmp_path / "runs/run-a")

    text = (agreement([tmp_path], tmp_path / "out") / "agreement.md").read_text()

    assert "1 target, 0 reviews." in text
    assert "| Question |" not in text
    assert "No reviews yet." in text
