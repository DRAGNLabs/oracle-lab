import subprocess
import sys

import pytest

import oracle_lab.container.helpers.prepare as prepare_module
from oracle_lab.container.helpers.prepare import remove_existing_tests, replace_git_history


def test_helper_annotations_work_on_old_repository_python():
    assert remove_existing_tests.__annotations__["patterns"] is list


def test_remove_existing_tests_expands_files_directories_and_nested_globs(tmp_path):
    production = tmp_path / "package" / "module.py"
    nested_test = tmp_path / "package" / "feature" / "tests" / "test_feature.py"
    top_level_test = tmp_path / "test_project.py"
    unrelated = tmp_path / "examples" / "test_example.py"
    runtime_helper = nested_test.parent / "runner.py"
    package_marker = nested_test.parent / "__init__.py"
    for path in [
        production,
        nested_test,
        top_level_test,
        unrelated,
        runtime_helper,
        package_marker,
    ]:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("content")

    remove_existing_tests(tmp_path, ["package/**/tests", "test_project.py"])

    assert production.exists()
    assert unrelated.exists()
    assert nested_test.parent.exists()
    assert runtime_helper.exists()
    assert package_marker.exists()
    assert not nested_test.exists()
    assert not top_level_test.exists()


def test_removing_tests_preserves_runtime_imports_from_test_package(tmp_path):
    package = tmp_path / "example"
    tests = package / "tests"
    tests.mkdir(parents=True)
    (package / "__init__.py").write_text("from .tests.runner import run\n")
    (tests / "__init__.py").write_text("")
    (tests / "runner.py").write_text("def run(): return 'available'\n")
    (tests / "test_behavior.py").write_text("def test_behavior(): assert False\n")
    (tests / "tests.py").write_text("def test_legacy_name(): assert False\n")

    remove_existing_tests(tmp_path, ["example/tests"])

    result = subprocess.run(
        [sys.executable, "-c", "import example; print(example.run())"],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "available"
    assert not (tests / "test_behavior.py").exists()
    assert not (tests / "tests.py").exists()


def test_replace_git_history_removes_original_commits_and_objects(tmp_path):
    def git(*args, check=True):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            text=True,
            capture_output=True,
            check=check,
        )

    git("init", "--quiet")
    git("config", "user.email", "test@localhost")
    git("config", "user.name", "Test")
    hidden_test = tmp_path / "test_hidden.py"
    hidden_test.write_text("def test_secret(): pass\n")
    git("add", "test_hidden.py")
    git("commit", "--quiet", "-m", "original history")
    original_commit = git("rev-parse", "HEAD").stdout.strip()
    hidden_test.unlink()

    replace_git_history(tmp_path)
    git("config", "user.email", "test@localhost")
    git("config", "user.name", "Test")
    (tmp_path / "module.py").write_text("VALUE = 1\n")
    git("add", "-A")
    git("commit", "--quiet", "-m", "prepared snapshot")

    assert git("rev-list", "--count", "HEAD").stdout.strip() == "1"
    assert git("cat-file", "-e", original_commit, check=False).returncode != 0
    assert git("show", "HEAD^:test_hidden.py", check=False).returncode != 0


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    """A repository with two commits, isolated from the user's Git configuration."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "repo"
    (root / "package" / "tests").mkdir(parents=True)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run([*git[:3], "init", "--quiet"], check=True)
    (root / "package" / "core.py").write_text("VALUE = 1\n")
    (root / "package" / "tests" / "test_core.py").write_text("def test_core(): pass\n")
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "first"], check=True)
    (root / "package" / "core.py").write_text("VALUE = 2\n")
    subprocess.run([*git, "commit", "--quiet", "-am", "second"], check=True)
    # Only root can hand files to the agent's user; record the request instead.
    chowns = []
    run = subprocess.run

    def run_except_chown(argv, **kwargs):
        if argv[0] == "chown":
            chowns.append(argv)
            return subprocess.CompletedProcess(argv, 0)
        return run(argv, **kwargs)

    monkeypatch.setattr(prepare_module.subprocess, "run", run_except_chown)
    monkeypatch.chdir(tmp_path)
    return root, chowns


def settings(root, *, hide: bool, sanitize_history: bool) -> dict:
    return {
        "workdir": str(root),
        "generated_dir": "oracle_tests",
        "hide": hide,
        "sanitize_history": sanitize_history,
        "existing_test_globs": ["package/tests"],
    }


def commits(root) -> list[str]:
    log = subprocess.run(
        ["git", "-C", str(root), "log", "--format=%s"], check=True, capture_output=True, text=True
    )
    return log.stdout.splitlines()


def test_the_generation_workspace_has_no_history_no_hidden_tests_and_an_empty_test_dir(checkout):
    root, chowns = checkout

    prepare_module.prepare(settings(root, hide=True, sanitize_history=True))

    assert commits(root) == ["Oracle Lab workspace"]
    assert not (root / "package" / "tests" / "test_core.py").exists()
    assert (root / "package" / "core.py").read_text() == "VALUE = 2\n"
    assert list((root / "oracle_tests").iterdir()) == []
    status = subprocess.run(
        ["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True
    )
    assert status.stdout == ""
    assert chowns == [["chown", "-R", "10001:10001", str(root)]]


def test_a_reference_workspace_keeps_existing_tests_and_history(checkout):
    root, _ = checkout

    prepare_module.prepare(settings(root, hide=False, sanitize_history=False))

    assert (root / "package" / "tests" / "test_core.py").exists()
    assert commits(root) == ["Oracle Lab workspace", "second", "first"]


def test_an_existing_generated_directory_is_refused(checkout):
    root, _ = checkout
    (root / "oracle_tests").mkdir()

    with pytest.raises(FileExistsError):
        prepare_module.prepare(settings(root, hide=True, sanitize_history=True))
