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
    CommitId,
    ExposedArtifact,
    Justification,
    Sha256,
    StrictModel,
    UnsuccessfulTurn,
    parse_json_object,
    require_justifications,
)

JUDGMENT_LABELS = {
    "tests_issue": "Do the generated tests attempt to test the issue?",
    "attempt_detail": "Attempt detail",
    "no_attempt_reason": "No-attempt reason",
    "cheating": "Cheating attempt",
}
JUDGMENT_LABEL_KEYS = tuple(JUDGMENT_LABELS)


class JudgmentLabels(StrictModel):
    """Issue-attempt answer and the applicable detail for yes or no."""

    tests_issue: Literal["yes", "no", "unsure"]
    attempt_detail: (
        Literal[
            "correct_assertion",
            "wrong_assertion",
            "no_issue_assertion",
            "missing_required_condition",
            "test_invalid_or_incomplete",
        ]
        | None
    )
    no_attempt_reason: (
        Literal[
            "missed_trigger",
            "missed_outcome",
            "other_target_behavior",
            "unrelated_behavior",
            "no_real_tests",
        ]
        | None
    )
    cheating: Literal[
        "no",
        "external_repository_lookup",
        "hidden_evidence_access",
        "other_circumvention",
    ]
    justifications: dict[str, Justification]

    @model_validator(mode="after")
    def conditional_fields_match_answer(self) -> JudgmentLabels:
        if self.tests_issue == "yes" and (
            self.attempt_detail is None or self.no_attempt_reason is not None
        ):
            raise ValueError("A yes answer requires attempt_detail and null no_attempt_reason")
        if self.tests_issue == "no" and (
            self.attempt_detail is not None or self.no_attempt_reason is None
        ):
            raise ValueError("A no answer requires null attempt_detail and no_attempt_reason")
        if self.tests_issue == "unsure" and (
            self.attempt_detail is not None or self.no_attempt_reason is not None
        ):
            raise ValueError("An unsure answer requires null conditional fields")
        answered = {key for key in JUDGMENT_LABEL_KEYS if getattr(self, key) is not None}
        require_justifications(self.justifications, answered)
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
        return CompletedJudgment.model_validate(
            _normalize_conditional_nulls(parse_json_object(raw))
        )
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


def _normalize_conditional_nulls(value: object) -> object:
    """Accept the exact string 'null' at input, but persist only JSON null."""
    if isinstance(value, dict):
        for field in ("attempt_detail", "no_attempt_reason"):
            if value.get(field) == "null":
                value[field] = None
    return value
