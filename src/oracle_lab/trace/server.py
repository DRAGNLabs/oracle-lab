"""The trace viewer's HTTP server: the page's files and its JSON API, on 127.0.0.1.

Routing and safety checks follow `web/server.py`. A run's `RunTrace` is built on
first request and kept for the session.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from oracle_lab.paths import RunPaths

STATIC = Path(__file__).resolve().parent / "static"
# The review page's pinned highlight.js, served under /vendor/.
VENDOR = Path(__file__).resolve().parent.parent / "web" / "static" / "vendor"


def serve(runs: list[RunPaths], *, port: int = 0, open_browser: bool = True) -> int:
    """Serve the viewer for these runs until interrupted."""
    raise NotImplementedError


class TraceServer(ThreadingHTTPServer):
    """An HTTP server for a set of runs, keyed by run directory name, on 127.0.0.1."""

    def __init__(self, runs: list[RunPaths], port: int = 0):
        raise NotImplementedError

    @property
    def url(self) -> str:
        raise NotImplementedError

    def listing(self) -> list[dict]:
        """`GET /api/runs`: every run's `RunSummary`, for the picker to group by instance."""
        raise NotImplementedError

    def run_trace(self, run_id: str) -> dict:
        """`GET /api/run?id=`: one run's `RunTrace`, built once and cached."""
        raise NotImplementedError


class _TraceHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        raise NotImplementedError
