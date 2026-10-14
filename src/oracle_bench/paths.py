"""Stable, lifecycle-oriented paths for run artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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

    @classmethod
    def open(cls, root: Path) -> "RunPaths":
        return cls(root)

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
    def judge(self) -> "JudgePaths":
        return JudgePaths(self.root / "judge", self.root / "judge-history")


@dataclass(frozen=True)
class HumanJudgePaths:
    """Blinded human ratings, kept beside the automated attempt they replicate."""

    judge_root: Path

    @property
    def judgments(self) -> Path:
        return self.judge_root / "human"

    @property
    def history(self) -> Path:
        return self.judge_root / "human-history"

    @property
    def workspaces(self) -> Path:
        return self.judge_root / "human-workspaces"

    @property
    def log(self) -> Path:
        return self.judge_root / "human-workspace.log"


@dataclass(frozen=True)
class JudgePaths:
    """The optional judge stage's artifact tree.

    ``root`` holds the current attempt; ``history`` holds archived ones. Human
    ratings live under ``human`` and survive automated reruns.
    """

    root: Path
    history: Path

    @property
    def rubric(self) -> Path:
        return self.root / "rubric.md"

    @property
    def instructions(self) -> Path:
        return self.root / "instructions.md"

    @property
    def prompt(self) -> Path:
        return self.root / "prompt.md"

    @property
    def log(self) -> Path:
        return self.root / "workspace.log"

    @property
    def workspace_spec(self) -> Path:
        return self.root / "workspace-spec.json"

    @property
    def bundle_manifest(self) -> Path:
        return self.root / "bundle-manifest.json"

    @property
    def image(self) -> Path:
        return self.root / "image.json"

    @property
    def judgment(self) -> Path:
        return self.root / "judgment.json"

    @property
    def judgment_raw(self) -> Path:
        return self.root / "judgment.raw.txt"

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
    def human(self) -> HumanJudgePaths:
        return HumanJudgePaths(self.root)
