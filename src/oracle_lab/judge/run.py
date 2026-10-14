"""Standalone execution of the privileged generated-test judge."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from string import Template

from docker import DockerClient
from docker.errors import DockerException

from oracle_lab.config import RunConfig
from oracle_lab.container.images import require_saved_image
from oracle_lab.container.sandbox import Profile, docker_client, open_sandbox
from oracle_lab.generation import verify_bundle
from oracle_lab.harnesses import provenance, run_turn, turn_request
from oracle_lab.harnesses.launch import require_credential
from oracle_lab.harnesses.retry import with_retries
from oracle_lab.io import archive_files, read_json, require_files, write_json
from oracle_lab.judge.contracts import JudgmentResult, parse_judgment, validate_judgment
from oracle_lab.judge.workspace import JUDGE_ROOT, build_judge_workspace
from oracle_lab.paths import RunPaths
from oracle_lab.results import (
    EvaluationResult,
    InstanceRecord,
    SubmissionManifest,
    failed_turn,
    last_error_message,
    read_evaluation_result,
    read_instance_record,
)


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
            paths.judge.instructions,
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
        judgment = judge_stage(client, config, paths, image, instance, evaluation, manifest)

    if paths.status.is_file():
        status = read_json(paths.status)
        status["judge_status"] = judgment.status
        status["updated_at"] = datetime.now(timezone.utc).isoformat()
        write_json(paths.status, status)
    return judgment


def judge_stage(
    client: DockerClient,
    config: RunConfig,
    paths: RunPaths,
    image: str,
    instance: InstanceRecord,
    evaluation: EvaluationResult,
    manifest: SubmissionManifest,
) -> JudgmentResult:
    """Build the workspace, run one judge turn, and always save a judgment either way."""
    # Built before anything is moved, so a bad harness pin fails without destroying
    # the previous attempt. The failure handler needs it to record provenance.
    request = turn_request(
        config, config.require_judge(), paths.judge.prompt, JUDGE_ROOT, paths.judge.agent
    )
    try:
        # Move a previous automated attempt aside. Human ratings stay put.
        human = paths.judge.human
        preserved = {
            paths.judge.rubric.name,
            paths.judge.instructions.name,
            human.judgments.name,
            human.history.name,
            human.workspaces.name,
        }
        previous = [path for path in paths.judge.root.iterdir() if path.name not in preserved]
        archive_files(previous, paths.judge.history)

        # The instructions name the workspace; the rubric follows them verbatim.
        instructions = Template(paths.judge.instructions.read_text())
        paths.judge.prompt.write_text(
            instructions.substitute(root=JUDGE_ROOT) + paths.judge.rubric.read_text()
        )

        def attempt(will_retry: Callable[[dict], bool]) -> tuple[dict, None]:
            with open_sandbox(client, image, config, Profile.JUDGE, paths.judge.log) as sandbox:
                build_judge_workspace(sandbox, config, paths, image, instance, evaluation, manifest)
                result = run_turn(sandbox, request)
                if not will_retry(result):
                    # Save before container cleanup so a cleanup failure cannot
                    # erase a valid answer the completed turn already produced.
                    _save_judgment(result, paths)
            return result, None

        with_retries(attempt, lambda: archive_files([paths.judge.agent], paths.judge.history))
    except (RuntimeError, OSError, ValueError, DockerException) as error:
        # A judge failure never touches generation or evaluation evidence. A result
        # already saved is kept, since a late failure such as cleanup doesn't undo it.
        attempt_path = paths.judge.result
        if attempt_path.is_file():
            saved = read_json(attempt_path)
        else:
            saved = {
                "status": "failed",
                "duration_seconds": 0,
                "usage": None,
                "errors": [],
                **provenance(request),
            }
        saved["errors"] = [*saved["errors"], {"message": str(error)}]
        write_json(attempt_path, saved)
        if not paths.judge.judgment_raw.exists():
            # Keep whatever the model managed to write before the failure.
            final = paths.judge.final
            paths.judge.judgment_raw.write_text(
                final.read_text(errors="replace") if final.is_file() else ""
            )
        if not paths.judge.judgment.is_file():
            write_json(paths.judge.judgment, failed_turn(str(error)).model_dump())
    return validate_judgment(read_json(paths.judge.judgment))


def _save_judgment(result: dict, paths: RunPaths) -> None:
    """Parse and save the final judge attempt after retry policy is exhausted."""
    final = paths.judge.final
    raw = final.read_text(errors="replace") if final.is_file() else ""
    paths.judge.judgment_raw.write_text(raw)
    if result["status"] == "completed":
        judgment = parse_judgment(raw)
    else:
        # Prefer the harness's own last error over a generic status line.
        reason = last_error_message(result["errors"]) or (
            f"Judge harness ended with status {result['status']}"
        )
        judgment = failed_turn(reason, timed_out=result["status"] == "timeout")
    write_json(paths.judge.judgment, judgment.model_dump())
