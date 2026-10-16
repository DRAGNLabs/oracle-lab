"""Strict contracts for a human review of one LLM judge output."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Annotated, Literal, get_args

from pydantic import Field

from oracle_lab.results import StrictModel

RATER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
Rating = Literal["correct", "defensible", "incorrect"]
RATINGS = get_args(Rating)
# What the draft holds for each question until the rater chooses.
PLACEHOLDER = "CHOOSE: " + " | ".join(RATINGS)


class Review(StrictModel):
    """One rating for each question the LLM judge answered."""

    ratings: dict[str, Rating]


class ReviewProvenance(StrictModel):
    """What a review was based on: the output it rated and what the rater was shown."""

    stage: Literal["judge", "classification"]
    rater: Annotated[str, Field(pattern=RATER_ID.pattern)]
    container: Annotated[str, Field(min_length=1)]
    # Relative to the run or classification attempt directory.
    reviewed_output: Annotated[str, Field(min_length=1)]
    reviewed_output_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    judge_harness: Annotated[str, Field(min_length=1)]
    judge_model: Annotated[str, Field(min_length=1)]
    stage_rubric_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    workspace_spec_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def draft_review(questions: Collection[str]) -> dict:
    """The file a rater fills in: every question still set to the placeholder."""
    return {"ratings": {question: PLACEHOLDER for question in questions}}


def parse_review(raw: str, questions: Collection[str]) -> Review:
    """Validate a filled-in draft against the questions the LLM judge answered."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid review JSON: {error}") from error

    ratings = value.get("ratings") if isinstance(value, dict) else None
    if isinstance(ratings, dict):
        unrated = sorted(question for question, rating in ratings.items() if rating == PLACEHOLDER)
        if unrated:
            raise ValueError(f"Rate these questions before collecting: {unrated}")

    review = Review.model_validate(value)
    if set(review.ratings) != set(questions):
        raise ValueError(
            f"ratings must cover exactly the answered questions {sorted(questions)}, "
            f"not {sorted(review.ratings)}"
        )
    return review
