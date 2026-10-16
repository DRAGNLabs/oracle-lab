"""A review is accepted only when it rates exactly the questions the judge answered."""

import json

import pytest

from oracle_lab.review.contracts import RATING_DEFINITIONS, RATINGS, Review, parse_review

QUESTIONS = ["tests_issue", "attempt_detail", "cheating"]


def filled(**changes) -> str:
    ratings = {"tests_issue": "correct", "attempt_detail": "defensible", "cheating": "incorrect"}
    ratings.update(changes)
    return json.dumps({"ratings": ratings})


def test_a_complete_review_parses():
    assert parse_review(filled(), QUESTIONS) == Review(
        ratings={"tests_issue": "correct", "attempt_detail": "defensible", "cheating": "incorrect"}
    )


@pytest.mark.parametrize(
    "raw",
    [
        "{not json}",
        json.dumps([]),
        json.dumps({}),
        json.dumps({"ratings": {}, "notes": "extra field"}),
        filled(tests_issue="wrong"),
        filled(tests_issue="Correct"),
        filled(tests_issue=None),
        filled(no_attempt_reason="correct"),
        json.dumps({"ratings": {"tests_issue": "correct", "attempt_detail": "correct"}}),
    ],
)
def test_a_bad_review_is_refused(raw):
    with pytest.raises(ValueError):
        parse_review(raw, QUESTIONS)


def test_every_rating_has_a_definition_for_raters():
    assert tuple(RATING_DEFINITIONS) == RATINGS
