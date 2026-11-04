"""Localize a run: name the part of the buggy repository the golden repair changes.

Preflight runs this stage after the image build, once per problem. Runs and task
classifications then copy what preflight found with `load_target`. It reads each
changed production file from the prepared image, applies the golden patch on the
host, and compares the two versions' syntax trees (`target`). The result is
saved as `inputs/target.json` and becomes the prompt's `${test_target}`.
"""

from __future__ import annotations

import io
import shutil
import tempfile
import tokenize
from pathlib import Path, PurePosixPath

from docker import DockerClient

from oracle_lab.config import ClassificationConfig, RunConfig
from oracle_lab.container.sandbox import Profile, open_sandbox
from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.run import (
    InstanceRecord,
    LocalizedTarget,
    read_instance_record,
    read_localized_target,
    read_status,
)
from oracle_lab.localization.patches import FileDiff, apply_patch, parse_patch
from oracle_lab.localization.target import choose_target, compare
from oracle_lab.paths import InstancePaths, OutputPaths, RunPaths


def is_test_path(path: PurePosixPath) -> bool:
    return "tests" in path.parts or path.name.startswith(("test_", "tests_"))


def production_diffs(patch: str, source_roots: list[str]) -> list[tuple[str, FileDiff]]:
    """The patch's changes to existing Python files under a source root, tests excluded.

    Each change comes with its path in the buggy repository.
    """
    roots = [PurePosixPath(root) for root in source_roots]
    found = []
    for diff in parse_patch(patch):
        if diff.old_path is None:
            continue  # a file the repair creates has no buggy version
        path = PurePosixPath(diff.old_path)
        in_source = any(root == path or root in path.parents for root in roots)
        if path.suffix == ".py" and in_source and not is_test_path(path):
            found.append((diff.old_path, diff))
    return found


def read_source(data: bytes) -> str:
    """Decode a Python file the way Python does: by its coding line, else UTF-8."""
    encoding, _ = tokenize.detect_encoding(io.BytesIO(data).readline)
    return data.decode(encoding)


def localize(
    client: DockerClient,
    config: RunConfig | ClassificationConfig,
    image: str,
    instance: InstanceRecord,
    paths: InstancePaths,
) -> LocalizedTarget:
    runtime = config.require_runtime()
    diffs = production_diffs(instance.golden_patch, runtime.source_roots)
    changes = []
    with (
        open_sandbox(client, image, config, Profile.LOCALIZATION, paths.target_log) as sandbox,
        tempfile.TemporaryDirectory() as scratch,
    ):
        # Read the code the agent will see: setup steps in the image can edit
        # tracked files, and the agent's workspace is reset the same way.
        sandbox.run(
            ["git", "reset", "--hard", instance.base_commit], timeout=config.limits.setup_seconds
        )
        for number, (path, diff) in enumerate(diffs):
            local = Path(scratch) / f"{number}.py"
            sandbox.download(str(PurePosixPath(runtime.workdir) / path), local)
            buggy = read_source(local.read_bytes())
            fixed = apply_patch(buggy, diff) if diff.new_path is not None else None
            changes.append(compare(path, buggy, fixed))
    target = choose_target(changes)
    write_result(paths.target, target)
    return target


def load_target(
    output: OutputPaths, instance: InstanceRecord, paths: InstancePaths
) -> LocalizedTarget:
    """Copy the target the newest passed preflight of this problem found.

    The preflight must have passed and resolved the same record: the same instance
    ID, base commit, and record checksum. Without one, the problem cannot be run or
    classified until it passes preflight.
    """
    for preflight in reversed(output.saved_preflights()):
        if _localized(preflight, instance):
            shutil.copyfile(preflight.target, paths.target)
            shutil.copyfile(preflight.target_log, paths.target_log)
            paths.target_source.write_text(
                preflight.root.relative_to(output.root).as_posix() + "\n"
            )
            return read_localized_target(paths.target)
    raise ValueError(
        f"No passed preflight has a target for {instance.instance_id}: "
        "run `oracle-lab preflight` with a localized config first"
    )


def _localized(preflight: RunPaths, instance: InstanceRecord) -> bool:
    """Whether this preflight passed and localized the same record."""
    if not (
        preflight.status.is_file() and preflight.instance.is_file() and preflight.target.is_file()
    ):
        return False
    status = read_status(preflight.status)
    saved = read_instance_record(preflight.instance)
    return (
        (status.stage, status.state) == ("finished", "passed")
        and saved.instance_id == instance.instance_id
        and saved.base_commit == instance.base_commit
        and saved.source.record_sha256 == instance.source.record_sha256
    )
