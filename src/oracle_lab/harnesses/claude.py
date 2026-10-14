"""Runs Claude Code: builds the command, then reads back its JSON event stream."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from oracle_lab.container.sandbox import Sandbox
from oracle_lab.harnesses.launch import (
    AgentTurnRequest,
    read_trace,
    run_agent_turn,
    turn_status,
)
from oracle_lab.results import Usage


def parse_trace(path: Path) -> dict:
    events, malformed = read_trace(path)
    result = next((event for event in reversed(events) if event.get("type") == "result"), None)

    completed = bool(result and result.get("subtype") == "success" and not result.get("is_error"))
    errors = []
    if result and not completed:
        detail = result.get("errors") or result.get("result") or result.get("subtype")
        if isinstance(detail, list):
            detail = "; ".join(
                str(item.get("message", item)) if isinstance(item, dict) else str(item)
                for item in detail
            )
        errors = [{"message": str(detail)}]
    return {
        "turn_completed": completed,
        "turn_failed": bool(result and not completed),
        "usage": _usage(result.get("usage")) if result else None,
        "errors": errors,
        "unparsed_trace_lines": malformed,
        "final_text": result.get("result", "") if result else "",
    }


def _usage(raw: dict | None) -> dict | None:
    if not raw:
        return None
    # Claude's input_tokens leaves out the prompt tokens read from or written to cache.
    cached = raw.get("cache_read_input_tokens", 0)
    return Usage(
        input_tokens=raw.get("input_tokens", 0)
        + cached
        + raw.get("cache_creation_input_tokens", 0),
        cached_input_tokens=cached,
        output_tokens=raw.get("output_tokens", 0),
        reasoning_tokens=(raw.get("output_tokens_details") or {}).get("thinking_tokens"),
        raw_usage=raw,
    ).model_dump()


def run_turn(sandbox: Sandbox, request: AgentTurnRequest) -> dict:
    """Run Claude Code under exactly the stopping limit its configuration selects."""
    harness = request.harness
    directory = request.artifact_directory

    argv = [
        "claude",
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--no-session-persistence",
        "--safe-mode",
        "--strict-mcp-config",
        "--dangerously-skip-permissions",
        "--model",
        harness.model,
    ]
    if not harness.multi_agent:
        argv += ["--tools", "Bash,Read,Write,Edit,Glob,Grep"]

    environment = {"CLAUDE_CONFIG_DIR": "/home/oracle/.claude", "DISABLE_AUTOUPDATER": "1"}
    credential_name = harness.credential_env
    if harness.provider == "openrouter":
        environment.update(
            ANTHROPIC_BASE_URL="https://openrouter.ai/api",
            ANTHROPIC_API_KEY="",
        )
        credential_name = "ANTHROPIC_AUTH_TOKEN"

    outcome = run_agent_turn(
        sandbox,
        request,
        argv,
        environment,
        credential_name,
    )

    summary = {**asdict(outcome), **parse_trace(directory / "trace.jsonl")}
    (directory / "final.txt").write_text(summary.pop("final_text"))
    summary["status"] = turn_status(outcome, summary)
    return summary
