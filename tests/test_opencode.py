import json

from oracle_lab.harnesses.opencode import parse_trace


def test_parse_trace_accumulates_usage_and_final_text(tmp_path):
    trace = tmp_path / "trace.jsonl"
    events = [
        {"type": "text", "part": {"text": "working"}},
        {
            "type": "step_finish",
            "part": {
                "reason": "tool-calls",
                "tokens": {
                    "input": 10,
                    "output": 2,
                    "reasoning": 1,
                    "cache": {"read": 100, "write": 5},
                },
            },
        },
        {"type": "text", "part": {"text": "done"}},
        {
            "type": "step_finish",
            "part": {
                "reason": "stop",
                "tokens": {
                    "input": 20,
                    "output": 3,
                    "reasoning": 2,
                    "cache": {"read": 200, "write": 0},
                },
            },
        },
    ]
    trace.write_text("\n".join(map(json.dumps, events)))

    result = parse_trace(trace)

    assert result["turn_completed"]
    usage = result["usage"]
    # OpenCode counts cache reads, cache writes and reasoning apart; they are added back.
    assert usage["input_tokens"] == 30 + 300 + 5
    assert usage["cached_input_tokens"] == 300
    assert usage["output_tokens"] == 5 + 3
    assert usage["reasoning_tokens"] == 3
    assert len(usage["raw_usage"]["steps"]) == 2
    assert result["final_text"] == "done"


def test_parse_trace_treats_missing_final_event_as_incomplete(tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text(json.dumps({"type": "text", "part": {"text": "partial"}}))

    result = parse_trace(trace)

    assert not result["turn_completed"]
