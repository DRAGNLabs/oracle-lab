"""Run the AST localizer over SWE-bench Verified, with its cross-check and the old heuristic.

For each instance, every changed production Python file is downloaded from
GitHub at `base_commit` (cached under `.cache/`), the golden patch is applied,
and three answers are recorded:

- `target`: the AST localizer, the proposed method (`localize.py`);
- `crosscheck`: an independent line-attribution implementation (`crosscheck.py`);
- `old`: Oracle Bench's old diff-text heuristic, frozen in `old_heuristic.py`.

Writes `results/instances.jsonl` and `results/summary.json`.
"""

from __future__ import annotations

import json
import re
import statistics
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath

from crosscheck import attribute_lines
from datasets import load_dataset
from localize import compare, localize
from old_heuristic import _is_test_path, localized_test_target
from patches import apply_patch, locate_hunks, parse_patch

from oracle_bench.config import SWEBENCH_DATASET_REVISIONS, SWEBENCH_DATASETS
from oracle_bench.instance.swebench import REPOSITORY_LAYOUTS

HERE = Path(__file__).parent
CACHE = HERE / ".cache" / "files"
RESULTS = HERE / "results"
# Source roots for the repositories Oracle Bench excludes, so all of Verified is scored.
EXCLUDED_ROOTS = {"django/django": "django", "sympy/sympy": "sympy"}
OLD_TARGET = re.compile(r"(\S+?\.py)(?: \(([^)]*)\))?(?:, |$)")


def source_root(repo: str) -> str:
    return (
        REPOSITORY_LAYOUTS[repo].source_root if repo in REPOSITORY_LAYOUTS else EXCLUDED_ROOTS[repo]
    )


def is_production(path: str | None, root: str) -> bool:
    if not path or not path.endswith(".py"):
        return False
    candidate, base = PurePosixPath(path), PurePosixPath(root)
    return (base == candidate or base in candidate.parents) and not _is_test_path(candidate)


def fetch(repo: str, commit: str, path: str) -> str:
    cached = CACHE / repo / commit / path
    if not cached.is_file():
        url = f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(data)
    return cached.read_text(encoding="utf-8", errors="replace")


def old_target(patch: str, root: str) -> list[str]:
    """The current heuristic's answer as `path::symbol`, or bare paths."""
    try:
        rendered = localized_test_target(patch, [root])
    except ValueError:
        return []
    names = []
    for path, symbols in OLD_TARGET.findall(rendered):
        names += [f"{path}::{s}" for s in symbols.split(", ") if s] or [path]
    return names


def evaluate(record: dict) -> dict:
    repo, commit, patch = record["repo"], record["base_commit"], record["patch"]
    root = source_root(repo)
    changes, crosscheck, files, errors = [], [], [], []
    offset_hunks = 0
    for diff in parse_patch(patch):
        path = diff.old_path or diff.new_path
        if not is_production(path, root):
            continue
        if diff.old_path is None:
            files.append({"path": path, "status": "added"})
            continue
        buggy = fetch(repo, commit, diff.old_path)
        try:
            fixed = apply_patch(buggy, diff) if diff.new_path else ""
            change = compare(path, buggy, fixed)
            attributed = attribute_lines(buggy, diff)
        except (SyntaxError, ValueError) as error:
            errors.append(f"{path}: {type(error).__name__}: {error}")
            continue
        located = locate_hunks(buggy, diff)
        stated = [h.old_start if h.old_length else h.old_start + 1 for h in diff.hunks]
        offset_hunks += sum(a != b for a, b in zip(located, stated))
        changes.append(change)
        crosscheck += [f"{path}::{name}" for name in attributed.units]
        files.append(
            {
                "path": path,
                "status": "modified" if diff.new_path else "deleted",
                "lines": change.lines,
                "functions": [d.qualname for d in change.functions],
                "classes": [d.qualname for d in change.classes],
                "altered": change.altered,
            }
        )
    target = localize(changes) if changes else None
    changed_functions = [f"{c.path}::{d.qualname}" for c in changes for d in c.functions]
    return {
        "instance_id": record["instance_id"],
        "repo": repo,
        "supported": repo in REPOSITORY_LAYOUTS,
        "files": files,
        "errors": errors,
        "offset_hunks": offset_hunks,
        "target": target and {"level": target.level, "names": target.names, "lines": target.lines},
        "changed_functions": list(dict.fromkeys(changed_functions)),
        "crosscheck": list(dict.fromkeys(crosscheck)),
        "old": old_target(patch, root),
        "file_lines": sum(c.lines for c in changes),
    }


def old_verdict(result: dict) -> dict:
    """Compare the old heuristic's names with the AST target, matching bare names leniently.

    An old name counts as correct when a target name in the same file ends with
    it, so a bare `reset` matches `Handler.reset`.
    """
    target = set(result["target"]["names"])
    level = result["target"]["level"]
    correct, wrong, covered = [], [], set()
    for name in result["old"]:
        if "::" not in name:  # the old heuristic named only a file
            hits = {name} & target if level == "file" else set()
        else:
            path, symbol = name.split("::")
            hits = {
                t
                for t in target
                if t.startswith(path + "::")
                and (t.endswith("::" + symbol) or t.endswith("." + symbol))
            }
        (correct if hits else wrong).append(name)
        covered |= hits
    return {
        "correct": correct,
        "wrong": wrong,
        "missed": sorted(target - covered),
        "exact": not wrong and covered == target,
    }


def summarize(rows: list[dict]) -> dict:
    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    def spread(values: list[int]) -> dict:
        values = sorted(values)
        if not values:
            return {}
        at = lambda q: values[min(len(values) - 1, int(q * len(values)))]  # noqa: E731
        return {
            "median": statistics.median(values),
            "p75": at(0.75),
            "p90": at(0.9),
            "max": values[-1],
        }

    scored = [r for r in rows if r["target"]]
    levels = {
        level: [r for r in scored if r["target"]["level"] == level]
        for level in ("function", "class", "file")
    }
    verdicts = [r["old_verdict"] for r in scored]
    agree = [set(r["crosscheck"]) == set(r["changed_functions"]) for r in scored]
    target_names = sum(len(r["target"]["names"]) for r in scored)
    return {
        "instances": len(rows),
        "scored": len(scored),
        "unscored": [r["instance_id"] for r in rows if not r["target"]],
        "errors": sum(bool(r["errors"]) for r in rows),
        "offset_hunk_instances": sum(r["offset_hunks"] > 0 for r in scored),
        "levels": {level: len(found) for level, found in levels.items()},
        "crosscheck_agreement": rate(sum(agree), len(scored)),
        "crosscheck_disagreements": [r["instance_id"] for r, ok in zip(scored, agree) if not ok],
        "old": {
            "exact": rate(sum(v["exact"] for v in verdicts), len(scored)),
            "names": sum(len(r["old"]) for r in scored),
            "precision": rate(
                sum(len(v["correct"]) for v in verdicts), sum(len(r["old"]) for r in scored)
            ),
            "recall": rate(target_names - sum(len(v["missed"]) for v in verdicts), target_names),
            "instances_with_a_wrong_name": sum(bool(v["wrong"]) for v in verdicts),
            "instances_missing_a_target_name": sum(bool(v["missed"]) for v in verdicts),
        },
        "size": {
            "names_per_function_target": spread(
                [len(r["target"]["names"]) for r in levels["function"]]
            ),
            "function_target_lines": spread([r["target"]["lines"] for r in levels["function"]]),
            "class_target_lines": spread([r["target"]["lines"] for r in levels["class"]]),
            "any_target_lines": spread([r["target"]["lines"] for r in scored]),
            "changed_file_lines": spread([r["file_lines"] for r in scored]),
        },
    }


def main() -> None:
    revision = SWEBENCH_DATASET_REVISIONS["verified"]
    records = list(load_dataset(SWEBENCH_DATASETS["verified"], split="test", revision=revision))
    with ThreadPoolExecutor(16) as pool:
        results = sorted(pool.map(evaluate, records), key=lambda r: r["instance_id"])
    for result in results:
        if result["target"]:
            result["old_verdict"] = old_verdict(result)

    RESULTS.mkdir(exist_ok=True)
    with (RESULTS / "instances.jsonl").open("w") as out:
        for result in results:
            out.write(json.dumps(result, sort_keys=True) + "\n")
    summary = {
        "dataset": SWEBENCH_DATASETS["verified"],
        "dataset_revision": revision,
        "supported": summarize([r for r in results if r["supported"]]),
        "all": summarize(results),
    }
    (RESULTS / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
