"""Runs Claude Code: builds the command, then reads back its JSON event stream."""

from __future__ import annotations

import re
from dataclasses import asdict, replace
from pathlib import Path

from oracle_lab.container.sandbox import Sandbox
from oracle_lab.harnesses.launch import (
    AgentTurnRequest,
    run_agent_turn,
    turn_status,
)
from oracle_lab.harnesses.steps import (
    Action,
    EditFile,
    OtherTool,
    ReadFile,
    Search,
    Shell,
    Step,
    WriteFile,
    read_trace,
)
from oracle_lab.harnesses.transcript import as_text
from oracle_lab.results import Usage

# Whether `read_steps` reasoning is the model's own text or a CLI-written summary.
REASONING = "summary"


def parse_trace(path: Path) -> dict:
    numbered, malformed = read_trace(path)
    events = [event for _, event in numbered]
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


def read_steps(path: Path) -> list[Step]:
    """The run's steps. `tool_use` and `tool_result` blocks pair up by id.

    `thinking` and `text` blocks become reasoning and message steps. The many
    `system/thinking_tokens` progress events are dropped. A tool result marked as an
    error is a failed call, except for Bash, where it means a nonzero exit.
    """
    steps: list[Step] = []
    calls: dict[str, int] = {}  # tool_use id -> index in steps
    for line, event in read_trace(path)[0]:
        message = event.get("message")
        if event.get("type") not in {"assistant", "user"} or not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        stamp = event.get("timestamp")
        for block in content:
            kind = block.get("type") if isinstance(block, dict) else None
            if kind == "thinking" and block.get("thinking"):
                steps.append(Step("reasoning", line, text=block["thinking"], timestamp=stamp))
            elif kind == "text" and event["type"] == "assistant" and block.get("text"):
                steps.append(Step("message", line, text=block["text"], timestamp=stamp))
            elif kind == "tool_use":
                calls[block.get("id")] = len(steps)
                action = _action(block.get("name", ""), block.get("input") or {})
                steps.append(Step("tool", line, action=action, timestamp=stamp))
            elif kind == "tool_result" and block.get("tool_use_id") in calls:
                index = calls.pop(block["tool_use_id"])
                steps[index] = _with_result(steps[index], block, event.get("tool_use_result"))
    return steps


def render_event(event: dict) -> list[str] | None:
    """`session.log` lines for a message event: its typed content blocks."""
    message = event.get("message")
    if not isinstance(message, dict) or not isinstance(message.get("content"), list):
        return None
    lines = []
    for block in message["content"]:
        if not isinstance(block, dict):
            lines.append(as_text(block))
            continue
        block_type = block.get("type")
        if block_type == "text":
            lines.append(block.get("text", ""))
        elif block_type == "thinking":
            lines += ["[Thinking output]", block.get("thinking") or "[Not exposed by CLI]"]
        elif block_type == "tool_use":
            lines += [f"Tool: {block.get('name')} [{block.get('id')}]", as_text(block.get("input"))]
        elif block_type == "tool_result":
            lines += [
                f"Tool result: {block.get('tool_use_id')} (error={block.get('is_error', False)})",
                as_text(block.get("content")),
            ]
        else:
            lines.append(as_text(block))
    if event.get("parent_tool_use_id"):
        lines.append(f"Parent tool: {event['parent_tool_use_id']}")
    return lines


def _action(name: str, arguments: dict) -> Action:
    path = arguments.get("file_path", "")
    if name == "Read":
        return ReadFile(path)  # the result gives the exact lines shown
    if name == "Grep":
        return Search(arguments.get("pattern", ""), arguments.get("path"))
    if name == "Write":
        return WriteFile(path, arguments.get("content", ""))
    if name == "Edit":
        return EditFile(path, arguments.get("old_string"), arguments.get("new_string"))
    if name == "Bash":
        return Shell(arguments.get("command", ""))
    return OtherTool(name, arguments)


def _with_result(step: Step, block: dict, result: object) -> Step:
    output = _result_text(block.get("content"))
    exit_code = None
    failed = bool(block.get("is_error"))
    if isinstance(step.action, Shell):
        failed = False
        exit_code = 0
        if block.get("is_error"):
            match = re.match(r"Error: Exit code (\d+)", output)
            exit_code = int(match.group(1)) if match else None
    action = step.action
    if isinstance(action, ReadFile) and isinstance(result, dict):
        shown = result.get("file") or {}
        if shown.get("startLine") and shown.get("numLines") is not None:
            start = shown["startLine"]
            action = replace(action, start=start, end=start + max(shown["numLines"] - 1, 0))
    return replace(step, action=action, output=output, exit_code=exit_code, failed=failed)


def _result_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
    return ""


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
