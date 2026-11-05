"""Shared helpers for model-written output and harness errors."""

import json

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


def test_a_model_answer_is_its_largest_json_object():
    assert parse_json_object('{"a": 1}') == {"a": 1}
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('My answer:\n\n```json\n{"a": 1}\n```\n') == {"a": 1}
    assert parse_json_object('Done. I will now output it.{\n  "a": {"b": 1}\n}') == {"a": {"b": 1}}
    assert parse_json_object('{"a": 1, "b": 2}\nNote that `{x}` and {"c": 3} hold.') == {
        "a": 1,
        "b": 2,
    }
    # A draft and its final version: the later one wins a tie.
    assert parse_json_object('Draft: {"a": 1}\nFinal: {"a": 2}') == {"a": 2}
    with pytest.raises(ValueError, match="must contain a JSON object"):
        parse_json_object("[1, 2]")
    with pytest.raises(ValueError, match="must contain a JSON object"):
        parse_json_object("No JSON here.")


def test_a_quote_a_model_left_unescaped_is_escaped_without_losing_text():
    raw = 'Answer: {"why": "It asserts `"cafe"` is returned.", "steps": [3]} Done.'

    assert parse_json_object(raw) == {"why": 'It asserts `"cafe"` is returned.', "steps": [3]}


@pytest.mark.parametrize("raw", ['{"a": 1,}', '{"a": "cut off', '{"a": "x" "b": 1}'])
def test_json_that_escaping_cannot_fix_is_refused_with_the_original_error(raw):
    with pytest.raises(json.JSONDecodeError) as original:
        json.loads(raw)
    with pytest.raises(json.JSONDecodeError) as refused:
        parse_json_object(raw)

    assert str(refused.value) == str(original.value)
