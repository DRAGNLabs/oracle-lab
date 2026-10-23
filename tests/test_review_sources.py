"""Raters browse the judge's buggy and golden views, rebuilt from a shallow Git fetch."""

import pytest
from fixtures import classified_attempt, instance_record, judged_run, upstream_repository

from oracle_lab.review.sources import SourceError, fetch, open_views
from oracle_lab.review.targets import ClassificationTarget, JudgmentTarget

GENERATED = "oracle_tests/test_generated.py"
REFERENCE_TESTS = (
    "diff --git a/tests/test_core.py b/tests/test_core.py\n"
    "--- a/tests/test_core.py\n+++ b/tests/test_core.py\n@@ -0,0 +1 @@\n+x\n"
)


@pytest.fixture
def upstream(tmp_path):
    return upstream_repository(tmp_path / "upstream")


def judged(tmp_path, upstream):
    instance = instance_record(
        base_commit=upstream.base_commit,
        golden_patch=upstream.repair,
        reference_test_patch=REFERENCE_TESTS,
    )
    return JudgmentTarget.load(judged_run(tmp_path / "run", instance=instance).root)


def test_the_buggy_view_is_the_base_commit_plus_the_submission(tmp_path, upstream):
    views = open_views(judged(tmp_path, upstream), remote=upstream.url, cache=tmp_path / "c")

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
    views = open_views(judged(tmp_path, upstream), remote=upstream.url, cache=tmp_path / "c")

    golden = views["golden"]
    assert golden.read("package/core.py") == b"new\n"
    assert golden.read("package/added.py") == b"created by the repair\n"
    assert golden.read(GENERATED) == b"def test_generated(): pass\n"
    assert "package/gone.py" not in golden.tree()
    assert golden.tree()["package/added.py"] == ("repair",)


def test_a_classification_has_only_the_buggy_base_commit(tmp_path, upstream):
    instance = instance_record(base_commit=upstream.base_commit)
    target = ClassificationTarget.load(
        classified_attempt(tmp_path / "attempt", instance=instance).root
    )

    views = open_views(target, remote=upstream.url, cache=tmp_path / "c")

    assert list(views) == ["buggy"]
    assert set(views["buggy"].tree()) == {"README.md", "package/core.py", "package/gone.py"}


@pytest.mark.parametrize("path", ["../upstream/README.md", "package", "/etc/passwd", "missing.py"])
def test_only_files_in_the_view_can_be_read(tmp_path, upstream, path):
    views = open_views(judged(tmp_path, upstream), remote=upstream.url, cache=tmp_path / "c")

    with pytest.raises(LookupError, match="No such file"):
        views["buggy"].read(path)


def test_a_fetched_commit_is_reused_without_the_remote(tmp_path, upstream):
    first = fetch("owner/project", upstream.base_commit, remote=upstream.url, cache=tmp_path / "c")

    again = fetch(
        "owner/project", upstream.base_commit, remote="file:///nonexistent", cache=tmp_path / "c"
    )

    assert again == first == tmp_path / "c/owner/project"


def test_a_commit_the_remote_does_not_have_is_refused(tmp_path, upstream):
    with pytest.raises(SourceError, match="fetch failed"):
        fetch("owner/project", "f" * 40, remote=upstream.url, cache=tmp_path / "c")


@pytest.mark.parametrize(
    "repo", ["project", "owner/../project", "../owner", "./owner", "a/b/c", "owner/name\n"]
)
def test_a_repository_name_must_be_owner_and_name(tmp_path, repo):
    with pytest.raises(SourceError, match="owner/name"):
        fetch(repo, "a" * 40, remote="file:///nonexistent", cache=tmp_path / "c")
