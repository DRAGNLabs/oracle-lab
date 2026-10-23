"""Stable paths for everything Oracle Lab writes, under one output root."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


def new_id() -> str:
    """A run, batch, or attempt ID: its UTC start time, then a random suffix."""
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class OutputPaths:
    """The output root: every command writes into its own directory here."""

    root: Path

    @property
    def runs(self) -> Path:
        return self.root / "runs"

    @property
    def preflight(self) -> Path:
        return self.root / "preflight"

    @property
    def batches(self) -> Path:
        return self.root / "batches"

    @property
    def classifications(self) -> Path:
        return self.root / "classifications"

    @property
    def agreement(self) -> Path:
        return self.root / "agreement"

    def new_run(self) -> RunPaths:
        return RunPaths.create(_new_directory(self.runs))

    def new_preflight(self) -> RunPaths:
        return RunPaths.create(_new_directory(self.preflight))

    def new_batch(self) -> Path:
        return _new_directory(self.batches)

    def new_attempt(self, instance_id: str) -> AttemptPaths:
        return AttemptPaths.create(_new_directory(self.classifications / instance_id))

    def saved_runs(self) -> list[RunPaths]:
        """Every run directory under `runs/`, in ID order."""
        return [RunPaths(run) for run in _subdirectories(self.runs)]

    def saved_attempts(self) -> list[AttemptPaths]:
        """Every classification attempt, by instance ID and then attempt ID."""
        return [
            AttemptPaths(attempt)
            for instance in _subdirectories(self.classifications)
            for attempt in _subdirectories(instance)
        ]


def _new_directory(parent: Path) -> Path:
    directory = parent / new_id()
    directory.mkdir(parents=True)
    return directory


def _subdirectories(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(child for child in path.iterdir() if child.is_dir())


@dataclass(frozen=True)
class RunPaths:
    """Resolve lifecycle-oriented paths for one benchmark run."""

    root: Path

    @classmethod
    def create(cls, root: Path) -> "RunPaths":
        paths = cls(root)
        for directory in (
            paths.inputs,
            paths.build,
            paths.reference,
            paths.generation,
            paths.submission,
            paths.evaluation,
            paths.ground_truth,
            paths.judge.root,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        return paths

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def build(self) -> Path:
        return self.root / "image-build"

    @property
    def reference(self) -> Path:
        return self.root / "reference-check"

    @property
    def environment(self) -> Path:
        return self.root / "environment-check"

    @property
    def generation(self) -> Path:
        return self.root / "generation"

    @property
    def submission(self) -> Path:
        return self.root / "submission"

    @property
    def evaluation(self) -> Path:
        return self.root / "evaluation"

    @property
    def config(self) -> Path:
        return self.inputs / "config.resolved.yaml"

    @property
    def instance(self) -> Path:
        return self.inputs / "instance.json"

    @property
    def prompt(self) -> Path:
        return self.inputs / "prompt.txt"

    @property
    def target(self) -> Path:
        return self.inputs / "target.json"

    @property
    def target_log(self) -> Path:
        return self.inputs / "target.log"

    @property
    def runtime(self) -> Path:
        return self.build / "runtime.json"

    @property
    def results(self) -> Path:
        return self.evaluation / "results.json"

    @property
    def report(self) -> Path:
        return self.root / "report.md"

    @property
    def status(self) -> Path:
        return self.root / "status.json"

    @property
    def cost(self) -> Path:
        return self.root / "cost.json"

    @property
    def evaluation_history(self) -> Path:
        return self.root / "evaluation-history"

    @property
    def generation_history(self) -> Path:
        return self.root / "generation-history"

    @property
    def ground_truth(self) -> Path:
        return self.root / "ground-truth"

    @property
    def judge(self) -> JudgmentPaths:
        return JudgmentPaths(self.root / "judge")

    @property
    def output(self) -> OutputPaths:
        """The output root the run is saved in, as `<output>/runs/<run-id>`."""
        return OutputPaths(self.root.parent.parent)


@dataclass(frozen=True)
class AttemptPaths:
    """One task-classification attempt."""

    root: Path

    @classmethod
    def create(cls, root: Path) -> AttemptPaths:
        paths = cls(root)
        for directory in (paths.inputs, paths.build, paths.judge.agent):
            directory.mkdir(parents=True, exist_ok=True)
        return paths

    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def build(self) -> Path:
        return self.root / "image-build"

    @property
    def config(self) -> Path:
        return self.inputs / "config.resolved.yaml"

    @property
    def instance(self) -> Path:
        return self.inputs / "instance.json"

    @property
    def runtime(self) -> Path:
        return self.build / "runtime.json"

    @property
    def status(self) -> Path:
        return self.root / "status.json"

    @property
    def cost(self) -> Path:
        return self.root / "cost.json"

    @property
    def judge(self) -> ClassifierPaths:
        return ClassifierPaths(self.root / "judge")


@dataclass(frozen=True)
class JudgePaths:
    """An LLM judge's directory: its rubric, workspace, turn, and the human reviews of
    its output. The generated-test judge and the task classifier share this shape."""

    root: Path

    @property
    def rubric(self) -> Path:
        return self.root / "rubric.md"

    @property
    def log(self) -> Path:
        return self.root / "workspace.log"

    @property
    def workspace_spec(self) -> Path:
        return self.root / "workspace-spec.json"

    @property
    def agent(self) -> Path:
        return self.root / "agent"

    @property
    def result(self) -> Path:
        return self.agent / "result.json"

    @property
    def final(self) -> Path:
        return self.agent / "final.txt"

    @property
    def agent_history(self) -> Path:
        """Earlier turns retried after a transient failure."""
        return self.root / "agent-history"

    @property
    def reviews(self) -> Path:
        return self.root / "reviews"

    @property
    def review_history(self) -> Path:
        return self.root / "reviews-history"


@dataclass(frozen=True)
class JudgmentPaths(JudgePaths):
    """The generated-test judge's directory in a run."""

    @property
    def history(self) -> Path:
        """Archived judge attempts, beside the current one."""
        return self.root.with_name("judge-history")

    @property
    def prompt(self) -> Path:
        return self.root / "prompt.md"

    @property
    def bundle_manifest(self) -> Path:
        return self.root / "bundle-manifest.json"

    @property
    def image(self) -> Path:
        return self.root / "image.json"

    @property
    def trace(self) -> Path:
        """The agent's trace as built when the judge's workspace was, for the judge and raters."""
        return self.root / "trace.json"

    @property
    def judgment(self) -> Path:
        return self.root / "judgment.json"

    @property
    def judgment_raw(self) -> Path:
        return self.root / "judgment.raw.txt"


@dataclass(frozen=True)
class ClassifierPaths(JudgePaths):
    """The task classifier's directory in a classification attempt."""

    @property
    def classification(self) -> Path:
        return self.root / "classification.json"

    @property
    def classification_raw(self) -> Path:
        return self.root / "classification.raw.txt"
