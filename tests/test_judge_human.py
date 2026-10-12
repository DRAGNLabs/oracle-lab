import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fixtures import context_of, detecting_result, instance_record, write_submission

from oracle_bench.config import JudgeConfig, LimitsConfig, RuntimeConfig
from oracle_bench.io import read_json, write_json
from oracle_bench.judge.contracts import WorkspaceSpec
from oracle_bench.judge.human import (
    HUMAN_JUDGMENT_TEMPLATE,
    RATER_LABEL,
    RUN_DIRECTORY_LABEL,
    _prepare_human_files,
    collect_human_judgment,
    create_human_workspace,
    remove_human_workspace,
)
from oracle_bench.paths import RunPaths
from oracle_bench.results import write_result


def runtime_config():
    return RuntimeConfig(
        image="example/runtime",
        source_roots=["package"],
        import_modules=["package"],
        existing_test_globs=["tests"],
    )


def run_config():
    runtime = runtime_config()
    judge = JudgeConfig(
        rubric="rubric.md",
        instructions="instructions.md",
        harness="codex",
        model="judge-model",
        limit={"kind": "wall_seconds", "value": 60.0},
    )
    return SimpleNamespace(
        judge=judge,
        runtime=runtime,
        limits=LimitsConfig(),
        require_runtime=lambda: runtime,
        require_judge=lambda: judge,
    )


def workspace_spec():
    return WorkspaceSpec(
        prepared_image={"reference": "example/runtime", "image_id": "image-id", "digests": []},
        base_commit="a" * 40,
        repair_patch_sha256="b" * 64,
        reference_test_patch_sha256="c" * 64,
        rubric_sha256="d" * 64,
        workspace_method="git_worktree",
        reconstruction_commands=[],
        artifacts=[],
    )


def prepared_paths(tmp_path: Path) -> RunPaths:
    paths = RunPaths.create(tmp_path)
    paths.config.write_text("resolved config")
    write_json(paths.runtime, {"image": "example/runtime"})
    write_result(paths.instance, instance_record(instance_id="example"))
    write_json(paths.results, detecting_result())
    write_submission(paths)
    write_json(paths.judge.workspace_spec, workspace_spec().model_dump(mode="json"))
    write_json(
        paths.judge.human.workspaces / "workspace.json",
        {
            "container": "workspace",
            "rater": "rater_1",
            "network_mode": "none",
            "rubric_sha256": "b" * 64,
            "workspace_spec_sha256": "c" * 64,
        },
    )
    return paths


def human_container(run_dir: Path, rater: str = "rater_1"):
    container = Mock()
    container.attrs = {
        "Config": {
            "Labels": {
                "oracle-bench.profile": "human-judge",
                RUN_DIRECTORY_LABEL: str(run_dir),
                RATER_LABEL: rater,
            }
        }
    }
    return container


def install_client(monkeypatch, client):
    client_context = context_of(client)

    monkeypatch.setattr("oracle_bench.judge.human.docker_client", client_context)
    return client


def valid_rating():
    return {
        "no_attempt_reason": None,
        "tests_issue": "yes",
        "attempt_detail": "correct_assertion",
        "cheating": "no",
        "rationale": "The generated test reaches and checks the reported behavior.",
    }


def test_create_uses_blinded_networkless_container_and_saved_spec(tmp_path, monkeypatch):
    prepared_paths(tmp_path)
    client = Mock()
    container = Mock()
    client.containers.create.return_value = container
    install_client(monkeypatch, client)
    monkeypatch.setattr(
        "oracle_bench.judge.human.build_judge_workspace",
        lambda *args, **kwargs: workspace_spec(),
    )
    workspace_file = tmp_path / "judge/human-workspaces/example.code-workspace"
    monkeypatch.setattr(
        "oracle_bench.judge.human._prepare_human_files",
        lambda *args, **kwargs: workspace_file,
    )

    output = create_human_workspace(run_config(), RunPaths.open(tmp_path), "rater_1")

    options = client.containers.create.call_args.kwargs
    assert options["network_mode"] == "none"
    assert options["user"] == "10001:10001"
    assert options.get("environment") is None
    assert options["labels"]["oracle-bench.profile"] == "human-judge"
    assert options["labels"][RATER_LABEL] == "rater_1"
    assert "rater_1" in output
    container.start.assert_called_once_with()
    container.remove.assert_not_called()


def test_human_workspace_starts_with_instructions_and_editable_template(tmp_path):
    paths = prepared_paths(tmp_path)
    paths.judge.rubric.write_text("# Rubric\n")
    sandbox = Mock(runtime=runtime_config())
    uploaded = {}

    def upload(source, destination, **kwargs):
        uploaded[destination] = source.read_text()

    sandbox.upload.side_effect = upload

    _prepare_human_files(sandbox, paths, "workspace", "rater_1")

    assert "/oracle-judge/HUMAN_INSTRUCTIONS.md" not in uploaded
    assert "/oracle-judge/instructions.md" not in uploaded
    assert uploaded["/oracle-judge/output/judgment.json"] == HUMAN_JUDGMENT_TEMPLATE.read_text()
    assert json.loads(uploaded["/oracle-judge/output/judgment.json"])["tests_issue"].startswith(
        "CHOOSE:"
    )
    assert json.loads(uploaded["/oracle-judge/output/judgment.json"])["cheating"].startswith(
        "CHOOSE:"
    )
    sandbox.run.assert_any_call(["chmod", "600", "/oracle-judge/output/judgment.json"])


def test_mismatched_reconstruction_is_removed(tmp_path, monkeypatch):
    prepared_paths(tmp_path)
    client = Mock()
    container = Mock()
    client.containers.create.return_value = container
    install_client(monkeypatch, client)
    different = workspace_spec().model_copy(update={"rubric_sha256": "e" * 64})
    monkeypatch.setattr(
        "oracle_bench.judge.human.build_judge_workspace", lambda *args, **kwargs: different
    )

    with pytest.raises(ValueError, match="does not match"):
        create_human_workspace(run_config(), RunPaths.open(tmp_path), "rater_1")

    container.remove.assert_called_once_with(force=True)


def test_collect_validates_and_preserves_normalized_and_raw_rating(tmp_path, monkeypatch):
    prepared_paths(tmp_path)
    container = human_container(tmp_path)
    client = Mock()
    client.containers.get.return_value = container
    install_client(monkeypatch, client)
    monkeypatch.setattr(
        "oracle_bench.judge.human.load_config", lambda *args, **kwargs: run_config()
    )

    def download(self, source, destination, **kwargs):
        destination.write_text(json.dumps(valid_rating()))

    monkeypatch.setattr("oracle_bench.judge.human.Sandbox.download", download)

    saved = collect_human_judgment("workspace")

    assert read_json(saved)["status"] == "completed"
    assert read_json(saved)["tests_issue"] == "yes"
    assert read_json(saved.parent / "judgment.raw.json") == valid_rating()
    assert read_json(saved.parent / "provenance.json")["rubric_sha256"] == "b" * 64
    container.remove.assert_not_called()


@pytest.mark.parametrize(
    "answer",
    ["yes", "no", "unsure"],
)
def test_human_answer_is_preserved(tmp_path, monkeypatch, answer):
    prepared_paths(tmp_path)
    client = Mock()
    client.containers.get.return_value = human_container(tmp_path)
    install_client(monkeypatch, client)
    monkeypatch.setattr(
        "oracle_bench.judge.human.load_config", lambda *args, **kwargs: run_config()
    )
    draft = {
        **valid_rating(),
        "tests_issue": answer,
        "attempt_detail": "correct_assertion" if answer == "yes" else None,
        "no_attempt_reason": "nearby_behavior" if answer == "no" else None,
    }
    monkeypatch.setattr(
        "oracle_bench.judge.human.Sandbox.download",
        lambda self, source, destination, **kwargs: destination.write_text(json.dumps(draft)),
    )

    saved = collect_human_judgment("workspace")

    assert read_json(saved)["tests_issue"] == answer
    assert read_json(saved.parent / "judgment.raw.json") == draft


def test_invalid_draft_remains_in_running_container(tmp_path, monkeypatch):
    prepared_paths(tmp_path)
    container = human_container(tmp_path)
    client = Mock()
    client.containers.get.return_value = container
    install_client(monkeypatch, client)
    monkeypatch.setattr(
        "oracle_bench.judge.human.load_config", lambda *args, **kwargs: run_config()
    )

    def download(self, source, destination, **kwargs):
        destination.write_text('{"tests_issue": "maybe"}')

    monkeypatch.setattr("oracle_bench.judge.human.Sandbox.download", download)

    with pytest.raises(ValueError, match="Human judgment is invalid"):
        collect_human_judgment("workspace")

    assert not (tmp_path / "judge/human/rater_1").exists()
    container.remove.assert_not_called()


def test_collection_requires_explicit_archive_before_replacement(tmp_path, monkeypatch):
    prepared_paths(tmp_path)
    container = human_container(tmp_path)
    client = Mock()
    client.containers.get.return_value = container
    install_client(monkeypatch, client)
    monkeypatch.setattr(
        "oracle_bench.judge.human.load_config", lambda *args, **kwargs: run_config()
    )

    def download(self, source, destination, **kwargs):
        destination.write_text(json.dumps(valid_rating()))

    monkeypatch.setattr("oracle_bench.judge.human.Sandbox.download", download)
    collect_human_judgment("workspace")

    with pytest.raises(FileExistsError, match="--archive-existing"):
        collect_human_judgment("workspace")
    collect_human_judgment("workspace", archive_existing=True)

    history = tmp_path / "judge/human-history/rater_1"
    assert len(list(history.iterdir())) == 1


def test_remove_refuses_unrelated_container(monkeypatch):
    client = Mock()
    unrelated = Mock()
    unrelated.attrs = {"Config": {"Labels": {"oracle-bench.profile": "generation"}}}
    client.containers.get.return_value = unrelated
    install_client(monkeypatch, client)

    with pytest.raises(ValueError, match="non-human-judge"):
        remove_human_workspace("unrelated")

    unrelated.remove.assert_not_called()
