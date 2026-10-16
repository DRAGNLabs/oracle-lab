"""Raters browse the judge's buggy and golden views, rebuilt from a shallow Git fetch."""

import dataclasses
import subprocess

import pytest
from fixtures import classified_attempt, instance_record, judged_run

from oracle_lab.review.sources import SourceError, fetch, open_views
from oracle_lab.review.targets import classification_target, judge_target

GENERATED = "oracle_tests/test_generated.py"


def git(directory, *args):
    command = [
        "git",
        "-C",
        str(directory),
        "-c",
        "user.name=Test",
        "-c",
        "user.email=t@example.com",
    ]
    command += ["-c", "commit.gpgsign=false", *args]
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def upstream(tmp_path):
    """An upstream repository at the base commit, and the repair as a Git diff."""
    repo = tmp_path / "upstream"
    (repo / "package").mkdir(parents=True)
    (repo / "package/core.py").write_text("old\n")
    (repo / "package/gone.py").write_text("removed by the repair\n")
    (repo / "README.md").write_text("project\n")
    git(tmp_path, "init", "--quiet", "--initial-branch=main", str(repo))
    git(repo, "add", ".")
    git(repo, "commit", "--quiet", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")

    # Write the repair on a branch, take its diff, and leave main at the base commit.
    git(repo, "switch", "--quiet", "-c", "fixed")
    (repo / "package/core.py").write_text("new\n")
    (repo / "package/added.py").write_text("created by the repair\n")
    (repo / "package/gone.py").unlink()
    git(repo, "add", "-A")
    git(repo, "commit", "--quiet", "-m", "fix")
    repair = git(repo, "diff", base, "HEAD") + "\n"
    git(repo, "switch", "--quiet", "main")
    return repo, base, repair


def remote(upstream):
    return "file://" + str(upstream[0])


def judged(tmp_path, upstream):
    _, base, repair = upstream
    instance = instance_record(
        base_commit=base,
        golden_patch=repair,
        reference_test_patch=(
            "diff --git a/tests/test_core.py b/tests/test_core.py\n"
            "--- a/tests/test_core.py\n+++ b/tests/test_core.py\n@@ -0,0 +1 @@\n+x\n"
        ),
    )
    target = judge_target(judged_run(tmp_path / "run"))
    return dataclasses.replace(target, instance=instance)


def test_the_buggy_view_is_the_base_commit_plus_the_submission(tmp_path, upstream):
    views = open_views(judged(tmp_path, upstream), remote=remote(upstream), cache=tmp_path / "c")

    buggy = views["buggy"]
    assert buggy.read("package/core.py") == b"old\n"
    assert buggy.read(GENERATED) == b"def test_generated(): pass\n"
    assert buggy.tree() == {
        "README.md": (),
        GENERATED: ("submission",),
        "package/core.py": ("repair",),
        "package/gone.py": ("repair",),
    }


def test_the_golden_view_also_has_the_repair(tmp_path, upstream):
    views = open_views(judged(tmp_path, upstream), remote=remote(upstream), cache=tmp_path / "c")

    golden = views["golden"]
    assert golden.read("package/core.py") == b"new\n"
    assert golden.read("package/added.py") == b"created by the repair\n"
    assert golden.read(GENERATED) == b"def test_generated(): pass\n"
    assert "package/gone.py" not in golden.tree()
    assert golden.tree()["package/added.py"] == ("repair",)


def test_a_classification_has_only_the_buggy_base_commit(tmp_path, upstream):
    _, base, _ = upstream
    target = classification_target(classified_attempt(tmp_path / "attempt"))
    target = dataclasses.replace(target, instance=instance_record(base_commit=base))

    views = open_views(target, remote=remote(upstream), cache=tmp_path / "c")

    assert list(views) == ["buggy"]
    assert set(views["buggy"].tree()) == {"README.md", "package/core.py", "package/gone.py"}


@pytest.mark.parametrize("path", ["../upstream/README.md", "package", "/etc/passwd", "missing.py"])
def test_only_files_in_the_view_can_be_read(tmp_path, upstream, path):
    views = open_views(judged(tmp_path, upstream), remote=remote(upstream), cache=tmp_path / "c")

    with pytest.raises(SourceError, match="No such file"):
        views["buggy"].read(path)


def test_a_fetched_commit_is_reused_without_the_remote(tmp_path, upstream):
    _, base, _ = upstream
    first = fetch("owner/project", base, remote=remote(upstream), cache=tmp_path / "c")

    again = fetch("owner/project", base, remote="file:///nonexistent", cache=tmp_path / "c")

    assert again == first == tmp_path / "c/owner/project"


def test_a_commit_the_remote_does_not_have_is_refused(tmp_path, upstream):
    with pytest.raises(SourceError, match="fetch failed"):
        fetch("owner/project", "f" * 40, remote=remote(upstream), cache=tmp_path / "c")


@pytest.mark.parametrize("repo", ["project", "owner/../project", "../owner", "a/b/c"])
def test_a_repository_name_must_be_owner_and_name(tmp_path, repo):
    with pytest.raises(SourceError, match="owner/name"):
        fetch(repo, "a" * 40, remote="file:///nonexistent", cache=tmp_path / "c")
