"""Localization on real golden patches, against targets checked by hand.

Each case in `data/localization` is one SWE-bench Verified or SWE-rebench V2
instance: its golden patch, the buggy production files it changes, and the
expected target. `data/localization/build.py` made them.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from oracle_lab.localization import production_diffs, read_source
from oracle_lab.localization.patches import apply_patch
from oracle_lab.localization.target import choose_target, compare

CORPUS = Path(__file__).parent / "data/localization"
CASES = sorted(path.parent for path in CORPUS.glob("*/case.json"))
GIT = shutil.which("git")


def load(directory: Path) -> dict:
    return json.loads((directory / "case.json").read_text())


def buggy_files(directory: Path) -> set[str]:
    root = directory / "buggy"
    return {path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()}


def test_the_corpus_is_present():
    assert len(CASES) >= 30


@pytest.mark.parametrize("directory", CASES, ids=[case.name for case in CASES])
def test_the_target_matches_the_hand_checked_one(directory):
    case = load(directory)
    diffs = production_diffs(case["patch"], case["source_roots"])
    assert {path for path, _ in diffs} == buggy_files(directory)
    changes = []
    for path, diff in diffs:
        buggy = read_source((directory / "buggy" / path).read_bytes())
        fixed = apply_patch(buggy, diff) if diff.new_path is not None else None
        changes.append(compare(path, buggy, fixed))
    assert choose_target(changes).model_dump(exclude={"text"}) == case["expected"]


@pytest.mark.skipif(GIT is None, reason="Needs the git binary")
@pytest.mark.parametrize("directory", CASES, ids=[case.name for case in CASES])
def test_apply_patch_matches_git_apply(directory, tmp_path):
    case = load(directory)
    diffs = production_diffs(case["patch"], case["source_roots"])
    shutil.copytree(directory / "buggy", tmp_path, dirs_exist_ok=True)
    (tmp_path / "golden.patch").write_text(case["patch"])
    include = [f"--include={diff.new_path or path}" for path, diff in diffs]
    include += [f"--include={path}" for path, _ in diffs]
    subprocess.run(
        [GIT, "apply", *include, "golden.patch"], cwd=tmp_path, check=True, capture_output=True
    )
    for path, diff in diffs:
        if diff.new_path is None:
            continue
        buggy = read_source((directory / "buggy" / path).read_bytes())
        expected = read_source((tmp_path / diff.new_path).read_bytes())
        assert apply_patch(buggy, diff) == expected, path
