"""Construct the privileged, reproducible workspace used by test judges."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal

from oracle_lab.config import RunConfig, repo_path
from oracle_lab.container.paths import JUDGE_ROOT
from oracle_lab.container.sandbox import Sandbox
from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.judgment import BundleManifest, PreparedImage, WorkspaceSpec
from oracle_lab.contracts.run import (
    InstanceRecord,
    SubmissionManifest,
)
from oracle_lab.contracts.turn import ExposedArtifact
from oracle_lab.instance.swebench import test_ids
from oracle_lab.io import digest, read_json, require_files, write_json
from oracle_lab.paths import RunPaths
from oracle_lab.trace.build import build_run_trace
from oracle_lab.trace.events import read_run_trace
from oracle_lab.trace.markdown import trace_markdown

HELPER = "/judge_workspace.py"
# The judge compares these two checkouts of the same repository.
VIEWS = (JUDGE_ROOT + "/buggy", JUDGE_ROOT + "/golden")
REPAIR = JUDGE_ROOT + "/instance/fix.patch"


def build_judge_workspace(
    sandbox: Sandbox,
    config: RunConfig,
    paths: RunPaths,
    image: str,
    instance: InstanceRecord,
    manifest: SubmissionManifest,
) -> WorkspaceSpec:
    """Assemble /oracle-judge in the container: two repo checkouts plus the evidence.

    `judge_evidence` is the whole exposure contract. Nothing outside it is uploaded,
    and the returned spec records every file so a reader can check what the judge
    was shown.
    """
    config.require_judge()
    runtime = config.require_runtime()
    base_commit = instance.base_commit

    artifacts: list[ExposedArtifact] = []
    with TemporaryDirectory(prefix="oracle-judge-bundle-") as temporary:
        staging = Path(temporary)
        save_trace(paths)  # first, so the evidence can expose it
        evidence = judge_evidence(paths, staging, instance, manifest)

        # images.json is read at the end for the spec, not shown to the judge.
        images = paths.build / "images.json"
        require_files([source for source, _ in evidence] + [images], "Judge workspace inputs")

        # 1. Reset the checkout to the instance's base commit, then cut two views of it.
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

        # 2. Upload the evidence, recording each file as it goes.
        for source, destination in evidence:
            sandbox.upload(source, destination)
            artifacts.append(_exposed(paths.root, source, destination))

        # 3. The repair goes into the golden view only. The buggy view stays broken.
        apply = ["git", "-C", JUDGE_ROOT + "/golden", "apply"]
        sandbox.run([*apply, "--check", REPAIR], timeout=config.limits.setup_seconds)
        sandbox.run([*apply, REPAIR], timeout=config.limits.setup_seconds)

        # 4. The same submission goes into both views, so the judge reads one set of
        #    tests against two versions of the code.
        for view in VIEWS:
            sandbox.upload(paths.submission / "files", view, contents=True)
            for name, info in manifest.files.items():
                source = paths.submission / "files" / repo_path(name)
                artifacts.append(_exposed(paths.root, source, f"{view}/{name}", info.sha256))

        # 5. Trust the container over our own copy: make it hash what it actually holds
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

    sandbox.run(["chmod", "-R", "a+rX", JUDGE_ROOT])
    artifacts.sort(key=lambda artifact: (artifact.destination, artifact.source))

    runtime_image = read_json(images)["runtime"]
    image_record = PreparedImage(
        reference=image, image_id=runtime_image["id"], digests=runtime_image["digests"]
    )
    spec = WorkspaceSpec(
        prepared_image=image_record,
        base_commit=base_commit,
        repair_patch_sha256=digest((paths.ground_truth / "fix.patch").read_bytes()),
        reference_test_patch_sha256=digest(instance.reference_test_patch.encode()),
        rubric_sha256=digest(paths.judge.rubric.read_bytes()),
        workspace_method=method,
        reconstruction_commands=commands,
        artifacts=artifacts,
    )
    write_json(paths.judge.image, image_record.model_dump())
    write_json(paths.judge.bundle_manifest, BundleManifest(artifacts=artifacts).model_dump())
    write_json(paths.judge.workspace_spec, spec.model_dump())
    return spec


def save_trace(paths: RunPaths) -> bool:
    """Build the agent's trace and save it as `judge/trace.json`. False when it cannot be
    built, in which case the judge reads the raw harness trace instead."""
    paths.judge.trace.unlink(missing_ok=True)
    try:
        trace = build_run_trace(paths)
    except Exception:  # noqa: BLE001 - it parses agent output; any failure means no summary
        return False
    write_json(paths.judge.trace, trace.to_json())
    return True


def judge_evidence(
    paths: RunPaths,
    staging: Path,
    instance: InstanceRecord,
    manifest: SubmissionManifest,
) -> list[tuple[Path, str]]:
    """Every file the judge may read, and where it lands in the container.

    Adding a row here is the only way to expose something new. Evidence that is not
    already a file in the run is written to `staging`, so it never lands in the run.
    Human review shows the same rows.
    """
    reference_patch = staging / "reference-tests.patch"
    metadata = staging / "metadata.json"
    submission_manifest = staging / "submission-manifest.json"
    original = instance.original_record
    reference_patch.write_text(instance.reference_test_patch)
    write_json(
        metadata,
        {
            "instance_id": instance.instance_id,
            "repo": instance.repo,
            "base_commit": instance.base_commit,
            "FAIL_TO_PASS": test_ids(original, "FAIL_TO_PASS"),
            "PASS_TO_PASS": test_ids(original, "PASS_TO_PASS"),
            "reference_test_ids": instance.reference_test_ids,
        },
    )
    write_result(submission_manifest, manifest)

    return [
        (paths.judge.rubric, JUDGE_ROOT + "/rubric.md"),
        (paths.ground_truth / "issue.md", JUDGE_ROOT + "/instance/issue.md"),
        (paths.ground_truth / "fix.patch", REPAIR),
        (reference_patch, JUDGE_ROOT + "/instance/reference-tests.patch"),
        (metadata, JUDGE_ROOT + "/instance/metadata.json"),
        (submission_manifest, JUDGE_ROOT + "/evidence/submission-manifest.json"),
        (paths.generation / "session.log", JUDGE_ROOT + "/evidence/agent-session.log"),
        *_trace_evidence(paths, staging),
        (paths.generation / "workspace.diff", JUDGE_ROOT + "/evidence/workspace.diff"),
        (paths.results, JUDGE_ROOT + "/evidence/paired-results.json"),
        (paths.evaluation / "buggy/tests.json", JUDGE_ROOT + "/evidence/buggy-tests.json"),
        (paths.evaluation / "buggy/output.log", JUDGE_ROOT + "/evidence/buggy-output.log"),
        (paths.evaluation / "golden/tests.json", JUDGE_ROOT + "/evidence/golden-tests.json"),
        (paths.evaluation / "golden/output.log", JUDGE_ROOT + "/evidence/golden-output.log"),
    ]


def _trace_evidence(paths: RunPaths, staging: Path) -> list[tuple[Path, str]]:
    """The agent's trace twice, summarized and in full, or the raw trace without a build."""
    if not paths.judge.trace.is_file():
        return [(paths.generation / "trace.jsonl", JUDGE_ROOT + "/evidence/agent-trace.jsonl")]
    summary = staging / "agent-trace.md"
    summary.write_text(trace_markdown(read_run_trace(paths.judge.trace).to_json()))
    return [
        (summary, JUDGE_ROOT + "/evidence/agent-trace.md"),
        (paths.judge.trace, JUDGE_ROOT + "/evidence/agent-trace.json"),
    ]


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
