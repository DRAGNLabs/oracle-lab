"""Saving a rater's review beside the LLM output it rates, and reading it back."""

from __future__ import annotations

import shutil
from pathlib import Path

from oracle_lab.io import digest, read_json, utc_stamp, write_json
from oracle_lab.review.contracts import RATER_ID, Review, ReviewProvenance, parse_review
from oracle_lab.review.targets import ReviewTarget


def save_review(target: ReviewTarget, rater: str, raw: str) -> Review:
    """Validate submitted ratings and save them with their provenance.

    A rater's earlier review of the same target moves to `reviews-history/` first.
    """
    directory = review_directory(target, rater)
    review = parse_review(raw, target.answered_questions())
    turn = read_json(target.agent_result)
    provenance = ReviewProvenance(
        stage=target.stage,
        rater=rater,
        reviewed_output=target.reviewed_output.relative_to(target.root).as_posix(),
        reviewed_output_sha256=digest(target.reviewed_output.read_bytes()),
        judge_harness=turn["harness"],
        judge_model=turn["model"],
        stage_rubric_sha256=digest(target.stage_rubric.read_bytes()),
        workspace_spec_sha256=digest(target.workspace_spec.read_bytes()),
    )

    if directory.exists():
        history = target.reviews.parent / "reviews-history" / rater / utc_stamp()
        history.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(directory, history)
    write_json(directory / "review.json", review.model_dump())
    write_json(directory / "provenance.json", provenance.model_dump())
    return review


def read_review(target: ReviewTarget, rater: str) -> Review | None:
    """This rater's saved review of the target, if any. Other raters' are never read."""
    path = review_directory(target, rater) / "review.json"
    return Review.model_validate(read_json(path)) if path.is_file() else None


def review_directory(target: ReviewTarget, rater: str) -> Path:
    if not RATER_ID.match(rater):
        raise ValueError(f"Rater IDs are letters, digits, '-' and '_', not {rater!r}")
    return target.reviews / rater
