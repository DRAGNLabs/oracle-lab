"""The local review page: a small HTTP server over one rater's review targets.

`ReviewSession` answers every question the page asks. `serve` puts it behind the
standard library's HTTP server on 127.0.0.1 and opens the browser.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from oracle_lab.classification.contracts import CLASSIFICATION_LABELS
from oracle_lab.judge.contracts import JUDGMENT_LABELS
from oracle_lab.paths import RunPaths
from oracle_lab.results import EvaluationResult, read_submission_manifest
from oracle_lab.review.contracts import RATER_ID, RATING_DEFINITIONS
from oracle_lab.review.sources import SourceError, SourceView, open_views
from oracle_lab.review.store import read_review, save_review
from oracle_lab.review.targets import ReviewTarget, find_targets

STATIC = Path(__file__).resolve().parent / "static"


class ReviewSession:
    """The review targets under one directory, as one rater sees them."""

    def __init__(
        self, root: Path, rater: str, *, remote: str | None = None, cache: Path | None = None
    ):
        if not RATER_ID.match(rater):
            raise ValueError(f"Rater IDs are letters, digits, '-' and '_', not {rater!r}")
        self.rater = rater
        self._remote = remote
        self._cache = cache
        targets, self.skipped = find_targets(root)
        self.targets = {_target_id(root, target.root): target for target in targets}
        self._skipped_ids = [_target_id(root, skipped.root) for skipped in self.skipped]
        # Views and evidence are built on first use and kept for the session.
        self._lock = threading.Lock()
        self._views: dict[str, dict[str, SourceView]] = {}
        self._evidence: dict[str, dict[str, bytes]] = {}

    def listing(self) -> dict:
        """Every target with this rater's progress, and every candidate that was skipped."""
        return {
            "rater": self.rater,
            "rating_definitions": RATING_DEFINITIONS,
            "targets": [
                {
                    "id": target_id,
                    "stage": target.stage,
                    "instance_id": target.instance.instance_id,
                    "reviewed": read_review(target, self.rater) is not None,
                }
                for target_id, target in self.targets.items()
            ],
            "skipped": [
                {"id": skipped_id, "stage": skipped.stage, "reason": skipped.reason}
                for skipped_id, skipped in zip(self._skipped_ids, self.skipped)
            ],
        }

    def overview(self, target_id: str) -> dict:
        """What the page shows first: the questions, the answers, and the core evidence."""
        target = self._target(target_id)
        evidence = self._evidence_of(target_id)
        labels = JUDGMENT_LABELS if target.stage == "judge" else CLASSIFICATION_LABELS
        answers = target.answers.model_dump(mode="json")
        review = read_review(target, self.rater)
        overview = {
            "id": target_id,
            "stage": target.stage,
            "instance_id": target.instance.instance_id,
            "repo": target.repo,
            "base_commit": target.base_commit,
            "views": list(target.views),
            "questions": [
                {
                    "key": key,
                    "label": labels[key],
                    "answer": answers[key],
                    "justification": answers["justifications"][key],
                }
                for key in target.answered_questions()
            ],
            "rating_definitions": RATING_DEFINITIONS,
            "review": review.ratings if review else None,
            "stage_rubric": target.stage_rubric.read_text(),
            "repair_patch": target.instance.golden_patch,
            "reference_test_patch": target.instance.reference_test_patch,
            "reference_test_ids": target.instance.reference_test_ids,
            "evidence": sorted(evidence),
        }
        if target.stage == "judge":
            overview.update(_judge_overview(target, evidence))
        else:
            overview.update(_classification_overview(target))
        return overview

    def tree(self, target_id: str, view: str) -> dict:
        files = self._view(target_id, view).tree()
        return {
            "files": [{"path": path, "touched": list(touched)} for path, touched in files.items()]
        }

    def file(self, target_id: str, view: str, path: str) -> dict:
        return {"path": path, **_text(self._view(target_id, view).read(path))}

    def evidence_file(self, target_id: str, name: str) -> dict:
        evidence = self._evidence_of(target_id)
        if name not in evidence:
            raise LookupError(f"No such evidence file: {name}")
        return {"name": name, **_text(evidence[name])}

    def submit(self, target_id: str, raw: str) -> dict:
        review = save_review(self._target(target_id), self.rater, raw)
        return {"review": review.ratings}

    def _target(self, target_id: str) -> ReviewTarget:
        if target_id not in self.targets:
            raise LookupError(f"No such review target: {target_id}")
        return self.targets[target_id]

    def _evidence_of(self, target_id: str) -> dict[str, bytes]:
        target = self._target(target_id)
        with self._lock:
            if target_id not in self._evidence:
                files = target.evidence()
                self._evidence[target_id] = {file.name: file.content for file in files}
            return self._evidence[target_id]

    def _view(self, target_id: str, view: str) -> SourceView:
        target = self._target(target_id)
        if view not in target.views:
            raise LookupError(f"No {view} view for {target_id}")
        with self._lock:
            if target_id not in self._views:
                self._views[target_id] = open_views(target, remote=self._remote, cache=self._cache)
            return self._views[target_id][view]


def serve(root: Path, rater: str, *, port: int = 0, open_browser: bool = True) -> int:
    """Serve the review page until interrupted."""
    session = ReviewSession(root, rater)
    server = make_server(session, port)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    print(f"Reviewing {len(session.targets)} targets as {rater} at {url}")
    if session.skipped:
        print(f"{len(session.skipped)} cannot be reviewed; the page lists why.")
    print("Press Ctrl+C to stop.")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


def make_server(session: ReviewSession, port: int = 0) -> ThreadingHTTPServer:
    """An HTTP server for the session on 127.0.0.1, not yet serving."""

    class Handler(_ReviewHandler):
        pass

    Handler.session = session
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


class _ReviewHandler(BaseHTTPRequestHandler):
    session: ReviewSession

    def do_GET(self) -> None:
        self._respond(self._get)

    def do_POST(self) -> None:
        self._respond(self._post)

    def log_message(self, format: str, *args: object) -> None:
        pass  # The terminal shows the URL; per-request lines would bury it.

    def _get(self, path: str, query: _Query) -> None:
        session = self.session
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
        self._json(self.session.submit(query.get("id"), raw))

    def _respond(self, method) -> None:
        port = self.server.server_address[1]
        if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            # Refuse pages that reach this server through another host name.
            self._error(HTTPStatus.FORBIDDEN, "Unexpected Host header")
            return
        url = urlsplit(self.path)
        try:
            method(url.path, _Query(parse_qs(url.query)))
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


class _Query:
    def __init__(self, values: dict[str, list[str]]):
        self._values = values

    def get(self, name: str) -> str:
        if name not in self._values:
            raise ValueError(f"Missing query parameter: {name}")
        return self._values[name][0]


def _judge_overview(target: ReviewTarget, evidence: dict[str, bytes]) -> dict:
    """The judge-specific evidence: the issue, the paired outcomes, and the submission."""
    evaluation = EvaluationResult.model_validate_json(evidence["evidence/paired-results.json"])
    manifest = read_submission_manifest(RunPaths.open(target.root).submission / "manifest.json")
    return {
        "issue": evidence["instance/issue.md"].decode(errors="replace"),
        "outcomes": {
            "matrix": {cell.value: entry.count for cell, entry in evaluation.matrix.items()},
            "tests": [outcome.model_dump(mode="json") for outcome in evaluation.tests],
            "other_outcomes": [
                outcome.model_dump(mode="json") for outcome in evaluation.other_outcomes
            ],
        },
        "submission": {
            "files": sorted(manifest.files),
            "forbidden_changes": manifest.forbidden_changes,
            "compliant": manifest.compliant,
        },
    }


# The task record's readable texts beside the issue, by dataset field, in page order.
RECORD_TEXTS = {
    "hints_text": "Issue comments",
    "pr_description": "Pull request description",
    "interface": "Interface",
}


def _classification_overview(target: ReviewTarget) -> dict:
    """The task record's texts the classifier read. The full record is evidence."""
    record = target.instance.original_record
    return {
        "issue": record.get("problem_statement", ""),
        "record_texts": [
            {"title": title, "text": record[field]}
            for field, title in RECORD_TEXTS.items()
            if isinstance(record.get(field), str) and record[field].strip()
        ],
    }


def _text(content: bytes) -> dict:
    """A file as the page shows it: text, or a note that it is binary."""
    binary = b"\0" in content[:8192]
    text = None if binary else content.decode("utf-8", errors="replace")
    return {"binary": binary, "size": len(content), "text": text}


def _target_id(root: Path, target: Path) -> str:
    """A target's ID is its path under the directory being reviewed."""
    if target == root:
        return target.resolve().name
    return target.relative_to(root).as_posix()
