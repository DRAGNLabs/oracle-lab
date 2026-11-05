"""A review is accepted only when it rates exactly the items the judge answered, and
gives a reason for every disagree."""

import json

import pytest

from oracle_lab.contracts.review import RATING_DEFINITIONS, RATINGS, parse_review

ITEMS = ["tests_issue", "cheating", "failure_modes.flagged_bug", "added_failure_modes.0"]
REASON = {"cheating": "The agent read the golden patch."}


def filled(justifications=REASON, **changes) -> str:
    ratings = dict.fromkeys(ITEMS, "agree") | {"cheating": "disagree"}
    ratings.update(changes)
    return json.dumps({"ratings": ratings, "justifications": justifications})


def test_a_complete_review_parses():
    review = parse_review(filled(), ITEMS)

    assert review.ratings["cheating"] == "disagree"
    assert review.justifications == REASON


def test_a_review_of_only_agrees_needs_no_justifications():
    raw = json.dumps({"ratings": dict.fromkeys(ITEMS, "agree")})

    assert parse_review(raw, ITEMS).justifications == {}


@pytest.mark.parametrize(
    "raw",
    [
        "{not json}",
        json.dumps([]),
        json.dumps({}),
        json.dumps({"ratings": {}, "notes": "extra field"}),
        filled(tests_issue="wrong"),
        filled(tests_issue="correct"),
        filled(tests_issue="defensible"),
        filled(tests_issue=None),
        filled(no_attempt_reason="agree"),
        json.dumps({"ratings": {"tests_issue": "agree"}}),
        filled(justifications={}),  # a disagree without a reason
        filled(justifications={"cheating": "   "}),  # a blank reason
        filled(justifications={**REASON, "tests_issue": "Agreed anyway."}),  # a reason for an agree
        json.dumps({"ratings": dict.fromkeys(ITEMS, "agree"), "other_failure_modes": []}),
    ],
)
def test_a_bad_review_is_refused(raw):
    with pytest.raises(ValueError):
        parse_review(raw, ITEMS)


def test_every_rating_has_a_definition_for_raters():
    assert tuple(RATING_DEFINITIONS) == RATINGS == ("agree", "disagree")
