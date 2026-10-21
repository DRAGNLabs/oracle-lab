"""A review target is one completed LLM output and the evidence its judge was shown."""

import pytest
from fixtures import (
    classified_attempt,
    completed_judgment,
    instance_record,
    judged_run,
    out_of_scope_classification,
)

from oracle_lab.paths import RunPaths
from oracle_lab.review.targets import (
    ClassificationTarget,
    JudgmentTarget,
    TargetError,
    find_problems,
    find_targets,
)

FAILED = {"status": "failed", "error": "The harness crashed."}


def test_a_judgment_is_reviewed_on_the_questions_it_answered(tmp_path):
    target = JudgmentTarget.load(judged_run(tmp_path).root)

    assert target.reviews == tmp_path / "judge/reviews"
    assert target.review_history == tmp_path / "judge/reviews-history"
    assert target.answered_questions() == ["tests_issue", "attempt_detail", "cheating"]


def test_a_null_judge_answer_is_not_reviewed(tmp_path):
    judgment = completed_judgment(tests_issue="unsure", attempt_detail=None)
    target = JudgmentTarget.load(judged_run(tmp_path, judgment).root)

    assert target.answered_questions() == ["tests_issue", "cheating"]


def test_an_in_scope_classification_is_reviewed_on_all_seven_questions(tmp_path):
    target = ClassificationTarget.load(classified_attempt(tmp_path))

    assert target.reviews == tmp_path / "reviews"
    assert len(target.answered_questions()) == 7


def test_an_out_of_scope_classification_is_reviewed_on_one_question(tmp_path):
    result = {"status": "completed", **out_of_scope_classification()}
    target = ClassificationTarget.load(classified_attempt(tmp_path, result))

    assert target.answered_questions() == ["task_nature"]


def test_judge_evidence_is_what_the_judge_was_shown_without_the_views(tmp_path):
    evidence = JudgmentTarget.load(judged_run(tmp_path).root).evidence()

    assert evidence["evidence/agent-session.log"] == b"agent session\n"
    assert "instance/reference-tests.patch" in evidence
    assert "instructions.md" in evidence
    assert not any(name.startswith(("buggy/", "golden/")) for name in evidence)


def test_classification_evidence_is_the_record_and_the_rubric(tmp_path):
    evidence = ClassificationTarget.load(classified_attempt(tmp_path)).evidence()

    assert list(evidence) == ["instance.json", "rubric.md"]


def test_each_target_reads_the_issue_its_judge_read(tmp_path):
    judgment = JudgmentTarget.load(judged_run(tmp_path / "run").root)
    classification = ClassificationTarget.load(classified_attempt(tmp_path / "attempt"))

    assert judgment.issue() == "# Issue\n\nIncorrect result.\n"
    assert classification.issue() == "Broken behavior."


def test_a_judgment_adds_its_submission_to_the_views(tmp_path):
    judgment = JudgmentTarget.load(judged_run(tmp_path / "run").root)
    classification = ClassificationTarget.load(classified_attempt(tmp_path / "attempt"))

    (generated,) = judgment.submitted_files().values()
    assert generated.content == b"def test_generated(): pass\n"
    assert classification.submitted_files() == {}


def test_missing_evidence_is_refused(tmp_path):
    paths = judged_run(tmp_path)
    target = JudgmentTarget.load(paths.root)
    (paths.evaluation / "buggy/output.log").unlink()

    with pytest.raises(TargetError, match="missing"):
        target.evidence()


@pytest.mark.parametrize(
    "judgment, reason",
    [
        (FAILED, "failed, not completed"),
        ({"status": "stale", "reason": "Rerun.", "previous_judgment": FAILED}, "stale"),
        ({"status": "skipped", "reason": "No tests."}, "skipped"),
    ],
)
def test_a_judgment_that_is_not_completed_is_refused(tmp_path, judgment, reason):
    with pytest.raises(TargetError, match=reason):
        JudgmentTarget.load(judged_run(tmp_path, judgment).root)


def test_a_failed_classification_is_refused(tmp_path):
    with pytest.raises(TargetError, match="failed, not completed"):
        ClassificationTarget.load(classified_attempt(tmp_path, FAILED))


def test_find_targets_lists_a_data_repository(tmp_path):
    judged_run(tmp_path / "runs/run-a")
    judged_run(tmp_path / "runs/run-b", FAILED)
    classified_attempt(tmp_path / "classifications/owner__project-1/attempt-1")
    classified_attempt(tmp_path / "classifications/owner__project-1/attempt-2", FAILED)

    targets, unreviewable = find_targets(tmp_path)

    assert [(type(t), t.root.name) for t in targets] == [
        (JudgmentTarget, "run-a"),
        (ClassificationTarget, "attempt-1"),
    ]
    assert [(item.stage, item.root.name) for item in unreviewable] == [
        ("judge", "run-b"),
        ("classification", "attempt-2"),
    ]
    assert all("failed, not completed" in item.reason for item in unreviewable)


def test_problems_group_targets_by_instance_with_classifications_first(tmp_path):
    other = instance_record(instance_id="other__project-2")
    judged_run(tmp_path / "runs/run-b")
    judged_run(tmp_path / "runs/run-a")
    judged_run(tmp_path / "runs/run-c", instance=other)
    classified_attempt(tmp_path / "classifications/owner__project-1/attempt-1")

    problems = find_problems(find_targets(tmp_path)[0])

    assert [problem.instance_id for problem in problems] == ["other__project-2", "owner__project-1"]
    assert [target.root.name for target in problems[1].tasks] == ["attempt-1", "run-a", "run-b"]
    assert problems[1].issue() == "Broken behavior."
    assert JudgmentTarget.load(tmp_path / "runs/run-a").config().agent.harness == "claude"


def test_find_targets_accepts_one_run_or_one_attempt(tmp_path):
    run = judged_run(tmp_path / "run").root
    attempt = classified_attempt(tmp_path / "attempt")

    assert [type(t) for t in find_targets(run)[0]] == [JudgmentTarget]
    assert [type(t) for t in find_targets(attempt)[0]] == [ClassificationTarget]


def test_a_run_without_a_judgment_is_unreviewable_with_its_reason(tmp_path):
    paths = judged_run(tmp_path / "runs/run-a")
    paths.judge.judgment.unlink()

    targets, unreviewable = find_targets(tmp_path)

    assert targets == []
    assert unreviewable[0].reason == "The run has no judgment"


def test_a_run_with_an_invalid_config_is_unreviewable(tmp_path):
    paths = judged_run(tmp_path / "runs/run-a")
    paths.config.write_text("source: {}\n")

    _, [item] = find_targets(tmp_path)

    assert "validation error" in item.reason


def test_find_targets_refuses_a_directory_without_targets(tmp_path):
    with pytest.raises(TargetError, match="No runs or classification attempts"):
        find_targets(tmp_path)


def test_run_paths_name_the_reviews_directory(tmp_path):
    assert RunPaths.open(tmp_path).judge.reviews == tmp_path / "judge/reviews"
