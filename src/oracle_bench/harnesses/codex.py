"""Runs Codex: builds the command, wires up OpenRouter, reads back its JSON events."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from oracle_bench.container.sandbox import Sandbox
from oracle_bench.harnesses.launch import (
    AgentTurnRequest,
    read_trace,
    run_agent_turn,
    turn_status,
)
from oracle_bench.results import Usage

# Codex writes its final response to a file instead of the JSON event stream.
LAST_MESSAGE_PATH = "/tmp/oracle-agent/final.txt"


def parse_trace(path: Path) -> dict:
    usage = None
    errors = []
    completed = False
    failed = False
    events, malformed = read_trace(path)
    for event in events:
        if event.get("type") == "turn.completed":
            usage = _usage(event.get("usage"))
            completed = True
            failed = False
        if event.get("type") == "turn.failed":
            failed = True
        if event.get("type") in {"error", "turn.failed"}:
            errors.append(event)

    return {
        "usage": usage,
        "errors": errors,
        "unparsed_trace_lines": malformed,
        "turn_completed": completed,
        "turn_failed": failed,
    }


def _usage(raw: dict | None) -> dict | None:
    if not raw:
        return None
    # Codex's input_tokens already counts the cached prompt tokens.
    return Usage(
        input_tokens=raw.get("input_tokens", 0),
        cached_input_tokens=raw.get("cached_input_tokens", 0),
        output_tokens=raw.get("output_tokens", 0),
        reasoning_tokens=raw.get("reasoning_output_tokens"),
        raw_usage=raw,
    ).model_dump()


def run_turn(sandbox: Sandbox, request: AgentTurnRequest) -> dict:
    """Run Codex with caller-supplied prompt, workspace, and artifact paths."""
    harness = request.harness
    directory = request.artifact_directory

    argv = [
        "codex",
        "exec",
        "--json",
        "--ephemeral",
        "--ignore-user-config",
        "--ignore-rules",
        "--dangerously-bypass-approvals-and-sandbox",
        "--model",
        harness.model,
        "--cd",
        request.working_directory,
        "--output-last-message",
        LAST_MESSAGE_PATH,
        "-",
    ]
    settings = []
    if not harness.multi_agent:
        settings.append("features.multi_agent=false")
    if harness.provider == "openrouter":
        # CLI overrides still apply with --ignore-user-config. Credentials stay
        # in the process environment, never in the saved command or config.
        settings += [
            'model_provider="openrouter"',
            'model_providers.openrouter.name="OpenRouter"',
            'model_providers.openrouter.base_url="https://openrouter.ai/api/v1"',
            'model_providers.openrouter.wire_api="responses"',
            # Keep transient provider and SSE failures inside this bounded turn.
            # These values match Codex's documented defaults but remain explicit
            # so a CLI upgrade cannot silently change benchmark behavior.
            "model_providers.openrouter.request_max_retries=4",
            "model_providers.openrouter.stream_max_retries=5",
            'model_providers.openrouter.auth.command="sh"',
            'model_providers.openrouter.auth.args=["-c", "echo $OPENROUTER_API_KEY"]',
        ]
    # Settings go right after `codex exec`, in the order written above.
    argv[2:2] = [part for setting in settings for part in ("--config", setting)]
    credential_name = "OPENROUTER_API_KEY" if harness.provider == "openrouter" else "CODEX_API_KEY"

    outcome = run_agent_turn(
        sandbox,
        request,
        argv,
        {"CODEX_HOME": "/home/oracle/.codex"},
        credential_name,
        last_message_path=LAST_MESSAGE_PATH,
    )

    summary = {**asdict(outcome), **parse_trace(directory / "trace.jsonl")}
    summary["status"] = turn_status(outcome, summary)
    return summary
