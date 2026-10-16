"""The review page's HTTP server: the page's files and its JSON API, on 127.0.0.1.

Every API request is answered by the `ReviewSession`; this module only routes
requests to it and turns its answers and errors into HTTP responses.
"""

from __future__ import annotations

import json
import mimetypes
import webbrowser
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

from oracle_lab.review.sources import SourceError
from oracle_lab.web.session import ReviewSession

STATIC = Path(__file__).resolve().parent / "static"


def serve(root: Path, rater: str, *, port: int = 0, open_browser: bool = True) -> int:
    """Serve the review page until interrupted."""
    server = ReviewServer(ReviewSession(root, rater), port)
    print(f"Reviewing {len(server.session.targets)} targets as {rater} at {server.url}")
    if server.session.unreviewable:
        print(f"{len(server.session.unreviewable)} cannot be reviewed; the page lists why.")
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


class ReviewServer(ThreadingHTTPServer):
    """An HTTP server for one review session, bound to 127.0.0.1 and not yet serving."""

    def __init__(self, session: ReviewSession, port: int = 0):
        super().__init__(("127.0.0.1", port), _ReviewHandler)
        self.session = session

    @property
    def port(self) -> int:
        return self.server_address[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"


class _Query:
    """A request's query parameters, each required."""

    def __init__(self, query: str):
        self._values = parse_qs(query)

    def get(self, name: str) -> str:
        if name not in self._values:
            raise ValueError(f"Missing query parameter: {name}")
        return self._values[name][0]


class _ReviewHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self._respond(self._get)

    def do_POST(self) -> None:
        self._respond(self._post)

    def log_message(self, format: str, *args: object) -> None:
        pass  # The terminal shows the URL; per-request lines would bury it.

    @property
    def _server(self) -> ReviewServer:
        return cast(ReviewServer, self.server)

    def _get(self, path: str, query: _Query) -> None:
        session = self._server.session
        if path == "/":
            self._static("index.html")
        elif path.startswith("/static/"):
            self._static(path.removeprefix("/static/"))
        elif path == "/api/targets":
            self._json(session.listing())
        elif path == "/api/target":
            self._json(session.overview(query.get("id")))
        elif path == "/api/tree":
            self._json(session.tree(query.get("id"), query.get("view")))
        elif path == "/api/file":
            self._json(session.file(query.get("id"), query.get("view"), query.get("path")))
        elif path == "/api/evidence":
            self._json(session.evidence_file(query.get("id"), query.get("name")))
        else:
            raise LookupError(f"Not found: {path}")

    def _post(self, path: str, query: _Query) -> None:
        if path != "/api/review":
            raise LookupError(f"Not found: {path}")
        # A JSON body cannot be sent cross-site without a preflight this server refuses.
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            raise ValueError("Reviews must be sent as application/json")
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode()
        self._json(self._server.session.submit(query.get("id"), raw))

    def _respond(self, route: Callable[[str, _Query], None]) -> None:
        port = self._server.port
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            # Refuse pages that reach this server through another host name.
            self._error(HTTPStatus.FORBIDDEN, "Unexpected Host header")
            return
        url = urlsplit(self.path)
        try:
            route(url.path, _Query(url.query))
        except LookupError as error:
            self._error(HTTPStatus.NOT_FOUND, str(error))
        except ValueError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))
        except (SourceError, OSError) as error:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, str(error))

    def _static(self, name: str) -> None:
        path = (STATIC / name).resolve()
        if not path.is_relative_to(STATIC) or not path.is_file():
            raise LookupError(f"Not found: {name}")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, path.read_bytes(), content_type)

    def _json(self, value: object) -> None:
        self._send(HTTPStatus.OK, json.dumps(value).encode(), "application/json")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._send(status, json.dumps({"error": message}).encode(), "application/json")

    def _send(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)
