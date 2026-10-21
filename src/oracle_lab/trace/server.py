"""The trace viewer's HTTP server: the page's files and its JSON API, on 127.0.0.1.

Routing and safety checks follow `web/server.py`. A run's `RunTrace` is built on
first request and kept for the session.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

from oracle_lab.paths import RunPaths
from oracle_lab.trace.build import build_run_trace, read_run_facts

STATIC = Path(__file__).resolve().parent / "static"
# The review page's pinned highlight.js and marked, served under /vendor/.
VENDOR = Path(__file__).resolve().parent.parent / "web" / "static" / "vendor"


def serve(runs: list[RunPaths], *, port: int = 0, open_browser: bool = True) -> int:
    """Serve the viewer for these runs until interrupted."""
    server = TraceServer(runs, port)
    print(f"Viewing {len(runs)} runs at {server.url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


class TraceServer(ThreadingHTTPServer):
    """An HTTP server for a set of runs, keyed by run directory name, on 127.0.0.1."""

    def __init__(self, runs: list[RunPaths], port: int = 0):
        super().__init__(("127.0.0.1", port), _TraceHandler)
        self.runs = {paths.root.name: paths for paths in runs}
        self._traces: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    def listing(self) -> list[dict]:
        """`GET /api/runs`: every run's `RunSummary`, for the picker to group by instance."""
        listed = []
        for paths in self.runs.values():
            try:
                listed.append(vars(read_run_facts(paths).summary))
            except (OSError, KeyError, TypeError, ValueError) as error:
                listed.append({"run_id": paths.root.name, "error": str(error)})
        return listed

    def run_trace(self, run_id: str) -> dict:
        """`GET /api/run?id=`: one run's `RunTrace`, built once and cached."""
        if run_id not in self.runs:
            raise LookupError(f"Unknown run: {run_id}")
        with self._lock:
            if run_id not in self._traces:
                self._traces[run_id] = build_run_trace(self.runs[run_id]).to_json()
            return self._traces[run_id]


class _TraceHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = cast(TraceServer, self.server)
        if self.headers.get("Host") not in (f"127.0.0.1:{server.port}", f"localhost:{server.port}"):
            # Refuse pages that reach this server through another host name.
            self._json({"error": "Unexpected Host header"}, HTTPStatus.FORBIDDEN)
            return
        url = urlsplit(self.path)
        query = parse_qs(url.query)
        try:
            if url.path == "/":
                self._file(STATIC, "index.html")
            elif url.path.startswith("/static/"):
                self._file(STATIC, url.path.removeprefix("/static/"))
            elif url.path.startswith("/vendor/"):
                self._file(VENDOR, url.path.removeprefix("/vendor/"))
            elif url.path == "/api/runs":
                self._json(server.listing())
            elif url.path == "/api/run":
                if "id" not in query:
                    raise ValueError("Missing query parameter: id")
                self._json(server.run_trace(query["id"][0]))
            else:
                raise LookupError(f"Not found: {url.path}")
        except LookupError as error:
            self._json({"error": str(error)}, HTTPStatus.NOT_FOUND)
        except ValueError as error:
            self._json({"error": str(error)}, HTTPStatus.BAD_REQUEST)
        except (OSError, KeyError, TypeError) as error:
            self._json({"error": str(error)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def log_message(self, format: str, *args: object) -> None:
        pass  # The terminal shows the URL; per-request lines would bury it.

    def _file(self, root: Path, name: str) -> None:
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise LookupError(f"Not found: {name}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, path.read_bytes(), content_type)

    def _json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        self._send(status, json.dumps(value).encode(), "application/json")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
