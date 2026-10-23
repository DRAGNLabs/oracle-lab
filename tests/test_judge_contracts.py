import json

import pytest
from fixtures import added_failure_mode, failure_modes
from fixtures import judgment_labels as labels

from oracle_lab.io import read_json, write_json
from oracle_lab.judge.contracts import (
    CompletedJudgment,
    SkippedJudgment,
    mark_judgments_stale,
    parse_judgment,
)
from oracle_lab.paths import RunPaths
from oracle_lab.results import MAX_JUSTIFICATION_LENGTH, failed_turn


def justified(**texts):
    """Labels for the default yes answer, with the given questions' justifications."""
    default = labels()
    return labels(**{key: {**default[key], "justification": text} for key, text in texts.items()})


def answer(field):
    return field.answer if field else None


@pytest.mark.parametrize(
    "detail",
    [
        "correct_assertion",
        "wrong_assertion",
        "no_issue_assertion",
        "missing_required_condition",
        "test_invalid_or_incomplete",
    ],
)
def test_parser_accepts_every_attempt_detail(detail):
    result = parse_judgment(json.dumps(labels(attempt_detail=detail)))

    assert isinstance(result, CompletedJudgment)
    assert answer(result.attempt_detail) == detail


@pytest.mark.parametrize(
    "cheating",
    [
        "no",
        "external_repository_lookup",
        "hidden_evidence_access",
        "other_circumvention",
    ],
)
def test_parser_accepts_every_cheating_label(cheating):
    result = parse_judgment(json.dumps(labels(cheating=cheating)))

    assert isinstance(result, CompletedJudgment)
    assert answer(result.cheating) == cheating


@pytest.mark.parametrize(
    "issue,detail,reason",
    [
        ("yes", "correct_assertion", None),
        ("no", None, "missed_trigger"),
        ("no", None, "missed_outcome"),
        ("no", None, "other_target_behavior"),
        ("no", None, "unrelated_behavior"),
        ("no", None, "no_real_tests"),
        ("unsure", None, None),
    ],
)
def test_parser_accepts_each_answer_and_conditional_fields(issue, detail, reason):
    result = parse_judgment(
        json.dumps(labels(tests_issue=issue, attempt_detail=detail, no_attempt_reason=reason))
    )

    assert answer(result.tests_issue) == issue
    assert answer(result.attempt_detail) == detail
    assert answer(result.no_attempt_reason) == reason


@pytest.mark.parametrize(
    "issue,detail,reason",
    [
        ("yes", None, None),
        ("yes", "correct_assertion", "other_target_behavior"),
        ("no", "correct_assertion", "other_target_behavior"),
        ("no", None, None),
        ("unsure", "correct_assertion", None),
        ("unsure", None, "unrelated_behavior"),
    ],
)
def test_parser_rejects_conditional_fields_that_conflict_with_answer(issue, detail, reason):
    result = parse_judgment(
        json.dumps(labels(tests_issue=issue, attempt_detail=detail, no_attempt_reason=reason))
    )

    assert result.status == "invalid_output"


def test_parser_accepts_one_json_markdown_fence():
    raw = "```json\n" + json.dumps(labels()) + "\n```"
    assert parse_judgment(raw).status == "completed"


@pytest.mark.parametrize(
    "raw",
    [
        "before " + json.dumps(labels()),
        "```json\n" + json.dumps(labels()) + "\n```after",
        "{not json}",
        json.dumps([]),
        json.dumps({**labels(), "tests_issue": "yes"}),
        json.dumps(labels(tests_issue={"answer": "yes"})),
        json.dumps(labels(justifications={"tests_issue": "A separate justification."})),
        json.dumps(labels(no_attempt_reason="null")),
        json.dumps(labels(unexpected="field")),
        json.dumps(labels(oracle_alignment="behaviorally_aligned")),
        json.dumps(labels(tests_issue="maybe")),
        json.dumps(labels(attempt_detail=["correct_assertion"])),
        json.dumps(labels(no_attempt_reason="NULL")),
        json.dumps(labels(cheating="git_history_recovery")),
        json.dumps(labels(attempt_detail="none")),
        json.dumps(justified(tests_issue="   ")),
        json.dumps(justified(tests_issue="x" * (MAX_JUSTIFICATION_LENGTH + 1))),
        json.dumps(justified(tests_issue=3)),
    ],
)
def test_parser_returns_explicit_invalid_output(raw):
    result = parse_judgment(raw)

    assert result.status == "invalid_output"
    assert result.error


def test_parser_preserves_independent_answer_even_without_fail_to_pass():
    result = parse_judgment(json.dumps(labels()))

    assert answer(result.tests_issue) == "yes"


def test_failure_results_distinguish_failure_from_timeout():
    assert failed_turn("harness exited").status == "failed"
    assert failed_turn("deadline exceeded", timed_out=True).status == "timed_out"


def test_skipped_judgment_records_why_no_turn_was_run():
    assert SkippedJudgment(reason="generation failed").model_dump() == {
        "status": "skipped",
        "reason": "generation failed",
    }


def test_marking_stale_retains_the_previous_annotation(tmp_path):
    paths = RunPaths.create(tmp_path)
    previous = parse_judgment(json.dumps(labels()))
    write_json(paths.judge.judgment, previous.model_dump())

    mark_judgments_stale(paths)

    stale = read_json(paths.judge.judgment)
    assert stale["status"] == "stale"
    assert stale["previous_judgment"] == previous.model_dump()

    first = read_json(paths.judge.judgment)
    mark_judgments_stale(paths)
    assert read_json(paths.judge.judgment) == first


def test_parser_accepts_failure_mode_decisions_and_added_labels():
    raw = json.dumps(
        labels(
            failure_modes=failure_modes(changed_test_to_pass=[23, 24]),
            added_failure_modes=[added_failure_mode()],
        )
    )

    judgment = parse_judgment(raw)

    assert judgment.status == "completed"
    assert judgment.failure_modes["changed_test_to_pass"].steps == [23, 24]
    assert judgment.failure_modes["never_saw_bug"].decision == "absent"
    assert judgment.added_failure_modes[0].name == "Tested only defaults"


def test_every_start_label_must_be_decided():
    decided = failure_modes()
    del decided["flagged_bug"]

    result = parse_judgment(json.dumps(labels(failure_modes=decided)))

    assert result.status == "invalid_output"
    assert "flagged_bug" in result.error


@pytest.mark.parametrize(
    "decision",
    [
        {"decision": "present", "steps": [], "justification": "Step 3 shows it."},
        {"decision": "present", "justification": "No steps cited."},
        {"decision": "present", "steps": [3]},
        {"decision": "absent", "justification": "Absent labels carry nothing else."},
        {"decision": "maybe"},
    ],
)
def test_a_present_label_needs_steps_and_a_justification_and_an_absent_one_nothing(decision):
    decided = failure_modes() | {"never_saw_bug": decision}

    assert parse_judgment(json.dumps(labels(failure_modes=decided))).status == "invalid_output"
