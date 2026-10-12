"""Construct the privileged, reproducible workspace used by test judges."""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from string import Template
from tempfile import TemporaryDirectory
from typing import Literal

from oracle_bench.config import RunConfig, repo_path
from oracle_bench.container.sandbox import Sandbox
from oracle_bench.io import digest, read_json, require_files, write_json
from oracle_bench.judge.contracts import BundleManifest, PreparedImage, WorkspaceSpec
from oracle_bench.paths import RunPaths
from oracle_bench.results import (
    EvaluationResult,
    ExposedArtifact,
    InstanceRecord,
    SubmissionManifest,
    write_result,
)

JUDGE_ROOT = "/oracle-judge"
HELPER = "/judge_workspace.py"
# The judge compares these two checkouts of the same repository.
VIEWS = (JUDGE_ROOT + "/buggy", JUDGE_ROOT + "/golden")
INSTRUCTIONS_TEMPLATE = Path(__file__).resolve().parent / "templates/workspace-instructions.md"


def build_judge_workspace(
    sandbox: Sandbox,
    config: RunConfig,
    paths: RunPaths,
    image: str,
    instance: InstanceRecord,
    evaluation: EvaluationResult,
    manifest: SubmissionManifest,
    *,
    persist: bool = True,
) -> WorkspaceSpec:
    """Assemble /oracle-judge in the container: two repo checkouts plus the evidence.

    The `evidence` table below is the whole exposure contract. Nothing outside it is
    uploaded, and the returned spec records every file so a reader can check what the
    judge was shown.
    """
    config.require_judge()
    runtime = config.require_runtime()
    base_commit = instance.base_commit

    artifacts: list[ExposedArtifact] = []
    with TemporaryDirectory(prefix="oracle-judge-bundle-") as temporary:
        staging = Path(temporary)

        # 1. Evidence that is not already a file on disk. Written to a throwaway
        #    directory so it never lands in the run.
        reference_patch = staging / "reference-tests.patch"
        metadata = staging / "metadata.json"
        instructions = staging / "instructions.md"
        submission_manifest = staging / "submission-manifest.json"
        original = instance.original_record
        reference_patch.write_text(instance.reference_test_patch)
        write_json(
            metadata,
            {
                "instance_id": instance.instance_id,
                "repo": instance.repo,
                "base_commit": base_commit,
                "FAIL_TO_PASS": _normalized_test_ids(original.get("FAIL_TO_PASS", [])),
                "PASS_TO_PASS": _normalized_test_ids(original.get("PASS_TO_PASS", [])),
                "reference_test_ids": instance.reference_test_ids,
            },
        )
        write_result(submission_manifest, manifest)
        instructions.write_text(_workspace_instructions(instance, manifest, evaluation))

        # 2. Every file the judge may read, and where it lands in the container.
        #    Adding a row here is the only way to expose something new.
        rubric = paths.judge.rubric
        fix = paths.ground_truth / "fix.patch"
        fix_in_container = JUDGE_ROOT + "/instance/fix.patch"
        evidence = [
            (rubric, JUDGE_ROOT + "/rubric.md"),
            (paths.ground_truth / "issue.md", JUDGE_ROOT + "/instance/issue.md"),
            (fix, fix_in_container),
            (reference_patch, JUDGE_ROOT + "/instance/reference-tests.patch"),
            (metadata, JUDGE_ROOT + "/instance/metadata.json"),
            (instructions, JUDGE_ROOT + "/instructions.md"),
            (submission_manifest, JUDGE_ROOT + "/evidence/submission-manifest.json"),
            (paths.generation / "session.log", JUDGE_ROOT + "/evidence/agent-session.log"),
            (paths.generation / "workspace.diff", JUDGE_ROOT + "/evidence/workspace.diff"),
            (paths.results, JUDGE_ROOT + "/evidence/paired-results.json"),
            (paths.evaluation / "buggy/tests.json", JUDGE_ROOT + "/evidence/buggy-tests.json"),
            (paths.evaluation / "buggy/output.log", JUDGE_ROOT + "/evidence/buggy-output.log"),
            (paths.evaluation / "golden/tests.json", JUDGE_ROOT + "/evidence/golden-tests.json"),
            (paths.evaluation / "golden/output.log", JUDGE_ROOT + "/evidence/golden-output.log"),
        ]
        # images.json is read at the end for the spec, not shown to the judge.
        images = paths.build / "images.json"
        require_files([source for source, _ in evidence] + [images], "Judge workspace inputs")

        # 3. Reset the checkout to the instance's base commit, then cut two views of it.
        #    Worktrees are cheap, so try those first and copy the directory only if the
        #    image's Git cannot add one.
        sandbox.run(["mkdir", "-p", JUDGE_ROOT + "/instance", JUDGE_ROOT + "/evidence"])
        sandbox.run(
            ["git", "-C", runtime.workdir, "reset", "--hard", base_commit],
            timeout=config.limits.setup_seconds,
        )
        method: Literal["git_worktree", "directory_copy"] = "git_worktree"
        commands = [
            ["git", "-C", runtime.workdir, "worktree", "add", "--detach", view, base_commit]
            for view in VIEWS
        ]
        results = [
            sandbox.run(command, timeout=config.limits.setup_seconds, check=False)
            for command in commands
        ]
        if not all(result.exit_code == 0 for result in results):
            # A partly created worktree would poison the copies, so the helper clears both.
            method = "directory_copy"
            commands = [
                [runtime.python, sandbox.helpers + HELPER, "copy-views", runtime.workdir, *VIEWS]
            ]
            sandbox.run(commands[0], timeout=config.limits.setup_seconds)

        # 4. Upload the table, recording each file as it goes.
        for source, destination in evidence:
            sandbox.upload(source, destination)
            artifacts.append(_exposed(paths.root, source, destination))

        # 5. The repair goes into the golden view only. The buggy view stays broken.
        apply = ["git", "-C", JUDGE_ROOT + "/golden", "apply"]
        sandbox.run([*apply, "--check", fix_in_container], timeout=config.limits.setup_seconds)
        sandbox.run([*apply, fix_in_container], timeout=config.limits.setup_seconds)

        # 6. The same submission goes into both views, so the judge reads one set of
        #    tests against two versions of the code.
        for view in VIEWS:
            sandbox.upload(paths.submission / "files", view, contents=True)
            for name, info in manifest.files.items():
                source = paths.submission / "files" / repo_path(name)
                artifacts.append(_exposed(paths.root, source, f"{view}/{name}", info.sha256))

        # 7. Trust the container over our own copy: make it hash what it actually holds
        #    in both views, and refuse to judge if either disagrees with the manifest.
        expected = {name: info.sha256 for name, info in manifest.files.items()}
        settings = staging / "verify.json"
        request = "/tmp/oracle-judge-verify.json"
        response = "/tmp/oracle-judge-file-hashes.json"
        observed_path = staging / "actual-files.json"
        write_json(settings, {"expected": expected, "views": list(VIEWS)})
        sandbox.upload(settings, request)
        sandbox.run([runtime.python, sandbox.helpers + HELPER, "verify", request, response])
        sandbox.download(response, observed_path)
        observed = read_json(observed_path)
        if set(observed) != set(VIEWS) or any(files != expected for files in observed.values()):
            raise ValueError("Generated tests differ between the saved bundle and judge views")
        reference_patch_sha256 = digest(reference_patch.read_bytes())

    sandbox.run(["chmod", "-R", "a+rX", JUDGE_ROOT])
    artifacts.sort(key=lambda artifact: (artifact.destination, artifact.source))

    runtime_image = read_json(images)["runtime"]
    image_record = PreparedImage(
        reference=image, image_id=runtime_image["id"], digests=runtime_image["digests"]
    )
    spec = WorkspaceSpec(
        prepared_image=image_record,
        base_commit=base_commit,
        repair_patch_sha256=digest(fix.read_bytes()),
        reference_test_patch_sha256=reference_patch_sha256,
        rubric_sha256=digest(rubric.read_bytes()),
        workspace_method=method,
        reconstruction_commands=commands,
        artifacts=artifacts,
    )
    if persist:
        write_json(paths.judge.image, image_record.model_dump())
        write_json(paths.judge.bundle_manifest, BundleManifest(artifacts=artifacts).model_dump())
        write_json(paths.judge.workspace_spec, spec.model_dump())
    return spec


def _exposed(
    run_dir: Path, source: Path, destination: str, sha256: str | None = None
) -> ExposedArtifact:
    """Record one file the judge can see, named by where it came from in the run.

    Pass `sha256` when the submission manifest already has it; otherwise the file is
    hashed here. Files built in staging have no home in the run, so they get `derived/`.
    """
    try:
        relative = source.resolve().relative_to(run_dir.resolve()).as_posix()
    except ValueError:
        relative = "derived/" + source.name
    return ExposedArtifact(
        source=relative,
        destination=destination,
        sha256=sha256 or digest(source.read_bytes()),
    )


def _normalized_test_ids(value: object) -> list[str]:
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("SWE-bench test IDs must be a list of strings")
    return value


def _patch_paths(patch: str) -> list[str]:
    paths = set()
    for line in patch.splitlines():
        if not line.startswith("diff --git "):
            continue
        fields = shlex.split(line)
        if len(fields) != 4 or not fields[3].startswith("b/"):
            raise ValueError(f"Cannot interpret patch path: {line}")
        paths.add(repo_path(fields[3][2:]))
    return sorted(paths)


def _workspace_instructions(
    instance: InstanceRecord, manifest: SubmissionManifest, evaluation: EvaluationResult
) -> str:
    """Fill the checked-in guide with task-specific paths and execution results."""
    outcome_rows = [
        f"| `{row.test_id}` | `{row.buggy}` | `{row.golden}` | `{row.cell or 'incomplete'}` |"
        for row in evaluation.tests
    ]
    matrix_names = (
        "fail_on_buggy_pass_on_golden",
        "pass_on_both",
        "fail_on_both",
        "pass_on_buggy_fail_on_golden",
    )
    values = {
        "repair_files": "\n".join(f"- `{path}`" for path in _patch_paths(instance.golden_patch)),
        "reference_test_files": "\n".join(
            f"- `{path}`" for path in _patch_paths(instance.reference_test_patch)
        ),
        "submission_files": "\n".join(f"- `{path}`" for path in sorted(manifest.files)),
        "reference_test_ids": "\n".join(
            f"- `{test_id}`" for test_id in instance.reference_test_ids
        ),
        "matrix_totals": "\n".join(
            f"- `{name}`: **{evaluation.matrix[name].count}**" for name in matrix_names
        ),
        "outcome_rows": "\n".join(outcome_rows or ["| — | — | — | — |"]),
        "other_outcomes_count": str(len(evaluation.other_outcomes)),
    }
    return Template(INSTRUCTIONS_TEMPLATE.read_text()).substitute(values)
