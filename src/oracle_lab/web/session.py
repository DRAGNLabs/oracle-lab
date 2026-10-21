"""What the review page shows: one rater's view of the targets under a directory.

Each method answers one request the page makes, as JSON-ready data. Repository
views, evidence, and agent traces are built on first use and kept for the session.
"""

from __future__ import annotations

import threading
from pathlib import Path

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
        its number of questions, and whether this rater has saved a review.
        """
        raise NotImplementedError

    def problem(self, instance_id: str) -> dict:
        """What the problem page and every Problem tab show: the instance, the issue, the
        record's other texts, the golden patch, the reference tests, and its tasks."""
        raise NotImplementedError

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
            "questions": [
                {
                    "key": key,
                    "label": target.labels[key],
                    "answer": answers[key],
                    "justification": answers["justifications"][key],
                }
                for key in target.answered_questions()
            ],
            "review": review.ratings if review else None,
            "stage_rubric": target.stage_rubric.read_text(),
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
        """A judged run's agent trace, as `RunTrace.to_json()`. Built once per session."""
        raise NotImplementedError

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
    return {
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


def _text(content: bytes) -> dict:
    """A file as the page shows it: text, or a note that it is binary."""
    binary = b"\0" in content[:8192]
    text = None if binary else content.decode("utf-8", errors="replace")
    return {"binary": binary, "size": len(content), "text": text}


def _target_id(root: Path, target: Path) -> str:
    if target == root:
        return target.resolve().name
    return target.relative_to(root).as_posix()
