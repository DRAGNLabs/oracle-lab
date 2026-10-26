"""Saving a rater's review beside the LLM output it rates, and reading reviews back."""

from __future__ import annotations

import shutil
from pathlib import Path

from oracle_lab.contracts.review import (
    RATER_ID,
    Review,
    ReviewProvenance,
    parse_review,
    require_rater,
)
from oracle_lab.io import digest, read_json, utc_stamp, write_json
from oracle_lab.review.targets import ReviewTarget


def save_review(target: ReviewTarget, rater: str, raw: str) -> Review:
    """Validate submitted ratings and save them with their provenance.

    A rater's earlier review of the same target moves to `reviews-history/` first.
    """
    directory = target.judge.reviews / require_rater(rater)
    review = parse_review(raw, target.rating_keys())
    provenance = _provenance(target, rater)
    if directory.exists():
        history = target.judge.review_history / rater / utc_stamp()
        history.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(directory, history)
    write_json(directory / "review.json", review.model_dump())
    write_json(directory / "provenance.json", provenance.model_dump())
    return review


def read_review(target: ReviewTarget, rater: str) -> Review | None:
    """This rater's saved review of the target, if any. Other raters' are never read."""
    return _read(target.judge.reviews / require_rater(rater))


def read_all_reviews(target: ReviewTarget) -> dict[str, Review]:
    """Every rater's saved review of the target. Only agreement reads these."""
    reviews = {}
    if target.judge.reviews.is_dir():
        for directory in sorted(target.judge.reviews.iterdir()):
            review = _read(directory) if RATER_ID.fullmatch(directory.name) else None
            if review is not None:
                reviews[directory.name] = review
    return reviews


def read_provenance(target: ReviewTarget, rater: str) -> ReviewProvenance:
    """What a rater's saved review of the target was based on."""
    path = target.judge.reviews / require_rater(rater) / "provenance.json"
    return ReviewProvenance.model_validate(read_json(path))


def _read(directory: Path) -> Review | None:
    path = directory / "review.json"
    return Review.model_validate(read_json(path)) if path.is_file() else None


def _provenance(target: ReviewTarget, rater: str) -> ReviewProvenance:
    turn = read_json(target.judge.result)
    return ReviewProvenance(
        stage=target.stage,
        rater=rater,
        reviewed_output=target.reviewed_output.relative_to(target.root).as_posix(),
        reviewed_output_sha256=digest(target.reviewed_output.read_bytes()),
        judge_harness=turn["harness"],
        judge_model=turn["model"],
        stage_rubric_sha256=digest(target.judge.rubric.read_bytes()),
        workspace_spec_sha256=digest(target.judge.workspace_spec.read_bytes()),
    )
