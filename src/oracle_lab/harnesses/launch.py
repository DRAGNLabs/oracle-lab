"""Shared execution and artifact policy for one coding-agent turn."""

import os
from dataclasses import dataclass
from pathlib import Path

from oracle_lab.config import HarnessConfig
from oracle_lab.container.lifecycle import preserve_failure
from oracle_lab.container.paths import ORACLE_HOME
from oracle_lab.container.sandbox import ORACLE_USER, CommandResult, Sandbox
from oracle_lab.io import write_json


def require_credential(harness: HarnessConfig) -> None:
    """Fail before a container starts if the selected credential is missing."""
    if not os.environ.get(harness.credential_env):
        raise RuntimeError(f"Set {harness.credential_env} to run the {harness.harness} harness")


@dataclass(frozen=True)
class AgentTurnRequest:
    """Where one bounded agent turn runs and where its evidence is written.

    Callers describe the task; the adapter decides how to run it.
    """

    harness: HarnessConfig
    harness_version: str
    prompt: Path
    working_directory: str
    artifact_directory: Path


def turn_status(outcome: CommandResult, parsed: dict) -> str:
    """One rule for every harness: a timeout, a failure, or a completed turn."""
    if outcome.timed_out:
        return "timeout"
    if outcome.exit_code or parsed["turn_failed"] or not parsed["turn_completed"]:
        return "failed"
    return "completed"


def run_agent_turn(
    sandbox: Sandbox,
    request: AgentTurnRequest,
    argv: list[str],
    environment: dict[str, str],
    credential_name: str,
    *,
    last_message_path: str | None = None,
) -> CommandResult:
    """Launch one harness command while enforcing credential and I/O policy."""
    require_credential(request.harness)
    directory = request.artifact_directory
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "launch.log"
    key = os.environ[request.harness.credential_env]
    environment = {**environment, credential_name: key, "HOME": ORACLE_HOME}
    write_json(directory / "command.json", argv)

    # The CLIs expect their config directories to already exist.
    sandbox.run(
        [
            "mkdir",
            "-p",
            "/tmp/oracle-agent",
            ORACLE_HOME + "/.codex",
            ORACLE_HOME + "/.claude",
            ORACLE_HOME + "/.config/opencode",
            ORACLE_HOME + "/.local/share/opencode",
        ],
        user=ORACLE_USER,
        log=log,
    )
    # Save what version of the CLI actually ran.
    sandbox.run(
        [argv[0], "--version"],
        user=ORACLE_USER,
        stdout=directory / "version.txt",
        log=log,
        secrets=(key,),
        workdir=request.working_directory,
    )

    try:
        outcome = sandbox.run(
            argv,
            timeout=request.harness.timeout_seconds,
            user=ORACLE_USER,
            environment=environment,
            stdin=request.prompt,
            stdout=directory / "trace.jsonl",
            stderr=directory / "stderr.log",
            log=log,
            check=False,
            secrets=(key,),
            workdir=request.working_directory,
        )
    finally:
        with preserve_failure(
            "Agent output collection also failed; streamed evidence is retained."
        ):
            collect_output(sandbox, directory, key, last_message_path)
    return outcome


def collect_output(
    sandbox: Sandbox, directory: Path, key: str, last_message_path: str | None
) -> None:
    # Detached descendants can escape a process group. Restarting the same
    # disposable container freezes the filesystem before collecting evidence.
    sandbox.stop_background_processes()
    if last_message_path:
        sandbox.download(
            last_message_path,
            directory / "final.txt",
            required=False,
            secrets=(key,),
        )
