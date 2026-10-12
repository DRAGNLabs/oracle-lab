"""Run the frozen submission on both revisions and pair the outcomes.

Covers two run stages: the private reference check that proves an instance can
distinguish its revisions, and the paired evaluation of the agent's submission.
"""

from __future__ import annotations

from pathlib import Path

from docker import DockerClient
from docker.errors import DockerException

from oracle_bench.config import RunConfig
from oracle_bench.container.lifecycle import preserve_failure
from oracle_bench.container.sandbox import Profile, Sandbox, open_sandbox
from oracle_bench.evaluation.outcomes import (
    incomplete_result,
    pair_results,
    record_unavailable_coverage,
)
from oracle_bench.generation import verify_bundle
from oracle_bench.io import archive_files, read_json, write_json
from oracle_bench.paths import RunPaths
from oracle_bench.repository import Repository
from oracle_bench.results import (
    VERSIONS,
    CoverageResult,
    EvaluationResult,
    HarnessResult,
    InstanceRecord,
    VersionResult,
    read_localized_target,
    read_version_result,
    write_result,
)


def run_tests(
    sandbox: Sandbox, config: RunConfig, targets: list[str], directory: Path
) -> VersionResult:
    """Run the tests in a container, then turn what came back into a result.

    Anything short of a clean run becomes an explicit incomplete result, never a
    silent pass.
    """
    runtime = config.require_runtime()
    settings = {
        "workdir": runtime.workdir,
        "output": "/tmp/oracle-results",
        "targets": targets,
        "source_roots": runtime.source_roots,
        "import_modules": runtime.import_modules,
        "pytest_plugins": runtime.pytest_plugins,
    }
    write_json(directory / "runner.json", settings)
    sandbox.upload(directory / "runner.json", "/tmp/oracle-runner.json")
    try:
        execution = sandbox.run(
            [runtime.python, sandbox.helpers + "/pytest_runner.py", "/tmp/oracle-runner.json"],
            timeout=config.limits.evaluation_seconds,
            check=False,
        )
    finally:
        # Collect evidence on every exit, without hiding an execution failure.
        with preserve_failure("Partial runner output could not be collected."):
            sandbox.download("/tmp/oracle-results", directory, contents=True, required=False)
    write_json(directory / "execution.json", vars(execution))

    # Only now that the process and the download are done can the evidence be read.
    if execution.timed_out:
        return incomplete_result(directory, "timeout", "Evaluation exceeded its time limit")
    if not (directory / "tests.json").exists():
        return incomplete_result(directory, "runner_error", "Runner did not produce results")
    try:
        result = read_version_result(directory / "tests.json")
    except (OSError, ValueError) as error:
        # Container output enters the host here, so malformed evidence becomes an
        # explicit incomplete result instead of reaching the comparison.
        return incomplete_result(directory, "runner_error", f"Unreadable runner output: {error}")
    if execution.exit_code and result.status == "completed":
        return incomplete_result(directory, "runner_error", "Runner exited unsuccessfully")
    record_unavailable_coverage(directory, "Runner did not produce coverage output")
    return result


def run_version(
    client: DockerClient,
    config: RunConfig,
    image: str,
    instance: InstanceRecord,
    paths: RunPaths,
    version: str,
    *,
    reference: bool = False,
) -> VersionResult:
    runtime = config.require_runtime()
    directory = (paths.reference if reference else paths.evaluation) / version
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "output.log"
    try:
        profile = Profile.REFERENCE if reference else Profile.EVALUATION
        with open_sandbox(client, image, config, profile, log) as sandbox:
            workspace = Repository(sandbox, config)
            workspace.prepare(instance.base_commit, directory, reference=reference)
            if version == "golden":
                workspace.apply_patch(instance.golden_patch, directory / "repair.patch")
                workspace.rebuild()
            if reference:
                workspace.apply_patch(instance.reference_test_patch, directory / "reference.patch")
                targets = instance.reference_test_ids
                if not targets:
                    raise ValueError("No reference test IDs available for the pair smoke check")
            else:
                manifest = verify_bundle(paths)
                if manifest["empty"]:
                    return incomplete_result(directory, "no_tests", "Agent produced no test files")
                sandbox.upload(paths.submission / "files", runtime.workdir, contents=True)
                targets = [config.task.generated_dir]
            return run_tests(sandbox, config, targets, directory)
    except (RuntimeError, OSError, ValueError, DockerException) as exc:
        return incomplete_result(directory, "infrastructure_error", str(exc))


def check_reference(
    client: DockerClient,
    config: RunConfig,
    image: str,
    instance: InstanceRecord,
    paths: RunPaths,
) -> None:
    buggy = run_version(client, config, image, instance, paths, "buggy", reference=True)
    golden = run_version(client, config, image, instance, paths, "golden", reference=True)
    paired = pair_results(buggy, golden)
    write_result(paths.reference / "results.json", paired)
    if not paired.has_fail_on_buggy_pass_on_golden:
        raise RuntimeError(
            "Reference pair check did not reproduce a fail/pass test; "
            "inspect reference-check/ before spending on generation"
        )


def archive_previous_evaluation(paths: RunPaths) -> None:
    previous = [
        paths.evaluation / name
        for name in ("buggy", "golden", "results.json")
        if (paths.evaluation / name).exists()
    ]
    archive_files(previous, paths.evaluation_history)


def evaluate(
    client: DockerClient,
    config: RunConfig,
    image: str,
    instance: InstanceRecord,
    paths: RunPaths,
) -> EvaluationResult:
    manifest = verify_bundle(paths)
    archive_previous_evaluation(paths)
    buggy = run_version(client, config, image, instance, paths, "buggy")
    golden = run_version(client, config, image, instance, paths, "golden")
    paired = pair_results(buggy, golden)
    result = EvaluationResult(
        **paired.model_dump(),
        instance_id=instance.instance_id,
        artifact_sha256=manifest["sha256"],
        submission_compliant=manifest["compliant"],
        forbidden_changes=manifest["forbidden_changes"],
        task_scope=config.task.scope,
        test_target=read_localized_target(paths.target)
        if config.task.scope == "localized"
        else None,
        existing_tests=config.task.existing_tests,
        agent=HarnessResult.model_validate(read_json(paths.generation / "result.json")),
        coverage={
            version: CoverageResult.model_validate(
                read_json(paths.evaluation / version / "coverage.json")
            )
            for version in VERSIONS
        },
    )
    write_result(paths.results, result)
    return result
