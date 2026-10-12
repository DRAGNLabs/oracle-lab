"""The shared retry loop for agent turns."""

import pytest

from oracle_bench.harnesses import retry

TRANSIENT = {"status": "failed", "errors": [{"message": "503 Service Unavailable"}]}
PERMANENT = {"status": "failed", "errors": [{"message": "invalid API key"}]}
COMPLETED = {"status": "completed", "errors": []}


@pytest.fixture
def waits(monkeypatch):
    slept = []
    monkeypatch.setattr(retry, "sleep", slept.append)
    return slept


def run(results):
    """Run `with_retries` over a scripted series of turn results."""
    turns, archived, saved = iter(results), [], []

    def attempt(will_retry):
        result = next(turns)
        if not will_retry(result):
            saved.append(result)
        return result, result["status"]

    value = retry.with_retries(attempt, lambda: archived.append(True))
    return value, len(archived), saved


def test_a_transient_failure_is_retried_after_a_wait_and_only_the_last_turn_is_saved(waits):
    value, archived, saved = run([TRANSIENT, TRANSIENT, COMPLETED])

    assert value == "completed"
    assert archived == 2
    assert saved == [COMPLETED]
    assert waits == [30, 90]


@pytest.mark.parametrize("result", [COMPLETED, PERMANENT])
def test_a_completed_or_permanently_failed_turn_is_not_retried(waits, result):
    value, archived, saved = run([result])

    assert value == result["status"]
    assert (archived, saved, waits) == (0, [result], [])


def test_the_last_retry_is_kept_even_when_it_fails_transiently(waits):
    value, archived, saved = run([TRANSIENT] * 4)

    assert value == "failed"
    assert archived == 3
    assert saved == [TRANSIENT]
    assert waits == list(retry.RETRY_DELAYS_SECONDS)
