"""Runs OpenCode: builds the command, reads back its JSON events. OpenRouter only."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from oracle_lab.container.sandbox import Sandbox
from oracle_lab.harnesses.launch import (
    AgentTurnRequest,
    read_trace,
    run_agent_turn,
    turn_status,
)
from oracle_lab.harnesses.steps import Step
from oracle_lab.results import Usage

# Whether `read_steps` reasoning is the model's own text or a CLI-written summary.
REASONING = "raw"


def parse_trace(path: Path) -> dict:
    """Normalize OpenCode's JSON event stream into the shared harness result."""
    steps = []
    final_text = ""
    errors = []
    completed = False
    events, malformed = read_trace(path)
    for event in events:
        part = event.get("part") or {}
        if event.get("type") == "text":
            final_text = str(part.get("text", ""))
        elif event.get("type") == "step_finish":
            if part.get("tokens"):
                steps.append(part["tokens"])
            completed = part.get("reason") == "stop"
        elif event.get("type") == "error":
            errors.append(event)

    return {
        "usage": _usage(steps),
        "errors": errors,
        "unparsed_trace_lines": malformed,
        "turn_completed": completed,
        "turn_failed": bool(errors),
        "final_text": final_text,
    }


def read_steps(path: Path) -> list[Step]:
    """The run's steps. Each `tool` part is one step.

    Its `reasoning_details` text becomes a reasoning step placed just before it.
    `text` parts become message steps. Aborted `unknown` calls and `invalid`
    retries are kept as failed tool steps.
    """
    raise NotImplementedError


def _usage(steps: list[dict]) -> dict | None:
    if not steps:
        return None
    reads = sum((step.get("cache") or {}).get("read", 0) for step in steps)
    writes = sum((step.get("cache") or {}).get("write", 0) for step in steps)
    reasoning = sum(step.get("reasoning", 0) for step in steps)
    # OpenCode counts cached prompt tokens and reasoning apart from input and output.
    return Usage(
        input_tokens=sum(step.get("input", 0) for step in steps) + reads + writes,
        cached_input_tokens=reads,
        output_tokens=sum(step.get("output", 0) for step in steps) + reasoning,
        reasoning_tokens=reasoning,
        raw_usage={"steps": steps},
    ).model_dump()


def run_turn(sandbox: Sandbox, request: AgentTurnRequest) -> dict:
    """Run OpenCode with caller-supplied prompt, workspace, and artifacts."""
    harness = request.harness
    directory = request.artifact_directory
    model = f"openrouter/{harness.model}"

    argv = [
        "opencode",
        "run",
        "--format",
        "json",
        "--auto",
        "--dir",
        request.working_directory,
        "--model",
        model,
    ]

    environment = {
        "XDG_CONFIG_HOME": "/home/oracle/.config",
        "XDG_DATA_HOME": "/home/oracle/.local/share",
        "OPENCODE_DISABLE_AUTOUPDATE": "true",
    }
    if not harness.multi_agent:
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            {"permission": {"task": "deny"}}, separators=(",", ":")
        )

    outcome = run_agent_turn(
        sandbox,
        request,
        argv,
        environment,
        "OPENROUTER_API_KEY",
    )

    parsed = parse_trace(directory / "trace.jsonl")
    (directory / "final.txt").write_text(parsed.pop("final_text"))
    summary = {**asdict(outcome), **parsed}
    summary["status"] = turn_status(outcome, summary)
    return summary
