"""Strict contracts for human reviews of the LLM judges, and their agreement report."""

from __future__ import annotations

import json
import re
from collections.abc import Collection
from typing import Annotated, Literal, get_args

from pydantic import Field

from oracle_lab.judge.contracts import TraceSteps
from oracle_lab.results import Sha256, StrictModel

# The two LLM judges a review can rate: the generated-test judge and the classifier.
Stage = Literal["judge", "classification"]

RATER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
Rating = Literal["agree", "disagree"]
RATINGS = get_args(Rating)
# What each rating means, shown to raters beside its button.
RATING_DEFINITIONS: dict[Rating, str] = {
    "agree": "You would give the same answer, given the evidence and the stage rubric.",
    "disagree": "You would give a different answer, given the evidence and the stage rubric.",
}

Share = Annotated[float, Field(ge=0, le=1)]


def require_rater(rater: str) -> str:
    """Return the rater ID, refusing one that could name another directory."""
    if not RATER_ID.fullmatch(rater):
        raise ValueError(f"Rater IDs are letters, digits, '-' and '_', not {rater!r}")
    return rater


class RaterFailureMode(StrictModel):
    """A failure mode outside the start list that a rater saw and the judge missed."""

    name: Annotated[str, Field(min_length=1, max_length=80)]
    description: Annotated[str, Field(min_length=1, max_length=500)]
    steps: TraceSteps


class Review(StrictModel):
    """One rating for each item the LLM judge answered, and the rater's own labels.

    Items are the target's `rating_keys()`: submission or classification questions,
    and for a judgment `failure_modes.<key>` and `added_failure_modes.<index>`.
    """

    ratings: dict[str, Rating]
    other_failure_modes: list[RaterFailureMode] = Field(default_factory=list)


class ReviewProvenance(StrictModel):
    """What a review was based on: the output it rated and what the rater was shown."""

    stage: Stage
    rater: Annotated[str, Field(pattern=RATER_ID.pattern)]
    # Relative to the run or classification attempt directory.
    reviewed_output: Annotated[str, Field(min_length=1)]
    reviewed_output_sha256: Sha256
    judge_harness: Annotated[str, Field(min_length=1)]
    judge_model: Annotated[str, Field(min_length=1)]
    stage_rubric_sha256: Sha256
    workspace_spec_sha256: Sha256


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


class QuestionAgreement(StrictModel):
    """How raters rated one question of one stage, across every target."""

    label: str
    targets: int  # targets with at least one rating of this question
    ratings: dict[Rating, int]
    agree_share: Share  # how often raters agreed with the judge
    rater_pairs: int  # pairs of raters who rated the same target
    pairwise_agreement: Share | None  # None without a pair
    # Krippendorff's alpha for ordinal data; None without pairs or without variation.
    alpha: Annotated[float, Field(le=1)] | None


class StageAgreement(StrictModel):
    targets: int
    reviews: int
    questions: dict[str, QuestionAgreement]


class AddedLabelRatings(StrictModel):
    """One failure mode a judge added to one run, and how raters rated it."""

    target: str
    name: str
    ratings: dict[Rating, int]


class AgreementReport(StrictModel):
    """Inter-rater agreement and judge quality over a set of reviewed targets."""

    created_at: str
    inputs: list[str]
    raters: list[str]
    stages: dict[Stage, StageAgreement]
    # Every failure mode a judge added, with its ratings, by target.
    added_labels: list[AddedLabelRatings]
    # How many raters' own labels used each name, across every target.
    rater_labels: dict[str, int]
    # Each target some raters did not review, and those raters.
    missing: dict[str, list[str]]
    # Each target that could not be reviewed, and why.
    unreviewable: dict[str, str]
