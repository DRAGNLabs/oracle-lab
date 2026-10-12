"""Complete artifact fixtures shared by the suites that read saved run results.

Keeping one builder per persisted contract means a schema change breaks in one
place instead of drifting silently across suites.
"""

import json
from contextlib import contextmanager
from pathlib import Path

import oracle_bench.container
from oracle_bench.config import RunConfig, RuntimeConfig, load_config
from oracle_bench.io import digest, write_json
from oracle_bench.paths import RunPaths
from oracle_bench.results import MATRIX_CELLS, InstanceRecord, SubmissionManifest

# The run config tests start from, independent of the sample configs.
RUN_CONFIG = Path(__file__).parent / "run-config.yaml"

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


def localized_target(**changes) -> dict:
    """A saved localized target naming one changed method."""
    target = {
        "level": "function",
        "files": [{"path": "package/core.py", "names": ["Client.send"], "lines": 12}],
        "text": "- `package/core.py`: `Client.send`",
    }
    target.update(changes)
    return target


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


GENERATED_TEST = {"oracle_tests/test_generated.py": "def test_generated(): pass\n"}


def submission_manifest(files: dict[str, str] | None = None, **changes) -> dict:
    """A manifest for these file contents, hashed the way capture hashes them."""
    files = GENERATED_TEST if files is None else files
    entries = {
        name: {"sha256": digest(text.encode()), "mode": 0o644} for name, text in files.items()
    }
    manifest = {
        "files": entries,
        "forbidden_changes": [],
        "compliant": True,
        "empty": not entries,
        "sha256": digest(json.dumps(entries, sort_keys=True).encode()),
    }
    manifest.update(changes)
    return manifest


def write_submission(
    paths: RunPaths, files: dict[str, str] | None = None, **changes
) -> SubmissionManifest:
    """A frozen bundle on disk and its manifest. `changes` override manifest fields."""
    files = GENERATED_TEST if files is None else files
    root = paths.submission / "files"
    root.mkdir(parents=True, exist_ok=True)
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text)
    manifest = submission_manifest(files, **changes)
    write_json(paths.submission / "manifest.json", manifest)
    return SubmissionManifest.model_validate(manifest)


def run_config(
    source_root: str = "package",
    *,
    image: str = "example/image",
    existing_test_globs: list[str] | None = None,
) -> RunConfig:
    """The test run config with a resolved runtime for a one-package repository."""
    config = load_config(RUN_CONFIG)
    config.runtime = RuntimeConfig(
        image=image,
        source_roots=[source_root],
        import_modules=[source_root],
        existing_test_globs=["tests"] if existing_test_globs is None else existing_test_globs,
    )
    return config


def context_of(value):
    """A stand-in for a context-manager factory, such as `docker_client`, that yields `value`."""

    @contextmanager
    def factory(*args, **kwargs):
        yield value

    return factory
