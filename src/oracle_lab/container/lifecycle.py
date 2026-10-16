"""Stopping work that hangs, and failing without hiding the original error."""

import signal
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from threading import Event, Timer
from types import FrameType
from typing import NoReturn

from docker.errors import DockerException
from docker.models.containers import Container


@contextmanager
def preserve_failure(note: str) -> Iterator[None]:
    """Use inside a finally block when cleanup must preserve the original error.

    Cleanup failures still propagate after successful work. During unwinding,
    annotate the original failure instead of replacing it with a secondary one.
    """
    original = sys.exception()
    try:
        yield
    except Exception:
        if original is None:
            raise
        original.add_note(note)


@contextmanager
def execution_watchdog(container: Container, timeout: float) -> Iterator[Event]:
    """Stop a stalled supervisor after its own deadline and cleanup grace period."""
    fired = Event()

    def abort() -> None:
        fired.set()
        try:
            container.kill()
        except DockerException:
            pass  # The foreground exec reports the transport or supervisor failure.

    # Allow ten seconds for child-process cleanup plus time for SDK transport.
    timer = Timer(timeout + 30, abort)
    timer.daemon = True
    timer.start()
    try:
        yield fired
    finally:
        timer.cancel()
        timer.join()


@contextmanager
def setup_deadline(seconds: float) -> Iterator[None]:
    """Interrupt even a silent pull/build stream and restore the caller's handler.

    The CLI runs on the main thread. Docker's streaming APIs can disable socket
    timeouts, so their HTTP timeout alone cannot enforce our setup deadline.
    """

    def expired(signum: int, frame: FrameType | None) -> NoReturn:
        raise TimeoutError(f"Docker setup exceeded {seconds:g} seconds")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
