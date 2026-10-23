"""What the review page shows: one rater's view of the targets under a directory.

Each method answers one request the page makes, as JSON-ready data. Repository
views, evidence, and agent traces are built on first use and kept for the session.
"""

from __future__ import annotations

import threading
from pathlib import Path
from string import Template

from oracle_lab.io import read_json
from oracle_lab.results import InstanceRecord
from oracle_lab.review.contracts import RATING_DEFINITIONS, require_rater
from oracle_lab.review.sources import SourceView, open_views
from oracle_lab.review.store import read_review, save_review
from oracle_lab.review.targets import (
    JudgmentTarget,
    Problem,
    ReviewTarget,
    UnreviewableTarget,
    find_problems,
    find_targets,
)

HOW_TO_REVIEW = Path(__file__).resolve().parent / "templates/how-to-review.md"

# The task record's readable texts beside the issue, by dataset field, in page order.
RECORD_TEXTS = {
    "hints_text": "Issue comments",
    "pr_description": "Pull request description",
    "interface": "Interface",
}


class ReviewSession:
    """The review targets under one directory, as one rater sees them."""

    def __init__(
        self, root: Path, rater: str, *, remote: str | None = None, cache: Path | None = None
    ):
        self.rater = require_rater(rater)
        targets, unreviewable = find_targets(root)
        # A target's ID is its path under the directory being reviewed.
        self.targets: dict[str, ReviewTarget] = {
            _target_id(root, target.root): target for target in targets
        }
        self.unreviewable: dict[str, UnreviewableTarget] = {
            _target_id(root, item.root): item for item in unreviewable
        }
        self._ids = {target.root: target_id for target_id, target in self.targets.items()}
        self.problems: dict[str, Problem] = {
            problem.instance_id: problem for problem in find_problems(targets)
        }
        self._remote = remote
        self._cache = cache
        self._lock = threading.Lock()
        self._views: dict[str, dict[str, SourceView]] = {}
        self._evidence: dict[str, dict[str, bytes]] = {}
        self._traces: dict[str, dict] = {}

    def listing(self) -> dict:
        """Every problem with its tasks in rating order and this rater's progress, and
        every target that cannot be reviewed.

        Each task carries its stage, its label (harness, model, and scope for a run),
        its question keys, and whether this rater has saved a review.
        """
        return {
            "rater": self.rater,
            "rating_definitions": RATING_DEFINITIONS,
            "how_to_review": how_to_review(),
            "problems": [
                {
                    "instance_id": problem.instance_id,
                    "repo": problem.instance.repo,
                    "title": _issue_title(problem.instance),
                    "tasks": [self._task(self._ids[target.root]) for target in problem.tasks],
                }
                for problem in self.problems.values()
            ],
            "unreviewable": [
                {"id": target_id, "stage": item.stage, "reason": item.reason}
                for target_id, item in self.unreviewable.items()
            ],
        }

    def problem(self, instance_id: str) -> dict:
        """What every rating page's Problem tab shows: the instance, the issue, the
        record's other texts, the golden patch, and the reference tests."""
        if instance_id not in self.problems:
            raise LookupError(f"No such problem: {instance_id}")
        problem = self.problems[instance_id]
        instance = problem.instance
        record = instance.original_record
        return {
            "instance_id": instance_id,
            "repo": instance.repo,
            "dataset": instance.source.name,
            "base_commit": instance.base_commit,
            "issue": problem.issue(),
            "record_texts": [
                {"title": title, "text": record[field]}
                for field, title in RECORD_TEXTS.items()
                if isinstance(record.get(field), str) and record[field].strip()
            ],
            "repair_patch": instance.golden_patch,
            "reference_test_patch": instance.reference_test_patch,
            "reference_test_ids": instance.reference_test_ids,
        }

    def overview(self, target_id: str) -> dict:
        """The rating page's own data: the questions, the answers, the stage rubric, and,
        for a judgment, the outcomes and the submission. The problem's issue, patches,
        and record texts come from `problem`."""
        target = self._target(target_id)
        answers = target.answers.model_dump(mode="json")
        review = read_review(target, self.rater)
        overview = {
            "id": target_id,
            "stage": target.stage,
            "instance_id": target.instance.instance_id,
            "views": list(target.views),
            # Every rating item in rail order, with its section ("submission",
            # "failure_modes", or "added_failure_modes"), the judge's answer or
            # decision, its steps for a failure mode, and its justification.
            "questions": _rating_items(target, answers),
            "review": review.model_dump() if review else None,
            "stage_rubric": target.judge.rubric.read_text(),
            "evidence": sorted(self._evidence_of(target_id)),
        }
        if isinstance(target, JudgmentTarget):
            overview |= _judgment_details(target)
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

    def trace(self, target_id: str) -> dict:
        """A judged run's agent trace as saved when it was judged (`judge/trace.json`).

        Read once per session. A run whose trace could not be built has none.
        """
        target = self._target(target_id)
        if not isinstance(target, JudgmentTarget):
            raise LookupError(f"{target_id} has no agent trace")
        with self._lock:
            if target_id not in self._traces:
                if not target.paths.judge.trace.is_file():
                    raise LookupError(
                        "This run's trace could not be built when it was judged; "
                        "the judge read the raw trace (agent-trace.jsonl in Evidence)."
                    )
                self._traces[target_id] = read_json(target.paths.judge.trace)
            return self._traces[target_id]

    def submit(self, target_id: str, raw: str) -> dict:
        review = save_review(self._target(target_id), self.rater, raw)
        return {"review": review.model_dump()}

    def _task(self, target_id: str) -> dict:
        """One target as the home page lists it, with this rater's progress."""
        target = self._target(target_id)
        return {
            "id": target_id,
            "stage": target.stage,
            "instance_id": target.instance.instance_id,
            "run": _run_label(target) if isinstance(target, JudgmentTarget) else None,
            "questions": target.rating_keys(),
            "reviewed": read_review(target, self.rater) is not None,
        }

    def _target(self, target_id: str) -> ReviewTarget:
        if target_id not in self.targets:
            raise LookupError(f"No such review target: {target_id}")
        return self.targets[target_id]

    def _evidence_of(self, target_id: str) -> dict[str, bytes]:
        target = self._target(target_id)
        with self._lock:
            if target_id not in self._evidence:
                self._evidence[target_id] = target.evidence()
            return self._evidence[target_id]

    def _view(self, target_id: str, view: str) -> SourceView:
        target = self._target(target_id)
        if view not in target.views:
            raise LookupError(f"No {view} view for {target_id}")
        with self._lock:
            if target_id not in self._views:
                self._views[target_id] = open_views(target, remote=self._remote, cache=self._cache)
            return self._views[target_id][view]


def _judgment_details(target: JudgmentTarget) -> dict:
    """The paired outcomes and the submission the judge rated."""
    evaluation = target.evaluation()
    manifest = target.manifest()
    prompt = target.paths.prompt
    return {
        "run": _run_label(target),
        "test_target": evaluation.test_target.text if evaluation.test_target else None,
        "prompt": prompt.read_text() if prompt.is_file() else None,
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


def how_to_review() -> str:
    """The reviewer's instructions as Markdown, with each rating's definition filled in."""
    ratings = "\n".join(
        f"- **{rating.capitalize()}:** {definition}"
        for rating, definition in RATING_DEFINITIONS.items()
    )
    return Template(HOW_TO_REVIEW.read_text()).substitute(ratings=ratings)


def _rating_items(target: ReviewTarget, answers: dict) -> list[dict]:
    """Every rating item in rail order, as the rail shows it."""
    items = []
    for key in target.rating_keys():
        section, _, name = key.partition(".")
        item = {"key": key, "label": target.rating_label(key)}
        if section == "failure_modes":
            decision = answers["failure_modes"][name]
            item |= {"section": section, "answer": decision["decision"]}
            item |= {field: decision.get(field) for field in ("steps", "justification")}
        elif section == "added_failure_modes":
            added = answers["added_failure_modes"][int(name)]
            item |= {"section": section, "answer": "present", "definition": added["definition"]}
            item |= {field: added[field] for field in ("steps", "justification")}
        else:
            item |= {
                "section": "submission",
                "answer": answers[key],
                "justification": answers["justifications"][key],
            }
        items.append(item)
    return items


def _run_label(target: JudgmentTarget) -> dict:
    """What tells a problem's runs apart: the agent and the prompt's scope."""
    config = target.config()
    return {
        "harness": config.agent.harness,
        "model": config.agent.model,
        "scope": config.task.scope,
    }


def _issue_title(instance: InstanceRecord) -> str:
    """The issue's first line, as dataset issues start with their title."""
    issue = instance.original_record.get("problem_statement")
    lines = issue.strip().splitlines() if isinstance(issue, str) else []
    return lines[0].lstrip("# ").strip() if lines else ""


def _text(content: bytes) -> dict:
    """A file as the page shows it: text, or a note that it is binary."""
    binary = b"\0" in content[:8192]
    text = None if binary else content.decode("utf-8", errors="replace")
    return {"binary": binary, "size": len(content), "text": text}


def _target_id(root: Path, target: Path) -> str:
    if target == root:
        return target.resolve().name
    return target.relative_to(root).as_posix()
