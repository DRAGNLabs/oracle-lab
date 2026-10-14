from unittest.mock import Mock

import pytest
from fixtures import RUN_CONFIG, detecting_result, instance_record, run_config, write_submission

from oracle_lab.config import JudgeConfig
from oracle_lab.container.sandbox import CommandResult
from oracle_lab.generation import verify_bundle
from oracle_lab.io import read_json, write_json
from oracle_lab.judge.workspace import JUDGE_ROOT, build_judge_workspace
from oracle_lab.paths import RunPaths
from oracle_lab.results import read_evaluation_result

SAMPLE = RUN_CONFIG


def prepared_run(tmp_path):
    paths = RunPaths.create(tmp_path)
    config = run_config("package", image="example/runtime")
    config.judge = JudgeConfig(
        rubric="rubric.md",
        instructions="instructions.md",
        harness="claude",
        model="judge-model",
        limit={"kind": "wall_seconds", "value": 300},
    )
    paths.judge.rubric.write_text("# Rubric\n")
    (paths.ground_truth / "issue.md").write_text("# issue\n\nIncorrect result.\n")
    fix = "diff --git a/package/core.py b/package/core.py\n--- a/package/core.py\n+++ b/package/core.py\n@@ -1 +1 @@\n-old\n+new\n"
    reference_patch = "diff --git a/tests/test_core.py b/tests/test_core.py\n"
    (paths.ground_truth / "fix.patch").write_text(fix)
    (paths.generation / "session.log").write_text("agent session\n")
    (paths.generation / "workspace.diff").write_text("generated diff\n")
    manifest = write_submission(
        paths, {"oracle_tests/test_generated.py": "def test_generated():\n    assert True\n"}
    )
    files = {name: entry.model_dump() for name, entry in manifest.files.items()}
    write_json(
        paths.results,
        detecting_result(
            tests=[
                {
                    "test_id": "oracle_tests/test_generated.py::test_generated",
                    "buggy": "fail",
                    "golden": "pass",
                    "cell": "fail_on_buggy_pass_on_golden",
                }
            ]
        ),
    )
    for version in ("buggy", "golden"):
        directory = paths.evaluation / version
        directory.mkdir(parents=True)
        write_json(directory / "tests.json", {"status": "completed", "tests": []})
        (directory / "output.log").write_text(version + " output\n")
    write_json(
        paths.build / "images.json",
        {
            "runtime": {
                "id": "sha256:" + "1" * 64,
                "digests": ["example/runtime@sha256:" + "2" * 64],
            }
        },
    )
    instance = instance_record(
        golden_patch=fix,
        reference_test_patch=reference_patch,
        original_record={
            "FAIL_TO_PASS": '["tests/test_core.py::test_fixed"]',
            "PASS_TO_PASS": [],
        },
    )
    return config, paths, instance, files, read_evaluation_result(paths.results)


def fake_sandbox(files, *, worktrees=True):
    sandbox = Mock(helpers="/opt/oracle-lab/container-helpers")
    sandbox.uploaded = {}
    expected = {name: info["sha256"] for name, info in files.items()}

    def run(command, **kwargs):
        if "worktree" in command and not worktrees:
            return CommandResult(1, 0)
        return CommandResult(0, 0)

    def download(source, destination, **kwargs):
        assert source == "/tmp/oracle-judge-file-hashes.json"
        write_json(
            destination,
            {
                JUDGE_ROOT + "/buggy": expected,
                JUDGE_ROOT + "/golden": expected,
            },
        )

    def upload(source, destination, **kwargs):
        if source.is_file():
            sandbox.uploaded[destination] = source.read_bytes()

    sandbox.run.side_effect = run
    sandbox.download.side_effect = download
    sandbox.upload.side_effect = upload
    return sandbox


def test_workspace_exposes_complete_hashed_bundle_and_identical_tests(tmp_path):
    config, paths, instance, files, evaluation = prepared_run(tmp_path)
    sandbox = fake_sandbox(files)

    spec = build_judge_workspace(
        sandbox, config, paths, "example/runtime", instance, evaluation, verify_bundle(paths)
    )

    assert spec.workspace_method == "git_worktree"
    assert spec.prepared_image.image_id == "sha256:" + "1" * 64
    destinations = {artifact.destination for artifact in spec.artifacts}
    assert JUDGE_ROOT + "/instance/issue.md" in destinations
    assert JUDGE_ROOT + "/instructions.md" in destinations
    assert JUDGE_ROOT + "/evidence/agent-session.log" in destinations
    assert JUDGE_ROOT + "/evidence/paired-results.json" in destinations
    assert JUDGE_ROOT + "/buggy/oracle_tests/test_generated.py" in destinations
    assert JUDGE_ROOT + "/golden/oracle_tests/test_generated.py" in destinations
    assert read_json(paths.judge.workspace_spec) == spec.model_dump(mode="json")
    assert read_json(paths.judge.image) == spec.prepared_image.model_dump(mode="json")
    assert read_json(paths.judge.bundle_manifest)["artifacts"] == [
        artifact.model_dump(mode="json") for artifact in spec.artifacts
    ]
    instructions = sandbox.uploaded[JUDGE_ROOT + "/instructions.md"].decode()
    assert "## Reading order" in instructions
    assert "## Human judgment" in instructions
    assert "Fill `output/judgment.json`" in instructions
    assert "`evidence/agent-session.log`" in instructions
    assert "package/core.py" in instructions
    assert "tests/test_core.py::test_fixed" in instructions
    assert "fail_on_buggy_pass_on_golden" in instructions
    assert "`evidence/buggy-tests.json` and `evidence/golden-tests.json`" in instructions
    assert "| `oracle_tests/test_generated.py::test_generated` | `fail` | `pass` |" in instructions


def test_workspace_falls_back_to_explicit_repository_copies(tmp_path):
    config, paths, instance, files, evaluation = prepared_run(tmp_path)
    sandbox = fake_sandbox(files, worktrees=False)

    spec = build_judge_workspace(
        sandbox, config, paths, "example/runtime", instance, evaluation, verify_bundle(paths)
    )

    assert spec.workspace_method == "directory_copy"
    assert all("copy-views" in command for command in spec.reconstruction_commands)


def test_workspace_reconstruction_manifest_is_deterministic(tmp_path):
    config, paths, instance, files, evaluation = prepared_run(tmp_path)

    first = build_judge_workspace(
        fake_sandbox(files),
        config,
        paths,
        "example/runtime",
        instance,
        evaluation,
        verify_bundle(paths),
    )
    second = build_judge_workspace(
        fake_sandbox(files),
        config,
        paths,
        "example/runtime",
        instance,
        evaluation,
        verify_bundle(paths),
    )

    assert first == second


def test_workspace_reports_missing_evaluation_evidence(tmp_path):
    config, paths, instance, files, evaluation = prepared_run(tmp_path)
    (paths.evaluation / "golden/tests.json").unlink()

    with pytest.raises(FileNotFoundError, match="golden/tests.json"):
        build_judge_workspace(
            fake_sandbox(files),
            config,
            paths,
            "example/runtime",
            instance,
            evaluation,
            verify_bundle(paths),
        )
