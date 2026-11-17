"""Judge accuracy and inter-rater agreement over saved human reviews.

Raters agree or disagree with each LLM answer and failure-mode decision. The
ratings say how often the judge is right: each rated item is decided by its
raters' majority. Agreement between raters says how far those ratings can be
trusted. The two stages are reported separately.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Hashable, Iterable, Sequence
from datetime import datetime, timezone
from itertools import combinations
from math import comb
from pathlib import Path
from typing import Literal

from oracle_lab.contracts.review import (
    RATINGS,
    AddedLabelRatings,
    AgreementReport,
    HarnessAccuracy,
    MajorityVote,
    QuestionAgreement,
    Rating,
    Review,
    Stage,
    StageAgreement,
)
from oracle_lab.io import write_json
from oracle_lab.paths import AgreementPaths, OutputPaths
from oracle_lab.review.store import read_all_reviews
from oracle_lab.review.targets import TARGET_KINDS, JudgmentTarget, ReviewTarget, find_targets

TITLES: dict[Stage, str] = {
    "judge": "Generation judgments",
    "classification": "Problem classifications",
}
# Rating items for failure modes a judge added, which are pooled into one question.
ADDED = "added_failure_modes"
ADDED_TITLE = "Failure modes the judge added"

# An item's verdict: what most of its raters said about the judge's answer.
Verdict = Literal["correct", "wrong", "disputed"]


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
    added_labels = _added_labels(targets, reviews)

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
        missing=missing,
        unreviewable=unreviewable,
    )


def _added_labels(
    targets: Sequence[ReviewTarget], reviews: dict[Path, dict[str, Review]]
) -> list[AddedLabelRatings]:
    """Every judge-added failure mode with its ratings."""
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
    return added


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
            "### Judge accuracy",
            "",
            "| Question | Items | Correct | Wrong | Disputed | Accuracy | 95% interval "
            "| Agree ratings |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for question in summary.questions.values():
            lines.append(
                f"| {question.label} | {question.targets} "
                f"| {_vote_cells(question.majority)} | {_percent(question.agree_share)} |"
            )
        lines.append(
            f"| **Whole item** | {_decided(summary.items) + summary.items.disputed} "
            f"| {_vote_cells(summary.items)} | |"
        )
        if summary.by_harness:
            harnesses = list(summary.by_harness)
            lines += [
                "",
                "### Judge accuracy by harness",
                "",
                "| Question | " + " | ".join(harnesses) + " |",
                "|---|" + "---:|" * len(harnesses),
            ]
            for key, question in summary.questions.items():
                cells = [
                    _fraction(accuracy.questions[key]) if key in accuracy.questions else "—"
                    for accuracy in summary.by_harness.values()
                ]
                lines.append(f"| {question.label} | " + " | ".join(cells) + " |")
            cells = [_fraction(accuracy.items) for accuracy in summary.by_harness.values()]
            lines.append("| **Whole item** | " + " | ".join(cells) + " |")
        lines += [
            "",
            "### Agreement between raters",
            "",
            "| Question | Rater pairs | Raw agreement | Alpha |",
            "|---|---:|---:|---:|",
        ]
        for question in summary.questions.values():
            lines.append(
                f"| {question.label} | {question.rater_pairs} "
                f"| {_percent(question.pairwise_agreement)} "
                f"| {'—' if question.alpha is None else f'{question.alpha:.3f}'} |"
            )
    lines += [
        "",
        "Each rated item is decided by a strict majority of the raters who rated it: "
        "correct when more agreed with the judge than disagreed, wrong in the reverse "
        "case, and disputed on an even split. Accuracy is correct items over correct and "
        "wrong ones; disputed items are left out of it. The interval is the exact "
        "(Clopper-Pearson) 95% interval. Agree ratings pools every rating instead. A whole "
        "item is a run or classification: wrong when any of its questions is, else "
        "disputed when any is, else correct. By harness, each cell is correct over "
        "decided items, with disputed ones after a plus sign.",
        "",
        "Raw agreement is the share of rater pairs on the same item that gave the same "
        "rating. Alpha is Krippendorff's alpha over the two ratings. It is low when "
        "nearly every rating is the same, even if raters agree; raw agreement shows that "
        "case. A dash means there was nothing to compute it from. Failure modes a judge "
        "added differ by run, so they are pooled into one question and listed below.",
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
    """Accuracy and agreement for one stage, from each target and its reviews by rater.

    Each question and each start-list failure mode is one question. The failure modes
    a judge added differ by run, so they are pooled into one question.
    """
    # One unit per target and rating item: the ratings its raters gave that item.
    units: dict[str, list[list[Rating]]] = {}
    labels: dict[str, str] = {}
    # Each rated target's harness (None for a classification) and its items' verdicts.
    decided: list[tuple[str | None, dict[str, list[Verdict]]]] = []
    for target, by_rater in reviewed:
        verdicts: dict[str, list[Verdict]] = {}
        for key in target.rating_keys():
            question = ADDED if key.startswith(ADDED + ".") else key
            labels[question] = ADDED_TITLE if question == ADDED else target.rating_label(key)
            unit = [review.ratings[key] for review in by_rater.values() if key in review.ratings]
            if unit:
                units.setdefault(question, []).append(unit)
                verdicts.setdefault(question, []).append(verdict(unit))
        if verdicts:
            harness = target.config().agent.harness if isinstance(target, JudgmentTarget) else None
            decided.append((harness, verdicts))
    order = {key: index for index, key in enumerate(labels)}
    questions = sorted(units, key=order.get)

    by_harness = {}
    for harness in sorted({harness for harness, _ in decided if harness is not None}):
        runs = [verdicts for name, verdicts in decided if name == harness]
        by_harness[harness] = HarnessAccuracy(
            items=majority(_item_verdict(verdicts) for verdicts in runs),
            questions={
                key: majority(v for verdicts in runs for v in verdicts.get(key, []))
                for key in questions
                if any(key in verdicts for verdicts in runs)
            },
        )
    return StageAgreement(
        targets=len(reviewed),
        reviews=sum(len(by_rater) for _, by_rater in reviewed),
        questions={key: _question(labels[key], units[key]) for key in questions},
        items=majority(_item_verdict(verdicts) for _, verdicts in decided),
        by_harness=by_harness,
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
        majority=majority(verdict(unit) for unit in units),
        rater_pairs=len(pairs),
        pairwise_agreement=sum(pairs) / len(pairs) if pairs else None,
        alpha=krippendorff_alpha(units, RATINGS),
    )


def verdict(unit: Sequence[Rating]) -> Verdict:
    """What a strict majority of the raters who rated an item said; an even split is disputed."""
    agree = sum(rating == "agree" for rating in unit)
    disagree = len(unit) - agree
    if agree == disagree:
        return "disputed"
    return "correct" if agree > disagree else "wrong"


def _item_verdict(verdicts: dict[str, list[Verdict]]) -> Verdict:
    """A whole target: wrong if any item is, else disputed if any item is, else correct."""
    every = {v for item in verdicts.values() for v in item}
    return "wrong" if "wrong" in every else "disputed" if "disputed" in every else "correct"


def majority(verdicts: Iterable[Verdict]) -> MajorityVote:
    counts = Counter(verdicts)
    correct, wrong = counts["correct"], counts["wrong"]
    decided = correct + wrong
    low, high = clopper_pearson(correct, decided) if decided else (None, None)
    return MajorityVote(
        correct=correct,
        wrong=wrong,
        disputed=counts["disputed"],
        accuracy=correct / decided if decided else None,
        interval_low=low,
        interval_high=high,
    )


def clopper_pearson(successes: int, trials: int, confidence: float = 0.95) -> tuple[float, float]:
    """The exact binomial confidence interval for `successes` out of `trials`.

    Each bound is the proportion at which seeing this result, or one further from
    it, has probability (1 - confidence) / 2. With no failures the upper bound is 1
    and the lower bound is still informative, which is the case a reliable judge gives.
    """
    tail = (1 - confidence) / 2

    def probability(p: float, outcomes: range) -> float:
        return sum(comb(trials, k) * p**k * (1 - p) ** (trials - k) for k in outcomes)

    def solve(increasing) -> float:
        # Bisect for the root of a function that increases in p.
        low, high = 0.0, 1.0
        for _ in range(60):
            middle = (low + high) / 2
            low, high = (middle, high) if increasing(middle) < 0 else (low, middle)
        return (low + high) / 2

    at_least = range(successes, trials + 1)
    at_most = range(successes + 1)
    lower = 0.0 if successes == 0 else solve(lambda p: probability(p, at_least) - tail)
    upper = 1.0 if successes == trials else solve(lambda p: tail - probability(p, at_most))
    return lower, upper


def _decided(vote: MajorityVote) -> int:
    return vote.correct + vote.wrong


def _vote_cells(vote: MajorityVote) -> str:
    """The correct, wrong, disputed, accuracy, and interval cells of a table row."""
    interval = (
        "—"
        if vote.interval_low is None
        else f"{_percent(vote.interval_low)}–{_percent(vote.interval_high)}"
    )
    return (
        f"{vote.correct} | {vote.wrong} | {vote.disputed} | {_percent(vote.accuracy)} | {interval}"
    )


def _fraction(vote: MajorityVote) -> str:
    """Correct over decided items, with any disputed ones after a plus sign."""
    if not _decided(vote) and not vote.disputed:
        return "—"
    text = f"{vote.correct}/{_decided(vote)}"
    return text + (f" +{vote.disputed}" if vote.disputed else "")


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" + ("" if number == 1 else "s")


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.1%}"
