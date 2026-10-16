"""Readable, offline rendering of the events emitted by any of the agent CLIs."""

import json
from pathlib import Path

from oracle_lab.paths import RunPaths


def _text(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)


def _render_content_blocks(message: dict) -> list[str]:
    """Render Claude's message content. It comes as a list of typed blocks."""
    lines = []
    for block in message["content"]:
        if not isinstance(block, dict):
            lines.append(_text(block))
            continue
        block_type = block.get("type")
        if block_type == "text":
            lines.append(block.get("text", ""))
        elif block_type == "thinking":
            lines += ["[Thinking output]", block.get("thinking") or "[Not exposed by CLI]"]
        elif block_type == "tool_use":
            lines += [f"Tool: {block.get('name')} [{block.get('id')}]", _text(block.get("input"))]
        elif block_type == "tool_result":
            lines += [
                f"Tool result: {block.get('tool_use_id')} (error={block.get('is_error', False)})",
                _text(block.get("content")),
            ]
        else:
            lines.append(_text(block))
    return lines


def _render_item(item: dict) -> list[str]:
    """Render a Codex item. It is either a command that ran, or some text."""
    if item.get("type") == "command_execution":
        return [
            f"Command [{item.get('id')}]: {item.get('command', '')}",
            f"Status: {item.get('status')} | Exit code: {item.get('exit_code')}",
            item.get("aggregated_output", ""),
        ]
    return [f"{item.get('type')} [{item.get('id')}]", _text(item["text"])]


def _render_event(number: int, raw: str) -> list[str]:
    """Render one line of the trace. Unknown event types are dumped as-is, not skipped."""
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return [f"=== UNPARSED EVENT {number} ===", raw, ""]
    if not isinstance(event, dict):
        return [f"=== EVENT {number} ===", _text(event), ""]

    kind = event.get("type", "unknown")
    stamp = event.get("timestamp", "")
    lines = [f"=== EVENT {number}: {kind} {stamp} ===".rstrip()]

    message = event.get("message")
    item = event.get("item")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        lines += _render_content_blocks(message)
        if event.get("parent_tool_use_id"):
            lines.append(f"Parent tool: {event['parent_tool_use_id']}")
    elif isinstance(item, dict) and (item.get("type") == "command_execution" or "text" in item):
        lines += _render_item(item)
    else:
        # Codex emits edits and collaboration events too, and both CLIs add event
        # types over time. Keep every field of anything this renderer does not know.
        lines.append(_text(event))
    lines.append("")
    return lines


def render_session(paths: RunPaths) -> Path:
    """Render the generation turn's prompt and trace as `generation/session.log`.

    The judge's trace is kept raw in `judge/agent/trace.jsonl` and is not rendered.
    """
    directory = paths.generation
    directory.mkdir(exist_ok=True)
    lines = [
        "AGENT SESSION",
        "Rendered from saved CLI events; raw events remain in trace.jsonl.",
        "Only output exposed by the CLI is available. Tool output may be truncated by the CLI.",
        "",
    ]
    if paths.prompt.exists():
        lines += ["=== PROMPT ===", paths.prompt.read_text(), ""]

    trace = directory / "trace.jsonl"
    if trace.exists():
        for number, raw in enumerate(trace.read_text(errors="replace").splitlines(), 1):
            if raw.strip():
                lines += _render_event(number, raw)
    else:
        lines += ["[No trace captured]", ""]

    stderr = directory / "stderr.log"
    if stderr.exists() and stderr.stat().st_size:
        lines += [
            "=== STDERR (ordering relative to events unavailable) ===",
            stderr.read_text(errors="replace"),
            "",
        ]
    result = directory / "result.json"
    if result.exists():
        lines += ["=== HARNESS RESULT ===", result.read_text(), ""]

    path = directory / "session.log"
    path.write_text("\n".join(lines))
    return path
