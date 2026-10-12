"""Do the failing reference tests execute the target on the buggy version?

Oracle Bench's reference check runs each instance's `FAIL_TO_PASS` tests on the
buggy checkout under coverage. This reads that saved coverage from existing runs
and asks, for every function in the AST target, whether any line of its body ran.
`def` and decorator lines are left out because they run when the module is
imported, whether or not the function is ever called.

It also asks whether the changed lines themselves were reached. A removed line
is reached when it ran. An insertion is reached when the first executable line
after it, inside the same function, ran: code inserted there would have run too.

Usage: python coverage_check.py RUN_DIR [RUN_DIR ...]
Writes `results/coverage_check.json`.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

from datasets import load_dataset
from patches import locate_hunks, parse_patch

from oracle_bench.config import SWEBENCH_DATASET_REVISIONS, SWEBENCH_DATASETS

HERE = Path(__file__).parent
CACHE = HERE / ".cache" / "files"
FUNCTION = (ast.FunctionDef, ast.AsyncFunctionDef)


def body_lines(source: str, qualname: str) -> list[range]:
    """Line ranges of each same-named definition's body, without its docstring."""
    found = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.ClassDef):
                walk(child, prefix + child.name + ".")
            elif isinstance(child, FUNCTION):
                if prefix + child.name == qualname:
                    body = child.body
                    if (
                        len(body) > 1
                        and isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and isinstance(body[0].value.value, str)
                    ):
                        body = body[1:]
                    found.append(range(body[0].lineno, child.end_lineno + 1))
            else:
                walk(child, prefix)

    walk(ast.parse(source), "")
    return found


def changed_lines(source: str, diff) -> tuple[list[int], list[int]]:
    """Buggy-file lines the patch removes, and the old line each insertion precedes."""
    removed, inserted_before = [], []
    for hunk, position in zip(diff.hunks, locate_hunks(source, diff)):
        for line in hunk.lines:
            tag = line[:1]
            if tag == "-" and line[1:].strip():
                removed.append(position)
            elif tag == "+" and line[1:].strip():
                inserted_before.append(position)
            if tag in (" ", "", "-"):
                position += 1
    return removed, sorted(set(inserted_before))


def check(run: Path, instances: dict, patches: dict) -> dict:
    instance = json.loads((run / "inputs/instance.json").read_text())
    instance_id = instance["instance_id"]
    result = instances[instance_id]
    coverage = json.loads((run / "reference-check/buggy/coverage.json").read_text())
    reference = json.loads((run / "reference-check/results.json").read_text())
    functions = []
    for name in result["target"]["names"]:
        path, qualname = name.split("::")
        source = (CACHE / result["repo"] / instance["base_commit"] / path).read_text()
        executed = set(coverage["files"].get(path, {}).get("executed_lines", []))
        file = coverage["files"].get(path, {})
        executable = executed | set(file.get("missing_lines", []))
        spans = body_lines(source, qualname)
        hit = sorted(line for span in spans for line in span if line in executed)
        diff = next(d for d in parse_patch(patches[instance_id]) if d.old_path == path)
        removed, inserted_before = changed_lines(source, diff)
        inside = lambda line: any(line in span for span in spans)  # noqa: E731
        reached_removed = [n for n in removed if inside(n) and n in executable]
        reached_inserts = []
        for line in inserted_before:
            following = [n for n in sorted(executable) if n >= line and inside(n)]
            if inside(line) or (following and inside(following[0])):
                reached_inserts.append(bool(following) and following[0] in executed)
        functions.append(
            {
                "name": name,
                "body_lines": sum(len(span) for span in spans),
                "executed_lines": len(hit),
                "executed": bool(hit),
                "removed_lines": len(reached_removed),
                "removed_lines_ran": sum(n in executed for n in reached_removed),
                "insertions": len(reached_inserts),
                "insertions_reached": sum(reached_inserts),
                "change_reached": any(n in executed for n in reached_removed)
                or any(reached_inserts),
            }
        )
    return {
        "instance_id": instance_id,
        "run": run.name,
        "target_level": result["target"]["level"],
        "reference_tests": reference["matrix"]["fail_on_buggy_pass_on_golden"]["test_ids"],
        "coverage_status": coverage["status"],
        "functions": functions,
        "all_executed": all(f["executed"] for f in functions),
        "change_reached": all(f["change_reached"] for f in functions),
        "any_executed": any(f["executed"] for f in functions),
    }


def main(runs: list[str]) -> None:
    instances = {}
    for line in (HERE / "results/instances.jsonl").read_text().splitlines():
        record = json.loads(line)
        instances[record["instance_id"]] = record
    revision = SWEBENCH_DATASET_REVISIONS["verified"]
    dataset = load_dataset(SWEBENCH_DATASETS["verified"], split="test", revision=revision)
    patches = {row["instance_id"]: row["patch"] for row in dataset}
    checks = [check(Path(run), instances, patches) for run in runs]
    out = HERE / "results/coverage_check.json"
    out.write_text(json.dumps(checks, indent=2) + "\n")
    for c in checks:
        status = "all" if c["all_executed"] else "some" if c["any_executed"] else "none"
        reached = "reached" if c["change_reached"] else "NOT reached"
        print(f"{c['instance_id']}: {c['target_level']}, executed: {status}, change {reached}")
        for f in c["functions"]:
            print(
                f"    {f['name']}: {f['executed_lines']}/{f['body_lines']} body lines ran; "
                f"removed {f['removed_lines_ran']}/{f['removed_lines']} ran; "
                f"insertions {f['insertions_reached']}/{f['insertions']} reached"
            )


if __name__ == "__main__":
    main(sys.argv[1:])
