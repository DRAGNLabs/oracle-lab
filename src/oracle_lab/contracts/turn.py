"""What both LLM judges share about a model turn: answers, failures, and parsing its JSON."""

from __future__ import annotations

import json
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import AfterValidator, Field

from oracle_lab.contracts.base import Sha256, StrictModel

MAX_JUSTIFICATION_LENGTH = 2_000


def _require_text(value: str) -> str:
    if not value.strip():
        raise ValueError("justification must not be blank")
    return value


Justification = Annotated[
    str, Field(max_length=MAX_JUSTIFICATION_LENGTH), AfterValidator(_require_text)
]


A = TypeVar("A")


class Answered(StrictModel, Generic[A]):
    """One rubric question's answer, and the evidence for it."""

    answer: A
    justification: Justification


class UnsuccessfulTurn(StrictModel):
    """A judge or classifier turn that produced no usable answer."""

    status: Literal["invalid_output", "failed", "timed_out"]
    error: Annotated[str, Field(min_length=1)]


class ExposedArtifact(StrictModel):
    """One file copied into a privileged workspace, and its hash."""

    source: str
    destination: str
    sha256: Sha256


def failed_turn(error: str, *, timed_out: bool = False) -> UnsuccessfulTurn:
    """A harness failure, without pretending the model gave an answer."""
    return UnsuccessfulTurn(status="timed_out" if timed_out else "failed", error=error)


def turn_failure(result: dict, who: str) -> UnsuccessfulTurn | None:
    """Why a harness turn gave no answer to parse, or None when it completed.

    Prefers the harness's own last error over a generic status line.
    """
    if result["status"] == "completed":
        return None
    reason = last_error_message(result["errors"]) or (
        f"{who} harness ended with status {result['status']}"
    )
    return failed_turn(reason, timed_out=result["status"] == "timeout")


def parse_json_object(raw: str) -> dict:
    """The JSON object a model answered with: the largest complete top-level object
    in its answer, the last one on a tie.

    Prose before or after it, and a Markdown fence around it, are ignored; the raw
    answer keeps them. When no object parses, the first parse error is raised.
    """
    found: list[tuple[int, dict]] = []
    first_error: json.JSONDecodeError | None = None
    start = raw.find("{")
    while start >= 0:
        try:
            value, end = _decode_escaping_stray_quotes(raw, start)
        except json.JSONDecodeError as error:
            first_error = first_error or error
            start = raw.find("{", start + 1)
            continue
        found.append((end - start, value))
        start = raw.find("{", end)
    if found:
        return max(reversed(found), key=lambda item: item[0])[1]
    if first_error:
        raise first_error
    raise ValueError("Model output must contain a JSON object")


def _decode_escaping_stray_quotes(text: str, start: int, attempts: int = 10) -> tuple[dict, int]:
    """Decode the JSON object at `start`, and return it with the index just past it.

    A `"` a model left unescaped inside a string ends the string early, and the
    decoder then stops at the text after it, expecting a comma. Escaping the last
    unescaped quote before that point and decoding again recovers the text. It
    only adds backslashes, never removes text. Any other error, or one that
    escaping does not fix, is raised as it was first found.
    """
    decoder = json.JSONDecoder()
    added = 0
    try:
        value, end = decoder.raw_decode(text, start)
        return value, end
    except json.JSONDecodeError as exc:
        first = error = exc
    for _ in range(attempts):
        if not error.msg.startswith("Expecting ',' delimiter"):
            break
        quote = text.rfind('"', start, error.pos)
        while quote > start and text[quote - 1] == "\\":
            quote = text.rfind('"', start, quote - 1)
        if quote <= start:
            break
        text = text[:quote] + "\\" + text[quote:]
        added += 1
        try:
            value, end = decoder.raw_decode(text, start)
            # Every added backslash sits inside the object, before its end.
            return value, end - added
        except json.JSONDecodeError as next_error:
            error = next_error
    raise first


def last_error_message(errors: list) -> str | None:
    """The message of a harness's last error, which may be nested under `error`."""
    if not errors:
        return None
    last = errors[-1]
    if isinstance(last, dict):
        nested = last.get("error")
        message = last.get("message") or (
            nested.get("message") if isinstance(nested, dict) else None
        )
        if message:
            return str(message)
    return str(last)
