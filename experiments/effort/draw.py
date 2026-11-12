"""Draw the effort experiment's problems from the study batch, at random within groups.

Each study problem gets one group from its three baseline judgments (Claude,
Codex, OpenCode), checked in this order:

- excluded: any run's tests attempt the issue with a correct assertion
- A: any run attempts the issue with a wrong assertion
- B: any run attempts the issue with another flaw (missing_required_condition,
  no_issue_assertion)
- C: any run does not attempt the issue for a reason other than a missed trigger
  (missed_outcome, unrelated_behavior)
- D: every run missed the trigger

Three problems are drawn from each group with a seed from the operating system,
unless one is given. The seed, the groups, and every problem's labels are saved,
so the draw can be checked without the study's output directory.

    uv run python experiments/effort/draw.py <study-batch-dir> [--seed N]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from oracle_lab.contracts.run import read_instance_record

PER_GROUP = 3
OUT = Path(__file__).with_name("selection.json")


def label(judgment: dict) -> str:
    def answer(field: str):
        value = judgment.get(field)
        return value.get("answer") if isinstance(value, dict) else value

    if answer("tests_issue") == "yes":
        return str(answer("attempt_detail"))
    return "no:" + str(answer("no_attempt_reason"))


def group(labels: set[str]) -> str:
    if "correct_assertion" in labels:
        return "excluded"
    if "wrong_assertion" in labels:
        return "A"
    if labels & {"missing_required_condition", "no_issue_assertion"}:
        return "B"
    if labels & {"no:missed_outcome", "no:unrelated_behavior"}:
        return "C"
    if labels == {"no:missed_trigger"}:
        return "D"
    raise ValueError(f"No group for labels {sorted(labels)}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("batch", type=Path)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    seed = args.seed if args.seed is not None else random.SystemRandom().getrandbits(63)

    manifest = json.loads((args.batch / "batch.resolved.json").read_text())
    problems: dict[str, dict] = {}
    for job in manifest["jobs"]:
        run = Path(job["run_dir"])
        harness = job["config"].rsplit("-", 1)[1].removesuffix(".yaml")
        instance = read_instance_record(run / "inputs" / "instance.json")
        judgment = json.loads((run / "judge" / "judgment.json").read_text())
        if judgment["status"] != "completed":
            raise ValueError(f"{run} has no completed judgment")
        entry = problems.setdefault(
            instance.instance_id, {"dataset": instance.source.name, "baseline": {}}
        )
        entry["baseline"][harness] = {"run": run.name, "label": label(judgment)}

    groups: dict[str, list[str]] = {}
    for instance_id, entry in sorted(problems.items()):
        entry["group"] = group({run["label"] for run in entry["baseline"].values()})
        groups.setdefault(entry["group"], []).append(instance_id)

    rng = random.Random(seed)
    drawn = {name: sorted(rng.sample(groups[name], PER_GROUP)) for name in "ABCD"}

    OUT.write_text(
        json.dumps(
            {
                "baseline_batch": args.batch.name,
                "seed": seed,
                "per_group": PER_GROUP,
                "group_sizes": {name: len(ids) for name, ids in sorted(groups.items())},
                "drawn": drawn,
                "problems": problems,
            },
            indent=2,
        )
        + "\n"
    )
    for name in "ABCD":
        print(name, f"({len(groups[name])})", ", ".join(drawn[name]))
    print("seed", seed, "->", OUT)


if __name__ == "__main__":
    main()
