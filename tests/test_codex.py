import json

from oracle_lab.harnesses.codex import parse_trace


def test_codex_usage_already_counts_cached_prompt_tokens(tmp_path):
    raw = {
        "input_tokens": 103067,
        "cached_input_tokens": 82068,
        "cache_write_input_tokens": 20523,
        "output_tokens": 3728,
        "reasoning_output_tokens": 852,
    }
    path = tmp_path / "trace.jsonl"
    path.write_text("not-json\n" + json.dumps({"type": "turn.completed", "usage": raw}) + "\n")
    result = parse_trace(path)
    assert result["usage"] == {
        "input_tokens": 103067,
        "cached_input_tokens": 82068,
        "output_tokens": 3728,
        "reasoning_tokens": 852,
        "raw_usage": raw,
    }
    assert result["unparsed_trace_lines"] == 1


def test_recovered_stream_error_does_not_mean_the_turn_failed(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        '{"type":"error","message":"reconnecting"}\n'
        '{"type":"turn.completed","usage":{"output_tokens":2}}\n'
    )
    result = parse_trace(path)
    assert result["turn_completed"] and not result["turn_failed"]
    assert result["errors"]
