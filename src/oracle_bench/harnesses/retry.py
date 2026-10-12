"""Retry policy for agent turns interrupted by transient provider failures."""

from collections.abc import Callable
from time import sleep
from typing import TypeVar

T = TypeVar("T")

TRANSIENT_ERROR_MARKERS = (
    "429 too many requests",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    "connection reset",
    "error decoding response body",
    "network error",
    "stream closed before completion",
    "stream disconnected",
    "the operation was aborted",
)

# These are delays before three retries, not three total attempts. Provider
# throttles often outlast the harness's own rapid request retries.
RETRY_DELAYS_SECONDS = (30, 90, 180)


def is_transient_failure(result: dict) -> bool:
    """Return whether a failed turn ended for a recognized transport reason."""
    if result["status"] != "failed":
        return False
    details = " ".join(str(error).lower() for error in result["errors"])
    return any(marker in details for marker in TRANSIENT_ERROR_MARKERS)


def with_retries(
    attempt: Callable[[Callable[[dict], bool]], tuple[dict, T]],
    archive: Callable[[], object],
) -> T:
    """Run `attempt` in a fresh container until it ends for a non-transient reason.

    `attempt` receives `will_retry(result)` and returns the harness result plus its
    value. It must not save a final answer when `will_retry` is true, since that
    attempt is discarded: `archive` moves its evidence aside before the wait.
    """
    for delay in RETRY_DELAYS_SECONDS:
        result, value = attempt(is_transient_failure)
        if not is_transient_failure(result):
            return value
        archive()
        sleep(delay)
    _, value = attempt(lambda _: False)
    return value
