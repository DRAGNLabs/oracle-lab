import json

from oracle_lab.harnesses import claude, codex, opencode
from oracle_lab.harnesses.transcript import render_session
from oracle_lab.paths import RunPaths


def test_session_preserves_messages_tools_errors_and_unknown_events(tmp_path):
    paths = RunPaths.create(tmp_path)
    agent = paths.generation
    paths.prompt.write_text("Generate tests")
    events = [
        {
            "type": "assistant",
            "message": {
                "content": [
                    {"type": "text", "text": "Reading the code"},
                    {
                        "type": "tool_use",
                        "name": "Write",
                        "id": "tool-1",
                        "input": {"content": "test code"},
                    },
                ]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "tool-1",
                        "content": "line one\nline two",
                    },
                ]
            },
        },
        {"type": "future-event", "payload": "preserve me"},
    ]
    (agent / "trace.jsonl").write_text("\n".join(map(json.dumps, events)) + "\npartial-json")
    (agent / "stderr.log").write_text("warning")
    text = render_session(paths, claude.render_event).read_text()
    for expected in [
        "Generate tests",
        "Reading the code",
        "test code",
        "tool-1",
        "line one\nline two",
        "preserve me",
        "partial-json",
        "warning",
    ]:
        assert expected in text
    assert text.index("Reading the code") < text.index("line one") < text.index("preserve me")


def test_codex_commands_render_with_their_output(tmp_path):
    paths = RunPaths.create(tmp_path)
    event = {
        "type": "item.completed",
        "item": {
            "type": "command_execution",
            "command": "pytest",
            "aggregated_output": "8 passed\nfinished",
            "exit_code": 0,
        },
    }
    (paths.generation / "trace.jsonl").write_text(json.dumps(event) + "\n")

    text = render_session(paths, codex.render_event).read_text()

    assert "Command [None]: pytest" in text
    assert "8 passed\nfinished" in text


def test_opencode_events_are_kept_whole(tmp_path):
    paths = RunPaths.create(tmp_path)
    event = {"type": "text", "part": {"text": "done"}}
    (paths.generation / "trace.jsonl").write_text(json.dumps(event) + "\n")

    text = render_session(paths, opencode.render_event).read_text()

    assert '"text": "done"' in text


def test_missing_trace_is_explicit(tmp_path):
    RunPaths.create(tmp_path)
    assert "No trace captured" in render_session(RunPaths(tmp_path), codex.render_event).read_text()
