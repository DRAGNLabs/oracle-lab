"""The exported tables' rows, read from saved runs and attempts through their contracts.

A file that exists must validate. A file a stopped run never wrote leaves its
columns null. A run or attempt that cannot be read is skipped and named, never
half-exported.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from oracle_lab.config import load_config
from oracle_lab.contracts.base import ArtifactModel
from oracle_lab.contracts.classification import (
    CLASSIFICATION_LABELS,
    read_saved_classification,
)
from oracle_lab.contracts.export import (
    TABLES,
    ClassificationRow,
    FailureModeRow,
    GeneratedTestRow,
    JudgmentRow,
    RaterFailureModeRow,
    ReviewRow,
    RunRow,
    SkippedTarget,
    TraceStepRow,
)
from oracle_lab.contracts.judgment import (
    FAILURE_MODES,
    JUDGMENT_LABELS,
    CompletedJudgment,
    PresentFailureMode,
    validate_judgment,
)
from oracle_lab.contracts.run import (
    MATRIX_CELLS,
    read_agent_attempt,
    read_evaluation_result,
    read_instance_record,
    read_run_cost,
    read_status,
    read_submission_manifest,
)
from oracle_lab.io import digest, read_json
from oracle_lab.paths import AttemptPaths, OutputPaths, RunPaths
from oracle_lab.review.store import read_all_reviews, read_provenance
from oracle_lab.review.targets import ClassificationTarget, JudgmentTarget, ReviewTarget
from oracle_lab.trace.events import read_run_trace

T = TypeVar("T")
_TABLE_OF = {model: name for name, model in TABLES.items()}


@dataclass
class Collected:
    """Every row of every table, and which runs and attempts they came from."""

    tables: dict[str, list[ArtifactModel]] = field(
        default_factory=lambda: {name: [] for name in TABLES}
    )
    runs: list[RunPaths] = field(default_factory=list)
    attempts: list[AttemptPaths] = field(default_factory=list)
    skipped: list[SkippedTarget] = field(default_factory=list)

    def add(self, rows: list[ArtifactModel]) -> None:
        for row in rows:
            self.tables[_TABLE_OF[type(row)]].append(row)


def collect(root: Path) -> Collected:
    """Rows for every run and classification attempt under the output root `root`."""
    output = OutputPaths(root)
    collected = Collected()
    for run in output.saved_runs():
        try:
            collected.add(run_rows(run))
            collected.runs.append(run)
        except (OSError, ValueError, KeyError) as error:
            collected.skipped.append(_skipped(root, run.root, error))
    for attempt in output.saved_attempts():
        try:
            collected.add(attempt_rows(attempt))
            collected.attempts.append(attempt)
        except (OSError, ValueError, KeyError) as error:
            collected.skipped.append(_skipped(root, attempt.root, error))
    if not collected.runs and not collected.attempts and not collected.skipped:
        raise ValueError(f"No runs or classification attempts under {root}")
    return collected


def run_rows(paths: RunPaths) -> list[ArtifactModel]:
    """Every row one run contributes: the run, its tests, its judgment, and its reviews."""
    config = load_config(paths.config, resolved=True)
    status = read_status(paths.status)
    instance = _optional(read_instance_record, paths.instance)
    run_id = paths.root.name
    instance_id = instance.instance_id if instance else config.source.instance
    cost = read_run_cost(paths.cost)
    generation = _optional(read_agent_attempt, paths.generation / "result.json")
    usage = generation.usage if generation else None
    manifest = _optional(read_submission_manifest, paths.submission / "manifest.json")
    evaluation = _optional(read_evaluation_result, paths.results)
    judgment = _optional(lambda path: validate_judgment(read_json(path)), paths.judge.judgment)

    rows: list[ArtifactModel] = [
        RunRow(
            run_id=run_id,
            instance_id=instance_id,
            repo=instance.repo if instance else None,
            base_commit=instance.base_commit if instance else None,
            source_kind=config.source.kind,
            dataset=config.source.dataset,
            agent_harness=config.agent.harness,
            agent_provider=config.agent.provider,
            agent_model=config.agent.model,
            prompt=Path(config.task.prompt).name,
            task_scope=config.task.scope,
            existing_tests=config.task.existing_tests,
            generated_dir=config.task.generated_dir,
            stage=status.stage,
            state=status.state,
            error=status.error,
            cost_status=cost.status if cost else None,
            cost_usd=cost.cost_usd if cost else None,
            generation_status=generation.status if generation else None,
            generation_seconds=generation.duration_seconds if generation else None,
            input_tokens=usage.input_tokens if usage else None,
            cached_input_tokens=usage.cached_input_tokens if usage else None,
            output_tokens=usage.output_tokens if usage else None,
            reasoning_tokens=usage.reasoning_tokens if usage else None,
            test_files=len(manifest.files) if manifest else None,
            forbidden_changes=manifest.forbidden_changes if manifest else None,
            compliant=manifest.compliant if manifest else None,
            buggy_status=evaluation.buggy_status if evaluation else None,
            golden_status=evaluation.golden_status if evaluation else None,
            detected=evaluation.detected if evaluation else None,
            **{
                cell.value: evaluation.matrix[cell].count if evaluation else None
                for cell in MATRIX_CELLS
            },
            judge_status=(
                judgment.status
                if judgment
                else "missing"
                if paths.judge.rubric.is_file()
                else "disabled"
            ),
        )
    ]
    if evaluation:
        rows += [
            GeneratedTestRow(
                run_id=run_id,
                instance_id=instance_id,
                test_id=outcome.test_id,
                buggy=outcome.buggy,
                golden=outcome.golden,
                cell=outcome.cell.value if outcome.cell else None,
            )
            for outcome in [*evaluation.tests, *evaluation.other_outcomes]
        ]
    if judgment:
        judge = _optional(read_agent_attempt, paths.judge.result)
        rows.append(
            JudgmentRow(
                run_id=run_id,
                instance_id=instance_id,
                status=judgment.status,
                judge_harness=judge.harness if judge else None,
                judge_model=judge.model if judge else None,
                rubric_sha256=_sha256(paths.judge.rubric),
                error=getattr(judgment, "error", None) or getattr(judgment, "reason", None),
                **_answers(judgment, JUDGMENT_LABELS),
            )
        )
    if isinstance(judgment, CompletedJudgment):
        rows += _failure_mode_rows(run_id, instance_id, judgment)
        rows += review_rows(JudgmentTarget.load(paths.root))
    if paths.judge.trace.is_file():
        rows += [
            TraceStepRow(
                run_id=run_id,
                instance_id=instance_id,
                step=event.step,
                part=event.part,
                action=event.action.value,
                description=event.description,
                command=event.command,
                output=event.output,
                exit_code=event.exit_code,
                text=event.text,
                timestamp=event.timestamp,
            )
            for event in read_run_trace(paths.judge.trace).events
        ]
    return rows


def attempt_rows(paths: AttemptPaths) -> list[ArtifactModel]:
    """Every row one classification attempt contributes: its answer and its reviews.

    An attempt that never saved a classification has nothing to export.
    """
    if not paths.judge.classification.is_file():
        raise ValueError("The attempt has no classification")
    saved = read_saved_classification(paths.judge.classification)
    classifier = _optional(read_agent_attempt, paths.judge.result)
    result = saved.result
    rows: list[ArtifactModel] = [
        ClassificationRow(
            attempt_id=paths.root.name,
            instance_id=saved.instance_id,
            base_commit=saved.base_commit,
            status=result.status,
            classifier_harness=classifier.harness if classifier else None,
            classifier_model=classifier.model if classifier else None,
            rubric_sha256=saved.rubric_sha256,
            error=getattr(result, "error", None),
            **_answers(result, CLASSIFICATION_LABELS),
        )
    ]
    if result.status == "completed":
        rows += review_rows(ClassificationTarget.load(paths.root))
    return rows


def review_rows(target: ReviewTarget) -> list[ArtifactModel]:
    """Every rater's current review of the target: one row per rated item, and one
    per failure mode a rater added."""
    rows: list[ArtifactModel] = []
    for rater, review in read_all_reviews(target).items():
        provenance = read_provenance(target, rater)
        identity = {
            "stage": target.stage,
            "target_id": target.root.name,
            "instance_id": target.instance.instance_id,
            "rater": rater,
        }
        rows += [
            ReviewRow(
                **identity,
                item=item,
                rating=rating,
                reviewed_output_sha256=provenance.reviewed_output_sha256,
                judge_model=provenance.judge_model,
                stage_rubric_sha256=provenance.stage_rubric_sha256,
            )
            for item, rating in review.ratings.items()
        ]
        rows += [
            RaterFailureModeRow(
                **identity, name=mode.name, description=mode.description, steps=mode.steps
            )
            for mode in review.other_failure_modes
        ]
    return rows


def _failure_mode_rows(
    run_id: str, instance_id: str, judgment: CompletedJudgment
) -> list[ArtifactModel]:
    """Each failure mode under the item key its reviews use; see `rating_keys`."""
    rows: list[ArtifactModel] = []
    for key, title in FAILURE_MODES.items():
        decision = judgment.failure_modes[key]
        present = isinstance(decision, PresentFailureMode)
        rows.append(
            FailureModeRow(
                run_id=run_id,
                instance_id=instance_id,
                item=f"failure_modes.{key}",
                source="start_list",
                name=title,
                definition=None,
                present=present,
                steps=decision.steps if present else [],
                justification=decision.justification if present else None,
            )
        )
    for index, added in enumerate(judgment.added_failure_modes):
        rows.append(
            FailureModeRow(
                run_id=run_id,
                instance_id=instance_id,
                item=f"added_failure_modes.{index}",
                source="judge_added",
                name=added.name,
                definition=added.definition,
                present=True,
                steps=added.steps,
                justification=added.justification,
            )
        )
    return rows


def _answers(result: object, labels: dict[str, str]) -> dict:
    """Each question's answer and justification, null where the result has none."""
    answered = {key: getattr(result, key, None) for key in labels}
    return {
        **{key: value.answer if value else None for key, value in answered.items()},
        **{
            f"{key}_justification": value.justification if value else None
            for key, value in answered.items()
        },
    }


def _optional(read: Callable[[Path], T], path: Path) -> T | None:
    return read(path) if path.is_file() else None


def _sha256(path: Path) -> str | None:
    return digest(path.read_bytes()) if path.is_file() else None


def _skipped(root: Path, path: Path, error: Exception) -> SkippedTarget:
    return SkippedTarget(path=path.relative_to(root).as_posix(), error=str(error))
