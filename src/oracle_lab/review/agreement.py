"""Inter-rater agreement and judge quality over saved human reviews.

Raters agree or disagree with each LLM answer and failure-mode decision. Agreement
between raters says how far those ratings can be trusted; the ratings themselves
say how often the judge is right. The two stages are reported separately.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

from oracle_lab.contracts.review import (
    RATINGS,
    AddedLabelRatings,
    AgreementReport,
    QuestionAgreement,
    Rating,
    Review,
    Stage,
    StageAgreement,
)
from oracle_lab.io import write_json
from oracle_lab.paths import AgreementPaths, OutputPaths
from oracle_lab.review.store import read_all_reviews
from oracle_lab.review.targets import TARGET_KINDS, ReviewTarget, find_targets

TITLES: dict[Stage, str] = {
    "judge": "Generation judgments",
    "classification": "Problem classifications",
}
# Rating items for failure modes a judge added, which are pooled into one question.
ADDED = "added_failure_modes"
ADDED_TITLE = "Failure modes the judge added"


def agreement(paths: Sequence[Path], output: Path | None = None) -> Path:
    """Compute agreement over every target under `paths` and save it. Returns the directory."""
    report = compute_agreement(paths)
    if output is None:
        paths = OutputPaths(Path.cwd() / "output").new_agreement()
    else:
        paths = AgreementPaths(output)
        output.mkdir(parents=True, exist_ok=True)
    write_json(paths.result, report.model_dump(mode="json"))
    paths.report.write_text(render_agreement(report))
    return paths.root


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
        in_stage = [
            (target, reviews[target.root]) for target in targets if isinstance(target, kind)
        ]
        if in_stage:
            stages[kind.stage] = _stage(in_stage)
    added_labels, rater_labels = _added_labels(targets, reviews)

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
        added_labels=added_labels,
        rater_labels=rater_labels,
        missing=missing,
        unreviewable=unreviewable,
    )


def _added_labels(
    targets: Sequence[ReviewTarget], reviews: dict[Path, dict[str, Review]]
) -> tuple[list[AddedLabelRatings], dict[str, int]]:
    """Every judge-added failure mode with its ratings, and the raters' own labels
    counted by name."""
    added = []
    for target in targets:
        for key in target.rating_keys():
            if key.startswith(ADDED + "."):
                counts = Counter(
                    review.ratings[key]
                    for review in reviews[target.root].values()
                    if key in review.ratings
                )
                added.append(
                    AddedLabelRatings(
                        target=target.root.as_posix(),
                        name=target.rating_label(key),
                        ratings={rating: counts[rating] for rating in RATINGS},
                    )
                )
    names = Counter(
        label.name
        for by_rater in reviews.values()
        for review in by_rater.values()
        for label in review.other_failure_modes
    )
    return added, dict(sorted(names.items()))


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
            "| Question | Targets | Agree | Disagree | Judge agreed with "
            "| Rater pairs | Raw agreement | Alpha |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for question in summary.questions.values():
            counts = question.ratings
            lines.append(
                f"| {question.label} | {question.targets} | {counts['agree']} "
                f"| {counts['disagree']} | {_percent(question.agree_share)} "
                f"| {question.rater_pairs} | {_percent(question.pairwise_agreement)} "
                f"| {'—' if question.alpha is None else f'{question.alpha:.3f}'} |"
            )
    lines += [
        "",
        "Judge agreed with is the share of ratings that agree with the judge. Raw "
        "agreement is the share of rater pairs on the same target that gave the same "
        "rating. Alpha is Krippendorff's alpha over the two ratings. It is low when "
        "nearly every rating is the same, even if raters agree; raw agreement shows that "
        "case. A dash means there was no pair of raters, or no variation, to compute it "
        "from. Failure modes a judge added differ by run, so they are pooled into one "
        "question and listed below.",
    ]
    if report.added_labels:
        lines += [
            "",
            "## Failure modes the judges added",
            "",
            "| Target | Label | Agree | Disagree |",
            "|---|---|---:|---:|",
        ]
        lines += [
            f"| `{item.target}` | {item.name} | {item.ratings['agree']} | {item.ratings['disagree']} |"
            for item in report.added_labels
        ]
    if report.rater_labels:
        lines += ["", "## Failure modes raters added", "", "| Label | Times |", "|---|---:|"]
        lines += [f"| {name} | {count} |" for name, count in report.rater_labels.items()]
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


def _stage(reviewed: list[tuple[ReviewTarget, dict[str, Review]]]) -> StageAgreement:
    """Agreement for one stage, from each of its targets and their reviews by rater.

    Each question and each start-list failure mode is one question. The failure modes
    a judge added differ by run, so they are pooled into one question.
    """
    # One unit per target and rating item: the ratings its raters gave that item.
    units: dict[str, list[list[Rating]]] = {}
    labels: dict[str, str] = {}
    for target, by_rater in reviewed:
        for key in target.rating_keys():
            question = ADDED if key.startswith(ADDED + ".") else key
            labels[question] = ADDED_TITLE if question == ADDED else target.rating_label(key)
            unit = [review.ratings[key] for review in by_rater.values() if key in review.ratings]
            if unit:
                units.setdefault(question, []).append(unit)
    order = {key: index for index, key in enumerate(labels)}
    return StageAgreement(
        targets=len(reviewed),
        reviews=sum(len(by_rater) for _, by_rater in reviewed),
        questions={key: _question(labels[key], units[key]) for key in sorted(units, key=order.get)},
    )


def _question(label: str, units: list[list[Rating]]) -> QuestionAgreement:
    counts = Counter(rating for unit in units for rating in unit)
    total = sum(counts.values())
    pairs = [first == second for unit in units for first, second in combinations(unit, 2)]
    return QuestionAgreement(
        label=label,
        targets=len(units),
        ratings={rating: counts[rating] for rating in RATINGS},
        agree_share=counts["agree"] / total,
        rater_pairs=len(pairs),
        pairwise_agreement=sum(pairs) / len(pairs) if pairs else None,
        alpha=krippendorff_alpha(units, RATINGS),
    )


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.0%}"
