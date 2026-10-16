import json

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
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "pytest",
                "aggregated_output": "8 passed\nfinished",
                "exit_code": 0,
            },
        },
        {"type": "future-event", "payload": "preserve me"},
    ]
    (agent / "trace.jsonl").write_text("\n".join(map(json.dumps, events)) + "\npartial-json")
    (agent / "stderr.log").write_text("warning")
    text = render_session(RunPaths.open(tmp_path)).read_text()
    for expected in [
        "Generate tests",
        "Reading the code",
        "test code",
        "tool-1",
        "line one\nline two",
        "8 passed\nfinished",
        "preserve me",
        "partial-json",
        "warning",
    ]:
        assert expected in text
    assert text.index("Reading the code") < text.index("line one") < text.index("8 passed")


def test_missing_trace_is_explicit(tmp_path):
    RunPaths.create(tmp_path)
    assert "No trace captured" in render_session(RunPaths.open(tmp_path)).read_text()
