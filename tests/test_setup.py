"""Setup preflights and classifies every problem in a file, and a rerun picks up where it left off."""

from unittest.mock import Mock

import pytest
import yaml
from fixtures import classified_attempt, in_scope_classification, instance_record, passed_preflight

import oracle_lab.setup as problem_setup
from oracle_lab.paths import OutputPaths

FAILED = {"status": "failed", "error": "The harness crashed."}
REJECTED = {
    "status": "completed",
    **in_scope_classification(benchmark_quality="unfair_without_issue"),
}


class Problems:
    """A setup file, and fakes that record what each problem's preflight and classification did."""

    def __init__(self, tmp_path, monkeypatch, *instance_ids):
        self.output = OutputPaths(tmp_path / "output")
        self.path = tmp_path / "problems.yaml"
        self.path.write_text(
            yaml.safe_dump(
                {
                    "classifier": {"harness": "codex", "provider": "openrouter", "model": "m"},
                    "rubric": "rubric.md",
                    "output": "output",
                    "problems": [{"instance": i} for i in instance_ids],
                }
            )
        )
        self.calls: list[tuple[str, str]] = []
        self.broken_preflight: set[str] = set()
        self.classifications: dict[str, dict] = {}
        self.attempts = 0
        monkeypatch.setattr(problem_setup, "require_credential", Mock())
        monkeypatch.setattr(
            problem_setup,
            "resolve_source",
            lambda source: (instance_record(instance_id=source.instance), None),
        )
        monkeypatch.setattr(problem_setup, "preflight", self.preflight)
        monkeypatch.setattr(problem_setup, "classify", self.classify)

    def preflight(self, config):
        instance_id = config.source.instance
        self.calls.append(("preflight", instance_id))
        if instance_id in self.broken_preflight:
            raise RuntimeError("Reference pair did not reproduce")
        return passed_preflight(self.output.root, instance_record(instance_id=instance_id)).root

    def classify(self, config):
        instance_id = config.source.instance
        self.calls.append(("classify", instance_id))
        self.attempts += 1
        attempt = self.output.classifications / instance_id / f"20260101T00000{self.attempts}Z-0"
        result = self.classifications.get(instance_id)
        classified_attempt(attempt, result, instance_record(instance_id=instance_id))
        return attempt

    def setup(self, **flags) -> bool:
        self.calls.clear()
        flags = {"reclassify": False, "preflight_only": False, **flags}
        return problem_setup.setup(self.path, **flags)


def test_a_new_problem_is_preflighted_then_classified_then_cleared(tmp_path, monkeypatch, capsys):
    problems = Problems(tmp_path, monkeypatch, "owner__project-1")

    assert problems.setup()

    assert problems.calls == [("preflight", "owner__project-1"), ("classify", "owner__project-1")]
    line = capsys.readouterr().out.splitlines()[0]
    assert "preflight: passed  fix scope: function  classification: completed  -> cleared" in line


def test_a_rerun_skips_every_step_already_done(tmp_path, monkeypatch, capsys):
    problems = Problems(tmp_path, monkeypatch, "owner__project-1")
    problems.setup()

    assert problems.setup()

    assert problems.calls == []
    assert "owner__project-1  preflight: passed earlier" in capsys.readouterr().out


def test_a_failed_preflight_is_not_classified_and_does_not_stop_the_others(
    tmp_path, monkeypatch, capsys
):
    problems = Problems(tmp_path, monkeypatch, "broken__project-1", "owner__project-2")
    problems.broken_preflight.add("broken__project-1")

    assert not problems.setup()

    assert problems.calls == [
        ("preflight", "broken__project-1"),
        ("preflight", "owner__project-2"),
        ("classify", "owner__project-2"),
    ]
    out = capsys.readouterr().out
    assert "failed at resolve: Reference pair did not reproduce" in out
    assert "2 problems: 1 cleared, 0 rejected, 1 need another setup run." in out

    # Once fixed, a rerun sets up only the broken problem.
    problems.broken_preflight.clear()
    assert problems.setup()
    assert problems.calls == [("preflight", "broken__project-1"), ("classify", "broken__project-1")]


def test_a_failed_classification_is_retried_but_a_rejection_is_kept(tmp_path, monkeypatch, capsys):
    problems = Problems(tmp_path, monkeypatch, "failed__project-1", "rejected__project-2")
    problems.classifications = {"failed__project-1": FAILED, "rejected__project-2": REJECTED}

    assert not problems.setup()
    out = capsys.readouterr().out
    assert "classification: failed  -> not ready" in out
    assert "-> rejected: the classifier rated its benchmark quality unfair_without_issue" in out

    problems.classifications.pop("failed__project-1")
    assert problems.setup()
    assert problems.calls == [("classify", "failed__project-1")]


def test_reclassify_classifies_completed_problems_again(tmp_path, monkeypatch):
    problems = Problems(tmp_path, monkeypatch, "owner__project-1")
    problems.setup()

    problems.setup(reclassify=True)

    assert problems.calls == [("classify", "owner__project-1")]


def test_preflight_only_needs_no_credential_and_classifies_nothing(tmp_path, monkeypatch):
    problems = Problems(tmp_path, monkeypatch, "owner__project-1")
    monkeypatch.setattr(problem_setup, "require_credential", Mock(side_effect=AssertionError))

    assert problems.setup(preflight_only=True)

    assert problems.calls == [("preflight", "owner__project-1")]


@pytest.mark.parametrize("flag", [True, False])
def test_a_problem_whose_record_changed_is_preflighted_again(tmp_path, monkeypatch, flag):
    problems = Problems(tmp_path, monkeypatch, "owner__project-1")
    problems.setup()
    if flag:
        changed = instance_record(base_commit="b" * 40)
        monkeypatch.setattr(problem_setup, "resolve_source", lambda source: (changed, None))

    problems.setup(preflight_only=True)

    assert problems.calls == ([("preflight", "owner__project-1")] if flag else [])


def test_each_run_rewrites_a_summary_of_every_problem_in_the_file(tmp_path, monkeypatch, capsys):
    problems = Problems(tmp_path, monkeypatch, "broken__project-1", "owner__project-2")
    problems.broken_preflight.add("broken__project-1")
    problems.setup()
    report = problems.output.setup / "problems.md"
    assert f"Summary: {report}" in capsys.readouterr().out

    text = report.read_text()
    assert "**2** problems: **1** cleared, **0** rejected, **1** need another setup run." in text
    rows = {line.split(" | ")[0].removeprefix("| "): line for line in text.splitlines()}
    assert "failed at resolve: Reference pair did not reproduce" in rows["broken__project-1"]
    ready = rows["owner__project-2"]
    assert "[passed](../preflight/owner__project-2)" in ready
    assert "`package/core.py`: `Client.send`" in ready
    assert "| [completed](../classifications/owner__project-2/" in ready
    assert (
        "| behavioral_bug | validation_checking | exception_behavior | special_input "
        "| specified | docstring_or_comment | unit | usable | cleared |"
    ) in ready
    assert "| Task nature | Defect mechanisms |" in text
    assert ready.endswith("| cleared |")

    # A rerun after the fix describes the current state, not the earlier one.
    problems.broken_preflight.clear()
    problems.setup()
    assert "**2** cleared" in report.read_text()
