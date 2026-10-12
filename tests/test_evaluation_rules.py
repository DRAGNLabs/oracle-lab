"""The rules that define the experiment, checked on the real evaluation code.

A recording sandbox stands in for Docker. The real `Repository` drives it, so
each test sees the exact command sequence: patches, rebuilds, uploads, and the
tests the runner was asked to run.
"""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock

import pytest
from fixtures import instance_record

import oracle_bench.evaluation as evaluation
from oracle_bench.config import RuntimeConfig, load_config
from oracle_bench.container.sandbox import CommandResult
from oracle_bench.io import digest, read_json, write_json
from oracle_bench.paths import RunPaths
from oracle_bench.results import read_paired_result

RUNNER = "/pytest_runner.py"


class RecordingSandbox:
    helpers = "/opt/oracle-bench/container-helpers"

    def __init__(self, outcomes: dict[str, str]):
        self.events = []
        self.outcomes = outcomes

    def run(self, argv, **kwargs):
        self.events.append(("run", argv))
        return CommandResult(exit_code=0, duration_seconds=1.0)

    def upload(self, source, destination, contents=False):
        self.events.append(("upload", Path(source).name, destination))

    def download(self, source, destination, contents=False, required=True):
        if source == "/tmp/oracle-results":
            tests = {test: {"outcome": outcome} for test, outcome in self.outcomes.items()}
            write_json(
                destination / "tests.json",
                {"status": "completed", "collected": len(tests), "tests": tests},
            )
        return True

    def commands(self) -> list[list[str]]:
        return [event[1] for event in self.events if event[0] == "run"]

    def ran_runner(self) -> bool:
        return any(argv[1].endswith(RUNNER) for argv in self.commands() if len(argv) > 1)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    config = load_config(Path(__file__).parents[1] / "configs/smoke.yaml")
    config.runtime = RuntimeConfig(
        image="example/image",
        source_roots=["app"],
        import_modules=["app"],
        existing_test_globs=["tests"],
        rebuild="make install",
    )
    paths = RunPaths.create(tmp_path)
    instance = instance_record(
        golden_patch="GOLDEN PATCH",
        reference_test_patch="REFERENCE PATCH",
        reference_test_ids=["tests/test_core.py::test_fixed"],
    )
    sandboxes = []
    outcomes = {"buggy": {}, "golden": {}}

    @contextmanager
    def fake_open_sandbox(client, image, config, profile, log):
        version = log.parent.name
        sandbox = RecordingSandbox(outcomes[version])
        sandboxes.append((version, profile.value, sandbox))
        yield sandbox

    monkeypatch.setattr(evaluation, "open_sandbox", fake_open_sandbox)
    return config, paths, instance, sandboxes, outcomes


def write_bundle(paths: RunPaths, files: dict[str, str]) -> None:
    manifest_files = {}
    for name, text in files.items():
        path = paths.submission / "files" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        manifest_files[name] = {"sha256": digest(text.encode()), "mode": 0o644}
    (paths.submission / "files").mkdir(parents=True, exist_ok=True)
    write_json(
        paths.submission / "manifest.json",
        {
            "files": manifest_files,
            "forbidden_changes": [],
            "compliant": True,
            "empty": not manifest_files,
            "sha256": digest(json.dumps(manifest_files, sort_keys=True).encode()),
        },
    )


def applied_patches(sandbox: RecordingSandbox) -> list[str]:
    """The local patch files uploaded and then applied, in order."""
    return [
        event[1]
        for event in sandbox.events
        if event[0] == "upload" and event[2] == "/tmp/oracle.patch"
    ]


def rebuild_positions(sandbox: RecordingSandbox) -> list[int]:
    return [i for i, argv in enumerate(sandbox.commands()) if argv[:2] == ["/bin/bash", "-c"]]


def test_golden_gets_the_repair_and_a_rebuild_after_it_and_buggy_gets_neither(setup):
    config, paths, instance, sandboxes, _ = setup
    write_bundle(paths, {"oracle_tests/test_new.py": "def test_a(): pass\n"})

    evaluation.run_version(Mock(), config, "image", instance, paths, "buggy")
    evaluation.run_version(Mock(), config, "image", instance, paths, "golden")
    (_, _, buggy), (_, _, golden) = sandboxes

    assert applied_patches(buggy) == []
    assert applied_patches(golden) == ["repair.patch"]
    assert (paths.evaluation / "golden/repair.patch").read_text() == "GOLDEN PATCH"
    commands = golden.commands()
    applied = commands.index(["git", "apply", "/tmp/oracle.patch"])
    assert max(rebuild_positions(golden)) > applied
    # Both versions start from the same reset checkout.
    for sandbox in (buggy, golden):
        assert sandbox.commands()[0] == ["git", "reset", "--hard", instance.base_commit]


def test_the_reference_run_keeps_existing_tests_and_runs_the_reference_ids(setup):
    config, paths, instance, sandboxes, _ = setup

    evaluation.run_version(Mock(), config, "image", instance, paths, "buggy", reference=True)
    ((_, profile, sandbox),) = sandboxes
    directory = paths.reference / "buggy"

    assert profile == "reference"
    assert read_json(directory / "workspace.json")["hide"] is False
    assert applied_patches(sandbox) == ["reference.patch"]
    assert read_json(directory / "runner.json")["targets"] == instance.reference_test_ids


def test_evaluation_hides_existing_tests_and_runs_only_the_generated_directory(setup):
    config, paths, instance, sandboxes, _ = setup
    write_bundle(paths, {"oracle_tests/test_new.py": "def test_a(): pass\n"})

    evaluation.run_version(Mock(), config, "image", instance, paths, "buggy")
    ((_, profile, sandbox),) = sandboxes
    directory = paths.evaluation / "buggy"

    assert profile == "evaluation"
    assert read_json(directory / "workspace.json")["hide"] is True
    assert ("upload", "files", "/testbed") in sandbox.events
    assert read_json(directory / "runner.json")["targets"] == ["oracle_tests"]


def test_an_empty_submission_is_no_tests_and_never_starts_pytest(setup):
    config, paths, instance, sandboxes, _ = setup
    write_bundle(paths, {})

    result = evaluation.run_version(Mock(), config, "image", instance, paths, "buggy")
    ((_, _, sandbox),) = sandboxes

    assert result.status == "no_tests"
    assert not sandbox.ran_runner()


def test_both_versions_receive_the_same_verified_bundle(setup, monkeypatch):
    config, paths, instance, sandboxes, outcomes = setup
    write_bundle(paths, {"oracle_tests/test_new.py": "def test_a(): pass\n"})
    write_json(paths.generation / "result.json", {"status": "completed"})
    outcomes["buggy"]["oracle_tests/test_new.py::test_a"] = "fail"
    outcomes["golden"]["oracle_tests/test_new.py::test_a"] = "pass"
    uploaded = []
    real_upload = RecordingSandbox.upload

    def upload(self, source, destination, contents=False):
        if destination == "/testbed":
            uploaded.append(
                {
                    p.relative_to(source).as_posix(): p.read_bytes()
                    for p in Path(source).rglob("*")
                    if p.is_file()
                }
            )
        real_upload(self, source, destination, contents)

    monkeypatch.setattr(RecordingSandbox, "upload", upload)

    result = evaluation.evaluate(Mock(), config, "image", instance, paths)

    assert [version for version, _, _ in sandboxes] == ["buggy", "golden"]
    assert len(uploaded) == 2 and uploaded[0] == uploaded[1]
    assert result.artifact_sha256 == read_json(paths.submission / "manifest.json")["sha256"]
    assert result.detected


def test_a_tampered_bundle_stops_evaluation_before_any_container(setup):
    config, paths, instance, sandboxes, _ = setup
    write_bundle(paths, {"oracle_tests/test_new.py": "def test_a(): pass\n"})
    (paths.submission / "files/oracle_tests/test_new.py").write_text("def test_a(): assert 0\n")

    with pytest.raises(ValueError, match="checksum mismatch"):
        evaluation.evaluate(Mock(), config, "image", instance, paths)
    assert sandboxes == []


def test_a_reference_pair_that_does_not_distinguish_the_versions_stops_the_run(setup):
    config, paths, instance, _, outcomes = setup
    outcomes["buggy"]["tests/test_core.py::test_fixed"] = "pass"
    outcomes["golden"]["tests/test_core.py::test_fixed"] = "pass"

    with pytest.raises(RuntimeError, match="did not reproduce a fail/pass test"):
        evaluation.check_reference(Mock(), config, "image", instance, paths)
    saved = read_paired_result(paths.reference / "results.json")
    assert not saved.has_fail_on_buggy_pass_on_golden
