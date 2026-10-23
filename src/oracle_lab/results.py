"""Contracts and shared vocabulary for machine-readable run artifacts.

This module neither executes the benchmark nor renders its human report.
Containers write raw JSON under the repository's own Python, so artifact data is
validated here, where it reaches the host, rather than where it was produced.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from oracle_lab.io import read_json, write_json

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
CommitId = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


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
    record_sha256: Sha256


class InstanceRecord(ArtifactModel):
    """One benchmark instance: the buggy repo, plus the answer key.

    `golden_patch`, `reference_test_patch`, and `original_record` are secret. The
    judge sees them. The agent being tested never does.
    """

    instance_id: str
    repo: str
    base_commit: CommitId
    golden_patch: str
    reference_test_patch: str
    reference_test_ids: list[str]
    source: SourceProvenance
    original_record: dict


class SubmissionFile(ArtifactModel):
    sha256: Sha256
    mode: int


class SubmissionManifest(ArtifactModel):
    """The frozen submission: new test files under the generated directory, and their hashes.

    `sha256` hashes `files`, so the manifest and the bundle beside it can be
    checked together before either version receives them.
    """

    files: dict[str, SubmissionFile]
    forbidden_changes: list[str]  # edits outside the generated directory
    compliant: bool
    empty: bool
    sha256: Sha256


class TargetFile(ArtifactModel):
    """One file of a localized target, and what in it the agent is pointed at."""

    path: str
    # Qualified functions or classes, in file order; empty when the whole file is the target.
    names: list[str]
    lines: int  # buggy-file lines this entry spans


class LocalizedTarget(ArtifactModel):
    """The part of the buggy repository a localized run tells the agent to test.

    It names the existing code the golden repair changes, so it is derived from
    private data, but it contains no patch text or changed lines.
    """

    level: Literal["function", "class", "file"]
    files: Annotated[list[TargetFile], Field(min_length=1)]
    text: str  # exactly what replaces `${test_target}` in the prompt


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
        return self.matrix[MatrixCellName.FAIL_ON_BUGGY_PASS_ON_GOLDEN].count > 0


class EvaluationResult(PairedResult):
    """The paired comparison plus the submission and generation it describes."""

    instance_id: str
    artifact_sha256: str
    submission_compliant: bool
    forbidden_changes: list[str]
    task_scope: str
    test_target: LocalizedTarget | None = None  # what a localized run's agent was shown
    existing_tests: str
    agent: HarnessResult

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
            and self.has_fail_on_buggy_pass_on_golden
        )

    @property
    def completion_problems(self) -> list[str]:
        """Why this run is not a clean completion; empty when it is."""
        problems = []
        if self.agent.status != "completed":
            problems.append(f"generation {self.agent.status}")
        if self.no_tests:
            problems.append("no tests collected")
        else:
            problems += [
                f"{version} evaluation {status}"
                for version, status in (
                    ("buggy", self.buggy_status),
                    ("golden", self.golden_status),
                )
                if status != "completed"
            ]
        if self.diagnostic_only:
            problems.append("diagnostic only: edits outside the generated directory")
        return problems


class AgentAttempt(HarnessResult):
    """A model turn's harness result, plus which agent ran it: generation, judge, or classifier."""

    harness: str
    provider: str
    model: str
    harness_version: str
    limit: dict


# Contracts for what a model wrote. Unlike run artifacts, these refuse coercion too.


class StrictModel(BaseModel):
    """A model-written contract: rejects undeclared fields and coerced types."""

    model_config = ConfigDict(extra="forbid", strict=True)


MAX_JUSTIFICATION_LENGTH = 2_000


def _require_text(value: str) -> str:
    if not value.strip():
        raise ValueError("justification must not be blank")
    return value


Justification = Annotated[
    str, Field(max_length=MAX_JUSTIFICATION_LENGTH), AfterValidator(_require_text)
]


A = TypeVar("A")


class Answered(StrictModel, Generic[A]):
    """One rubric question's answer, and the evidence for it."""

    answer: A
    justification: Justification


class UnsuccessfulTurn(StrictModel):
    """A judge or classifier turn that produced no usable answer."""

    status: Literal["invalid_output", "failed", "timed_out"]
    error: Annotated[str, Field(min_length=1)]


class ExposedArtifact(StrictModel):
    """One file copied into a privileged workspace, and its hash."""

    source: str
    destination: str
    sha256: Sha256


def failed_turn(error: str, *, timed_out: bool = False) -> UnsuccessfulTurn:
    """A harness failure, without pretending the model gave an answer."""
    return UnsuccessfulTurn(status="timed_out" if timed_out else "failed", error=error)


def turn_failure(result: dict, who: str) -> UnsuccessfulTurn | None:
    """Why a harness turn gave no answer to parse, or None when it completed.

    Prefers the harness's own last error over a generic status line.
    """
    if result["status"] == "completed":
        return None
    reason = last_error_message(result["errors"]) or (
        f"{who} harness ended with status {result['status']}"
    )
    return failed_turn(reason, timed_out=result["status"] == "timeout")


# A fenced block on its own lines, anywhere in the answer.
_FENCED_JSON = re.compile(
    r"^```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*$", re.DOTALL | re.MULTILINE
)


def parse_json_object(raw: str) -> dict:
    """The one JSON object a model answered with: bare, or inside one Markdown fence
    that ends the answer. Prose before the fence is ignored; the raw answer keeps it."""
    candidate = raw
    if not raw.lstrip().startswith(("{", "[")):
        fences = list(_FENCED_JSON.finditer(raw))
        if len(fences) != 1 or raw[fences[0].end() :].strip():
            raise ValueError(
                "Expected exactly one JSON object in one Markdown fence that ends the answer"
            )
        candidate = fences[0].group("body")
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("Model output must be a JSON object")
    return value


def last_error_message(errors: list) -> str | None:
    """The message of a harness's last error, which may be nested under `error`."""
    if not errors:
        return None
    last = errors[-1]
    if isinstance(last, dict):
        nested = last.get("error")
        message = last.get("message") or (
            nested.get("message") if isinstance(nested, dict) else None
        )
        if message:
            return str(message)
    return str(last)


class RunStatus(ArtifactModel):
    """`status.json` of a run, preflight, or classification attempt: its latest checkpoint."""

    stage: str
    state: str
    updated_at: str
    error: str | None = None
    judge_status: str | None = None  # runs only
    classification_status: str | None = None  # classification attempts only


def write_status(directory: Path, stage: str, state: str = "running", **details: str) -> None:
    """Record the latest lifecycle checkpoint in `status.json` and print it."""
    status = RunStatus(stage=stage, state=state, updated_at=_now(), **details)
    save_status(directory / "status.json", status)
    print(f"[{stage}] {state}: {directory}", flush=True)


def save_status(path: Path, status: RunStatus) -> None:
    write_json(path, status.model_dump(exclude_none=True))


def read_status(path: Path) -> RunStatus:
    return RunStatus.model_validate(read_json(path))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_agent_attempt(path: Path) -> AgentAttempt:
    return AgentAttempt.model_validate(read_json(path))


def read_instance_record(path: Path) -> InstanceRecord:
    return InstanceRecord.model_validate(read_json(path))


def read_submission_manifest(path: Path) -> SubmissionManifest:
    return SubmissionManifest.model_validate(read_json(path))


def read_localized_target(path: Path) -> LocalizedTarget:
    return LocalizedTarget.model_validate(read_json(path))


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
