"""Measure a run's model spend from the OpenRouter key's running total.

OpenRouter updates the total about a minute after a request, and its readings can
briefly disagree, so the final reading waits until several in a row match.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
from collections.abc import Callable, Generator
from contextlib import contextmanager
from pathlib import Path

from oracle_lab.config import HarnessConfig
from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.run import RunCost

KEY_URL = "https://openrouter.ai/api/v1/key"
POLL_SECONDS = 5
MIN_WAIT_SECONDS = 60
MAX_WAIT_SECONDS = 180
STABLE_READINGS = 3


class SpendUnavailable(RuntimeError):
    pass


def key_spend(key: str) -> float:
    """Return the key's total spend in US dollars."""
    request = urllib.request.Request(KEY_URL, headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return float(json.load(response)["data"]["usage"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise SpendUnavailable(str(error).replace(key, "<redacted>")) from None


@contextmanager
def measure_spend(
    harnesses: list[HarnessConfig],
    path: Path,
    on_wait: Callable[[], None] = lambda: None,
) -> Generator[None]:
    """Save what the key spent inside this block, including when the block raises.

    An interrupt skips the measurement. A lookup failure is saved, never raised.
    """
    if not harnesses or any(harness.provider != "openrouter" for harness in harnesses):
        write_result(path, RunCost(status="not_measured"))
        yield
        return
    key = os.environ[harnesses[0].credential_env]
    try:
        before = key_spend(key)
    except SpendUnavailable as error:
        write_result(path, RunCost(status="unavailable", error=str(error)))
        yield
        return
    try:
        yield
    except Exception:
        on_wait()
        write_result(path, settle(key, before))
        raise
    on_wait()
    write_result(path, settle(key, before))


def settle(
    key: str,
    before: float,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> RunCost:
    """Poll until the total has risen and holds steady, or the wait runs out."""
    start = clock()
    readings: list[float] = []
    error = None
    while True:
        sleep(POLL_SECONDS)
        try:
            readings.append(key_spend(key))
        except SpendUnavailable as failure:
            error = str(failure)
        waited = clock() - start
        recent = readings[-STABLE_READINGS:]
        steady = len(recent) == STABLE_READINGS and len(set(recent)) == 1
        if waited >= MIN_WAIT_SECONDS and steady and recent[-1] > before:
            return _cost("measured", before, recent[-1], waited)
        if waited >= MAX_WAIT_SECONDS:
            if not readings:
                return RunCost(status="unavailable", before=before, error=error)
            return _cost("unsettled", before, readings[-1], waited)


def _cost(status: str, before: float, after: float, waited: float) -> RunCost:
    return RunCost.model_validate(
        {
            "status": status,
            "cost_usd": round(after - before, 8),
            "before": before,
            "after": after,
            "waited_seconds": round(waited),
        }
    )
