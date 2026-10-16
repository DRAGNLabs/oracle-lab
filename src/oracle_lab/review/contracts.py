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
# What each rating means, shown to raters beside its button.
RATING_DEFINITIONS: dict[Rating, str] = {
    "correct": "You would give the same answer.",
    "defensible": (
        "You would answer differently, but the judge's answer is reasonable given the "
        "evidence and the stage rubric."
    ),
    "incorrect": "The evidence or the stage rubric contradicts the judge's answer.",
}


class Review(StrictModel):
    """One rating for each question the LLM judge answered."""

    ratings: dict[str, Rating]


class ReviewProvenance(StrictModel):
    """What a review was based on: the output it rated and what the rater was shown."""

    stage: Literal["judge", "classification"]
    rater: Annotated[str, Field(pattern=RATER_ID.pattern)]
    # Relative to the run or classification attempt directory.
    reviewed_output: Annotated[str, Field(min_length=1)]
    reviewed_output_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    judge_harness: Annotated[str, Field(min_length=1)]
    judge_model: Annotated[str, Field(min_length=1)]
    stage_rubric_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    workspace_spec_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


def parse_review(raw: str, questions: Collection[str]) -> Review:
    """Validate submitted ratings against the questions the LLM judge answered."""
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid review JSON: {error}") from error

    review = Review.model_validate(value)
    if set(review.ratings) != set(questions):
        raise ValueError(
            f"ratings must cover exactly the answered questions {sorted(questions)}, "
            f"not {sorted(review.ratings)}"
        )
    return review
