"""What the review page shows one rater: targets, their evidence, and saved ratings."""

import json

import pytest
from fixtures import (
    REVIEWED_RUN,
    classified_attempt,
    instance_record,
    judgment_ratings,
    out_of_scope_classification,
    review_set,
    upstream_repository,
)

from oracle_lab.classification.contracts import CLASSIFICATION_LABELS
from oracle_lab.io import write_json
from oracle_lab.web.session import ReviewSession

ATTEMPT = "classifications/owner__project-1/attempt-1"
RATINGS = judgment_ratings(attempt_detail="disagree")


@pytest.fixture
def upstream(tmp_path):
    return upstream_repository(tmp_path / "upstream")


@pytest.fixture
def data(tmp_path, upstream):
    return review_set(tmp_path / "data", upstream)


@pytest.fixture
def open_session(tmp_path, data, upstream):
    """A fresh session over `data`, as a page reload after new files would see it."""
    return lambda: ReviewSession(data, "rater-1", remote=upstream.url, cache=tmp_path / "cache")


@pytest.fixture
def session(open_session):
    return open_session()


def test_the_listing_groups_tasks_by_problem_with_classification_first(session):
    listing = session.listing()

    assert set(listing["rating_definitions"]) == {"agree", "disagree"}
    assert "- **Disagree:** You would give a different answer" in listing["how_to_review"]
    assert "${" not in listing["how_to_review"]
    labels = json.loads(json.dumps(listing["labels"]))  # as the page receives them
    assert labels["actions"]["probe_code"] == "Probe code"
    assert "source" not in labels["flagged_categories"]
    assert labels["cells"]["fail_on_both"] == "Fail on both"
    [problem] = listing["problems"]
    assert (problem["instance_id"], problem["title"]) == ("owner__project-1", "Broken behavior.")
    assert [(task["id"], task["stage"]) for task in problem["tasks"]] == [
        (ATTEMPT, "classification"),
        (REVIEWED_RUN, "judge"),
    ]
    run = problem["tasks"][1]
    assert run["run"] == {"harness": "claude", "model": "test-model", "scope": "repository"}
    assert run["questions"] == list(RATINGS)
    assert run["reviewed"] is False
    assert problem["tasks"][0]["run"] is None
    assert listing["unreviewable"] == [
        {"id": "runs/run-b", "stage": "judge", "reason": "The judgment is failed, not completed"}
    ]


def test_a_problem_has_its_issue_record_texts_and_patches(data, open_session):
    record = {"problem_statement": "Broken behavior.", "hints_text": "A comment.", "interface": " "}
    classified_attempt(data / ATTEMPT, instance=instance_record(original_record=record))

    problem = open_session().problem("owner__project-1")

    assert problem["issue"] == "Broken behavior."
    assert problem["record_texts"] == [{"title": "Issue comments", "text": "A comment."}]
    assert problem["reference_test_ids"] == ["tests/test_core.py::test_fixed"]
    with pytest.raises(LookupError, match="No such problem"):
        open_session().problem("missing")


def test_a_judgment_overview_has_the_questions_answers_and_core_evidence(session):
    overview = session.overview(REVIEWED_RUN)

    assert [q["key"] for q in overview["questions"]] == list(RATINGS)
    assert overview["questions"][0] == {
        "key": "tests_issue",
        "label": "Do the generated tests attempt to test the issue?",
        "section": "submission",
        "answer": "yes",
        "justification": "The evidence supports this tests_issue answer.",
    }
    assert overview["questions"][3] == {
        "key": "failure_modes.never_saw_bug",
        "label": "Never saw the bug",
        "section": "failure_modes",
        "answer": "absent",
        "steps": None,
        "justification": None,
    }
    assert overview["review"] is None
    assert overview["stage_rubric"] == "# Judge rubric\n"
    assert "issue" not in overview
    assert overview["run"]["harness"] == "claude"
    assert overview["test_target"] is None
    assert overview["outcomes"]["matrix"]["fail_on_buggy_pass_on_golden"] == 1
    assert overview["submission"]["files"] == ["oracle_tests/test_generated.py"]
    assert "evidence/agent-session.log" in overview["evidence"]


def test_a_classification_overview_has_its_questions_and_evidence(session):
    overview = session.overview(ATTEMPT)

    assert overview["views"] == ["buggy"]
    assert len(overview["questions"]) == len(CLASSIFICATION_LABELS)
    assert overview["questions"][0]["label"] == "Task nature"
    assert overview["evidence"] == ["instance.json", "rubric.md"]
    assert "outcomes" not in overview


def test_only_a_judgment_has_an_agent_trace(session):
    assert session.trace(REVIEWED_RUN)["events"] == []
    assert session.trace(REVIEWED_RUN) is session.trace(REVIEWED_RUN)
    with pytest.raises(LookupError, match="no agent trace"):
        session.trace(ATTEMPT)


def test_the_trace_is_the_one_saved_when_the_run_was_judged(data, open_session):
    (data / REVIEWED_RUN / "judge/trace.json").unlink()

    with pytest.raises(LookupError, match="could not be built"):
        open_session().trace(REVIEWED_RUN)


def test_an_out_of_scope_classification_is_reviewed_on_one_question(data, open_session):
    attempt = "classifications/owner__project-1/attempt-2"
    classified_attempt(data / attempt, {"status": "completed", **out_of_scope_classification()})
    session = open_session()

    (question,) = session.overview(attempt)["questions"]
    session.submit(attempt, json.dumps({"ratings": {"task_nature": "disagree"}}))

    assert question["answer"] == "out_of_scope"
    assert session.overview(attempt)["review"] == {
        "ratings": {"task_nature": "disagree"},
        "other_failure_modes": [],
    }
    assert (data / attempt / "judge/reviews/rater-1/provenance.json").is_file()


def test_files_and_evidence_are_served_as_text(session):
    tree = session.tree(REVIEWED_RUN, "golden")["files"]
    assert {"path": "package/core.py", "touched": ["repair"]} in tree

    assert session.file(REVIEWED_RUN, "golden", "package/core.py")["text"] == "new\n"
    assert session.file(REVIEWED_RUN, "buggy", "package/core.py")["text"] == "old\n"
    assert session.evidence_file(REVIEWED_RUN, "evidence/agent-session.log") == {
        "name": "evidence/agent-session.log",
        "binary": False,
        "size": 14,
        "text": "agent session\n",
    }


def test_a_classification_has_no_golden_view(session):
    with pytest.raises(LookupError, match="No golden view"):
        session.tree(ATTEMPT, "golden")


def test_another_raters_review_is_never_shown(data, session):
    write_json(data / REVIEWED_RUN / "judge/reviews/other/review.json", {"ratings": RATINGS})

    assert session.listing()["problems"][0]["tasks"][1]["reviewed"] is False
    assert session.overview(REVIEWED_RUN)["review"] is None


def test_a_submitted_review_shows_as_reviewed(session):
    session.submit(REVIEWED_RUN, json.dumps({"ratings": RATINGS}))

    assert session.listing()["problems"][0]["tasks"][1]["reviewed"] is True
    assert session.overview(REVIEWED_RUN)["review"]["ratings"] == RATINGS


def test_a_rater_id_is_checked_before_anything_is_read(data):
    with pytest.raises(ValueError, match="Rater IDs"):
        ReviewSession(data, "../other")
