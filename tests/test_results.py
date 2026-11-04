"""Shared helpers for model-written output and harness errors."""

import pytest

from oracle_lab.contracts.turn import last_error_message, parse_json_object


@pytest.mark.parametrize(
    "errors,expected",
    [
        ([], None),
        ([{"message": "first"}, {"message": "rate limited"}], "rate limited"),
        ([{"type": "error", "error": {"message": "quota exceeded"}}], "quota exceeded"),
        ([{"type": "turn.failed"}], "{'type': 'turn.failed'}"),
        (["plain text"], "plain text"),
    ],
)
def test_the_last_error_message_is_found_even_when_nested(errors, expected):
    assert last_error_message(errors) == expected


def test_a_model_answer_is_one_json_object_bare_or_fenced():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('My answer:\n\n```json\n{"a": 1}\n```\n') == {"a": 1}
    with pytest.raises(ValueError, match="one Markdown fence"):
        parse_json_object('```json\n{"a": 1}\n```\nextra')
    with pytest.raises(ValueError, match="one Markdown fence"):
        parse_json_object('```json\n{"a": 1}\n```\n```json\n{"a": 2}\n```')
    with pytest.raises(ValueError, match="one Markdown fence"):
        parse_json_object('Answer: {"a": 1}')
    with pytest.raises(ValueError, match="must be a JSON object"):
        parse_json_object("[1, 2]")
