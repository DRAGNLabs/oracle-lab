"""SDK boundary tests: no Docker daemon, network, or model credentials."""

import io
import json
import signal
import subprocess
import sys
import tarfile
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from docker.errors import APIError, ImageNotFound, NotFound
from fixtures import DOCKERFILES, HELPERS, run_config

from oracle_lab.config import HARNESS_VERSIONS
from oracle_lab.container.files import RedactedOutput, download, upload
from oracle_lab.container.images import (
    build_image,
    image_tag,
    inspect_harness_versions,
    prepare_contexts,
    runtime_arguments,
)
from oracle_lab.container.lifecycle import setup_deadline
from oracle_lab.container.sandbox import Profile, Sandbox, docker_client, open_sandbox
from oracle_lab.repository import Repository


@pytest.fixture
def config():
    config = run_config("requests", image="example/image:latest")
    return config


def test_runtime_image_uses_source_specific_test_toolchain(config):
    config.runtime.pytest_version = "6.2.5"
    source = Mock(id="sha256:source")
    harnesses = Mock(id="sha256:harnesses")

    arguments = runtime_arguments(config, source, harnesses)

    assert arguments["PYTEST_VERSION"] == "6.2.5"


def test_runtime_image_checks_the_source_paths(config):
    config.runtime.workdir = "/project"
    config.runtime.python = "/usr/local/bin/python"

    arguments = runtime_arguments(config, Mock(id="sha256:source"), Mock(id="sha256:harnesses"))

    assert arguments["PROJECT_WORKDIR"] == "/project"
    assert arguments["PROJECT_PYTHON"] == "/usr/local/bin/python"


def archive_bytes(entries):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            info.type = kind
            if kind == tarfile.REGTYPE:
                info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_download_rejects_nonregular_files(tmp_path, kind):
    container = Mock()
    container.get_archive.return_value = ([archive_bytes([("escape", b"", kind)])], {})
    with pytest.raises(ValueError, match="Unsafe"):
        download(container, "/tmp/result", tmp_path / "result")
    assert not (tmp_path / "result").exists()


@pytest.mark.parametrize("name", ["../escape", "/tmp/escape", "root/../../escape"])
def test_download_rejects_traversal(tmp_path, name):
    container = Mock()
    container.get_archive.return_value = ([archive_bytes([(name, b"data", tarfile.REGTYPE)])], {})
    with pytest.raises(ValueError, match="Unsafe"):
        download(container, "/tmp/result", tmp_path / "result", contents=True)


def test_transfers_preserve_directory_contents(tmp_path):
    source = tmp_path / "files"
    source.mkdir()
    (source / "test_probe.py").write_text("probe")
    container = Mock()
    captured = []
    container.put_archive.side_effect = lambda parent, stream: captured.append(
        (parent, stream.read())
    )
    upload(container, source, "/repo", contents=True)
    assert captured[0][0] == "/repo"
    with tarfile.open(fileobj=io.BytesIO(captured[0][1])) as archive:
        assert archive.getnames() == ["test_probe.py"]
    data = archive_bytes(
        [("results", b"", tarfile.DIRTYPE), ("results/tests.json", b"{}", tarfile.REGTYPE)]
    )
    container.get_archive.return_value = ([data[:17], data[17:]], {"name": "results"})
    download(container, "/tmp/results", tmp_path / "out", contents=True)
    assert (tmp_path / "out/tests.json").read_text() == "{}"


@pytest.mark.parametrize("split", range(1, 18))
def test_redaction_crosses_every_chunk_boundary(tmp_path, split):
    secret = "credential-fixture"
    path = tmp_path / "log"
    with RedactedOutput(path, (secret,)) as output:
        output.write(b"prefix " + secret[:split].encode())
        output.write(secret[split:].encode() + b" suffix")
    assert path.read_text() == "prefix [REDACTED] suffix"


@pytest.mark.parametrize("profile", list(Profile))
def test_security_profiles_and_cleanup(config, tmp_path, profile):
    client = Mock()
    container = client.containers.create.return_value
    with pytest.raises(KeyboardInterrupt):
        with open_sandbox(client, "image", config, profile, tmp_path / "log"):
            raise KeyboardInterrupt
    options = client.containers.create.call_args.kwargs
    assert options["network_mode"] == "bridge"
    assert options["security_opt"] == ["no-new-privileges"]
    assert "volumes" not in options and "mounts" not in options
    container.remove.assert_called_once_with(force=True)


@pytest.mark.parametrize("profile", list(Profile))
def test_every_profile_imports_the_repository_source_path(config, tmp_path, profile):
    """Generation must import the same code evaluation runs, e.g. pytest's own src/."""
    config.runtime.pythonpath = "/testbed/src"
    client = Mock()
    with open_sandbox(client, "image", config, profile, tmp_path / "log"):
        pass
    assert client.containers.create.call_args.kwargs["environment"] == {
        "PYTHONPATH": "/testbed/src"
    }


def test_failed_start_is_cleaned_up_without_masking_failure(config, tmp_path):
    client = Mock()
    container = client.containers.create.return_value
    container.start.side_effect = APIError("start failed")
    container.remove.side_effect = APIError("cleanup failed")
    with pytest.raises(APIError, match="start failed") as error:
        with open_sandbox(client, "image", config, Profile.EVALUATION, tmp_path / "log"):
            pytest.fail("start failed")
    assert "cleanup also failed" in error.value.__notes__[0]


def test_a_container_that_cannot_be_removed_after_successful_work_is_an_error(config, tmp_path):
    client = Mock()
    container = client.containers.create.return_value
    container.remove.side_effect = APIError("cleanup failed")

    with pytest.raises(RuntimeError, match="Could not remove benchmark container"):
        with open_sandbox(client, "image", config, Profile.EVALUATION, tmp_path / "log"):
            pass


def test_docker_client_checks_daemon_and_closes_connection(monkeypatch):
    client = Mock()
    client.info.return_value = {"OSType": "linux"}
    from_env = Mock(return_value=client)
    monkeypatch.setattr("oracle_lab.container.sandbox.docker.from_env", from_env)

    with docker_client() as opened:
        assert opened is client

    from_env.assert_called_once_with(timeout=60)
    client.info.assert_called_once_with()
    client.close.assert_called_once_with()


def test_optional_output_only_suppresses_missing_files(config, tmp_path):
    container = Mock()
    sandbox = Sandbox(container, config.runtime, tmp_path / "log")
    container.get_archive.side_effect = NotFound("missing")
    assert sandbox.download("/missing", tmp_path / "file", required=False) is False
    container.get_archive.side_effect = APIError("transport failed")
    with pytest.raises(APIError):
        sandbox.download("/missing", tmp_path / "file", required=False)


@pytest.mark.parametrize(
    "visibility,reference,hide",
    [
        ("keep", False, False),
        ("hide_all", False, True),
        ("hide_all", True, False),
    ],
)
def test_workspace_preserves_preparation_order(config, tmp_path, visibility, reference, hide):
    config.task.existing_tests = visibility
    sandbox = Mock(helpers="/helpers")
    workspace = Repository(sandbox, config)
    workspace.prepare("a" * 40, tmp_path, reference=reference)
    calls = sandbox.run.call_args_list
    assert calls[0].args[0][:3] == ["git", "reset", "--hard"]
    assert calls[1].args[0][1] == "/helpers/prepare.py"
    settings = json.loads((tmp_path / "workspace.json").read_text())
    assert settings["hide"] == hide
    assert settings["sanitize_history"] is False


def test_generation_workspace_requests_sanitized_history(config, tmp_path):
    sandbox = Mock(helpers="/helpers")
    workspace = Repository(sandbox, config)

    workspace.prepare("a" * 40, tmp_path, sanitize_history=True)

    settings = json.loads((tmp_path / "workspace.json").read_text())
    assert settings["sanitize_history"] is True


def test_build_cache_changes_with_helpers_and_platform(tmp_path):
    context = tmp_path / "context"
    context.mkdir()
    (context / "runtime.Dockerfile").write_text("FROM example\nCOPY helper.py /helper.py\n")
    helper = context / "helper.py"
    helper.write_text("one")
    client = Mock()
    client.images.get.return_value = SimpleNamespace(id="image")
    with (tmp_path / "log").open("w") as log:
        build_image(client, context, "runtime.Dockerfile", {}, "linux/amd64", log)
        helper.write_text("two")
        build_image(client, context, "runtime.Dockerfile", {}, "linux/amd64", log)
        build_image(client, context, "runtime.Dockerfile", {}, "linux/arm64", log)
    tags = [call.args[0] for call in client.images.get.call_args_list]
    assert len(set(tags)) == 3


def test_shared_harness_context_contains_locked_complete_toolchain(tmp_path):
    harnesses, runtime = prepare_contexts(tmp_path / "build")
    package = json.loads((harnesses / "package.json").read_text())

    assert package["dependencies"] == {
        "@anthropic-ai/claude-code": HARNESS_VERSIONS["claude"],
        "@openai/codex": HARNESS_VERSIONS["codex"],
        "opencode-ai": HARNESS_VERSIONS["opencode"],
    }
    assert (harnesses / "package-lock.json").is_file()
    assert (harnesses / "harnesses.Dockerfile").is_file()
    assert (runtime / "runtime.Dockerfile").is_file()


def test_harness_identity_changes_with_lock_recipe_platform_and_pin(tmp_path):
    harnesses, _ = prepare_contexts(tmp_path / "build")

    def tag(platform="linux/amd64"):
        return image_tag(
            harnesses,
            "harnesses.Dockerfile",
            {"NODE_IMAGE": "sha256:node"},
            platform,
        )[0]

    original = tag()
    lockfile = harnesses / "package-lock.json"
    original_lock = lockfile.read_text()
    lockfile.write_text("changed lock")
    assert tag() != original
    lockfile.write_text(original_lock)
    package = harnesses / "package.json"
    package.write_text(package.read_text().replace(HARNESS_VERSIONS["codex"], "0.0.1"))
    assert tag() != original
    package.write_text((DOCKERFILES / "package.json").read_text())
    (harnesses / "harnesses.Dockerfile").write_text("changed recipe")
    assert tag() != original
    assert tag("linux/arm64") != tag("linux/amd64")


def test_harness_versions_are_inspected_offline_as_oracle_user():
    client = Mock()
    client.containers.run.side_effect = [
        b"codex-cli 0.153.4\n",
        b"2.1.280 (Claude Code)\n",
        b"1.18.32\n",
    ]

    observed = inspect_harness_versions(client, SimpleNamespace(id="runtime-image"))

    assert observed == {
        "codex": "codex-cli 0.153.4",
        "claude": "2.1.280 (Claude Code)",
        "opencode": "1.18.32",
    }
    for call in client.containers.run.call_args_list:
        assert call.kwargs == {
            "user": "10001:10001",
            "environment": {"HOME": "/home/oracle"},
            "network_disabled": True,
            "remove": True,
        }


def test_build_failure_leaves_inputs_and_raw_log(tmp_path):
    context = tmp_path / "context"
    context.mkdir()
    (context / "runtime.Dockerfile").write_text("FROM example\n")
    client = Mock()
    client.images.get.side_effect = ImageNotFound("missing")
    client.api.build.return_value = iter([{"stream": "step one"}, {"error": "install failed"}])
    with (tmp_path / "log").open("w") as log, pytest.raises(RuntimeError, match="build failed"):
        build_image(client, context, "runtime.Dockerfile", {}, "linux/amd64", log)
    assert (tmp_path / "runtime.inputs.json").exists()
    assert "install failed" in (tmp_path / "log").read_text()


@pytest.mark.parametrize(
    "program,timeout,timed_out,exit_code",
    [
        ("import sys; print(sys.stdin.read()); print('error', file=sys.stderr)", 2, False, 0),
        ("import sys; sys.exit(137)", 2, False, 137),
        ("import time; print('partial', flush=True); time.sleep(5)", 0.1, True, 124),
    ],
)
def test_process_supervisor_preserves_evidence(tmp_path, program, timeout, timed_out, exit_code):
    prompt = tmp_path / "prompt"
    prompt.write_text("prompt text")
    status = tmp_path / "status.json"
    result = subprocess.run(
        [
            sys.executable,
            str(HELPERS / "execute.py"),
            str(timeout),
            str(prompt),
            str(status),
            sys.executable,
            "-c",
            program,
        ],
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0
    outcome = json.loads(status.read_text())
    assert outcome["timed_out"] == timed_out
    assert outcome["exit_code"] == exit_code
    if timed_out:
        assert b"partial" in result.stdout
    elif exit_code == 0:
        assert b"prompt text" in result.stdout and b"error" in result.stderr


def test_sdk_execution_streams_redacted_output_and_reads_status(config, tmp_path):
    container = Mock(id="container-id")
    api = container.client.api
    api.exec_create.return_value = {"Id": "exec-id"}
    api.exec_start.return_value = iter([(b"credential-", None), (b"fixture", b"stderr")])
    api.exec_inspect.return_value = {"ExitCode": 0}
    status = json.dumps({"exit_code": 124, "timed_out": True, "duration_seconds": 1}).encode()
    container.get_archive.return_value = (
        [archive_bytes([("status.json", status, tarfile.REGTYPE)])],
        {},
    )
    sandbox = Sandbox(container, config.runtime, tmp_path / "log")
    result = sandbox.run(
        ["example", "argument"],
        user="10001:10001",
        environment={"SELECTED": "credential-fixture"},
        stdout=tmp_path / "stdout",
        stderr=tmp_path / "stderr",
        check=False,
        secrets=("credential-fixture",),
    )
    assert result.timed_out and result.exit_code == 124
    assert (tmp_path / "stdout").read_text() == "[REDACTED]"
    assert (tmp_path / "stderr").read_text() == "stderr"
    assert "credential-fixture" not in str(api.exec_create.call_args.args)
    assert api.exec_create.call_args.kwargs["user"] == "10001:10001"


def test_sdk_errors_do_not_expose_environment(config, tmp_path):
    container = Mock(id="container-id")
    container.client.api.exec_create.side_effect = APIError("request included credential-fixture")
    sandbox = Sandbox(container, config.runtime, tmp_path / "log")
    with pytest.raises(RuntimeError) as error:
        sandbox.run(["example"], environment={"SELECTED": "credential-fixture"})
    assert "credential-fixture" not in str(error.value)
    assert error.value.__suppress_context__


def test_setup_deadline_interrupts_silent_work_and_restores_handler():
    previous = signal.getsignal(signal.SIGALRM)
    with pytest.raises(TimeoutError, match="setup exceeded"):
        with setup_deadline(0.01):
            time.sleep(1)
    assert signal.getsignal(signal.SIGALRM) == previous
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0
