"""What a human review covers: one LLM judgment of a run, or one task classification."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from oracle_lab.classification.contracts import (
    CLASSIFICATION_LABELS,
    InScopeClassification,
    OutOfScopeClassification,
    read_saved_classification,
)
from oracle_lab.classification.run import CLASSIFICATION_ROOT, classification_evidence
from oracle_lab.io import read_json
from oracle_lab.judge.contracts import JUDGMENT_LABEL_KEYS, CompletedJudgment, validate_judgment
from oracle_lab.judge.workspace import JUDGE_ROOT, judge_evidence
from oracle_lab.paths import RunPaths
from oracle_lab.results import (
    InstanceRecord,
    read_evaluation_result,
    read_instance_record,
    read_submission_manifest,
)

Stage = Literal["judge", "classification"]
Answers = CompletedJudgment | InScopeClassification | OutOfScopeClassification


class TargetError(ValueError):
    """A directory that cannot be reviewed, and why."""


@dataclass(frozen=True)
class EvidenceFile:
    """One file the LLM judge read, named by its path in the judge's workspace."""

    name: str
    content: bytes


@dataclass(frozen=True)
class ReviewTarget:
    """One completed LLM output that raters review, and everything its judge saw."""

    stage: Stage
    root: Path  # the run directory or the classification attempt
    reviewed_output: Path
    stage_rubric: Path
    workspace_spec: Path
    reviews: Path
    instance: InstanceRecord
    answers: Answers
    # The repository views the judge read: buggy and golden for a judgment, buggy
    # only for a classification.
    views: tuple[str, ...]

    @property
    def repo(self) -> str:
        return self.instance.repo

    @property
    def base_commit(self) -> str:
        return self.instance.base_commit

    def answered_questions(self) -> list[str]:
        """The questions the LLM answered. A `null` answer is not reviewed."""
        keys = JUDGMENT_LABEL_KEYS if self.stage == "judge" else tuple(CLASSIFICATION_LABELS)
        return [key for key in keys if getattr(self.answers, key, None) is not None]

    def evidence(self) -> list[EvidenceFile]:
        """The files the judge read besides the repository views.

        Files built for the judge rather than copied from the run, such as its
        instructions, are rebuilt by the current code.
        """
        with TemporaryDirectory(prefix="oracle-review-evidence-") as staging:
            if self.stage == "judge":
                paths = RunPaths.open(self.root)
                manifest = read_submission_manifest(paths.submission / "manifest.json")
                evaluation = read_evaluation_result(paths.results)
                rows = judge_evidence(paths, Path(staging), self.instance, evaluation, manifest)
                workspace = JUDGE_ROOT
            else:
                rows = classification_evidence(self.root)
                workspace = CLASSIFICATION_ROOT
            return [_read(source, destination, workspace) for source, destination in rows]


@dataclass(frozen=True)
class SkippedTarget:
    """A run or attempt found while searching that cannot be reviewed."""

    stage: Stage
    root: Path
    reason: str


def judge_target(paths: RunPaths) -> ReviewTarget:
    """The LLM judgment of one run. Only a completed judgment can be reviewed."""
    if not paths.judge.judgment.is_file():
        raise TargetError("The run has no judgment")
    answers = validate_judgment(read_json(paths.judge.judgment))
    if not isinstance(answers, CompletedJudgment):
        raise TargetError(f"The judgment is {answers.status}, not completed")
    return ReviewTarget(
        stage="judge",
        root=paths.root,
        reviewed_output=paths.judge.judgment,
        stage_rubric=paths.judge.rubric,
        workspace_spec=paths.judge.workspace_spec,
        reviews=paths.judge.reviews,
        instance=read_instance_record(paths.instance),
        answers=answers,
        views=("buggy", "golden"),
    )


def classification_target(attempt: Path) -> ReviewTarget:
    """One classification attempt. Only a completed classification can be reviewed."""
    output = attempt / "classification.json"
    if not output.is_file():
        raise TargetError("The attempt has no classification")
    saved = read_saved_classification(output)
    if saved.result.status != "completed":
        raise TargetError(f"The classification is {saved.result.status}, not completed")
    instance = read_instance_record(attempt / "inputs/instance.json")
    if saved.instance_id != instance.instance_id:
        raise TargetError(
            f"The classification is for {saved.instance_id}, not {instance.instance_id}"
        )
    return ReviewTarget(
        stage="classification",
        root=attempt,
        reviewed_output=output,
        stage_rubric=attempt / "inputs/rubric.md",
        workspace_spec=attempt / "workspace-spec.json",
        reviews=attempt / "reviews",
        instance=instance,
        answers=saved.result,
        views=("buggy",),
    )


def find_targets(path: Path) -> tuple[list[ReviewTarget], list[SkippedTarget]]:
    """Every review target under `path`, and every candidate that cannot be reviewed.

    `path` is a run, a classification attempt, or a directory holding `runs/<run>/`
    and `classifications/<instance>/<attempt>/`, such as the review data repository.
    """
    if _is_run(path):
        candidates: list[tuple[Stage, Path]] = [("judge", path)]
    elif _is_attempt(path):
        candidates = [("classification", path)]
    else:
        candidates = [("judge", run) for run in _directories(path / "runs")]
        candidates += [
            ("classification", attempt)
            for instance in _directories(path / "classifications")
            for attempt in _directories(instance)
        ]
    if not candidates:
        raise TargetError(f"No runs or classification attempts under {path}")

    targets: list[ReviewTarget] = []
    skipped: list[SkippedTarget] = []
    for stage, root in candidates:
        try:
            if stage == "judge":
                targets.append(judge_target(RunPaths.open(root)))
            else:
                targets.append(classification_target(root))
        except (OSError, ValueError) as error:
            skipped.append(SkippedTarget(stage, root, str(error)))
    return targets, skipped


def _read(source: Path, destination: str, workspace: str) -> EvidenceFile:
    if not source.is_file():
        raise TargetError(f"Evidence file is missing: {source.name}")
    return EvidenceFile(destination.removeprefix(workspace + "/"), source.read_bytes())


def _is_run(path: Path) -> bool:
    return (path / "inputs/config.resolved.yaml").is_file() and (path / "judge").is_dir()


def _is_attempt(path: Path) -> bool:
    return (path / "classification.json").is_file()


def _directories(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(child for child in path.iterdir() if child.is_dir())
