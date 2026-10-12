"""Localize a run: name the part of the buggy repository the golden repair changes.

This stage runs after the image build, for localized runs only. It reads each
changed production file from the prepared image, applies the golden patch on the
host, and compares the two versions' syntax trees (`target`). The result is
saved as `inputs/target.json` and becomes the prompt's `${test_target}`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path, PurePosixPath

from docker import DockerClient

from oracle_bench.config import RunConfig
from oracle_bench.container.sandbox import Profile, open_sandbox
from oracle_bench.localization.patches import FileDiff, apply_patch, parse_patch
from oracle_bench.localization.target import choose_target, compare
from oracle_bench.paths import RunPaths
from oracle_bench.results import InstanceRecord, LocalizedTarget, write_result


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


def localize(
    client: DockerClient,
    config: RunConfig,
    image: str,
    instance: InstanceRecord,
    paths: RunPaths,
) -> LocalizedTarget:
    runtime = config.require_runtime()
    diffs = production_diffs(instance.golden_patch, runtime.source_roots)
    changes = []
    with (
        open_sandbox(client, image, config, Profile.REFERENCE, paths.target_log) as sandbox,
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
            buggy = local.read_text(encoding="utf-8")
            fixed = apply_patch(buggy, diff) if diff.new_path is not None else None
            changes.append(compare(path, buggy, fixed))
    target = choose_target(changes)
    write_result(paths.target, target)
    return target
