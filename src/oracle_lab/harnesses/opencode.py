"""Runs OpenCode: builds the command, reads back its JSON events. OpenRouter only."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
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
from oracle_lab.results import Usage

# Whether `read_steps` reasoning is the model's own text or a CLI-written summary.
REASONING = "raw"


def parse_trace(path: Path) -> dict:
    """Normalize OpenCode's JSON event stream into the shared harness result."""
    step_tokens = []
    final_text = ""
    errors = []
    completed = False
    numbered, malformed = read_trace(path)
    events = [event for _, event in numbered]
    for event in events:
        part = event.get("part") or {}
        if event.get("type") == "text":
            final_text = str(part.get("text", ""))
        elif event.get("type") == "step_finish":
            if part.get("tokens"):
                step_tokens.append(part["tokens"])
            completed = part.get("reason") == "stop"
        elif event.get("type") == "error":
            errors.append(event)

    return {
        "usage": _usage(step_tokens),
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
    retries are kept as failed tool steps. Calls in one model message can repeat the
    same reasoning, so each message's reasoning is kept once.
    """
    steps: list[Step] = []
    reasoned: set[tuple[str, str]] = set()
    for line, event in read_trace(path)[0]:
        part = event.get("part")
        if not isinstance(part, dict):
            continue
        stamp = _iso(event.get("timestamp"))
        message_id = part.get("messageID", "")
        kind = part.get("type")
        if kind == "reasoning" and part.get("text"):
            reasoned.add((message_id, part["text"]))
            steps.append(Step("reasoning", line, text=part["text"], timestamp=stamp))
        elif kind == "text" and part.get("text"):
            steps.append(Step("message", line, text=part["text"], timestamp=stamp))
        elif kind == "tool":
            details = ((part.get("metadata") or {}).get("openrouter") or {}).get(
                "reasoning_details"
            ) or []
            for detail in details:
                text = detail.get("text") if isinstance(detail, dict) else None
                if text and (message_id, text) not in reasoned:
                    reasoned.add((message_id, text))
                    steps.append(Step("reasoning", line, text=text, timestamp=stamp))
            steps.append(_tool_step(line, part, stamp))
    return steps


def render_event(event: dict) -> None:
    """OpenCode events are kept whole in `session.log`."""
    return None


def _tool_step(line: int, part: dict, stamp: str | None) -> Step:
    state = part.get("state") or {}
    name = part.get("tool", "")
    arguments = state.get("input") or {}
    metadata = state.get("metadata") or {}
    failed = state.get("status") != "completed" or name in {"unknown", "invalid"}
    output = state.get("output") or state.get("error") or ""
    exit_code = metadata.get("exit") if name == "bash" else None
    return Step(
        "tool",
        line,
        action=_action(name, arguments),
        output=output,
        exit_code=exit_code,
        failed=failed,
        timestamp=stamp,
    )


def _action(name: str, arguments: dict) -> Action:
    path = arguments.get("filePath", "")
    if name == "read":
        offset, limit = arguments.get("offset"), arguments.get("limit")
        start = offset if offset is not None else (1 if limit else None)
        end = start + limit - 1 if start is not None and limit else None
        return ReadFile(path, start, end)
    if name == "grep":
        return Search(arguments.get("pattern", ""), arguments.get("path"))
    if name == "write":
        return WriteFile(path, arguments.get("content", ""))
    if name == "edit":
        return EditFile(path, arguments.get("oldString"), arguments.get("newString"))
    if name == "bash":
        return Shell(arguments.get("command", ""))
    return OtherTool(name, arguments)


def _iso(milliseconds: object) -> str | None:
    if not isinstance(milliseconds, int):
        return None
    return datetime.fromtimestamp(milliseconds / 1000, UTC).isoformat()


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
