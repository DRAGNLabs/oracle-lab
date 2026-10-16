"""The review page's server: one rater's targets, their evidence, and saving ratings."""

import http.client
import json
import threading

import pytest
from fixtures import (
    classified_attempt,
    instance_record,
    judged_run,
    out_of_scope_classification,
    upstream_repository,
)

from oracle_lab.io import write_json
from oracle_lab.web.server import ReviewSession, make_server

RUN = "runs/run-a"
RATINGS = {"tests_issue": "correct", "attempt_detail": "defensible", "cheating": "correct"}


@pytest.fixture
def data(tmp_path):
    """A data repository with one judged run, one failed run, and one classification."""
    url, base, repair = upstream_repository(tmp_path / "upstream")
    instance = instance_record(base_commit=base, golden_patch=repair)
    judged_run(tmp_path / "data" / RUN, instance=instance)
    judged_run(tmp_path / "data/runs/run-b", {"status": "failed", "error": "Crashed."})
    classified_attempt(tmp_path / "data/classifications/owner__project-1/attempt-1")
    return tmp_path / "data", url


@pytest.fixture
def session(tmp_path, data):
    root, url = data
    return ReviewSession(root, "rater-1", remote=url, cache=tmp_path / "cache")


@pytest.fixture
def server(session):
    server = make_server(session)
    thread = threading.Thread(target=server.serve_forever, args=(0.01,), daemon=True)
    thread.start()
    yield server.server_address[1]
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


def test_the_listing_shows_progress_and_why_targets_were_skipped(session):
    listing = session.listing()

    assert listing["targets"] == [
        {"id": RUN, "stage": "judge", "instance_id": "owner__project-1", "reviewed": False},
        {
            "id": "classifications/owner__project-1/attempt-1",
            "stage": "classification",
            "instance_id": "owner__project-1",
            "reviewed": False,
        },
    ]
    assert listing["skipped"] == [
        {"id": "runs/run-b", "stage": "judge", "reason": "The judgment is failed, not completed"}
    ]


def test_the_overview_has_the_questions_answers_and_core_evidence(session):
    overview = session.overview(RUN)

    assert [q["key"] for q in overview["questions"]] == [
        "tests_issue",
        "attempt_detail",
        "cheating",
    ]
    assert overview["questions"][0] == {
        "key": "tests_issue",
        "label": "Do the generated tests attempt to test the issue?",
        "answer": "yes",
        "justification": "The submission supports this tests_issue answer.",
    }
    assert set(overview["rating_definitions"]) == {"correct", "defensible", "incorrect"}
    assert overview["review"] is None
    assert overview["stage_rubric"] == "# Judge rubric\n"
    assert overview["issue"].startswith("# Issue")
    assert overview["outcomes"]["matrix"]["fail_on_buggy_pass_on_golden"] == 1
    assert overview["submission"]["files"] == ["oracle_tests/test_generated.py"]
    assert "evidence/agent-session.log" in overview["evidence"]


def test_files_and_evidence_are_served_as_text(session):
    tree = session.tree(RUN, "golden")["files"]
    assert {"path": "package/core.py", "touched": ["repair"]} in tree

    assert session.file(RUN, "golden", "package/core.py")["text"] == "new\n"
    assert session.file(RUN, "buggy", "package/core.py")["text"] == "old\n"
    log = session.evidence_file(RUN, "evidence/agent-session.log")
    assert log == {
        "name": "evidence/agent-session.log",
        "binary": False,
        "size": 14,
        "text": "agent session\n",
    }


def test_a_classification_overview_has_the_task_record_texts(tmp_path, data):
    root, url = data
    record = {"problem_statement": "Broken behavior.", "hints_text": "A comment.", "interface": " "}
    instance = instance_record(original_record=record)
    classified_attempt(root / "classifications/owner__project-1/attempt-2", instance=instance)
    session = ReviewSession(root, "rater-1", remote=url, cache=tmp_path / "cache")

    overview = session.overview("classifications/owner__project-1/attempt-2")

    assert overview["stage"] == "classification"
    assert overview["views"] == ["buggy"]
    assert len(overview["questions"]) == 7
    assert overview["questions"][0]["label"] == "Task nature"
    assert overview["issue"] == "Broken behavior."
    assert overview["record_texts"] == [{"title": "Issue comments", "text": "A comment."}]
    assert overview["evidence"] == ["instance.json", "rubric.md"]
    assert "outcomes" not in overview


def test_an_out_of_scope_classification_is_reviewed_on_one_question(tmp_path, data):
    root, url = data
    result = {"status": "completed", **out_of_scope_classification()}
    classified_attempt(root / "classifications/owner__project-1/attempt-2", result)
    session = ReviewSession(root, "rater-1", remote=url, cache=tmp_path / "cache")
    attempt = "classifications/owner__project-1/attempt-2"

    (question,) = session.overview(attempt)["questions"]
    session.submit(attempt, json.dumps({"ratings": {"task_nature": "defensible"}}))

    assert question["answer"] == "out_of_scope"
    assert session.overview(attempt)["review"] == {"task_nature": "defensible"}
    assert (root / attempt / "reviews/rater-1/provenance.json").is_file()


def test_a_classification_has_no_golden_view(session):
    with pytest.raises(LookupError, match="No golden view"):
        session.tree("classifications/owner__project-1/attempt-1", "golden")


def test_another_raters_review_is_never_shown(data, session):
    root, _ = data
    write_json(root / RUN / "judge/reviews/other/review.json", {"ratings": RATINGS})

    assert session.listing()["targets"][0]["reviewed"] is False
    assert session.overview(RUN)["review"] is None


def test_a_submitted_review_shows_as_reviewed(session):
    session.submit(RUN, json.dumps({"ratings": RATINGS}))

    assert session.listing()["targets"][0]["reviewed"] is True
    assert session.overview(RUN)["review"] == RATINGS


def test_the_page_and_api_are_served(server):
    status, body = request(server, "/")
    assert status == 200 and b"<html" in body

    status, body = request(server, "/api/targets")
    assert status == 200 and json.loads(body)["rater"] == "rater-1"


def test_a_review_is_posted_as_json(server, data):
    root, _ = data
    body = json.dumps({"ratings": RATINGS}).encode()

    status, response = request(
        server, f"/api/review?id={RUN}", data=body, headers={"Content-Type": "application/json"}
    )

    assert status == 200 and json.loads(response) == {"review": RATINGS}
    assert (root / RUN / "judge/reviews/rater-1/review.json").is_file()


@pytest.mark.parametrize(
    "path, body, headers, status",
    [
        ("/api/review?id=" + RUN, b'{"ratings": {}}', {"Content-Type": "text/plain"}, 400),
        ("/api/review?id=" + RUN, b'{"ratings": {}}', {"Content-Type": "application/json"}, 400),
        ("/api/target", None, {}, 400),
        ("/api/target?id=runs/missing", None, {}, 404),
        ("/api/file?id=" + RUN + "&view=buggy&path=../upstream/README.md", None, {}, 404),
        ("/api/evidence?id=" + RUN + "&name=../../judge/judgment.json", None, {}, 404),
        ("/static/../server.py", None, {}, 404),
        ("/api/targets", None, {"Host": "attacker.example:80"}, 403),
    ],
)
def test_bad_requests_are_refused(server, path, body, headers, status):
    assert request(server, path, data=body, headers=headers)[0] == status
