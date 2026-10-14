"""Generate tests in a buggy checkout and freeze what the agent produced.

One disposable container covers the whole stage: prepare the repository, record
its state, run the agent, then capture only new files under the generated
directory. Everything outside that directory is reported as a forbidden change
rather than silently dropped.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from docker import DockerClient

from oracle_lab.config import RunConfig, repo_path
from oracle_lab.container.sandbox import Profile, Sandbox, open_sandbox
from oracle_lab.harnesses import generate
from oracle_lab.harnesses.retry import with_retries
from oracle_lab.io import archive_files, digest, read_json
from oracle_lab.paths import RunPaths
from oracle_lab.repository import Repository
from oracle_lab.results import (
    InstanceRecord,
    SubmissionFile,
    SubmissionManifest,
    write_result,
)


def generate_submission(
    client: DockerClient,
    config: RunConfig,
    image: str,
    instance: InstanceRecord,
    paths: RunPaths,
) -> SubmissionManifest:
    """Run generation in a fresh checkout, retrying transient provider failures."""

    def attempt(
        will_retry: Callable[[dict], bool],
    ) -> tuple[dict, SubmissionManifest | None]:
        log = paths.generation / "setup.log"
        with open_sandbox(client, image, config, Profile.GENERATION, log) as sandbox:
            workspace = Repository(sandbox, config)
            # The agent needs Git for normal inspection and diffing, but must not be able
            # to recover hidden tests or issue details from the source repository's past.
            workspace.prepare(instance.base_commit, paths.generation, sanitize_history=True)
            before = snapshot(sandbox, paths.generation / "before.json", log)
            baseline = workspace.baseline(paths.generation / "baseline.txt")
            result = generate(sandbox, config, paths)
            if will_retry(result):
                return result, None
            return result, capture(sandbox, config, paths, before, baseline)

    def archive() -> None:
        # An interrupted agent may have left incomplete tests and repository edits.
        archive_files(list(paths.generation.iterdir()), paths.generation_history)

    submission = with_retries(attempt, archive)
    assert submission is not None
    return submission


def changed_files(before: dict, after: dict, generated_dir: str) -> tuple[list[str], list[str]]:
    allowed, forbidden = [], []
    root = PurePosixPath(generated_dir)
    for name in sorted(before.keys() | after.keys()):
        if before.get(name) == after.get(name):
            continue
        path = PurePosixPath(repo_path(name))
        entry = after.get(name)
        # New regular files only; symlinks cannot cause host-side reads or replacement of code.
        if root in path.parents and name not in before and entry and entry["kind"] == "file":
            allowed.append(name)
        else:
            forbidden.append(name)
    return allowed, forbidden


def snapshot(sandbox: Sandbox, target: Path, log: Path) -> dict:
    runtime = sandbox.runtime
    sandbox.run(
        [
            runtime.python,
            sandbox.helpers + "/workspace.py",
            "snapshot",
            runtime.workdir,
            "/tmp/oracle-snapshot.json",
        ],
        log=log,
        timeout=120,
    )
    sandbox.download("/tmp/oracle-snapshot.json", target)
    return read_json(target)


def capture(
    sandbox: Sandbox, config: RunConfig, paths: RunPaths, before: dict, baseline: str
) -> SubmissionManifest:
    runtime = config.require_runtime()
    log = paths.generation / "capture.log"
    after = snapshot(sandbox, paths.generation / "after.json", log)
    allowed, forbidden = changed_files(before, after, config.task.generated_dir)
    generated = paths.submission
    # Keep the frozen bundle structurally complete even when the agent creates no
    # tests. Downstream verification and diagnostics can then inspect an empty
    # directory instead of failing because the bundle root never existed.
    (generated / "files").mkdir(parents=True, exist_ok=True)
    sandbox.run(
        [
            runtime.python,
            sandbox.helpers + "/workspace.py",
            "diff",
            runtime.workdir,
            "/tmp/oracle-workspace.diff",
            baseline,
        ],
        log=log,
        timeout=120,
        check=False,
    )
    sandbox.download(
        "/tmp/oracle-workspace.diff",
        paths.generation / "workspace.diff",
        required=False,
    )
    files: dict[str, SubmissionFile] = {}
    for name in allowed:
        target = generated / "files" / name
        sandbox.download(runtime.workdir + "/" + name, target)
        # Copy only regular files with the exact bytes measured in the final snapshot.
        if target.is_symlink() or digest(target.read_bytes()) != after[name]["sha256"]:
            raise RuntimeError(f"Artifact changed during capture: {name}")
        files[name] = SubmissionFile(sha256=after[name]["sha256"], mode=after[name]["mode"])
    manifest = SubmissionManifest(
        files=files,
        forbidden_changes=forbidden,
        compliant=not forbidden,
        empty=not files,
        sha256=_files_digest(files),
    )
    write_result(generated / "manifest.json", manifest)
    return manifest


def _files_digest(files: dict[str, SubmissionFile]) -> str:
    plain = {name: entry.model_dump() for name, entry in files.items()}
    return digest(json.dumps(plain, sort_keys=True).encode())


def verify_bundle(paths: RunPaths) -> SubmissionManifest:
    """Validate the external bundle before transferring it to either code version."""
    generated = paths.submission
    manifest = SubmissionManifest.model_validate(read_json(generated / "manifest.json"))
    if _files_digest(manifest.files) != manifest.sha256:
        raise ValueError("Generated manifest checksum mismatch")
    root = generated / "files"
    entries = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_symlink() or not path.is_dir()
    }
    if entries != set(manifest.files):
        raise ValueError("Generated bundle contains missing or unmanifested files")
    for name, info in manifest.files.items():
        path = root / repo_path(name)
        if path.is_symlink() or not path.is_file() or digest(path.read_bytes()) != info.sha256:
            raise ValueError(f"Generated artifact checksum mismatch: {name}")
    return manifest
