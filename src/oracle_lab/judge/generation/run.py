"""Standalone execution of the privileged generated-test judge."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from docker import DockerClient
from docker.errors import DockerException

from oracle_lab.config import RunConfig
from oracle_lab.container.images import require_saved_image
from oracle_lab.container.paths import JUDGE_ROOT
from oracle_lab.container.sandbox import Profile, docker_client, open_sandbox
from oracle_lab.contracts.judgment import JudgmentResult, parse_judgment, validate_judgment
from oracle_lab.contracts.run import (
    InstanceRecord,
    SubmissionManifest,
    read_evaluation_result,
    read_instance_record,
    read_status,
    save_status,
    verify_bundle,
)
from oracle_lab.contracts.turn import failed_turn, turn_failure
from oracle_lab.harnesses import record_turn_error, run_turn, save_raw_answer, turn_request
from oracle_lab.harnesses.launch import require_credential
from oracle_lab.harnesses.retry import with_retries
from oracle_lab.io import archive_files, read_json, require_files, write_json
from oracle_lab.judge.generation.workspace import build_judge_workspace
from oracle_lab.paths import RunPaths


def judge(config: RunConfig, paths: RunPaths) -> JudgmentResult:
    """Judge one completed run without repeating generation or evaluation.

    Rendering belongs to the caller, so this stage never depends on the report.
    """
    require_credential(config.require_judge())
    require_files(
        [
            paths.runtime,
            paths.instance,
            paths.results,
            paths.judge.rubric,
        ],
        "Judge inputs",
    )
    image = read_json(paths.runtime)["image"]
    instance = read_instance_record(paths.instance)
    evaluation = read_evaluation_result(paths.results)

    manifest = verify_bundle(paths)
    if manifest.empty or evaluation.no_tests:
        raise ValueError("Cannot judge a submission with no generated tests")
    with docker_client() as client:
        require_saved_image(client, image, "judging")
        judgment = judge_stage(client, config, paths, image, instance, manifest)

    if paths.status.is_file():
        status = read_status(paths.status)
        update = {
            "judge_status": judgment.status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        save_status(paths.status, status.model_copy(update=update))
    return judgment


def judge_stage(
    client: DockerClient,
    config: RunConfig,
    paths: RunPaths,
    image: str,
    instance: InstanceRecord,
    manifest: SubmissionManifest,
) -> JudgmentResult:
    """Build the workspace, run one judge turn, and always save a judgment either way."""
    # Built before anything is moved, so a bad harness pin fails without destroying
    # the previous attempt. The failure handler needs it to record provenance.
    request = turn_request(
        config, config.require_judge(), paths.judge.prompt, JUDGE_ROOT, paths.judge.agent
    )
    try:
        # Move a previous attempt aside. The frozen rubric stays put.
        previous = [path for path in paths.judge.root.iterdir() if path != paths.judge.rubric]
        archive_files(previous, paths.judge.history)

        # The rubric is the whole prompt.
        paths.judge.prompt.write_text(paths.judge.rubric.read_text())

        def attempt(will_retry: Callable[[dict], bool]) -> tuple[dict, None]:
            with open_sandbox(client, image, config, Profile.JUDGE, paths.judge.log) as sandbox:
                build_judge_workspace(sandbox, config, paths, image, instance, manifest)
                result = run_turn(sandbox, request)
                if not will_retry(result):
                    # Save before container cleanup so a cleanup failure cannot
                    # erase a valid answer the completed turn already produced.
                    _save_judgment(result, paths)
            return result, None

        with_retries(attempt, lambda: archive_files([paths.judge.agent], paths.judge.agent_history))
    except (RuntimeError, OSError, ValueError, DockerException) as error:
        # A judge failure never touches generation or evaluation evidence. A judgment
        # already saved is kept, since a late failure such as cleanup doesn't undo it.
        record_turn_error(paths.judge, paths.judge.judgment_raw, request, error)
        if not paths.judge.judgment.is_file():
            write_json(paths.judge.judgment, failed_turn(str(error)).model_dump())
    return validate_judgment(read_json(paths.judge.judgment))


def _save_judgment(result: dict, paths: RunPaths) -> None:
    """Parse and save the final judge attempt after retry policy is exhausted."""
    raw = save_raw_answer(paths.judge, paths.judge.judgment_raw)
    judgment = turn_failure(result, "Judge") or parse_judgment(raw)
    write_json(paths.judge.judgment, judgment.model_dump())
