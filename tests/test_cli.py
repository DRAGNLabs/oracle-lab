import os

import pytest
from fixtures import RUN_CONFIG

from oracle_lab import cli

SAMPLE = RUN_CONFIG


def test_saved_run_config_is_loaded_once_at_cli_boundary(tmp_path, monkeypatch):
    config = object()
    loads = []

    def load(path, *, resolved):
        loads.append((path, resolved))
        return config

    monkeypatch.setattr(cli, "load_config", load)

    loaded_config, paths = cli._load_saved_run(tmp_path)

    assert loaded_config is config
    assert paths.root == tmp_path.resolve()
    assert loads == [(paths.config, True)]


@pytest.mark.parametrize("shell_value", [None, "from-shell"])
def test_run_loads_local_keys_without_overriding_shell(tmp_path, monkeypatch, shell_value):
    import os

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if shell_value is not None:
        monkeypatch.setenv("OPENAI_API_KEY", shell_value)
    (tmp_path / ".env").write_text('OPENAI_API_KEY="literal-${HOME}"\n')

    def run(config):
        assert os.environ["OPENAI_API_KEY"] == (shell_value or "literal-${HOME}")
        assert "literal-${HOME}" not in str(config.model_dump())
        return tmp_path

    monkeypatch.setattr(cli, "run", run)
    monkeypatch.setattr(cli, "read_json", lambda path: {"state": "completed"})
    assert cli.main(["run", str(SAMPLE)]) == 0


@pytest.mark.parametrize("command", ["evaluate", "report"])
def test_offline_commands_do_not_load_credentials(tmp_path, monkeypatch, command):
    def unexpected_load(*args, **kwargs):
        pytest.fail("Offline commands must not load credentials")

    monkeypatch.setattr(cli, "load_dotenv", unexpected_load)
    monkeypatch.setattr(cli, "_load_saved_run", lambda path: (object(), object()))
    monkeypatch.setattr(cli, "reevaluate", lambda config, paths: tmp_path)
    monkeypatch.setattr(cli, "report", lambda path: tmp_path)
    monkeypatch.setattr(cli, "read_json", lambda path: {"state": "completed"})
    assert cli.main([command, str(tmp_path)]) == 0


def test_batch_loads_credentials_and_returns_batch_status(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("ANTHROPIC_API_KEY=test-value\n")
    batch_dir = tmp_path / "batches" / "batch-id"
    write_status = batch_dir / "status.json"
    write_status.parent.mkdir(parents=True)
    write_status.write_text('{"state": "completed"}')
    monkeypatch.setattr(cli, "run_batch", lambda path, resume: batch_dir)

    assert cli.main(["batch", "batch.yaml"]) == 0


def test_judge_loads_credentials_and_uses_saved_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=judge-fixture\n")
    (tmp_path / "status.json").write_text('{"state": "completed"}\n')

    config = object()
    monkeypatch.setattr(cli, "_load_saved_run", lambda path: (config, cli.RunPaths.open(path)))

    def invoke(loaded_config, paths):
        assert loaded_config is config
        assert paths.root == tmp_path.resolve()
        assert os.environ["OPENAI_API_KEY"] == "judge-fixture"
        return {"status": "completed"}

    monkeypatch.setattr(cli, "judge", invoke)
    monkeypatch.setattr(cli, "report", lambda paths: paths.report)

    assert cli.main(["judge", str(tmp_path)]) == 0


def test_classify_loads_credentials_and_uses_its_own_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=classifier-fixture\n")
    attempt = tmp_path / "classifications" / "instance" / "attempt"
    attempt.mkdir(parents=True)
    (attempt / "status.json").write_text('{"state": "completed"}\n')
    config = object()
    monkeypatch.setattr(cli, "load_classification_config", lambda path: config)
    monkeypatch.setattr(cli, "classify", lambda loaded: attempt if loaded is config else None)

    assert cli.main(["classify", "classification.yaml"]) == 0


def test_preflight_checks_every_config_and_fails_if_any_fails(tmp_path, monkeypatch, capsys):
    def unexpected_load(*args, **kwargs):
        pytest.fail("Preflight must not load credentials")

    def preflight(path):
        if path.name == "bad.yaml":
            raise RuntimeError("reference pair did not reproduce")
        return tmp_path / path.stem

    monkeypatch.setattr(cli, "load_dotenv", unexpected_load)
    monkeypatch.setattr(cli, "load_config", lambda path: path)
    monkeypatch.setattr(cli, "preflight", preflight)

    assert cli.main(["preflight", "bad.yaml", "good.yaml"]) == 1
    output = capsys.readouterr().out
    assert "failed  bad.yaml  reference pair did not reproduce" in output
    assert f"passed  good.yaml  {tmp_path / 'good'}" in output
