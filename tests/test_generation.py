import subprocess
import sys
from contextlib import contextmanager
from unittest.mock import Mock

import pytest
from fixtures import HELPERS, run_config, submission_manifest, write_submission

from oracle_lab.generation import capture, changed_files, generate_submission, verify_bundle
from oracle_lab.io import digest, read_json, write_json
from oracle_lab.paths import RunPaths


def entry(data, kind="file"):
    return {"sha256": digest(data), "kind": kind, "size": len(data), "mode": 0o644}


def test_only_new_regular_files_under_generated_directory_are_allowed():
    before = {"app.py": entry(b"old"), "tests/test_old.py": entry(b"existing")}
    after = {
        "app.py": entry(b"changed"),
        "oracle_tests/test_new.py": entry(b"test"),
        "oracle_tests/fixture.json": entry(b"{}"),
        "oracle_tests/escape": entry(b"/etc/passwd", "symlink"),
    }
    allowed, forbidden = changed_files(before, after, "oracle_tests")
    assert allowed == ["oracle_tests/fixture.json", "oracle_tests/test_new.py"]
    assert forbidden == ["app.py", "oracle_tests/escape", "tests/test_old.py"]


def test_a_name_no_repository_path_may_have_is_a_forbidden_change():
    before = {"a.py": {"kind": "file", "sha256": "1"}}
    after = {**before, "-v": {"kind": "file", "sha256": "2"}}

    assert changed_files(before, after, "oracle_tests") == ([], ["-v"])


def test_bundle_tampering_is_rejected(tmp_path):
    paths = RunPaths.create(tmp_path)
    written = write_submission(paths, {"oracle_tests/test_new.py": "def test_a(): pass\n"})
    assert verify_bundle(RunPaths(tmp_path)) == written
    (paths.submission / "files/oracle_tests/test_new.py").write_text("modified")
    with pytest.raises(ValueError, match="checksum"):
        verify_bundle(RunPaths(tmp_path))


def test_unmanifested_files_are_not_transferred_to_evaluation(tmp_path):
    paths = RunPaths.create(tmp_path)
    write_submission(paths, {})
    (paths.submission / "files/extra.py").write_text("unexpected code")
    with pytest.raises(ValueError, match="unmanifested"):
        verify_bundle(RunPaths(tmp_path))


def test_empty_bundle_has_a_real_files_directory(tmp_path):
    paths = RunPaths.create(tmp_path)
    write_submission(paths, {})

    assert verify_bundle(paths).empty
    assert (paths.submission / "files").is_dir()


def test_transient_generation_uses_clean_container_and_archives_attempt(tmp_path, monkeypatch):
    paths = RunPaths.create(tmp_path)
    sandboxes = [Mock(name="first_sandbox"), Mock(name="second_sandbox")]

    @contextmanager
    def sandbox_context(*args, **kwargs):
        yield sandboxes.pop(0)

    workspaces = []

    def repository(sandbox, config):
        workspace = Mock()
        workspace.baseline.return_value = "baseline"
        workspaces.append((sandbox, workspace))
        return workspace

    attempts = 0

    def generate(_sandbox, _config, run_paths):
        nonlocal attempts
        attempts += 1
        (run_paths.generation / "trace.jsonl").write_text(f"attempt {attempts}\n")
        return {
            "status": "failed" if attempts == 1 else "completed",
            "errors": [{"message": "stream disconnected"}] if attempts == 1 else [],
        }

    capture = Mock(return_value={"empty": False})
    delay = Mock()
    monkeypatch.setattr("oracle_lab.generation.open_sandbox", sandbox_context)
    monkeypatch.setattr("oracle_lab.generation.Repository", repository)
    monkeypatch.setattr("oracle_lab.generation.snapshot", Mock(return_value={}))
    monkeypatch.setattr("oracle_lab.generation.generate", generate)
    monkeypatch.setattr("oracle_lab.generation.capture", capture)
    monkeypatch.setattr("oracle_lab.harnesses.retry.sleep", delay)

    result = generate_submission(Mock(), Mock(), "runtime-image", Mock(base_commit="a" * 40), paths)

    assert result == {"empty": False}
    assert attempts == 2
    assert len(workspaces) == 2
    assert capture.call_count == 1
    delay.assert_called_once_with(30)
    archived = list(paths.generation_history.iterdir())
    assert len(archived) == 1
    assert (archived[0] / "trace.jsonl").read_text() == "attempt 1\n"
    assert (paths.generation / "trace.jsonl").read_text() == "attempt 2\n"


def test_snapshot_captures_untracked_files_and_symlinks_without_following_them(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "new.py").write_text("new")
    (root / "outside").symlink_to(tmp_path, target_is_directory=True)
    (root / "__pycache__").mkdir()
    (root / "__pycache__" / "ignored.pyc").write_bytes(b"cache")
    output = tmp_path / "snapshot.json"
    subprocess.run(
        [
            sys.executable,
            str(HELPERS / "workspace.py"),
            "snapshot",
            str(root),
            str(output),
        ],
        check=True,
    )
    result = read_json(output)
    assert set(result) == {"new.py", "outside"}
    assert result["outside"]["kind"] == "symlink"


def test_capture_diff_includes_agent_commits_and_untracked_files(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path).decode().strip()

    git("init", "-q")
    git("config", "user.name", "test")
    git("config", "user.email", "test@localhost")
    (tmp_path / "app.py").write_text("old\n")
    git("add", ".")
    git("commit", "-qm", "base")
    baseline = git("rev-parse", "HEAD")
    (tmp_path / "app.py").write_text("new\n")
    git("commit", "-qam", "agent edit")
    (tmp_path / "test_new.py").write_text("def test_new(): pass\n")
    output = tmp_path.parent / (tmp_path.name + ".diff")
    subprocess.run(
        [
            sys.executable,
            str(HELPERS / "workspace.py"),
            "diff",
            str(tmp_path),
            str(output),
            baseline,
        ],
        check=True,
    )
    diff = output.read_text()
    assert "-old" in diff and "+new" in diff and "+def test_new(): pass" in diff


class CaptureSandbox:
    """Serves the final workspace snapshot, the diff, and each file's bytes."""

    helpers = "/opt/oracle-lab/container-helpers"

    def __init__(self, runtime, after: dict, contents: dict[str, bytes], diff: str | None = "d"):
        self.runtime = runtime
        self.after = after
        self.contents = contents
        self.diff = diff
        self.downloads = []

    def run(self, argv, **kwargs):
        pass

    def download(self, source, destination, required=True):
        self.downloads.append(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source == "/tmp/oracle-snapshot.json":
            write_json(destination, self.after)
        elif source == "/tmp/oracle-workspace.diff":
            if self.diff is None:
                return False
            destination.write_text(self.diff)
        else:
            destination.write_bytes(self.contents[source.removeprefix("/testbed/")])
        return True


def capture_config():
    config = run_config("app", image="example/image")
    return config


def run_capture(tmp_path, after, contents, before=None, diff="d"):
    config = capture_config()
    paths = RunPaths.create(tmp_path)
    sandbox = CaptureSandbox(config.runtime, after, contents, diff)
    manifest = capture(sandbox, config, paths, before or {}, "baseline")
    return manifest, sandbox, paths


def test_capture_downloads_new_tests_and_writes_a_manifest_of_their_hashes(tmp_path):
    test = b"def test_new(): assert True\n"
    after = {"oracle_tests/test_new.py": entry(test)}

    manifest, sandbox, paths = run_capture(tmp_path, after, {"oracle_tests/test_new.py": test})

    assert "/testbed/oracle_tests/test_new.py" in sandbox.downloads
    assert (paths.submission / "files/oracle_tests/test_new.py").read_bytes() == test
    assert manifest.model_dump() == submission_manifest({"oracle_tests/test_new.py": test.decode()})
    assert read_json(paths.submission / "manifest.json") == manifest.model_dump()
    assert (paths.generation / "workspace.diff").read_text() == "d"
    # The bundle passes the same check evaluation runs.
    assert verify_bundle(paths) == manifest


def test_capture_records_forbidden_edits_without_downloading_them(tmp_path):
    before = {"app/core.py": entry(b"old")}
    after = {"app/core.py": entry(b"new"), "oracle_tests/test_new.py": entry(b"t")}

    manifest, sandbox, _ = run_capture(
        tmp_path, after, {"oracle_tests/test_new.py": b"t"}, before=before
    )

    assert manifest.forbidden_changes == ["app/core.py"]
    assert not manifest.compliant
    assert "/testbed/app/core.py" not in sandbox.downloads


def test_a_file_that_changes_during_capture_is_rejected(tmp_path):
    after = {"oracle_tests/test_new.py": entry(b"as snapshotted")}

    with pytest.raises(RuntimeError, match="changed during capture: oracle_tests/test_new.py"):
        run_capture(tmp_path, after, {"oracle_tests/test_new.py": b"rewritten since"})


def test_no_new_tests_is_an_empty_compliant_submission(tmp_path):
    manifest, _, paths = run_capture(tmp_path, {}, {}, diff=None)

    assert manifest.empty and manifest.compliant
    assert list((paths.submission / "files").iterdir()) == []
    assert not (paths.generation / "workspace.diff").exists()
