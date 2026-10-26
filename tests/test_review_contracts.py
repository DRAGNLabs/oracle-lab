"""A review is accepted only when it rates exactly the items the judge answered."""

import json

import pytest

from oracle_lab.contracts.review import RATING_DEFINITIONS, RATINGS, parse_review

ITEMS = ["tests_issue", "cheating", "failure_modes.flagged_bug", "added_failure_modes.0"]


def filled(**changes) -> str:
    ratings = dict.fromkeys(ITEMS, "agree") | {"cheating": "disagree"}
    ratings.update(changes)
    return json.dumps({"ratings": ratings})


def test_a_complete_review_parses():
    review = parse_review(filled(), ITEMS)

    assert review.ratings["cheating"] == "disagree"
    assert review.other_failure_modes == []


def test_a_review_carries_the_raters_own_failure_modes():
    label = {
        "name": "Tested only defaults",
        "description": "No test passes an argument.",
        "steps": [3],
    }
    raw = json.dumps({"ratings": json.loads(filled())["ratings"], "other_failure_modes": [label]})

    assert parse_review(raw, ITEMS).other_failure_modes[0].steps == [3]


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
        json.dumps(
            {
                "ratings": json.loads(filled())["ratings"],
                "other_failure_modes": [{"name": "No steps", "description": "x", "steps": []}],
            }
        ),
    ],
)
def test_a_bad_review_is_refused(raw):
    with pytest.raises(ValueError):
        parse_review(raw, ITEMS)


def test_every_rating_has_a_definition_for_raters():
    assert tuple(RATING_DEFINITIONS) == RATINGS == ("agree", "disagree")
