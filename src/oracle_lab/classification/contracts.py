"""Strict persisted contracts for task-classification attempts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, ValidationError, field_validator, model_validator

from oracle_lab.io import read_json
from oracle_lab.paths import AttemptPaths
from oracle_lab.results import (
    CommitId,
    ExposedArtifact,
    InstanceRecord,
    Justification,
    Sha256,
    StrictModel,
    UnsuccessfulTurn,
    parse_json_object,
    require_justifications,
)

CLASSIFICATION_LABELS = {
    "task_nature": "Task nature",
    "defect_mechanisms": "Defect mechanisms",
    "primary_assertion_target": "Primary assertion target",
    "required_test_setup": "Required test setup",
    "code_only_oracle_availability": "Code-only oracle availability",
    "required_test_scope": "Required test scope",
    "benchmark_quality": "Benchmark quality",
}


class InScopeClassification(StrictModel):
    status: Literal["completed"] = "completed"
    task_nature: Literal["behavioral_bug", "performance_problem"]
    defect_mechanisms: Annotated[
        list[
            Literal[
                "control_logic",
                "computation_algorithm",
                "data_state",
                "validation_checking",
                "error_handling",
                "interface_contract",
                "parsing_serialization",
                "resource_lifecycle",
                "concurrency_timing",
                "configuration_environment",
                "compatibility",
                "performance_resource_use",
                "presentation_output",
                "other",
            ]
        ],
        Field(min_length=1),
    ]
    primary_assertion_target: Literal[
        "return_value_or_status",
        "exception_behavior",
        "state_mutation",
        "produced_output_or_artifact",
        "external_interaction",
        "resource_or_performance",
        "nondeterminism",
        "import_build_or_collection",
    ]
    required_test_setup: Literal[
        "none",
        "special_input",
        "existing_state",
        "sequence",
        "environment_or_dependency",
        "concurrency_or_timing",
    ]
    code_only_oracle_availability: Literal[
        "local_contract",
        "repository_pattern",
        "repository_documentation",
        "api_convention",
        "general_property",
        "domain_knowledge",
        "unavailable_or_ambiguous",
    ]
    required_test_scope: Literal[
        "unit",
        "component",
        "multi_module",
        "integration",
        "environment_or_platform",
        "performance_or_concurrency",
    ]
    benchmark_quality: Literal[
        "usable",
        "unclear_expected_behavior",
        "reference_test_or_repair_misaligned",
        "unreliable_environment_or_execution",
        "solution_or_oracle_leakage",
        "unfair_without_issue",
    ]
    justifications: dict[str, Justification]

    @field_validator("defect_mechanisms")
    @classmethod
    def mechanisms_must_be_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("defect_mechanisms must not contain duplicates")
        return value

    @model_validator(mode="after")
    def justify_every_answer(self) -> InScopeClassification:
        require_justifications(self.justifications, set(CLASSIFICATION_LABELS))
        return self


class OutOfScopeClassification(StrictModel):
    status: Literal["completed"] = "completed"
    task_nature: Literal["out_of_scope"]
    justifications: dict[str, Justification]

    @model_validator(mode="after")
    def justify_the_one_answer(self) -> OutOfScopeClassification:
        require_justifications(self.justifications, {"task_nature"})
        return self


ClassificationResult = InScopeClassification | OutOfScopeClassification | UnsuccessfulTurn


class SavedClassification(StrictModel):
    """A classification plus immutable identity used by later benchmark runs."""

    instance_id: str
    base_commit: CommitId
    source_record_sha256: Sha256
    rubric_sha256: Sha256
    result: ClassificationResult


class WorkspaceSpec(StrictModel):
    image: str
    base_commit: CommitId
    artifacts: list[ExposedArtifact]


def parse_classification(raw: str) -> ClassificationResult:
    """Parse the rubric's bare or fenced object and add its persisted status."""
    try:
        value = parse_json_object(raw)
        value["status"] = "completed"
        if value.get("task_nature") == "out_of_scope":
            return OutOfScopeClassification.model_validate(value)
        return InScopeClassification.model_validate(value)
    except (json.JSONDecodeError, ValidationError, ValueError) as error:
        return UnsuccessfulTurn(status="invalid_output", error=str(error))


def read_saved_classification(path: Path) -> SavedClassification:
    return SavedClassification.model_validate(read_json(path))


def latest_classification(
    classifications: Path, instance: InstanceRecord
) -> tuple[dict, AttemptPaths | None]:
    """The newest attempt for this instance under `classifications`, or explicit absence.

    The newest attempt is used even when it failed, so a failure never silently
    falls back to an older answer. Its identity must match the instance.
    """
    root = classifications / instance.instance_id
    attempts = sorted((path for path in root.glob("*") if path.is_dir()), reverse=True)
    if not attempts:
        return {"status": "missing"}, None

    attempt = AttemptPaths(attempts[0])
    artifact = attempt.judge.classification
    if not artifact.is_file():
        detail = read_json(attempt.status) if attempt.status.is_file() else {}
        error = detail.get("error", "The latest classification attempt is incomplete")
        return {"status": "invalid_artifact", "error": error}, attempt
    try:
        saved = read_saved_classification(artifact)
    except (OSError, ValueError) as error:
        return {"status": "invalid_artifact", "error": str(error)}, attempt

    expected = {
        "instance_id": instance.instance_id,
        "base_commit": instance.base_commit,
        "source_record_sha256": instance.source.record_sha256,
    }
    mismatches = [key for key, value in expected.items() if getattr(saved, key) != value]
    if mismatches:
        return {
            "status": "mismatched",
            "error": "Classification provenance differs for: " + ", ".join(mismatches),
        }, attempt
    return saved.result.model_dump(mode="json"), attempt
