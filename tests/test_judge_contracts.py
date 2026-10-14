import json

import pytest
from fixtures import judgment_labels as labels

from oracle_lab.io import read_json, write_json
from oracle_lab.judge.contracts import (
    CompletedJudgment,
    complete_human_judgment,
    mark_judgments_stale,
    parse_judgment,
    skipped_judgment,
    validate_judgment,
)
from oracle_lab.paths import RunPaths
from oracle_lab.results import MAX_JUSTIFICATION_LENGTH, failed_turn


def justified(**texts):
    """Labels for the default yes answer, with the given justifications replaced."""
    default = labels()["justifications"]
    return labels(justifications={**default, **texts})


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
    assert result.attempt_detail == detail


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
    assert result.cheating == cheating


@pytest.mark.parametrize(
    "answer,detail,reason",
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
def test_parser_accepts_each_answer_and_conditional_fields(answer, detail, reason):
    result = parse_judgment(
        json.dumps(labels(tests_issue=answer, attempt_detail=detail, no_attempt_reason=reason))
    )

    assert result.tests_issue == answer
    assert result.attempt_detail == detail
    assert result.no_attempt_reason == reason


@pytest.mark.parametrize(
    "value,detail,reason",
    [
        (labels(no_attempt_reason="null"), "correct_assertion", None),
        (
            labels(
                tests_issue="no", attempt_detail="null", no_attempt_reason="other_target_behavior"
            ),
            None,
            "other_target_behavior",
        ),
        (labels(tests_issue="unsure", attempt_detail="null", no_attempt_reason="null"), None, None),
    ],
)
def test_parser_normalizes_quoted_null_only_in_conditional_fields(value, detail, reason):
    result = parse_judgment(json.dumps(value))

    assert isinstance(result, CompletedJudgment)
    assert result.attempt_detail == detail
    assert result.no_attempt_reason == reason


def test_human_input_accepts_quoted_null_but_saved_contract_does_not():
    raw = labels(tests_issue="no", attempt_detail="null", no_attempt_reason="other_target_behavior")

    judgment = complete_human_judgment(json.dumps(raw))

    assert judgment.attempt_detail is None
    assert judgment.model_dump()["attempt_detail"] is None
    with pytest.raises(ValueError):
        validate_judgment({"status": "completed", **raw})


@pytest.mark.parametrize(
    "answer,detail,reason",
    [
        ("yes", None, None),
        ("yes", "correct_assertion", "other_target_behavior"),
        ("no", "correct_assertion", "other_target_behavior"),
        ("no", None, None),
        ("unsure", "correct_assertion", None),
        ("unsure", None, "unrelated_behavior"),
    ],
)
def test_parser_rejects_conditional_fields_that_conflict_with_answer(answer, detail, reason):
    result = parse_judgment(
        json.dumps(labels(tests_issue=answer, attempt_detail=detail, no_attempt_reason=reason))
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
        json.dumps({key: value for key, value in labels().items() if key != "justifications"}),
        json.dumps(labels(justifications={"tests_issue": "Only one answer is justified."})),
        json.dumps(justified(no_attempt_reason="A null answer has no justification.")),
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

    assert result.tests_issue == "yes"


def test_human_rating_preserves_issue_attempt_even_when_assertion_is_wrong():
    judgment = complete_human_judgment(
        json.dumps(
            labels(
                tests_issue="yes",
                attempt_detail="wrong_assertion",
            )
        )
    )

    assert judgment.tests_issue == "yes"
    assert judgment.attempt_detail == "wrong_assertion"


def test_human_rating_rejects_an_unfilled_justification():
    with pytest.raises(ValueError, match="placeholder"):
        complete_human_judgment(json.dumps(justified(cheating="REPLACE: explain")))


def test_failure_results_distinguish_failure_from_timeout():
    assert failed_turn("harness exited").status == "failed"
    assert failed_turn("deadline exceeded", timed_out=True).status == "timed_out"


def test_skipped_judgment_records_why_no_turn_was_run():
    assert skipped_judgment("generation failed").model_dump() == {
        "status": "skipped",
        "reason": "generation failed",
    }


def test_marking_stale_retains_every_previous_annotation(tmp_path):
    paths = RunPaths.create(tmp_path)
    previous = parse_judgment(json.dumps(labels()))
    human = paths.judge.human.judgments / "rater_1/judgment.json"
    human.parent.mkdir(parents=True)
    for path in (paths.judge.judgment, human):
        write_json(path, previous.model_dump())

    mark_judgments_stale(paths)

    for path in (paths.judge.judgment, human):
        stale = read_json(path)
        assert stale["status"] == "stale"
        assert stale["previous_judgment"] == previous.model_dump()

    first = read_json(paths.judge.judgment)
    mark_judgments_stale(paths)
    assert read_json(paths.judge.judgment) == first
