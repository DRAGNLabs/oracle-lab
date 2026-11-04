"""What both LLM judges share about a model turn: answers, failures, and parsing its JSON."""

from __future__ import annotations

import json
import re
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


# A fenced block on its own lines, anywhere in the answer.
_FENCED_JSON = re.compile(
    r"^```(?:json)?[ \t]*\r?\n(?P<body>.*?)\r?\n```[ \t]*$", re.DOTALL | re.MULTILINE
)


def parse_json_object(raw: str) -> dict:
    """The one JSON object a model answered with: bare, or inside one Markdown fence
    that ends the answer. Prose before the fence is ignored; the raw answer keeps it."""
    candidate = raw
    if not raw.lstrip().startswith(("{", "[")):
        fences = list(_FENCED_JSON.finditer(raw))
        if len(fences) != 1 or raw[fences[0].end() :].strip():
            raise ValueError(
                "Expected exactly one JSON object in one Markdown fence that ends the answer"
            )
        candidate = fences[0].group("body")
    value = json.loads(candidate)
    if not isinstance(value, dict):
        raise ValueError("Model output must be a JSON object")
    return value


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
