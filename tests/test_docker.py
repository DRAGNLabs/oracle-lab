"""Opt-in checks using a prepared smoke image; never invoke an agent or model."""

import json
import os
import shutil
import uuid
from pathlib import Path

import docker
import pytest
import yaml
from fixtures import DOCKERFILES, HELPERS, judgment_labels, write_submission

from oracle_lab.config import JudgeConfig, load_config
from oracle_lab.container.images import build_image
from oracle_lab.container.sandbox import Profile, open_sandbox
from oracle_lab.evaluation import run_submission
from oracle_lab.evaluation.outcomes import pair_results
from oracle_lab.generation import verify_bundle
from oracle_lab.instance import write_private_instance_artifacts
from oracle_lab.io import read_json, write_json
from oracle_lab.judge.run import judge as run_judge
from oracle_lab.judge.workspace import JUDGE_ROOT, build_judge_workspace
from oracle_lab.localization import localize
from oracle_lab.paths import RunPaths
from oracle_lab.results import (
    read_evaluation_result,
    read_instance_record,
    read_localized_target,
    write_result,
)
from oracle_lab.run import reevaluate

pytestmark = pytest.mark.docker


def test_local_sdk_build_exec_and_copy(tmp_path):
    """Opt-in image/API smoke check using only a previously prepared runtime."""
    saved = os.environ.get("ORACLE_LAB_SMOKE_RUN")
    if not saved:
        pytest.skip("Set ORACLE_LAB_SMOKE_RUN to use a local prepared runtime")
    saved = Path(saved).resolve()
    saved_paths = RunPaths(saved)
    config = load_config(saved_paths.config, resolved=True)
    parent = read_json(saved_paths.runtime)["image"]
    context = tmp_path / "context"
    context.mkdir()
    shutil.copyfile(Path(__file__).parent / "docker/smoke.Dockerfile", context / "smoke.Dockerfile")
    shutil.copytree(
        HELPERS,
        context / "container_helpers",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    client = docker.from_env()
    try:
        with (tmp_path / "build.log").open("w") as log:
            image = build_image(
                client,
                context,
                "smoke.Dockerfile",
                {"BASE_IMAGE": parent, "SMOKE_ID": uuid.uuid4().hex},
                config.runtime.platform,
                log,
            )
        try:
            with open_sandbox(
                client, image.id, config, Profile.EVALUATION, tmp_path / "log"
            ) as sandbox:
                prompt = tmp_path / "prompt"
                prompt.write_text("fixture input")
                result = sandbox.run(
                    [
                        config.runtime.python,
                        "-c",
                        "import sys; print(sys.stdin.read()); print('stderr', file=sys.stderr)",
                    ],
                    stdin=prompt,
                    stdout=tmp_path / "stdout",
                    stderr=tmp_path / "stderr",
                )
                assert result.exit_code == 0
                assert (tmp_path / "stdout").read_text().strip() == "fixture input"
                assert (tmp_path / "stderr").read_text().strip() == "stderr"
                sandbox.upload(prompt, "/tmp/oracle-copy")
                sandbox.download("/tmp/oracle-copy", tmp_path / "copied")
                assert (tmp_path / "copied").read_bytes() == prompt.read_bytes()

                for executable in ("codex", "claude", "opencode"):
                    version = sandbox.run(
                        [executable, "--version"],
                        user="10001:10001",
                        stdout=tmp_path / f"{executable}.version",
                    )
                    assert version.exit_code == 0
                    assert (tmp_path / f"{executable}.version").read_text().strip()
        finally:
            client.images.remove(image.id)
    finally:
        client.close()


def test_reconstruct_privileged_judge_workspace(tmp_path, request):
    """Build both judge views from a saved run without calling a model."""
    saved = os.environ.get("ORACLE_LAB_SMOKE_RUN")
    if not saved:
        pytest.skip("Set ORACLE_LAB_SMOKE_RUN to use a completed saved run")
    saved = Path(saved).resolve()
    saved_paths = RunPaths(saved)
    config = load_config(saved_paths.config, resolved=True)
    config.judge = JudgeConfig(
        rubric=str(Path(__file__).parents[1] / "prompts/judge/generated-test-evaluation-rubric.md"),
        harness="claude",
        model="judge-fixture",
        limit={"kind": "wall_seconds", "value": 300},
    )
    instance = read_instance_record(saved_paths.instance)
    paths = RunPaths.create(tmp_path)
    shutil.copytree(saved_paths.submission, paths.submission, dirs_exist_ok=True)
    shutil.copyfile(saved_paths.generation / "workspace.diff", paths.generation / "workspace.diff")
    shutil.copyfile(saved_paths.results, paths.results)
    for version in ("buggy", "golden"):
        target = paths.evaluation / version
        target.mkdir(parents=True)
        for name in ("tests.json", "output.log"):
            shutil.copyfile(saved_paths.evaluation / version / name, target / name)
    shutil.copyfile(config.judge.rubric, paths.judge.rubric)
    write_private_instance_artifacts(paths, instance)
    if (saved_paths.build / "images.json").is_file():
        shutil.copyfile(saved_paths.build / "images.json", paths.build / "images.json")
    image = read_json(saved_paths.runtime)["image"]
    client = docker.from_env()
    request.addfinalizer(client.close)

    with open_sandbox(
        client, image, config, Profile.JUDGE, paths.judge.root / "docker.log"
    ) as sandbox:
        spec = build_judge_workspace(
            sandbox,
            config,
            paths,
            image,
            instance,
            read_evaluation_result(paths.results),
            verify_bundle(paths),
        )
        changed = next(
            line.split(" b/", 1)[1]
            for line in instance.golden_patch.splitlines()
            if line.startswith("diff --git a/")
        )
        comparison = sandbox.run(
            [
                "cmp",
                JUDGE_ROOT + "/buggy/" + changed,
                JUDGE_ROOT + "/golden/" + changed,
            ],
            check=False,
        )
        readable = sandbox.run(
            ["test", "-r", JUDGE_ROOT + "/evidence/paired-results.json"],
            user="10001:10001",
        )

    assert spec.workspace_method in {"git_worktree", "directory_copy"}
    assert comparison.exit_code == 1
    assert readable.exit_code == 0

    config.judge.rubric = str(paths.judge.rubric)
    paths.config.write_text(yaml.safe_dump(config.model_dump(), sort_keys=False))
    write_json(paths.runtime, {"image": image})
    write_result(paths.instance, instance)
    monkeypatch = pytest.MonkeyPatch()
    request.addfinalizer(monkeypatch.undo)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")

    def fake_judge_executable(sandbox, turn):
        payload = json.dumps(judgment_labels(tests_issue="unsure", attempt_detail=None))
        script = (
            "import pathlib; "
            "assert pathlib.Path('/oracle-judge/evidence/paired-results.json').is_file(); "
            f"print({payload!r})"
        )
        turn.artifact_directory.mkdir(parents=True, exist_ok=True)
        write_json(turn.artifact_directory / "command.json", [config.runtime.python, "-c", script])
        (turn.artifact_directory / "version.txt").write_text("deterministic-fixture\n")
        outcome = sandbox.run(
            [config.runtime.python, "-c", script],
            user="10001:10001",
            workdir=turn.working_directory,
            stdout=turn.artifact_directory / "final.txt",
            stderr=turn.artifact_directory / "stderr.log",
        )
        (turn.artifact_directory / "trace.jsonl").write_text("{}\n")
        return {
            "status": "completed",
            "exit_code": outcome.exit_code,
            "timed_out": outcome.timed_out,
            "duration_seconds": outcome.duration_seconds,
            "usage": None,
            "errors": [],
        }

    monkeypatch.setattr("oracle_lab.harnesses.claude.run_turn", fake_judge_executable)

    run_judge(config, paths)

    assert read_json(paths.judge.judgment)["status"] == "completed"
    assert read_json(paths.judge.result)["model"] == "judge-fixture"


# Deliberately handwritten evaluator probes, not benchmark predictions.
PROBES = """
import requests

def prepared_headers():
    return requests.Request('GET', 'https://example.test').prepare().headers

def test_pass_on_both():
    assert requests.Response().status_code is None

def test_fail_on_buggy_pass_on_golden():
    assert 'Content-Length' not in prepared_headers()

def test_pass_on_buggy_fail_on_golden():
    assert prepared_headers()['Content-Length'] == '0'

def test_fail_on_both():
    assert requests.Response().status_code == 123456
"""


@pytest.mark.parametrize("visibility", ["keep", "hide_all"])
def test_real_requests_pair_and_artifact_copy(tmp_path, visibility, monkeypatch, request):
    saved = os.environ.get("ORACLE_LAB_SMOKE_RUN")
    if not saved:
        pytest.skip("Set ORACLE_LAB_SMOKE_RUN to a run with a prepared Requests smoke image")
    saved = Path(saved).resolve()
    saved_paths = RunPaths(saved)
    config = load_config(saved_paths.config, resolved=True)
    config.task.existing_tests = visibility
    instance = read_instance_record(saved_paths.instance)
    assert instance.instance_id == "psf__requests-1142"
    image = read_json(saved_paths.runtime)["image"]
    client = docker.from_env()
    request.addfinalizer(client.close)
    paths = RunPaths.create(tmp_path)
    manifest = write_submission(paths, {f"{config.task.generated_dir}/test_probes.py": PROBES})
    buggy = run_submission(client, config, image, instance, paths, "buggy", manifest)
    golden = run_submission(client, config, image, instance, paths, "golden", manifest)
    result = pair_results(buggy, golden)
    assert buggy.status == golden.status == "completed", (buggy, golden)
    assert [cell.count for cell in result.matrix.values()] == [1, 1, 1, 1]
    if visibility == "keep":
        # Replay a saved artifact through the public reevaluation path, with no credentials.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        paths.config.write_text(yaml.safe_dump(config.model_dump()))
        write_result(paths.instance, instance)
        write_json(paths.runtime, {"image": image})
        write_json(
            paths.generation / "result.json",
            {
                "status": "not_run_handwritten_probe",
                "duration_seconds": 0,
                "usage": None,
            },
        )
        report = reevaluate(config, paths)
        assert report.is_file()
        replay = read_json(paths.results)
        assert replay["matrix"] == result["matrix"]
        assert replay["artifact_sha256"] == read_json(paths.submission / "manifest.json")["sha256"]


def test_localize_reads_the_saved_image_and_reproduces_the_saved_target(tmp_path, request):
    saved = os.environ.get("ORACLE_LAB_SMOKE_RUN")
    if not saved:
        pytest.skip("Set ORACLE_LAB_SMOKE_RUN to a localized run")
    saved_paths = RunPaths(Path(saved).resolve())
    if not saved_paths.target.is_file():
        pytest.skip("The saved run is not localized")
    config = load_config(saved_paths.config, resolved=True)
    instance = read_instance_record(saved_paths.instance)
    image = read_json(saved_paths.runtime)["image"]
    client = docker.from_env()
    request.addfinalizer(client.close)
    paths = RunPaths.create(tmp_path)

    target = localize(client, config, image, instance, paths)

    assert target == read_localized_target(saved_paths.target)


def record_removal_step() -> str:
    """The runtime recipe's instruction that removes a SWE-rebench V2 task record."""
    lines = (DOCKERFILES / "runtime.Dockerfile").read_text().splitlines()
    start = next(
        i
        for i, line in enumerate(lines)
        if line.startswith("RUN") and "swebench_matterhorn" in line
    )
    end = start
    while lines[end].endswith("\\"):
        end += 1
    return "\n".join(lines[start : end + 1])


@pytest.mark.parametrize("planted,builds", [("/swebench_matterhorn", True), ("/elsewhere", False)])
def test_runtime_build_removes_or_refuses_a_task_record(tmp_path, planted, builds):
    saved = os.environ.get("ORACLE_LAB_SMOKE_RUN")
    if not saved:
        pytest.skip("Set ORACLE_LAB_SMOKE_RUN to use a local prepared runtime")
    saved_paths = RunPaths(Path(saved).resolve())
    platform = load_config(saved_paths.config, resolved=True).require_runtime().platform
    context = tmp_path / "context"
    context.mkdir()
    (context / "record.Dockerfile").write_text(
        f"FROM {read_json(saved_paths.runtime)['image']}\n"
        "USER root\n"
        f"RUN mkdir -p {planted} && echo '{{}}' > {planted}/swebench_instance.json\n"
        f"{record_removal_step()}\n"
    )
    client = docker.from_env()
    with (tmp_path / "build.log").open("w") as log:
        if not builds:
            with pytest.raises(RuntimeError, match="Image build failed"):
                build_image(client, context, "record.Dockerfile", {}, platform, log)
            return
        image = build_image(client, context, "record.Dockerfile", {}, platform, log)
    try:
        output = client.containers.run(
            image.id,
            ["sh", "-c", "test -e /swebench_matterhorn && echo present || echo absent"],
            network_disabled=True,
            remove=True,
        )
        assert output.decode().strip() == "absent"
    finally:
        client.images.remove(image.id, force=True)
