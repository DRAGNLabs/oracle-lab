"""Build the localization corpus: real golden patches and the buggy files they change.

Run once with network access; the tests read only what this writes. It samples
supported SWE-bench Verified repositories and Python bug fixes from SWE-rebench
V2, fetches each changed production file at its base commit from GitHub, and
records the target the current code chooses. Check every `expected` by hand
before committing it.

    uv run python tests/data/localization/build.py
"""

from __future__ import annotations

import json
import random
import urllib.request
from collections import defaultdict
from pathlib import Path

import pyarrow.parquet as pq
from huggingface_hub import hf_hub_download

from oracle_lab.config import SOURCE_DATASETS
from oracle_lab.instance import swebench, swerebench_v2
from oracle_lab.localization import production_diffs, read_source
from oracle_lab.localization.patches import apply_patch
from oracle_lab.localization.target import choose_target, compare

HERE = Path(__file__).parent
SEED = 20261026
PER_REPOSITORY = 3
REBENCH_SAMPLE = 12


def dataset_rows(name: str, columns: list[str] | None = None) -> list[dict]:
    pinned = SOURCE_DATASETS[name]
    path = hf_hub_download(
        pinned.name,
        f"data/{pinned.split}-00000-of-00001.parquet",
        repo_type="dataset",
        revision=pinned.revision,
    )
    return pq.read_table(path, columns=columns).to_pylist()


def is_bug_fix(record: dict) -> bool:
    categories = ((record.get("meta") or {}).get("llm_metadata") or {}).get("pr_categories")
    return bool(categories) and all(c.endswith("_bug") for c in categories)


def sample() -> list[tuple[dict, list[str]]]:
    rng = random.Random(SEED)
    chosen = []
    by_repo = defaultdict(list)
    for record in dataset_rows("verified"):
        if record["repo"] in swebench.REPOSITORY_LAYOUTS:
            by_repo[record["repo"]].append(record)
    for repo in sorted(by_repo):
        roots = [swebench.REPOSITORY_LAYOUTS[repo].source_root]
        for record in rng.sample(by_repo[repo], min(PER_REPOSITORY, len(by_repo[repo]))):
            chosen.append((record, roots))

    candidates = defaultdict(list)
    for record in dataset_rows("swerebench_v2"):
        if record["language"] == "python" and is_bug_fix(record):
            candidates[record["repo"]].append(record)
    for repo in sorted(swerebench_v2.REPOSITORY_LAYOUTS):
        roots = [swerebench_v2.REPOSITORY_LAYOUTS[repo].source_root]
        chosen.append((rng.choice(candidates[repo]), roots))
    others = sorted(set(candidates) - set(swerebench_v2.REPOSITORY_LAYOUTS))
    for repo in rng.sample(others, REBENCH_SAMPLE):
        # An unsupported repository has no known layout: its whole tree is source.
        chosen.append((rng.choice(candidates[repo]), ["."]))
    return chosen


def fetch(repo: str, commit: str, path: str) -> bytes:
    url = f"https://raw.githubusercontent.com/{repo}/{commit}/{path}"
    with urllib.request.urlopen(url, timeout=30) as response:
        return response.read()


def build(record: dict, roots: list[str]) -> dict | None:
    directory = HERE / record["instance_id"]
    diffs = production_diffs(record["patch"], roots)
    if not diffs:
        print(f"skip {record['instance_id']}: no existing production Python file")
        return None
    changes = []
    for path, diff in diffs:
        data = fetch(record["repo"], record["base_commit"], path)
        (directory / "buggy" / path).parent.mkdir(parents=True, exist_ok=True)
        (directory / "buggy" / path).write_bytes(data)
        buggy = read_source(data)
        fixed = apply_patch(buggy, diff) if diff.new_path is not None else None
        changes.append(compare(path, buggy, fixed))
    expected = choose_target(changes)
    case = {
        "instance_id": record["instance_id"],
        "repo": record["repo"],
        "base_commit": record["base_commit"],
        "source_roots": roots,
        "patch": record["patch"],
        "expected": expected.model_dump(exclude={"text"}),
    }
    (directory / "case.json").write_text(json.dumps(case, indent=2) + "\n")
    return case


def main() -> None:
    for record, roots in sample():
        case = build(record, roots)
        if case:
            expected = case["expected"]
            print(case["instance_id"], expected["level"], [f["names"] for f in expected["files"]])


if __name__ == "__main__":
    main()
