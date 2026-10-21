import json

from oracle_lab.harnesses import read_steps, reasoning_kind
from oracle_lab.harnesses.steps import EditFile, OtherTool, ReadFile, Search, Shell, WriteFile


def write_trace(path, events):
    path.write_text("\n".join(json.dumps(event) for event in events) + "\nnot json\n")
    return path


def test_claude_pairs_tool_results_and_keeps_reasoning(tmp_path):
    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            {"type": "system", "subtype": "thinking_tokens"},
            {
                "type": "assistant",
                "timestamp": "2026-09-23T20:33:00Z",
                "message": {
                    "content": [
                        {"type": "thinking", "thinking": "Look at the parser."},
                        {
                            "type": "tool_use",
                            "id": "a",
                            "name": "Read",
                            "input": {"file_path": "/testbed/pkg/mod.py"},
                        },
                        {
                            "type": "tool_use",
                            "id": "b",
                            "name": "Bash",
                            "input": {"command": "pytest -q"},
                        },
                        {
                            "type": "tool_use",
                            "id": "c",
                            "name": "Edit",
                            "input": {
                                "file_path": "/testbed/oracle_tests/test_a.py",
                                "old_string": "x",
                                "new_string": "y",
                            },
                        },
                    ]
                },
            },
            {
                "type": "user",
                "message": {
                    "content": [{"type": "tool_result", "tool_use_id": "a", "content": "1\tx"}]
                },
                "tool_use_result": {"file": {"startLine": 20, "numLines": 11}},
            },
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "b",
                            "is_error": True,
                            "content": "Error: Exit code 1\n1 failed",
                        },
                        {
                            "type": "tool_result",
                            "tool_use_id": "c",
                            "is_error": True,
                            "content": "String not found",
                        },
                    ]
                },
            },
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "Done."}]}},
        ],
    )

    steps = read_steps("claude", trace)

    assert [step.kind for step in steps] == ["reasoning", "tool", "tool", "tool", "message"]
    assert steps[0].text == "Look at the parser."
    assert steps[1].action == ReadFile("/testbed/pkg/mod.py", 20, 30)
    assert steps[1].timestamp == "2026-09-23T20:33:00Z"
    assert steps[2].action == Shell("pytest -q")
    assert (steps[2].exit_code, steps[2].failed) == (1, False)
    assert isinstance(steps[3].action, EditFile) and steps[3].failed
    assert steps[4].text == "Done."
    assert reasoning_kind("claude") == "summary"


def test_codex_reads_completed_items_only(tmp_path):
    command = {"id": "1", "type": "command_execution", "command": "/bin/sh -lc 'ls'"}
    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            {
                "type": "item.completed",
                "item": {"id": "0", "type": "reasoning", "text": "**Plan**"},
            },
            {
                "type": "item.started",
                "item": {**command, "aggregated_output": "", "exit_code": None},
            },
            {
                "type": "item.completed",
                "item": {**command, "aggregated_output": "a\n", "exit_code": 0},
            },
            {"type": "item.completed", "item": {"id": "2", "type": "agent_message", "text": "Hi"}},
            {"type": "item.completed", "item": {"id": "3", "type": "web_search", "query": "x"}},
        ],
    )

    steps = read_steps("codex", trace)

    assert [step.kind for step in steps] == ["reasoning", "tool", "message", "tool"]
    assert steps[1].action == Shell("/bin/sh -lc 'ls'")
    assert (steps[1].output, steps[1].exit_code, steps[1].line) == ("a\n", 0, 3)
    assert steps[3].action == OtherTool("web_search", {"query": "x"})


def test_opencode_places_reasoning_once_before_its_calls_and_keeps_aborted_calls(tmp_path):
    reasoning = {"openrouter": {"reasoning_details": [{"type": "reasoning.text", "text": "Hmm."}]}}

    def tool(name, arguments, *, status="completed", output="", metadata=None):
        return {
            "type": "tool_use",
            "timestamp": 1790203417267,
            "part": {
                "type": "tool",
                "tool": name,
                "messageID": "m1",
                "metadata": reasoning,
                "state": {
                    "status": status,
                    "input": arguments,
                    "output": output,
                    "metadata": metadata or {},
                },
            },
        }

    trace = write_trace(
        tmp_path / "trace.jsonl",
        [
            tool("read", {"filePath": "/testbed/a.py", "offset": 220, "limit": 180}),
            tool("grep", {"pattern": "def f", "path": "/testbed"}),
            tool("bash", {"command": "ls"}, output="x", metadata={"exit": 2}),
            tool("write", {"filePath": "/testbed/oracle_tests/t.py", "content": "pass\n"}),
            tool("unknown", {}, status="error"),
            {"type": "text", "part": {"type": "text", "text": "Done", "messageID": "m2"}},
        ],
    )

    steps = read_steps("opencode", trace)

    assert [step.kind for step in steps] == [
        "reasoning",
        "tool",
        "tool",
        "tool",
        "tool",
        "tool",
        "message",
    ]
    assert steps[0].text == "Hmm."
    assert steps[1].action == ReadFile("/testbed/a.py", 220, 399)
    assert steps[1].timestamp.startswith("2026-09-")
    assert steps[2].action == Search("def f", "/testbed")
    assert (steps[3].action, steps[3].exit_code) == (Shell("ls"), 2)
    assert steps[4].action == WriteFile("/testbed/oracle_tests/t.py", "pass\n")
    assert steps[5].failed
    assert reasoning_kind("opencode") == "raw"
