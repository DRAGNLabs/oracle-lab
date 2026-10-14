"""Run the judge-workspace container helper directly on local fixtures."""

import json
import subprocess
import sys

import pytest
from fixtures import HELPERS

from oracle_lab.container.helpers.judge_workspace import copy_views, verify_submission
from oracle_lab.io import digest


def submission(root, name="oracle_tests/test_generated.py", body=b"def test_generated(): pass\n"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return {name: digest(body)}


def test_helper_annotations_work_on_old_repository_python():
    assert verify_submission.__annotations__["views"] is list


def test_identical_views_hash_identically(tmp_path):
    views = []
    expected = {}
    for name in ("buggy", "golden"):
        view = tmp_path / name
        expected = submission(view)
        views.append(str(view))

    observed = verify_submission(expected, views)

    assert observed == {view: expected for view in views}


def test_a_modified_view_is_reported_rather_than_accepted(tmp_path):
    expected = submission(tmp_path / "buggy")
    submission(tmp_path / "golden", body=b"def test_generated(): assert False\n")

    observed = verify_submission(expected, [str(tmp_path / "buggy"), str(tmp_path / "golden")])

    assert observed[str(tmp_path / "buggy")] == expected
    assert observed[str(tmp_path / "golden")] != expected


@pytest.mark.parametrize("escape", ["symlink", "traversal", "missing"])
def test_paths_that_leave_the_view_are_refused(tmp_path, escape):
    outside = tmp_path / "outside.py"
    outside.write_bytes(b"secret\n")
    view = tmp_path / "buggy"
    view.mkdir()
    name = "oracle_tests/test_generated.py"
    if escape == "symlink":
        (view / "oracle_tests").mkdir()
        (view / name).symlink_to(outside)
    elif escape == "traversal":
        name = "../outside.py"

    with pytest.raises(ValueError, match="Unsafe or missing generated test"):
        verify_submission({name: digest(outside.read_bytes())}, [str(view)])


def test_copy_views_replaces_partial_views_with_full_copies(tmp_path):
    source = tmp_path / "testbed"
    submission(source)
    (source / "package").mkdir()
    (source / "package/module.py").write_text("value = 1\n")
    views = [str(tmp_path / "buggy"), str(tmp_path / "golden")]
    # A half-created worktree must not survive into the copy.
    (tmp_path / "buggy").mkdir()
    (tmp_path / "buggy" / "stale.py").write_text("leftover\n")

    copy_views(str(source), views)

    for view in views:
        assert (tmp_path / view / "package/module.py").read_text() == "value = 1\n"
        assert not (tmp_path / view / "stale.py").exists()


def test_helper_runs_as_a_standalone_script(tmp_path):
    view = tmp_path / "buggy"
    expected = submission(view)
    settings = tmp_path / "verify.json"
    settings.write_text(json.dumps({"expected": expected, "views": [str(view)]}))
    output = tmp_path / "observed.json"

    subprocess.run(
        [
            sys.executable,
            str(HELPERS / "judge_workspace.py"),
            "verify",
            str(settings),
            str(output),
        ],
        check=True,
    )

    assert json.loads(output.read_text()) == {str(view): expected}
