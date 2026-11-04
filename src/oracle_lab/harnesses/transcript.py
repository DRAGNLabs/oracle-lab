"""Readable, offline rendering of the events emitted by any of the agent CLIs.

Each adapter's `render_event` reads its own event format; an event it does not
render is kept whole as JSON.
"""

import json
from collections.abc import Callable
from pathlib import Path

from oracle_lab.paths import RunPaths


def as_text(value: object) -> str:
    """A field as transcript text: strings as they are, anything else as indented JSON."""
    return value if isinstance(value, str) else json.dumps(value, indent=2, ensure_ascii=False)


RenderEvent = Callable[[dict], list[str] | None]


def _render_event(number: int, raw: str, render: RenderEvent) -> list[str]:
    """Render one line of the trace. Unknown event types are dumped as-is, not skipped."""
    try:
        event = json.loads(raw)
    except json.JSONDecodeError:
        return [f"=== UNPARSED EVENT {number} ===", raw, ""]
    if not isinstance(event, dict):
        return [f"=== EVENT {number} ===", as_text(event), ""]

    kind = event.get("type", "unknown")
    stamp = event.get("timestamp", "")
    lines = [f"=== EVENT {number}: {kind} {stamp} ===".rstrip()]

    # CLIs add event types over time. Keep every field of anything the adapter does not
    # render.
    rendered = render(event)
    lines += rendered if rendered is not None else [as_text(event)]
    lines.append("")
    return lines


def render_session(paths: RunPaths, render: RenderEvent) -> Path:
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
                lines += _render_event(number, raw, render)
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
