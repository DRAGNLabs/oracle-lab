"""The exported dataset: one row model per table, and the export manifest.

Each row model is a table's schema. The Parquet writer derives its column types
from these fields, so a table and its documentation cannot drift apart.
"""

from __future__ import annotations

from typing import Literal

from oracle_lab.results import ArtifactModel


class RunRow(ArtifactModel):
    """One run: what was asked, how each stage ended, and the paired matrix."""

    run_id: str
    instance_id: str
    repo: str | None
    base_commit: str | None
    source_kind: str
    dataset: str
    agent_harness: str
    agent_provider: str | None
    agent_model: str
    prompt: str  # the prompt file's name
    task_scope: str
    existing_tests: str
    generated_dir: str
    stage: str
    state: str
    error: str | None
    cost_status: str | None
    cost_usd: float | None
    generation_status: str | None
    generation_seconds: float | None
    input_tokens: int | None
    cached_input_tokens: int | None
    output_tokens: int | None
    reasoning_tokens: int | None
    test_files: int | None
    forbidden_changes: list[str] | None
    compliant: bool | None
    buggy_status: str | None
    golden_status: str | None
    detected: bool | None
    pass_on_both: int | None
    fail_on_buggy_pass_on_golden: int | None
    pass_on_buggy_fail_on_golden: int | None
    fail_on_both: int | None
    judge_status: str | None


class GeneratedTestRow(ArtifactModel):
    """One generated test's outcome on both versions. `cell` is null for outcomes
    outside the four matrix cells, such as skips and errors."""

    run_id: str
    instance_id: str
    test_id: str
    buggy: str
    golden: str
    cell: str | None


class JudgmentRow(ArtifactModel):
    """The generated-test judge's answer for one run, or why it has none."""

    run_id: str
    instance_id: str
    status: str
    judge_harness: str | None
    judge_model: str | None
    rubric_sha256: str | None
    tests_issue: str | None
    attempt_detail: str | None
    no_attempt_reason: str | None
    cheating: str | None
    tests_issue_justification: str | None
    attempt_detail_justification: str | None
    no_attempt_reason_justification: str | None
    cheating_justification: str | None
    error: str | None  # why an unsuccessful, skipped, or stale judgment has no answer


class FailureModeRow(ArtifactModel):
    """One failure mode the judge decided for a run: every start-list label, and each
    label it added. `item` is the key raters rated it under in `reviews`."""

    run_id: str
    instance_id: str
    item: str
    source: Literal["start_list", "judge_added"]
    name: str
    definition: str | None  # only an added label carries its own
    present: bool
    steps: list[int]
    justification: str | None


class TraceStepRow(ArtifactModel):
    """One event of a run's agent trace, numbered as the judge and raters cite it."""

    run_id: str
    instance_id: str
    step: int
    part: int
    action: str
    description: str
    command: str
    output: str
    exit_code: int | None
    text: str
    timestamp: str | None


class ClassificationRow(ArtifactModel):
    """The task classifier's answer in one attempt, or why it has none."""

    attempt_id: str
    instance_id: str
    base_commit: str
    status: str
    classifier_harness: str | None
    classifier_model: str | None
    rubric_sha256: str
    task_nature: str | None
    defect_mechanisms: list[str] | None
    primary_assertion_target: str | None
    required_test_setup: str | None
    code_only_oracle_availability: str | None
    required_test_scope: str | None
    benchmark_quality: str | None
    task_nature_justification: str | None
    defect_mechanisms_justification: str | None
    primary_assertion_target_justification: str | None
    required_test_setup_justification: str | None
    code_only_oracle_availability_justification: str | None
    required_test_scope_justification: str | None
    benchmark_quality_justification: str | None
    error: str | None


class ReviewRow(ArtifactModel):
    """One rater's rating of one item an LLM judge answered."""

    stage: str
    target_id: str  # the run ID or the attempt ID
    instance_id: str
    rater: str
    item: str
    rating: str
    reviewed_output_sha256: str
    judge_model: str
    stage_rubric_sha256: str


class RaterFailureModeRow(ArtifactModel):
    """A failure mode a rater added that the judge missed."""

    stage: str
    target_id: str
    instance_id: str
    rater: str
    name: str
    description: str
    steps: list[int]


TABLES: dict[str, type[ArtifactModel]] = {
    "runs": RunRow,
    "tests": GeneratedTestRow,
    "judgments": JudgmentRow,
    "failure_modes": FailureModeRow,
    "trace_steps": TraceStepRow,
    "classifications": ClassificationRow,
    "reviews": ReviewRow,
    "rater_failure_modes": RaterFailureModeRow,
}


class SkippedTarget(ArtifactModel):
    """A run or attempt left out of the export, and why."""

    path: str  # relative to the export source
    error: str


class ExportManifest(ArtifactModel):
    """What one export holds: its source, code version, contents, and omissions."""

    created_at: str
    source: str
    oracle_lab_version: str | None  # `git describe --always --dirty`, when available
    runs: list[str]
    attempts: list[str]  # `<instance-id>/<attempt-id>`
    skipped: list[SkippedTarget]
    rows: dict[str, int]
