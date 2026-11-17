"""Draw the problems humans rate, at random within each dataset.

Raters review each drawn problem's classification and all three of its
generation judgments (Claude Code, Codex, OpenCode). Every study problem must
have a reviewable classification and three reviewable judgments, or nothing is
drawn. The seed comes from the operating system unless one is given, and the
seed, the drawn problems, and their run and attempt directories are saved.

    uv run python experiments/human-review/draw.py <study-batch-dir> [--seed N]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from oracle_lab.contracts.classification import latest_classification
from oracle_lab.contracts.run import read_instance_record
from oracle_lab.review.targets import ClassificationTarget, JudgmentTarget

PER_DATASET = 15
HARNESSES = ("claude", "codex", "opencode")
OUT = Path(__file__).with_name("selection.json")


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
        target = JudgmentTarget.load(run)  # refuses a judgment that cannot be reviewed
        instance = target.instance
        entry = problems.setdefault(
            instance.instance_id, {"dataset": instance.source.name, "runs": {}}
        )
        entry["runs"][target.config().agent.harness] = run.name

    classifications = args.batch.parents[1] / "classifications"
    for instance_id, entry in problems.items():
        if sorted(entry["runs"]) != sorted(HARNESSES):
            raise ValueError(f"{instance_id} has runs for {sorted(entry['runs'])}")
        run = args.batch.parents[1] / "runs" / entry["runs"]["claude"]
        _, attempt = latest_classification(
            classifications, read_instance_record(run / "inputs/instance.json")
        )
        if attempt is None:
            raise ValueError(f"{instance_id} has no classification")
        ClassificationTarget.load(attempt.root)  # refuses one that cannot be reviewed
        entry["classification"] = attempt.root.name

    datasets: dict[str, list[str]] = {}
    for instance_id, entry in sorted(problems.items()):
        datasets.setdefault(entry["dataset"], []).append(instance_id)

    rng = random.Random(seed)
    drawn = {name: sorted(rng.sample(ids, PER_DATASET)) for name, ids in sorted(datasets.items())}

    OUT.write_text(
        json.dumps(
            {
                "study_batch": args.batch.name,
                "seed": seed,
                "per_dataset": PER_DATASET,
                "dataset_sizes": {name: len(ids) for name, ids in sorted(datasets.items())},
                "drawn": drawn,
                "problems": {
                    instance_id: problems[instance_id]
                    for ids in drawn.values()
                    for instance_id in ids
                },
            },
            indent=2,
        )
        + "\n"
    )
    for name, ids in drawn.items():
        print(name, f"({len(datasets[name])})", ", ".join(ids))
    print("seed", seed, "->", OUT)


if __name__ == "__main__":
    main()
