"""Set problems up for runs: preflight, then classify, every problem in a setup file.

A problem's state lives in its preflight directory and its classification
attempts, so rerunning a setup file picks up where it left off: a problem whose
preflight passed for the same record is not preflighted again, and one with a
completed classification is not classified again. Everything else is retried.

Each run rewrites `<output>/setup/<setup file name>.md`, a table of where every
problem in the file stands, so it never describes an earlier state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from oracle_lab.config import SetupConfig, SourceConfig, load_setup_config
from oracle_lab.contracts.classification import (
    CLASSIFICATION_LABELS,
    latest_classification,
    require_usable_classification,
)
from oracle_lab.contracts.run import read_localized_target, read_status
from oracle_lab.harnesses.launch import require_credential
from oracle_lab.instance import resolve_source
from oracle_lab.judge.classification.run import classify
from oracle_lab.localization import preflight_passed
from oracle_lab.paths import OutputPaths
from oracle_lab.preflight import preflight


@dataclass
class Outcome:
    """Where one problem stands after setup."""

    instance_id: str
    preflight: str
    classification: str = "not run"
    scope: str = "-"
    verdict: str = "not ready"
    ready: bool = False  # preflight passed and classification completed
    target: str = ""  # the localized target, one file per line
    answers: dict[str, str] = field(default_factory=dict)  # the classification's answers
    preflight_dir: Path | None = None
    attempt_dir: Path | None = None


def setup(path: Path, *, reclassify: bool = False, preflight_only: bool = False) -> bool:
    """Set up every problem in the file, list where each stands, and say if all are done.

    `reclassify` classifies problems again even when they have a completed
    classification. `preflight_only` stops after preflight, without a model.
    """
    config = load_setup_config(path)
    if not preflight_only:
        require_credential(config.classifier)
    outcomes = [
        setup_problem(config, source, reclassify=reclassify, preflight_only=preflight_only)
        for source in config.problems
    ]
    print(summary(outcomes))
    report = write_report(OutputPaths(Path(config.output)), path, outcomes)
    print(f"Summary: {report}")
    return all(outcome.ready for outcome in outcomes)


def setup_problem(
    config: SetupConfig, source: SourceConfig, *, reclassify: bool, preflight_only: bool
) -> Outcome:
    output = OutputPaths(Path(config.output))
    try:
        instance, _ = resolve_source(source)
    except Exception as error:
        return Outcome(source.instance, f"failed at resolve: {error}")

    preflight_dir = output.preflight_for(instance.instance_id)
    if preflight_passed(output, instance):
        outcome = Outcome(instance.instance_id, "passed earlier")
    else:
        try:
            preflight(config.problem(source.model_copy()))
        except Exception as error:
            stage = (
                read_status(preflight_dir.status).stage
                if preflight_dir.status.is_file()
                else "resolve"
            )
            return Outcome(
                instance.instance_id,
                f"failed at {stage}: {error}",
                preflight_dir=preflight_dir.root,
            )
        outcome = Outcome(instance.instance_id, "passed")
    outcome.preflight_dir = preflight_dir.root
    target = read_localized_target(preflight_dir.target)
    outcome.scope = target.scope
    outcome.target = target.text
    if preflight_only:
        outcome.ready = True
        outcome.verdict = "preflight only"
        return outcome

    latest, attempt = latest_classification(output.classifications, instance)
    if latest["status"] == "completed" and not reclassify:
        outcome.classification = "completed earlier"
    else:
        try:
            classify(config.classification(source.model_copy()))
        except Exception as error:
            outcome.classification = f"failed: {error}"
            return outcome
        latest, attempt = latest_classification(output.classifications, instance)
        outcome.classification = latest["status"]
    outcome.attempt_dir = attempt.root if attempt else None
    if latest["status"] != "completed":
        return outcome
    outcome.answers = _answers(latest)

    outcome.ready = True
    try:
        require_usable_classification(output.classifications, instance)
        outcome.verdict = "cleared"
    except ValueError as error:
        outcome.verdict = "rejected: " + str(error).split(": ", 1)[-1]
    return outcome


def summary(outcomes: list[Outcome]) -> str:
    """One line per problem, then the counts."""
    width = max(len(outcome.instance_id) for outcome in outcomes)
    lines = [
        f"{outcome.instance_id:<{width}}  preflight: {outcome.preflight}  "
        f"fix scope: {outcome.scope}  classification: {outcome.classification}  "
        f"-> {outcome.verdict}"
        for outcome in outcomes
    ]
    cleared = sum(outcome.verdict == "cleared" for outcome in outcomes)
    rejected = sum(outcome.verdict.startswith("rejected") for outcome in outcomes)
    unready = sum(not outcome.ready for outcome in outcomes)
    lines.append(
        f"\n{len(outcomes)} problems: {cleared} cleared, {rejected} rejected, "
        f"{unready} need another setup run."
    )
    return "\n".join(lines)


def _answers(classification: dict) -> dict[str, str]:
    """Each answered question's answer; an out-of-scope task answers only `task_nature`."""
    answers = {}
    for key in CLASSIFICATION_LABELS:
        if key in classification:
            answer = classification[key]["answer"]
            answers[key] = ", ".join(answer) if isinstance(answer, list) else answer
    return answers


def write_report(output: OutputPaths, setup_file: Path, outcomes: list[Outcome]) -> Path:
    """Rewrite the setup file's summary in `<output>/setup/`, and return its path."""
    path = output.setup / f"{setup_file.stem}.md"
    path.parent.mkdir(parents=True, exist_ok=True)

    def link(text: str, target: Path | None) -> str:
        if target is None:
            return _cell(text)
        return f"[{_cell(text)}]({Path('..') / target.relative_to(output.root)})"

    cleared = sum(outcome.verdict == "cleared" for outcome in outcomes)
    rejected = sum(outcome.verdict.startswith("rejected") for outcome in outcomes)
    unready = sum(not outcome.ready for outcome in outcomes)
    lines = [
        f"# Setup: {setup_file.name}",
        "",
        f"**{len(outcomes)}** problems: **{cleared}** cleared, **{rejected}** rejected, "
        f"**{unready}** need another setup run.",
        "",
        "| Problem | Preflight | Fix scope | Target | Classification | "
        + " | ".join(CLASSIFICATION_LABELS.values())
        + " | Verdict |",
        "|---" * (6 + len(CLASSIFICATION_LABELS)) + "|",
    ]
    for outcome in outcomes:
        target = "<br>".join(_cell(line.removeprefix("- ")) for line in outcome.target.splitlines())
        answers = " | ".join(outcome.answers.get(key, "") for key in CLASSIFICATION_LABELS)
        lines.append(
            f"| {outcome.instance_id} "
            f"| {link(outcome.preflight, outcome.preflight_dir)} "
            f"| {outcome.scope} | {target} "
            f"| {link(outcome.classification, outcome.attempt_dir)} "
            f"| {answers} | {_cell(outcome.verdict)} |"
        )
    path.write_text("\n".join(lines) + "\n")
    return path


def _cell(text: str, limit: int = 300) -> str:
    """Text that fits in one Markdown table cell."""
    text = " ".join(text.split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"
