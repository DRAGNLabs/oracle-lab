"""Complete artifact fixtures shared by the suites that read saved run results.

Keeping one builder per persisted contract means a schema change breaks in one
place instead of drifting silently across suites.
"""

from pathlib import Path

import oracle_bench.container
from oracle_bench.results import MATRIX_CELLS, InstanceRecord

# Checked-in build inputs, for tests that run a helper or build an image.
CONTAINER = Path(oracle_bench.container.__file__).parent
HELPERS = CONTAINER / "helpers"
DOCKERFILES = CONTAINER / "docker"


def instance_record(**changes) -> InstanceRecord:
    """A resolved instance. Tests pass only the fields they care about."""
    record = {
        "instance_id": "owner__project-1",
        "repo": "owner/project",
        "base_commit": "a" * 40,
        "golden_patch": "diff --git a/package/core.py b/package/core.py\n",
        "reference_test_patch": "diff --git a/tests/test_core.py b/tests/test_core.py\n",
        "reference_test_ids": ["tests/test_core.py::test_fixed"],
        "test_target": "package/core.py (send)",
        "source": {
            "kind": "swebench",
            "dataset": "lite",
            "name": "princeton-nlp/SWE-bench_Lite",
            "revision": "b" * 40,
            "split": "test",
            "upstream_revision": "c" * 40,
            "record_sha256": "d" * 64,
        },
        "original_record": {"problem_statement": "Broken behavior."},
    }
    record.update(changes)
    return InstanceRecord.model_validate(record)


def evaluation_result(**changes) -> dict:
    """A valid `evaluation/results.json`; tests override only what they exercise."""
    result = {
        "instance_id": "owner__project-1",
        "artifact_sha256": "a" * 64,
        "submission_compliant": True,
        "forbidden_changes": [],
        "task_scope": "repository",
        "test_target": None,
        "existing_tests": "hide_all",
        "matrix": {key: {"count": 0, "test_ids": []} for key in MATRIX_CELLS},
        "other_outcomes": [],
        "tests": [],
        "failure_kinds": {
            "buggy": {"assertion": 0, "exception": 0, "unknown": 0},
            "golden": {"assertion": 0, "exception": 0, "unknown": 0},
        },
        "buggy_status": "completed",
        "golden_status": "completed",
        "coverage": {},
        "agent": {"status": "completed", "duration_seconds": 3},
    }
    result.update(changes)
    return result


def detecting_result(**changes) -> dict:
    """An evaluation whose submission distinguished the two versions."""
    matrix = {key: {"count": 0, "test_ids": []} for key in MATRIX_CELLS}
    matrix["fail_on_buggy_pass_on_golden"] = {"count": 1, "test_ids": ["oracle_tests::test_bug"]}
    return evaluation_result(matrix=matrix, **changes)


def completed_judgment(**changes) -> dict:
    """A valid normalized judgment."""
    judgment = {
        "status": "completed",
        "no_attempt_reason": None,
        "tests_issue": "yes",
        "attempt_detail": "correct_assertion",
        "cheating": "no",
        "rationale": "The generated test reaches and checks the reported behavior.",
    }
    judgment.update(changes)
    return judgment


def usage(**changes) -> dict:
    """Normalized token counts, as every harness adapter reports them."""
    counts = {
        "input_tokens": 100,
        "cached_input_tokens": 0,
        "output_tokens": 20,
        "reasoning_tokens": None,
        "raw_usage": {},
    }
    counts.update(changes)
    return counts


def judge_attempt(**changes) -> dict:
    """A saved judge turn: harness result plus the provenance the dispatcher adds."""
    attempt = {
        "status": "completed",
        "duration_seconds": 4.5,
        "usage": usage(),
        "errors": [],
        "harness": "codex",
        "provider": "openai",
        "model": "judge-model",
        "harness_version": "1.2.3",
        "limit": {"kind": "wall_seconds", "value": 60},
    }
    attempt.update(changes)
    return attempt
