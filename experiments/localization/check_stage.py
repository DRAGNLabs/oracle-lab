"""Run Oracle Bench's real `localize` stage against real runtime images.

For each instance: resolve it from the dataset, build its runtime image with
`prepare_image`, run `localize`, and compare the target with the prototype's
answer in `results/instances.jsonl`. No model is called. Images are pulled and
built as in a normal run, and output goes to a temporary run directory.

Usage: python check_stage.py INSTANCE_ID [INSTANCE_ID ...]
Writes `results/stage_check.json`.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from oracle_bench.config import load_config
from oracle_bench.container.images import prepare_image
from oracle_bench.container.sandbox import docker_client
from oracle_bench.instance import resolve_source
from oracle_bench.localization import localize
from oracle_bench.paths import RunPaths

HERE = Path(__file__).parent
# A localized sample config; only its source instance changes.
CONFIG = HERE.parents[1] / "configs/validation/pytest-10051-claude.yaml"


def prototype(instance_id: str) -> dict:
    for line in (HERE / "results/instances.jsonl").read_text().splitlines():
        record = json.loads(line)
        if record["instance_id"] == instance_id:
            return record["target"]
    raise KeyError(instance_id)


def check(client, instance_id: str, scratch: Path) -> dict:
    config = load_config(CONFIG)
    config.source.instance = instance_id
    instance, config.runtime = resolve_source(config.source)
    paths = RunPaths.create(scratch / instance_id)
    image = prepare_image(client, config, paths.build)
    target = localize(client, config, image, instance, paths)
    if target.level == "file":
        names = [entry.path for entry in target.files]
    else:
        names = [f"{entry.path}::{name}" for entry in target.files for name in entry.names]
    stage = {"level": target.level, "names": names, "lines": sum(f.lines for f in target.files)}
    expected = prototype(instance_id)
    return {
        "instance_id": instance_id,
        "image": image,
        "stage": stage,
        "prototype": expected,
        "match": stage == expected,
        "text": target.text,
    }


def main(instance_ids: list[str]) -> None:
    checks = []
    with docker_client() as client, tempfile.TemporaryDirectory() as scratch:
        for instance_id in instance_ids:
            result = check(client, instance_id, Path(scratch))
            checks.append(result)
            print(f"{instance_id}: {'match' if result['match'] else 'DIFFERS'}")
            print("    " + result["text"].replace("\n", "\n    "))
            if not result["match"]:
                print(f"    stage:     {result['stage']}")
                print(f"    prototype: {result['prototype']}")
    out = HERE / "results/stage_check.json"
    previous = json.loads(out.read_text()) if out.is_file() else []
    merged = {c["instance_id"]: c for c in [*previous, *checks]}
    out.write_text(
        json.dumps(sorted(merged.values(), key=lambda c: c["instance_id"]), indent=2) + "\n"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
