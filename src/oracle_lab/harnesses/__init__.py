"""Dispatch to the selected harness adapter; policy stays inside each adapter."""

from pathlib import Path

from oracle_lab.config import ClassificationConfig, HarnessConfig, RunConfig
from oracle_lab.container.sandbox import Sandbox
from oracle_lab.harnesses import claude, codex, opencode
from oracle_lab.harnesses.launch import AgentTurnRequest, require_credential
from oracle_lab.harnesses.steps import Step
from oracle_lab.harnesses.transcript import render_session
from oracle_lab.io import write_json
from oracle_lab.paths import RunPaths

HARNESSES = {"claude": claude, "codex": codex, "opencode": opencode}


def require_credentials(config: RunConfig) -> None:
    """Check every credential a configured run needs before it spends any of them."""
    for harness in (config.agent, config.judge):
        if harness is not None:
            require_credential(harness)


def run_turn(sandbox: Sandbox, request: AgentTurnRequest) -> dict:
    """Run one bounded agent turn and save its result with model provenance."""
    summary = HARNESSES[request.harness.harness].run_turn(sandbox, request)
    summary.update(provenance(request))
    write_json(request.artifact_directory / "result.json", summary)
    return summary


def read_steps(harness: str, path: Path) -> list[Step]:
    """A saved trace's steps, read by the adapter of the harness that wrote it."""
    return HARNESSES[harness].read_steps(path)


def reasoning_kind(harness: str) -> str:
    """Whether the harness's reasoning steps are raw model text or summaries."""
    return HARNESSES[harness].REASONING


def provenance(request: AgentTurnRequest) -> dict:
    """Record exactly which CLI, model, and stopping policy produced a result."""
    harness = request.harness
    return {
        "harness": harness.harness,
        "provider": harness.provider,
        "model": harness.model,
        "harness_version": request.harness_version,
        "limit": harness.limit.model_dump(),
    }


def turn_request(
    config: RunConfig | ClassificationConfig,
    harness: HarnessConfig,
    prompt: Path,
    working_directory: str,
    artifact_directory: Path,
) -> AgentTurnRequest:
    """Build a turn request for the selected harness at the pinned toolchain version."""
    return AgentTurnRequest(
        harness=harness,
        harness_version=config.toolchain.harnesses[harness.harness],
        prompt=prompt,
        working_directory=working_directory,
        artifact_directory=artifact_directory,
    )


def generate(sandbox: Sandbox, config: RunConfig, paths: RunPaths) -> dict:
    """Run the generation turn in the repository checkout."""
    result = run_turn(
        sandbox,
        turn_request(
            config,
            config.agent,
            paths.prompt,
            config.require_runtime().workdir,
            paths.generation,
        ),
    )
    render_session(paths)
    return result
