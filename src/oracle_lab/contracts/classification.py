"""Strict persisted contracts for task-classification attempts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AfterValidator, Field, ValidationError, model_validator

from oracle_lab.contracts.base import CommitId, Sha256, StrictModel
from oracle_lab.contracts.run import InstanceRecord, read_status
from oracle_lab.contracts.turn import Answered, ExposedArtifact, UnsuccessfulTurn, parse_json_object
from oracle_lab.io import read_json
from oracle_lab.paths import AttemptPaths

CLASSIFICATION_LABELS = {
    "task_nature": "Task nature",
    "defect_mechanisms": "Defect mechanisms",
    "primary_assertion_target": "Primary assertion target",
    "required_test_setup": "Required test setup",
    "oracle_kind": "Oracle kind",
    "oracle_source": "Oracle source",
    "required_test_scope": "Required test scope",
    "benchmark_quality": "Benchmark quality",
}


DefectMechanism = Literal[
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


def _unique(value: list[str]) -> list[str]:
    if len(value) != len(set(value)):
        raise ValueError("defect_mechanisms must not contain duplicates")
    return value


DefectMechanisms = Annotated[list[DefectMechanism], Field(min_length=1), AfterValidator(_unique)]


# Test oracle kinds, after Barr et al., "The Oracle Problem in Software Testing: A
# Survey" (IEEE TSE, 2015), with the sources each one admits.
ORACLE_SOURCES = {
    "specified": (
        "docstring_or_comment",
        "signature_or_type",
        "repository_documentation",
        "external_specification",
    ),
    "derived": ("code_logic", "analogous_code", "property_relation", "api_convention"),
    "implicit": ("crash_or_exception", "hang_or_resource"),
    "human": ("domain_knowledge", "intent_only"),
}
# Both types are built from `ORACLE_SOURCES`, so a new kind or source is one edit.
OracleKind = Literal[tuple(ORACLE_SOURCES)]  # pyright: ignore[reportInvalidTypeForm]
OracleSource = Literal[  # pyright: ignore[reportInvalidTypeForm]
    tuple(source for sources in ORACLE_SOURCES.values() for source in sources)
]


class InScopeClassification(StrictModel):
    """Every question's answer and justification, for a task in scope."""

    status: Literal["completed"] = "completed"
    task_nature: Answered[Literal["behavioral_bug", "performance_problem"]]
    defect_mechanisms: Answered[DefectMechanisms]
    primary_assertion_target: Answered[
        Literal[
            "return_value_or_status",
            "exception_behavior",
            "state_mutation",
            "produced_output_or_artifact",
            "external_interaction",
            "resource_or_performance",
            "nondeterminism",
            "import_build_or_collection",
        ]
    ]
    required_test_setup: Answered[
        Literal[
            "none",
            "special_input",
            "existing_state",
            "sequence",
            "environment_or_dependency",
            "concurrency_or_timing",
        ]
    ]
    oracle_kind: Answered[OracleKind]
    oracle_source: Answered[OracleSource]
    required_test_scope: Answered[
        Literal[
            "unit",
            "component",
            "multi_module",
            "integration",
            "environment_or_platform",
            "performance_or_concurrency",
        ]
    ]
    benchmark_quality: Answered[
        Literal[
            "usable",
            "unclear_expected_behavior",
            "reference_test_or_repair_misaligned",
            "unreliable_environment_or_execution",
            "solution_or_oracle_leakage",
            "unfair_without_issue",
        ]
    ]

    @model_validator(mode="after")
    def source_belongs_to_kind(self) -> InScopeClassification:
        kind, source = self.oracle_kind.answer, self.oracle_source.answer
        if source not in ORACLE_SOURCES[kind]:
            raise ValueError(
                f"oracle_source {source!r} is not a {kind} oracle source; "
                f"choose one of {list(ORACLE_SOURCES[kind])}"
            )
        return self


class OutOfScopeClassification(StrictModel):
    """A task out of scope answers only the first question."""

    status: Literal["completed"] = "completed"
    task_nature: Answered[Literal["out_of_scope"]]


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
        nature = value.get("task_nature")
        if isinstance(nature, dict) and nature.get("answer") == "out_of_scope":
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
        status = read_status(attempt.status) if attempt.status.is_file() else None
        error = (status and status.error) or "The latest classification attempt is incomplete"
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
