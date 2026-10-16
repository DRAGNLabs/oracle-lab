"""Inter-rater agreement and judge quality over saved human reviews.

Raters rate each LLM answer `correct`, `defensible`, or `incorrect`. Agreement
between raters says how far those ratings can be trusted; the ratings themselves
say how often the judge is right. The two stages are reported separately.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

from oracle_lab.io import utc_stamp, write_json
from oracle_lab.review.contracts import (
    RATINGS,
    AgreementReport,
    QuestionAgreement,
    Rating,
    Review,
    Stage,
    StageAgreement,
)
from oracle_lab.review.store import read_all_reviews
from oracle_lab.review.targets import TARGET_KINDS, find_targets

TITLES: dict[Stage, str] = {"judge": "Test judgments", "classification": "Task classifications"}


def agreement(paths: Sequence[Path], output: Path | None = None) -> Path:
    """Compute agreement over every target under `paths` and save it. Returns the directory."""
    report = compute_agreement(paths)
    directory = output or Path.cwd() / "agreement" / utc_stamp()
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "agreement.json", report.model_dump(mode="json"))
    (directory / "agreement.md").write_text(render_agreement(report))
    return directory


def compute_agreement(paths: Sequence[Path]) -> AgreementReport:
    targets = []
    unreviewable = {}
    for path in paths:
        found, cannot = find_targets(path)
        targets += found
        unreviewable |= {item.root.as_posix(): item.reason for item in cannot}

    reviews = {target.root: read_all_reviews(target) for target in targets}
    raters = sorted({rater for by_rater in reviews.values() for rater in by_rater})

    stages = {}
    for kind in TARGET_KINDS:
        in_stage = [reviews[target.root] for target in targets if isinstance(target, kind)]
        if in_stage:
            stages[kind.stage] = _stage(in_stage, kind.labels)

    # Every rater is expected to review every target.
    missing = {}
    for target in targets:
        absent = [rater for rater in raters if rater not in reviews[target.root]]
        if absent:
            missing[target.root.as_posix()] = absent

    return AgreementReport(
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        inputs=[path.as_posix() for path in paths],
        raters=raters,
        stages=stages,
        missing=missing,
        unreviewable=unreviewable,
    )


def krippendorff_alpha(
    units: Iterable[Sequence[Hashable]], order: Sequence[Hashable]
) -> float | None:
    """Krippendorff's alpha for ordinal values, ordered as in `order`.

    Each unit holds the values its raters gave; a rater who did not rate it is
    simply absent. A unit with one value has no pair and does not count. Returns
    None when no unit has a pair or every paired value is the same, since alpha is
    undefined then.
    """
    rank = {value: index for index, value in enumerate(order)}
    size = len(order)
    # The coincidence matrix: every ordered pair of values within a unit,
    # weighted so that each unit contributes as many pairs as it has values.
    coincidence = [[0.0] * size for _ in range(size)]
    for values in units:
        count = len(values)
        if count < 2:
            continue
        for first, second in combinations(values, 2):
            coincidence[rank[first]][rank[second]] += 1 / (count - 1)
            coincidence[rank[second]][rank[first]] += 1 / (count - 1)
    totals = [sum(row) for row in coincidence]
    total = sum(totals)

    def distance(c: int, k: int) -> float:
        # The ordinal metric: how many values lie between c and k, counting each end half.
        low, high = min(c, k), max(c, k)
        return (sum(totals[low : high + 1]) - (totals[c] + totals[k]) / 2) ** 2

    cells = [(c, k) for c in range(size) for k in range(size)]
    observed = sum(coincidence[c][k] * distance(c, k) for c, k in cells)
    expected = sum(totals[c] * totals[k] * distance(c, k) for c, k in cells)
    if total == 0 or expected == 0:
        return None
    return 1 - (total - 1) * observed / expected


def render_agreement(report: AgreementReport) -> str:
    lines = [
        "# Human review agreement",
        "",
        f"Created {report.created_at} from {', '.join(f'`{path}`' for path in report.inputs)}.",
        "",
        f"Raters: {', '.join(report.raters) if report.raters else 'none'}.",
    ]
    for stage, summary in report.stages.items():
        lines += [
            "",
            f"## {TITLES[stage]}",
            "",
            f"{_count(summary.targets, 'target')}, {_count(summary.reviews, 'review')}.",
        ]
        if not summary.questions:
            continue
        lines += [
            "",
            "| Question | Targets | Correct | Defensible | Incorrect | Judge correct "
            "| Correct or defensible | Rater pairs | Raw agreement | Alpha |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for question in summary.questions.values():
            counts = question.ratings
            lines.append(
                f"| {question.label} | {question.targets} | {counts['correct']} "
                f"| {counts['defensible']} | {counts['incorrect']} "
                f"| {_percent(question.correct_share)} | {_percent(question.acceptable_share)} "
                f"| {question.rater_pairs} | {_percent(question.pairwise_agreement)} "
                f"| {'—' if question.alpha is None else f'{question.alpha:.3f}'} |"
            )
    lines += [
        "",
        "Judge correct is the share of ratings that are `correct`; correct or defensible "
        "adds `defensible`. Raw agreement is the share of rater pairs on the same target "
        "that gave the same rating. Alpha is Krippendorff's alpha for ordinal ratings, "
        "ordered correct, defensible, incorrect. It is low when nearly every rating is "
        "the same, even if raters agree; raw agreement shows that case. A dash means "
        "there was no pair of raters, or no variation, to compute it from.",
    ]
    lines += ["", "## Missing reviews", ""]
    if not report.raters:
        lines.append("No reviews yet.")
    elif report.missing:
        lines += ["| Target | Raters without a review |", "|---|---|"]
        lines += [
            f"| `{target}` | {', '.join(raters)} |" for target, raters in report.missing.items()
        ]
    else:
        lines.append("Every rater reviewed every target.")
    if report.unreviewable:
        lines += ["", "## Not reviewable", "", "| Target | Reason |", "|---|---|"]
        lines += [
            f"| `{target}` | {reason.splitlines()[0]} |"
            for target, reason in report.unreviewable.items()
        ]
    return "\n".join(lines) + "\n"


def _stage(reviews: list[dict[str, Review]], labels: dict[str, str]) -> StageAgreement:
    """Agreement for one stage, from each of its targets' reviews by rater."""
    questions = {}
    for key, label in labels.items():
        # One unit per target: the ratings its raters gave this question.
        units: list[list[Rating]] = []
        for by_rater in reviews:
            unit: list[Rating] = [
                review.ratings[key] for review in by_rater.values() if key in review.ratings
            ]
            if unit:
                units.append(unit)
        if units:
            questions[key] = _question(label, units)
    return StageAgreement(
        targets=len(reviews),
        reviews=sum(len(by_rater) for by_rater in reviews),
        questions=questions,
    )


def _question(label: str, units: list[list[Rating]]) -> QuestionAgreement:
    counts = Counter(rating for unit in units for rating in unit)
    total = sum(counts.values())
    pairs = [first == second for unit in units for first, second in combinations(unit, 2)]
    return QuestionAgreement(
        label=label,
        targets=len(units),
        ratings={rating: counts[rating] for rating in RATINGS},
        correct_share=counts["correct"] / total,
        acceptable_share=(counts["correct"] + counts["defensible"]) / total,
        rater_pairs=len(pairs),
        pairwise_agreement=sum(pairs) / len(pairs) if pairs else None,
        alpha=krippendorff_alpha(units, RATINGS),
    )


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"
