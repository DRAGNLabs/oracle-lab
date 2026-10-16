import json

import pytest

from oracle_lab.harnesses.claude import parse_trace


@pytest.mark.parametrize(
    "subtype,is_error,completed",
    [
        ("success", False, True),
        ("success", True, False),
        ("error", True, False),
    ],
)
def test_claude_result_preserves_failure_and_usage(tmp_path, subtype, is_error, completed):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "result",
                "subtype": subtype,
                "is_error": is_error,
                "usage": {"input_tokens": 20, "output_tokens": 5},
            }
        )
        + "\n"
    )
    result = parse_trace(path)
    assert result["turn_completed"] == completed
    assert bool(result["errors"]) != completed
    assert result["usage"]["input_tokens"] == 20


def test_claude_input_tokens_include_cached_prompt_tokens(tmp_path):
    raw = {
        "input_tokens": 24,
        "cache_read_input_tokens": 193473,
        "cache_creation_input_tokens": 32846,
        "output_tokens": 10854,
        "output_tokens_details": {"thinking_tokens": 1711},
    }
    path = tmp_path / "trace.jsonl"
    path.write_text(json.dumps({"type": "result", "subtype": "success", "usage": raw}) + "\n")

    assert parse_trace(path)["usage"] == {
        "input_tokens": 24 + 193473 + 32846,
        "cached_input_tokens": 193473,
        "output_tokens": 10854,
        "reasoning_tokens": 1711,
        "raw_usage": raw,
    }


def test_incomplete_claude_trace_is_not_success(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text('broken\n{"type":"assistant"}\n')
    result = parse_trace(path)
    assert not result["turn_completed"]
    assert result["unparsed_trace_lines"] == 1


def test_claude_error_lists_render_as_plain_messages(tmp_path):
    path = tmp_path / "trace.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "result",
                "subtype": "error",
                "is_error": True,
                "errors": ["Provider request failed"],
            }
        )
        + "\n"
    )

    assert parse_trace(path)["errors"] == [{"message": "Provider request failed"}]
