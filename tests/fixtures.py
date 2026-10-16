"""Complete artifact fixtures shared by the suites that read saved run results.

Keeping one builder per persisted contract means a schema change breaks in one
place instead of drifting silently across suites.
"""

import json
import subprocess
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

import oracle_lab.container
from oracle_lab.classification.contracts import CLASSIFICATION_LABELS
from oracle_lab.classification.run import classification_evidence
from oracle_lab.config import RunConfig, RuntimeConfig, load_config
from oracle_lab.io import digest, write_json
from oracle_lab.judge.contracts import JUDGMENT_LABEL_KEYS
from oracle_lab.judge.workspace import JUDGE_ROOT, judge_evidence
from oracle_lab.paths import RunPaths
from oracle_lab.results import (
    MATRIX_CELLS,
    InstanceRecord,
    SubmissionManifest,
    read_evaluation_result,
    write_result,
)

# The run config tests start from, independent of the sample configs.
RUN_CONFIG = Path(__file__).parent / "run-config.yaml"

# Checked-in build inputs, for tests that run a helper or build an image.
CONTAINER = Path(oracle_lab.container.__file__).parent
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


def swerebench_v2_record(**changes) -> dict:
    """A SWE-rebench V2 dataset record, with the fields the resolver reads."""
    record = {
        "instance_id": "wtforms__wtforms-614",
        "repo": "wtforms/wtforms",
        "base_commit": "a" * 40,
        "language": "python",
        "image_name": "docker.io/swerebenchv2/wtforms-wtforms:614-aaaaaaa",
        "patch": "diff --git a/src/wtforms/validators.py b/src/wtforms/validators.py\n",
        "test_patch": "diff --git a/tests/test_fields.py b/tests/test_fields.py\n",
        "FAIL_TO_PASS": ["tests/test_fields.py::TestFlags::test_existing_values"],
        "PASS_TO_PASS": [],
        "meta": {"llm_metadata": {"pr_categories": ["regression_bug", "edge_case_bug"]}},
    }
    record.update(changes)
    return record


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


def judgment_labels(**changes) -> dict:
    """A judge's labels, with one justification per answered question.

    Changing an answer changes which questions need a justification, so the
    justifications follow the answers unless a test passes its own.
    """
    labels = {
        "no_attempt_reason": None,
        "tests_issue": "yes",
        "attempt_detail": "correct_assertion",
        "cheating": "no",
    }
    labels.update(changes)
    labels.setdefault(
        "justifications",
        {
            key: f"The submission supports this {key} answer."
            for key in JUDGMENT_LABEL_KEYS
            if labels.get(key) not in (None, "null")
        },
    )
    return labels


def completed_judgment(**changes) -> dict:
    """A valid normalized judgment."""
    return {"status": "completed", **judgment_labels(**changes)}


def in_scope_classification(**changes) -> dict:
    """A classifier's answer for an in-scope task, before its status is added."""
    classification = {
        "task_nature": "behavioral_bug",
        "defect_mechanisms": ["validation_checking"],
        "primary_assertion_target": "exception_behavior",
        "required_test_setup": "special_input",
        "code_only_oracle_availability": "local_contract",
        "required_test_scope": "unit",
        "benchmark_quality": "usable",
        "justifications": {
            key: f"The task record and checkout support this {key} answer."
            for key in CLASSIFICATION_LABELS
        },
    }
    classification.update(changes)
    return classification


def out_of_scope_classification(**changes) -> dict:
    """A classifier's answer for an out-of-scope task, before its status is added."""
    classification = {
        "task_nature": "out_of_scope",
        "justifications": {"task_nature": "This task adds a new feature."},
    }
    classification.update(changes)
    return classification


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


REPAIR = (
    "diff --git a/package/core.py b/package/core.py\n"
    "--- a/package/core.py\n+++ b/package/core.py\n@@ -1 +1 @@\n-old\n+new\n"
)


def judged_run(
    root: Path, judgment: dict | None = None, instance: InstanceRecord | None = None
) -> RunPaths:
    """A run on disk with its judgment and a workspace spec matching its evidence."""
    paths = RunPaths.create(root)
    instance = instance or instance_record(golden_patch=REPAIR)
    write_result(paths.instance, instance)
    paths.config.write_text("instance: owner__project-1\n")
    paths.judge.rubric.write_text("# Judge rubric\n")
    (paths.ground_truth / "issue.md").write_text("# Issue\n\nIncorrect result.\n")
    (paths.ground_truth / "fix.patch").write_text(REPAIR)
    (paths.generation / "session.log").write_text("agent session\n")
    (paths.generation / "workspace.diff").write_text("generated diff\n")
    manifest = write_submission(paths)
    write_json(paths.results, detecting_result())
    for version in ("buggy", "golden"):
        (paths.evaluation / version).mkdir(parents=True, exist_ok=True)
        write_json(paths.evaluation / version / "tests.json", {"status": "completed"})
        (paths.evaluation / version / "output.log").write_text(version + " output\n")

    evaluation = read_evaluation_result(paths.results)
    with TemporaryDirectory() as staging:
        rows = judge_evidence(paths, Path(staging), instance, evaluation, manifest)
        artifacts = [_exposed(source, destination) for source, destination in rows]
    artifacts += [
        {
            "source": f"submission/files/{name}",
            "destination": f"{view}/{name}",
            "sha256": info.sha256,
        }
        for name, info in manifest.files.items()
        for view in (JUDGE_ROOT + "/buggy", JUDGE_ROOT + "/golden")
    ]
    write_json(
        paths.judge.workspace_spec,
        {
            "prepared_image": {"reference": "example/runtime", "image_id": "id", "digests": []},
            "base_commit": instance.base_commit,
            "repair_patch_sha256": digest(REPAIR.encode()),
            "reference_test_patch_sha256": digest(instance.reference_test_patch.encode()),
            "rubric_sha256": digest(paths.judge.rubric.read_bytes()),
            "workspace_method": "git_worktree",
            "reconstruction_commands": [],
            "artifacts": artifacts,
        },
    )
    write_json(paths.judge.result, judge_attempt())
    write_json(paths.judge.judgment, completed_judgment() if judgment is None else judgment)
    return paths


def classified_attempt(
    root: Path, result: dict | None = None, instance: InstanceRecord | None = None
) -> Path:
    """A classification attempt on disk with its result and workspace spec."""
    instance = instance or instance_record()
    (root / "inputs").mkdir(parents=True)
    write_result(root / "inputs/instance.json", instance)
    (root / "inputs/config.resolved.yaml").write_text("source: owner__project-1\n")
    (root / "inputs/rubric.md").write_text("# Classification rubric\n")
    write_json(root / "agent/result.json", judge_attempt(model="classifier-model"))
    rows = classification_evidence(root)
    write_json(
        root / "workspace-spec.json",
        {
            "image": "example/runtime",
            "base_commit": instance.base_commit,
            "artifacts": [_exposed(source, destination) for source, destination in rows],
        },
    )
    if result is None:
        result = {"status": "completed", **in_scope_classification()}
    write_json(
        root / "classification.json",
        {
            "instance_id": instance.instance_id,
            "base_commit": instance.base_commit,
            "source_record_sha256": instance.source.record_sha256,
            "rubric_sha256": digest((root / "inputs/rubric.md").read_bytes()),
            "result": result,
        },
    )
    return root


def _exposed(source: Path, destination: str) -> dict:
    return {
        "source": source.name,
        "destination": destination,
        "sha256": digest(source.read_bytes()),
    }


def git(directory: Path, *args: str) -> str:
    """Run Git in `directory` as a test author, ignoring the user's signing settings."""
    identity = ["-c", "user.name=Test", "-c", "user.email=t@example.com"]
    command = ["git", "-C", str(directory), *identity, "-c", "commit.gpgsign=false", *args]
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def upstream_repository(repo: Path) -> tuple[str, str, str]:
    """A local upstream at the base commit: its URL, the base commit, and a repair diff.

    The repair changes `package/core.py`, creates `package/added.py`, and deletes
    `package/gone.py`.
    """
    (repo / "package").mkdir(parents=True)
    (repo / "package/core.py").write_text("old\n")
    (repo / "package/gone.py").write_text("removed by the repair\n")
    (repo / "README.md").write_text("project\n")
    git(repo, "init", "--quiet", "--initial-branch=main")
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")

    # Write the repair on a branch, take its diff, and leave main at the base commit.
    git(repo, "switch", "--quiet", "-c", "fixed")
    (repo / "package/core.py").write_text("new\n")
    (repo / "package/added.py").write_text("created by the repair\n")
    (repo / "package/gone.py").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "fix")
    repair = git(repo, "diff", base, "HEAD") + "\n"
    git(repo, "switch", "--quiet", "main")
    return "file://" + str(repo), base, repair
