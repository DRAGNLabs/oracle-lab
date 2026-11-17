"""Assign the drawn problems to raters: some shared by every rater, the rest split.

Every rater reviews the shared problems, which measure agreement between raters,
and their own problems, which no other rater sees. Both parts take the same
number of problems from each dataset. The seed comes from the operating system
unless one is given, and the seed and assignment are saved.

    uv run python experiments/human-review/assign.py jay jack tessa mo [--seed N]
"""

from __future__ import annotations

import argparse
import json
import random
from pathlib import Path

from oracle_lab.contracts.review import require_rater

SHARED_PER_DATASET = 3
HERE = Path(__file__).parent
OUT = HERE / "assignment.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("raters", nargs="+", type=require_rater)
    parser.add_argument("--seed", type=int)
    args = parser.parse_args()
    seed = args.seed if args.seed is not None else random.SystemRandom().getrandbits(63)
    selection = json.loads((HERE / "selection.json").read_text())

    rng = random.Random(seed)
    shared: list[str] = []
    own: dict[str, list[str]] = {rater: [] for rater in args.raters}
    for dataset, problems in sorted(selection["drawn"].items()):
        problems = sorted(problems)
        rng.shuffle(problems)
        shared += problems[:SHARED_PER_DATASET]
        rest = problems[SHARED_PER_DATASET:]
        if len(rest) % len(args.raters):
            raise ValueError(f"{len(rest)} {dataset} problems do not split evenly among raters")
        size = len(rest) // len(args.raters)
        for index, rater in enumerate(args.raters):
            own[rater] += rest[index * size : (index + 1) * size]

    assignment = {
        "selection_seed": selection["seed"],
        "seed": seed,
        "shared": sorted(shared),
        "own": {rater: sorted(ids) for rater, ids in own.items()},
    }
    OUT.write_text(json.dumps(assignment, indent=2) + "\n")
    print("shared:", ", ".join(assignment["shared"]))
    for rater, ids in assignment["own"].items():
        print(f"{rater}:", ", ".join(ids))
    print("seed", seed, "->", OUT)


if __name__ == "__main__":
    main()
