"""Contracts and shared vocabulary for machine-readable run artifacts.

This module neither executes the benchmark nor renders its human report.
Containers write raw JSON under the repository's own Python, so artifact data is
validated here, where it reaches the host, rather than where it was produced.
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from oracle_bench.io import read_json, write_json


class MatrixCellName(StrEnum):
    """One binary paired outcome, including its execution and display meanings."""

    PASS_ON_BOTH = "pass_on_both"
    FAIL_ON_BUGGY_PASS_ON_GOLDEN = "fail_on_buggy_pass_on_golden"
    PASS_ON_BUGGY_FAIL_ON_GOLDEN = "pass_on_buggy_fail_on_golden"
    FAIL_ON_BOTH = "fail_on_both"

    @property
    def outcomes(self) -> tuple[str, str]:
        return {
            self.PASS_ON_BOTH: ("pass", "pass"),
            self.FAIL_ON_BUGGY_PASS_ON_GOLDEN: ("fail", "pass"),
            self.PASS_ON_BUGGY_FAIL_ON_GOLDEN: ("pass", "fail"),
            self.FAIL_ON_BOTH: ("fail", "fail"),
        }[self]

    @property
    def label(self) -> str:
        return {
            self.PASS_ON_BOTH: "Pass on both",
            self.FAIL_ON_BUGGY_PASS_ON_GOLDEN: "Fail on buggy, pass on golden",
            self.PASS_ON_BUGGY_FAIL_ON_GOLDEN: "Pass on buggy, fail on golden",
            self.FAIL_ON_BOTH: "Fail on both",
        }[self]


MATRIX_CELLS = tuple(MatrixCellName)
FAILURE_KINDS = ("assertion", "exception", "unknown")
VERSIONS = ("buggy", "golden")


class ArtifactModel(BaseModel):
    """A file a run saves. Rejects fields it does not know, tolerates JSON numbers."""

    model_config = ConfigDict(extra="forbid")


class SourceProvenance(ArtifactModel):
    """Which dataset and revision this instance came from."""

    kind: str
    dataset: str
    name: str
    revision: str
    split: str
    upstream_revision: str
    record_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class InstanceRecord(ArtifactModel):
    """One benchmark instance: the buggy repo, plus the answer key.

    `golden_patch`, `reference_test_patch`, and `original_record` are secret. The
    judge sees them. The agent being tested never does.
    """

    instance_id: str
    repo: str
    base_commit: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    golden_patch: str
    reference_test_patch: str
    reference_test_ids: list[str]
    test_target: str
    source: SourceProvenance
    original_record: dict


class TestCase(ArtifactModel):
    """One collected test as the in-container runner recorded it."""

    outcome: str
    phases: list[dict] = Field(default_factory=list)
    failure: dict | None = None


class VersionResult(ArtifactModel):
    """One version's execution: `tests.json`, plus any host-observed failure."""

    status: str
    exit_code: int | None = None
    collected: int = 0
    collection_errors: list[dict] = Field(default_factory=list)
    tests: dict[str, TestCase] = Field(default_factory=dict)
    error: str | None = None


class CoverageResult(ArtifactModel):
    status: Literal["available", "unavailable"]
    kind: str | None = None
    covered_lines: int | None = None
    executable_lines: int | None = None
    percent: float | None = None
    files: dict | None = None
    reason: str | None = None


class MatrixCell(ArtifactModel):
    count: int
    test_ids: list[str]


class PairedOutcome(ArtifactModel):
    """One test's outcome on both versions, and the matrix cell it earned."""

    test_id: str
    buggy: str
    golden: str
    cell: MatrixCellName | None = None
    buggy_failure: dict | None = None
    golden_failure: dict | None = None


class Usage(ArtifactModel):
    """Token counts with one meaning for every harness. `raw_usage` is the CLI's own."""

    # Every prompt token, read from cache or not.
    input_tokens: int
    cached_input_tokens: int
    # Every generated token, reasoning included.
    output_tokens: int
    reasoning_tokens: int | None
    raw_usage: dict


class HarnessResult(ArtifactModel):
    """A harness's own report. Adapters add their own trace fields."""

    model_config = ConfigDict(extra="allow")

    status: str
    duration_seconds: float | None = None
    usage: Usage | None = None
    errors: list[dict] = Field(default_factory=list)


class RunCost(ArtifactModel):
    """What the OpenRouter key spent while a run's model stages ran."""

    status: Literal["measured", "unsettled", "unavailable", "not_measured"]
    cost_usd: float | None = None
    before: float | None = None
    after: float | None = None
    waited_seconds: float | None = None
    error: str | None = None


class PairedResult(ArtifactModel):
    """Deterministic comparison of one submission on both versions."""

    matrix: dict[MatrixCellName, MatrixCell]
    other_outcomes: list[PairedOutcome]
    tests: list[PairedOutcome]
    failure_kinds: dict[str, dict[str, int]]
    buggy_status: str
    golden_status: str

    @property
    def both_completed(self) -> bool:
        return self.buggy_status == self.golden_status == "completed"

    @property
    def no_tests(self) -> bool:
        """Neither version collected a generated test, so there is nothing to judge."""
        return self.buggy_status == self.golden_status == "no_tests"

    @property
    def has_fail_on_buggy_pass_on_golden(self) -> bool:
        """Whether any completed test distinguishes the pair in the expected direction."""
        return self.matrix["fail_on_buggy_pass_on_golden"].count > 0


class EvaluationResult(PairedResult):
    """The paired comparison plus the submission and generation it describes."""

    instance_id: str
    artifact_sha256: str
    submission_compliant: bool
    forbidden_changes: list[str]
    task_scope: str
    test_target: str | None = None
    existing_tests: str
    agent: HarnessResult
    coverage: dict[str, CoverageResult]

    @property
    def diagnostic_only(self) -> bool:
        """A policy-violating submission remains useful only as diagnostic evidence."""
        return not self.submission_compliant

    @property
    def detected(self) -> bool:
        """A compliant submission that distinguished the versions by execution."""
        return (
            self.submission_compliant
            and self.both_completed
            and self.matrix["fail_on_buggy_pass_on_golden"].count > 0
        )


def read_instance_record(path: Path) -> InstanceRecord:
    return InstanceRecord.model_validate(read_json(path))


def read_version_result(path: Path) -> VersionResult:
    return VersionResult.model_validate(read_json(path))


def read_paired_result(path: Path) -> PairedResult:
    return PairedResult.model_validate(read_json(path))


def read_evaluation_result(path: Path) -> EvaluationResult:
    return EvaluationResult.model_validate(read_json(path))


def read_run_cost(path: Path) -> RunCost | None:
    return RunCost.model_validate(read_json(path)) if path.is_file() else None


def write_result(path: Path, result: ArtifactModel) -> None:
    write_json(path, result.model_dump(mode="json"))
