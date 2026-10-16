"""Container process supervisor (Python 3.6+); no benchmark-private inputs.

The Docker HTTP timeout does not stop an exec process. This helper gives the
command its own process group, enforces its deadline, and leaves explicit status.
"""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def run(timeout: str, stdin_path: str, status_path: str, argv: list) -> int:
    started = time.monotonic()
    timed_out = False
    with open(stdin_path, "rb") as stdin:
        process = subprocess.Popen(argv, stdin=stdin, start_new_session=True)
        try:
            exit_code = process.wait(timeout=float(timeout))
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                pass
            exit_code = 124
        finally:
            # Also reap background children after a normal parent exit, so they
            # cannot keep writing to stdout or the repository during capture.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    Path(status_path).write_text(
        json.dumps(
            {
                "exit_code": exit_code,
                "timed_out": timed_out,
                "duration_seconds": time.monotonic() - started,
            }
        )
    )
    return 0


if __name__ == "__main__":
    timeout, stdin_path, status_path, *argv = sys.argv[1:]
    raise SystemExit(run(timeout, stdin_path, status_path, argv))
