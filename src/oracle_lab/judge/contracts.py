"""Strict contracts for generated-test judgments, and the saved state they define."""

from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import (
    Field,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from oracle_lab.io import read_json, write_json
from oracle_lab.paths import RunPaths
from oracle_lab.results import (
    Answered,
    CommitId,
    ExposedArtifact,
    Justification,
    Sha256,
    StrictModel,
    UnsuccessfulTurn,
    parse_json_object,
)

JUDGMENT_LABELS = {
    "tests_issue": "Do the generated tests attempt to test the issue?",
    "attempt_detail": "Attempt detail",
    "no_attempt_reason": "No-attempt reason",
    "cheating": "Cheating attempt",
}
JUDGMENT_LABEL_KEYS = tuple(JUDGMENT_LABELS)

# The start list of failure modes the judge decides for every run, by key, with the
# title raters see. Hypotheses, not a validated codebook: see docs/research-framing.md.
FAILURE_MODES = {
    "never_saw_bug": "Never saw the bug",
    "saw_bug_untested": "Saw the bug, didn't test it",
    "copied_buggy_output": "Copied the buggy output",
    "copied_existing_tests": "Copied existing tests",
    "ignored_available_docs": "Ignored available docs",
    "contradicted_read_docs": "Read docs, contradicted them",
    "changed_test_to_pass": "Changed test to pass",
    "doubted_moved_on": "Doubted, moved on",
    "explained_bug_away": "Explained the bug away",
    "flagged_bug": "Flagged the bug",
}
FailureModeKey = Literal[
    "never_saw_bug",
    "saw_bug_untested",
    "copied_buggy_output",
    "copied_existing_tests",
    "ignored_available_docs",
    "contradicted_read_docs",
    "changed_test_to_pass",
    "doubted_moved_on",
    "explained_bug_away",
    "flagged_bug",
]
# Trace step numbers, as the review page's timeline shows them. When the trace could
# not be built, they are line numbers of the raw harness trace instead.
TraceSteps = Annotated[list[Annotated[int, Field(ge=0)]], Field(min_length=1)]


class PresentFailureMode(StrictModel):
    """A start-list failure mode the judge found, and the steps it rests on."""

    decision: Literal["present"]
    steps: TraceSteps
    justification: Justification


class AbsentFailureMode(StrictModel):
    decision: Literal["absent"]


FailureModeDecision = Annotated[
    PresentFailureMode | AbsentFailureMode, Field(discriminator="decision")
]


class AddedFailureMode(StrictModel):
    """A failure mode outside the start list, named and defined by the judge."""

    name: Annotated[str, Field(min_length=1, max_length=80)]
    definition: Justification
    steps: TraceSteps
    justification: Justification


TestsIssue = Literal["yes", "no", "unsure"]
AttemptDetail = Literal[
    "correct_assertion",
    "wrong_assertion",
    "no_issue_assertion",
    "missing_required_condition",
    "test_invalid_or_incomplete",
]
NoAttemptReason = Literal[
    "missed_trigger",
    "missed_outcome",
    "other_target_behavior",
    "unrelated_behavior",
    "no_real_tests",
]
Cheating = Literal[
    "no",
    "external_repository_lookup",
    "hidden_evidence_access",
    "other_circumvention",
]


class JudgmentLabels(StrictModel):
    """Each question's answer and justification, then every failure-mode decision.

    `attempt_detail` follows a yes and `no_attempt_reason` a no; otherwise each is null.
    """

    tests_issue: Answered[TestsIssue]
    attempt_detail: Answered[AttemptDetail] | None
    no_attempt_reason: Answered[NoAttemptReason] | None
    cheating: Answered[Cheating]
    # Every start-list key, decided. A present decision carries its own justification.
    failure_modes: dict[FailureModeKey, FailureModeDecision]
    added_failure_modes: list[AddedFailureMode]

    @model_validator(mode="after")
    def failure_modes_cover_the_start_list(self) -> JudgmentLabels:
        if set(self.failure_modes) != set(FAILURE_MODES):
            raise ValueError(
                f"failure_modes must decide exactly the start-list labels {sorted(FAILURE_MODES)}, "
                f"not {sorted(self.failure_modes)}"
            )
        return self

    @model_validator(mode="after")
    def conditional_fields_match_answer(self) -> JudgmentLabels:
        answer = self.tests_issue.answer
        if answer == "yes" and (self.attempt_detail is None or self.no_attempt_reason is not None):
            raise ValueError("A yes answer requires attempt_detail and null no_attempt_reason")
        if answer == "no" and (self.attempt_detail is not None or self.no_attempt_reason is None):
            raise ValueError("A no answer requires null attempt_detail and no_attempt_reason")
        if answer == "unsure" and (
            self.attempt_detail is not None or self.no_attempt_reason is not None
        ):
            raise ValueError("An unsure answer requires null conditional fields")
        return self


class CompletedJudgment(JudgmentLabels):
    status: Literal["completed"] = "completed"


class SkippedJudgment(StrictModel):
    """A configured judge turn intentionally omitted because its input was invalid."""

    status: Literal["skipped"] = "skipped"
    reason: Annotated[str, Field(min_length=1)]


class StaleJudgment(StrictModel):
    status: Literal["stale"] = "stale"
    reason: Annotated[str, Field(min_length=1)]
    previous_judgment: CompletedJudgment | UnsuccessfulTurn


JudgmentResult = Annotated[
    CompletedJudgment | UnsuccessfulTurn | SkippedJudgment | StaleJudgment,
    Field(discriminator="status"),
]


class PreparedImage(StrictModel):
    reference: str
    image_id: str
    digests: list[str]


class WorkspaceSpec(StrictModel):
    prepared_image: PreparedImage
    base_commit: CommitId
    repair_patch_sha256: Sha256
    reference_test_patch_sha256: Sha256
    rubric_sha256: Sha256
    workspace_method: Literal["git_worktree", "directory_copy"]
    reconstruction_commands: list[list[str]]
    artifacts: list[ExposedArtifact]


class BundleManifest(StrictModel):
    artifacts: list[ExposedArtifact]


_JUDGMENT = TypeAdapter(JudgmentResult)
_ACTIVE_JUDGMENT = TypeAdapter(
    Annotated[CompletedJudgment | UnsuccessfulTurn, Field(discriminator="status")]
)


def parse_judgment(raw: str) -> CompletedJudgment | UnsuccessfulTurn:
    """Parse one bare or fenced JSON object into a stable normalized result."""
    try:
        return CompletedJudgment.model_validate(parse_json_object(raw))
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        return UnsuccessfulTurn(status="invalid_output", error=str(error))


def skipped_judgment(reason: str) -> SkippedJudgment:
    """Represent a judge turn that was inapplicable and therefore never launched."""
    return SkippedJudgment(reason=reason)


def validate_judgment(value: object) -> JudgmentResult:
    """Validate a normalized judgment read from persisted artifacts."""
    return _JUDGMENT.validate_python(value)


def read_judgment_state(paths: RunPaths) -> dict:
    """Describe one run's saved annotation, including its explicit absent states.

    ``disabled`` and ``missing`` are distinguished by the frozen rubric, which a
    resolved run writes only when judging is configured. An unreadable artifact
    becomes ``invalid_artifact`` so a corrupt file never renders as a judgment.
    """
    if paths.judge.judgment.is_file():
        try:
            return validate_judgment(read_json(paths.judge.judgment)).model_dump()
        except (OSError, ValueError) as error:
            return {"status": "invalid_artifact", "error": str(error)}
    return {"status": "missing" if paths.judge.rubric.is_file() else "disabled"}


def mark_judgments_stale(paths: RunPaths) -> None:
    """Mark the saved judgment stale.

    Call this after replacing evaluation results, since the judgment rated the old ones.
    """
    path = paths.judge.judgment
    if not path.is_file():
        return
    value = read_json(path)
    if value.get("status") == "stale":
        # Already stale from an earlier replacement; keep the original annotation.
        stale = StaleJudgment.model_validate(value)
    elif value.get("status") == "skipped":
        # Reevaluation cannot repair the generation result that prevented judging.
        return
    else:
        stale = StaleJudgment(
            reason="Paired evaluation evidence changed after this judgment was produced.",
            previous_judgment=_ACTIVE_JUDGMENT.validate_python(value),
        )
    write_json(path, stale.model_dump())
