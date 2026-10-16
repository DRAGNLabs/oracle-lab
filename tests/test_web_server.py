"""The review page's HTTP server: its files, its JSON API, and the requests it refuses."""

import http.client
import json
import threading

import pytest
from fixtures import REVIEWED_RUN, review_set, upstream_repository

from oracle_lab.web.server import ReviewServer
from oracle_lab.web.session import ReviewSession

RATINGS = {"tests_issue": "correct", "attempt_detail": "defensible", "cheating": "correct"}


@pytest.fixture
def data(tmp_path):
    return review_set(tmp_path / "data", upstream_repository(tmp_path / "upstream"))


@pytest.fixture
def port(tmp_path, data):
    """A running server's port. The fixture shuts it down after the test."""
    session = ReviewSession(data, "rater-1", remote="file:///unused", cache=tmp_path / "cache")
    server = ReviewServer(session)
    threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True).start()
    yield server.port
    server.shutdown()
    server.server_close()


def request(port, path, *, data=None, headers=None):
    """The status and body of one request, sent exactly as given."""
    connection = http.client.HTTPConnection("127.0.0.1", port)
    try:
        connection.request("POST" if data else "GET", path, data, headers or {})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def test_the_page_and_api_are_served(port):
    status, body = request(port, "/")
    assert status == 200 and b"<html" in body

    status, body = request(port, "/api/targets")
    assert status == 200 and json.loads(body)["rater"] == "rater-1"


def test_a_review_is_posted_as_json(port, data):
    body = json.dumps({"ratings": RATINGS}).encode()

    status, response = request(
        port,
        f"/api/review?id={REVIEWED_RUN}",
        data=body,
        headers={"Content-Type": "application/json"},
    )

    assert status == 200 and json.loads(response) == {"review": RATINGS}
    assert (data / REVIEWED_RUN / "judge/reviews/rater-1/review.json").is_file()


@pytest.mark.parametrize(
    "path, body, headers, status",
    [
        (f"/api/review?id={REVIEWED_RUN}", b"{}", {"Content-Type": "text/plain"}, 400),
        (f"/api/review?id={REVIEWED_RUN}", b"{}", {"Content-Type": "application/json"}, 400),
        ("/api/target", None, {}, 400),
        ("/api/target?id=runs/missing", None, {}, 404),
        (f"/api/evidence?id={REVIEWED_RUN}&name=../../judge/judgment.json", None, {}, 404),
        ("/static/../server.py", None, {}, 404),
        ("/api/targets", None, {"Host": "attacker.example:80"}, 403),
    ],
)
def test_bad_requests_are_refused(port, path, body, headers, status):
    assert request(port, path, data=body, headers=headers)[0] == status
