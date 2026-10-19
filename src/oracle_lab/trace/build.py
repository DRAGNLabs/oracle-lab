"""Turn one saved run into the `RunTrace` the page shows."""

from __future__ import annotations

from dataclasses import dataclass

from oracle_lab.harnesses.steps import Step
from oracle_lab.paths import RunPaths
from oracle_lab.trace.events import Event, RunSummary, RunTrace
from oracle_lab.trace.facts import PatchedRegions, Repository


@dataclass(frozen=True)
class RunFacts:
    """The few run details the viewer needs from the config and evaluation results."""

    summary: RunSummary
    repository: Repository
    outcomes: dict[str, str] | None  # matrix cell by test id; None when not evaluated


def read_run_facts(paths: RunPaths) -> RunFacts:
    """Read run details loosely, from raw YAML and JSON, without the artifact contracts.

    DEMO ONLY. Most demo runs predate the current `RunConfig` and `EvaluationResult`
    contracts and fail strict validation in fields this viewer does not use. When the
    viewer joins the pipeline, delete this and use `load_config(..., resolved=True)`
    and `read_evaluation_result` instead. See `docs/features/trace-viewer/decisions.md`.
    """
    raise NotImplementedError


def build_run_trace(paths: RunPaths) -> RunTrace:
    """Read the run's trace, golden patch, target, submission, and outcomes, and annotate."""
    raise NotImplementedError


def to_events(steps: list[Step], repository: Repository, regions: PatchedRegions) -> list[Event]:
    """One event per step, or per sub-command for chained shell commands."""
    raise NotImplementedError
