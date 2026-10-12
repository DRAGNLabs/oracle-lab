"""Opt-in checks using a prepared smoke image; never invoke an agent or model."""

import json
import os
import shutil
import uuid
from pathlib import Path

import docker
import pytest
import yaml
from fixtures import DOCKERFILES, HELPERS

from oracle_bench.config import JudgeConfig, load_config
from oracle_bench.container.images import build_image
from oracle_bench.container.sandbox import Profile, open_sandbox
from oracle_bench.evaluation import run_version
from oracle_bench.evaluation.outcomes import pair_results
from oracle_bench.instance import write_private_instance_artifacts
from oracle_bench.io import digest, read_json, write_json
from oracle_bench.judge.human import (
    collect_human_judgment,
    create_human_workspace,
    remove_human_workspace,
)
from oracle_bench.judge.run import judge as run_judge
from oracle_bench.judge.workspace import JUDGE_ROOT, build_judge_workspace
from oracle_bench.localization import localize
from oracle_bench.paths import RunPaths
from oracle_bench.results import (
    read_evaluation_result,
    read_instance_record,
    read_localized_target,
    write_result,
)
from oracle_bench.run import reevaluate

pytestmark = pytest.mark.docker


def test_local_sdk_build_exec_and_copy(tmp_path):
    """Opt-in image/API smoke check using only a previously prepared runtime."""
    saved = os.environ.get("ORACLE_BENCH_SMOKE_RUN")
    if not saved:
        pytest.skip("Set ORACLE_BENCH_SMOKE_RUN to use a local prepared runtime")
    saved = Path(saved).resolve()
    saved_paths = RunPaths.open(saved)
    config = load_config(saved_paths.config, resolved=True)
    parent = read_json(saved_paths.runtime)["image"]
    context = tmp_path / "context"
    context.mkdir()
    shutil.copyfile(DOCKERFILES / "smoke.Dockerfile", context / "smoke.Dockerfile")
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
    saved = os.environ.get("ORACLE_BENCH_SMOKE_RUN")
    if not saved:
        pytest.skip("Set ORACLE_BENCH_SMOKE_RUN to use a completed saved run")
    saved = Path(saved).resolve()
    saved_paths = RunPaths.open(saved)
    config = load_config(saved_paths.config, resolved=True)
    config.judge = JudgeConfig(
        rubric=str(Path(__file__).parents[1] / "prompts/judge/generated-test-evaluation-rubric.md"),
        instructions=str(
            Path(__file__).parents[1] / "prompts/judge/generated-test-instructions.md"
        ),
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
            sandbox, config, paths, image, instance, read_evaluation_result(paths.results)
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
    paths.config.write_text(yaml.safe_dump(config.to_dict(), sort_keys=False))
    write_json(paths.runtime, {"image": image})
    write_result(paths.instance, instance)
    monkeypatch = pytest.MonkeyPatch()
    request.addfinalizer(monkeypatch.undo)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "credential-fixture")

    def fake_judge_executable(sandbox, turn):
        payload = json.dumps(
            {
                "no_attempt_reason": None,
                "tests_issue": "unsure",
                "attempt_detail": None,
                "cheating": "no",
                "rationale": "The deterministic smoke judge verified that all workspace evidence is readable.",
            }
        )
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

    monkeypatch.setattr("oracle_bench.harnesses.claude.run_turn", fake_judge_executable)

    run_judge(config, paths)

    assert read_json(paths.judge.judgment)["status"] == "completed"
    assert read_json(paths.judge.result)["model"] == "judge-fixture"

    workspace = create_human_workspace(config, paths, "smoke_rater")
    container_name = workspace.splitlines()[0].removeprefix("Container: ")
    human = client.containers.get(container_name)
    assert human.attrs["HostConfig"]["NetworkMode"] == "none"
    assert human.exec_run(["test", "-w", JUDGE_ROOT + "/output"], user="10001:10001").exit_code == 0
    assert (
        human.exec_run(["test", "!", "-w", JUDGE_ROOT + "/rubric.md"], user="10001:10001").exit_code
        == 0
    )
    assert (
        human.exec_run(
            ["test", "!", "-w", config.runtime.workdir + "/.git"], user="10001:10001"
        ).exit_code
        == 0
    )
    assert (
        human.exec_run(
            ["test", "!", "-e", JUDGE_ROOT + "/judgment.json"], user="10001:10001"
        ).exit_code
        == 0
    )
    human_rating = json.dumps(
        {
            "no_attempt_reason": None,
            "tests_issue": "unsure",
            "attempt_detail": None,
            "cheating": "no",
            "rationale": "The human-workspace smoke fixture validates collection.",
        }
    )
    write_draft = (
        "import pathlib; "
        f"pathlib.Path('/oracle-judge/output/judgment.json').write_text({human_rating!r})"
    )
    assert (
        human.exec_run([config.runtime.python, "-c", write_draft], user="10001:10001").exit_code
        == 0
    )
    collected = collect_human_judgment(container_name)
    assert read_json(collected)["status"] == "completed"
    assert read_json(collected.parent / "provenance.json")["rater"] == "smoke_rater"
    remove_human_workspace(container_name)


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
    saved = os.environ.get("ORACLE_BENCH_SMOKE_RUN")
    if not saved:
        pytest.skip("Set ORACLE_BENCH_SMOKE_RUN to a run with a prepared Requests smoke image")
    saved = Path(saved).resolve()
    saved_paths = RunPaths.open(saved)
    config = load_config(saved_paths.config, resolved=True)
    config.task.existing_tests = visibility
    instance = read_instance_record(saved_paths.instance)
    assert instance.instance_id == "psf__requests-1142"
    image = read_json(saved_paths.runtime)["image"]
    client = docker.from_env()
    request.addfinalizer(client.close)
    paths = RunPaths.create(tmp_path)
    path = paths.submission / "files" / config.task.generated_dir / "test_probes.py"
    path.parent.mkdir(parents=True)
    path.write_text(PROBES)
    files = {
        f"{config.task.generated_dir}/test_probes.py": {
            "sha256": digest(path.read_bytes()),
            "mode": 0o644,
        }
    }
    write_json(
        paths.submission / "manifest.json",
        {
            "files": files,
            "sha256": digest(json.dumps(files, sort_keys=True).encode()),
            "empty": False,
            "compliant": True,
            "forbidden_changes": [],
        },
    )
    buggy = run_version(client, config, image, instance, paths, "buggy")
    golden = run_version(client, config, image, instance, paths, "golden")
    result = pair_results(buggy, golden)
    assert buggy["status"] == golden["status"] == "completed", (buggy, golden)
    assert [cell["count"] for cell in result["matrix"].values()] == [1, 1, 1, 1]
    for version in ["buggy", "golden"]:
        coverage = read_json(paths.evaluation / version / "coverage.json")
        assert coverage["status"] == "available"
        assert coverage["covered_lines"] > 0
        assert coverage["executable_lines"] >= coverage["covered_lines"]
    if visibility == "keep":
        # Replay a saved artifact through the public reevaluation path, with no credentials.
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("CODEX_API_KEY", raising=False)
        paths.config.write_text(yaml.safe_dump(config.to_dict()))
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
    saved = os.environ.get("ORACLE_BENCH_SMOKE_RUN")
    if not saved:
        pytest.skip("Set ORACLE_BENCH_SMOKE_RUN to a localized run")
    saved_paths = RunPaths.open(Path(saved).resolve())
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
