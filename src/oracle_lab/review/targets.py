"""What a human review covers: one LLM judgment of a run, or one task classification.

Each kind of target knows everything particular to its stage: the questions its
judge could answer, the repository views and evidence files the judge read, and
where its files live. Raters work through targets one problem at a time: a
`Problem` is one task instance and its targets, in the order they are rated.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import ClassVar, NamedTuple, Self

from oracle_lab.classification.contracts import (
    CLASSIFICATION_LABELS,
    InScopeClassification,
    OutOfScopeClassification,
    read_saved_classification,
)
from oracle_lab.classification.workspace import CLASSIFICATION_ROOT, classification_evidence
from oracle_lab.config import RunConfig, load_config, repo_path
from oracle_lab.io import read_json
from oracle_lab.judge.contracts import JUDGMENT_LABELS, CompletedJudgment, validate_judgment
from oracle_lab.judge.workspace import JUDGE_ROOT, judge_evidence
from oracle_lab.paths import RunPaths
from oracle_lab.results import (
    EvaluationResult,
    InstanceRecord,
    SubmissionManifest,
    read_evaluation_result,
    read_instance_record,
    read_submission_manifest,
)
from oracle_lab.review.contracts import Stage


class TargetError(ValueError):
    """A directory that cannot be reviewed, and why."""


class SubmittedFile(NamedTuple):
    """A generated test file, placed in the judge's repository views."""

    content: bytes
    mode: int


@dataclass(frozen=True)
class ReviewTarget(ABC):
    """One completed LLM output that raters review, and everything its judge saw."""

    root: Path  # the run directory or the classification attempt
    instance: InstanceRecord
    answers: CompletedJudgment | InScopeClassification | OutOfScopeClassification

    stage: ClassVar[Stage]
    labels: ClassVar[dict[str, str]]  # every question the judge could answer
    views: ClassVar[tuple[str, ...]]  # the repository versions the judge read
    workspace: ClassVar[str]  # where the judge's evidence sat in its container

    @classmethod
    @abstractmethod
    def load(cls, root: Path) -> Self:
        """The target saved at `root`. Only a completed LLM output can be reviewed."""

    @property
    @abstractmethod
    def reviewed_output(self) -> Path: ...

    @property
    @abstractmethod
    def stage_rubric(self) -> Path: ...

    @property
    @abstractmethod
    def workspace_spec(self) -> Path: ...

    @property
    @abstractmethod
    def agent_result(self) -> Path:
        """The judge turn's result, which names its harness and model."""

    @property
    @abstractmethod
    def reviews(self) -> Path: ...

    @property
    def review_history(self) -> Path:
        return self.reviews.with_name("reviews-history")

    @abstractmethod
    def issue(self) -> str:
        """The issue text the judge read."""

    @abstractmethod
    def submitted_files(self) -> dict[str, SubmittedFile]:
        """Generated test files added to the judge's repository views."""

    def rating_keys(self) -> list[str]:
        """Every item a rater rates, in rail order: the questions the LLM answered (a
        `null` answer is not reviewed), then for a judgment each start-list failure
        mode as `failure_modes.<key>` and each added one as `added_failure_modes.<index>`."""
        raise NotImplementedError

    def rating_label(self, key: str) -> str:
        """What the rail and the agreement report call one rating item."""
        raise NotImplementedError

    def evidence(self) -> dict[str, bytes]:
        """The files the judge read besides the repository views, by path in its workspace.

        Files built for the judge rather than copied from the run, such as its
        instructions, are rebuilt by the current code.
        """
        with TemporaryDirectory(prefix="oracle-review-evidence-") as staging:
            return {
                destination.removeprefix(self.workspace + "/"): _read(source)
                for source, destination in self._evidence_table(Path(staging))
            }

    @abstractmethod
    def _evidence_table(self, staging: Path) -> list[tuple[Path, str]]:
        """The judge's own exposure table: each file and where the judge saw it."""


class JudgmentTarget(ReviewTarget):
    """The generated-test judge's judgment of one run."""

    stage = "judge"
    labels = JUDGMENT_LABELS
    views = ("buggy", "golden")
    workspace = JUDGE_ROOT

    @classmethod
    def load(cls, root: Path) -> Self:
        paths = RunPaths.open(root)
        if not paths.judge.judgment.is_file():
            raise TargetError("The run has no judgment")
        answers = validate_judgment(read_json(paths.judge.judgment))
        if not isinstance(answers, CompletedJudgment):
            raise TargetError(f"The judgment is {answers.status}, not completed")
        load_config(paths.config, resolved=True)  # the page labels each run from it
        return cls(root, read_instance_record(paths.instance), answers)

    @property
    def paths(self) -> RunPaths:
        return RunPaths.open(self.root)

    @property
    def reviewed_output(self) -> Path:
        return self.paths.judge.judgment

    @property
    def stage_rubric(self) -> Path:
        return self.paths.judge.rubric

    @property
    def workspace_spec(self) -> Path:
        return self.paths.judge.workspace_spec

    @property
    def agent_result(self) -> Path:
        return self.paths.judge.result

    @property
    def reviews(self) -> Path:
        return self.paths.judge.reviews

    def issue(self) -> str:
        return (self.paths.ground_truth / "issue.md").read_text()

    def config(self) -> RunConfig:
        """The run's resolved configuration, which names its harness, model, and scope."""
        return load_config(self.paths.config, resolved=True)

    def evaluation(self) -> EvaluationResult:
        return read_evaluation_result(self.paths.results)

    def manifest(self) -> SubmissionManifest:
        return read_submission_manifest(self.paths.submission / "manifest.json")

    def submitted_files(self) -> dict[str, SubmittedFile]:
        files = self.paths.submission / "files"
        return {
            name: SubmittedFile((files / repo_path(name)).read_bytes(), info.mode)
            for name, info in self.manifest().files.items()
        }

    def _evidence_table(self, staging: Path) -> list[tuple[Path, str]]:
        return judge_evidence(
            self.paths, staging, self.instance, self.evaluation(), self.manifest()
        )


class ClassificationTarget(ReviewTarget):
    """The task classifier's classification of one task, in one attempt."""

    stage = "classification"
    labels = CLASSIFICATION_LABELS
    views = ("buggy",)
    workspace = CLASSIFICATION_ROOT

    @classmethod
    def load(cls, root: Path) -> Self:
        output = root / "classification.json"
        if not output.is_file():
            raise TargetError("The attempt has no classification")
        saved = read_saved_classification(output)
        if saved.result.status != "completed":
            raise TargetError(f"The classification is {saved.result.status}, not completed")
        instance = read_instance_record(root / "inputs/instance.json")
        if saved.instance_id != instance.instance_id:
            raise TargetError(
                f"The classification is for {saved.instance_id}, not {instance.instance_id}"
            )
        return cls(root, instance, saved.result)

    @property
    def reviewed_output(self) -> Path:
        return self.root / "classification.json"

    @property
    def stage_rubric(self) -> Path:
        return self.root / "inputs/rubric.md"

    @property
    def workspace_spec(self) -> Path:
        return self.root / "workspace-spec.json"

    @property
    def agent_result(self) -> Path:
        return self.root / "agent/result.json"

    @property
    def reviews(self) -> Path:
        return self.root / "reviews"

    def issue(self) -> str:
        return self.instance.original_record.get("problem_statement", "")

    def submitted_files(self) -> dict[str, SubmittedFile]:
        return {}  # The classifier read the plain buggy checkout.

    def _evidence_table(self, staging: Path) -> list[tuple[Path, str]]:
        return classification_evidence(self.root)


TARGET_KINDS: tuple[type[ReviewTarget], ...] = (JudgmentTarget, ClassificationTarget)


@dataclass(frozen=True)
class UnreviewableTarget:
    """A run or attempt found while searching that cannot be reviewed, and why."""

    stage: Stage
    root: Path
    reason: str


@dataclass(frozen=True)
class Problem:
    """One task instance and every target that rates something about it."""

    instance_id: str
    classifications: list[ClassificationTarget]  # ordered by attempt directory
    judgments: list[JudgmentTarget]  # ordered by run ID, which starts with a timestamp

    @property
    def tasks(self) -> list[ReviewTarget]:
        """The targets in the order a rater works through them: classification first."""
        return [*self.classifications, *self.judgments]

    @property
    def instance(self) -> InstanceRecord:
        """The task record, from the first task; every target copies the same dataset record."""
        return self.tasks[0].instance

    def issue(self) -> str:
        return self.tasks[0].issue()


def find_problems(targets: list[ReviewTarget]) -> list[Problem]:
    """The targets grouped by task instance, ordered by instance ID."""
    by_instance: dict[str, list[ReviewTarget]] = {}
    for target in sorted(targets, key=lambda target: target.root):
        by_instance.setdefault(target.instance.instance_id, []).append(target)
    return [
        Problem(
            instance_id,
            [target for target in members if isinstance(target, ClassificationTarget)],
            [target for target in members if isinstance(target, JudgmentTarget)],
        )
        for instance_id, members in sorted(by_instance.items())
    ]


def find_targets(path: Path) -> tuple[list[ReviewTarget], list[UnreviewableTarget]]:
    """Every review target under `path`, and every candidate that cannot be reviewed.

    `path` is a run, a classification attempt, or a directory holding `runs/<run>/`
    and `classifications/<instance>/<attempt>/`, such as the review data repository.
    """
    if _is_run(path):
        candidates: list[tuple[type[ReviewTarget], Path]] = [(JudgmentTarget, path)]
    elif _is_attempt(path):
        candidates = [(ClassificationTarget, path)]
    else:
        candidates = [(JudgmentTarget, run) for run in _directories(path / "runs")]
        candidates += [
            (ClassificationTarget, attempt)
            for instance in _directories(path / "classifications")
            for attempt in _directories(instance)
        ]
    if not candidates:
        raise TargetError(f"No runs or classification attempts under {path}")

    targets: list[ReviewTarget] = []
    unreviewable: list[UnreviewableTarget] = []
    for kind, root in candidates:
        try:
            targets.append(kind.load(root))
        except (OSError, ValueError) as error:
            unreviewable.append(UnreviewableTarget(kind.stage, root, str(error)))
    return targets, unreviewable


def _read(source: Path) -> bytes:
    if not source.is_file():
        raise TargetError(f"Evidence file is missing: {source.name}")
    return source.read_bytes()


def _is_run(path: Path) -> bool:
    return (path / "inputs/config.resolved.yaml").is_file() and (path / "judge").is_dir()


def _is_attempt(path: Path) -> bool:
    return (path / "classification.json").is_file()


def _directories(path: Path) -> list[Path]:
    if not path.is_dir():
        return []
    return sorted(child for child in path.iterdir() if child.is_dir())
