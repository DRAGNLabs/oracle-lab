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
    monkeypatch.setattr(cli, "_load_saved_run", lambda path: (config, cli.RunPaths(path)))

    def invoke(loaded_config, paths):
        assert loaded_config is config
        assert paths.root == tmp_path.resolve()
        assert os.environ["OPENAI_API_KEY"] == "judge-fixture"
        return {"status": "completed"}

    monkeypatch.setattr(cli, "judge", invoke)
    monkeypatch.setattr(cli, "report", lambda paths: paths.report)

    assert cli.main(["judge", str(tmp_path)]) == 0


@pytest.mark.parametrize("done, code", [(True, 0), (False, 1)])
def test_setup_loads_credentials_passes_its_flags_and_fails_until_done(
    tmp_path, monkeypatch, done, code
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    (tmp_path / ".env").write_text("OPENROUTER_API_KEY=classifier-fixture\n")
    calls = []

    def setup(path, *, reclassify, preflight_only):
        calls.append((path.name, reclassify, preflight_only, os.environ["OPENROUTER_API_KEY"]))
        return done

    monkeypatch.setattr(cli, "setup", setup)

    assert cli.main(["setup", "problems.yaml", "--reclassify"]) == code
    assert calls == [("problems.yaml", True, False, "classifier-fixture")]


def test_review_serves_the_page_without_loading_credentials(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: pytest.fail("No credentials"))
    monkeypatch.setattr(cli, "serve", lambda *args, **kwargs: calls.append((args, kwargs)) or 0)

    assert cli.main(["review", str(tmp_path), "--rater", "rater-1", "--no-browser"]) == 0
    assert calls == [((tmp_path, "rater-1"), {"port": 0, "open_browser": False})]


def test_agreement_prints_where_it_saved(tmp_path, monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "load_dotenv", lambda *a, **k: pytest.fail("No credentials"))
    monkeypatch.setattr(
        cli, "agreement", lambda paths, output: calls.append((paths, output)) or tmp_path
    )

    assert cli.main(["agreement", str(tmp_path), "--output", str(tmp_path / "out")]) == 0
    assert calls == [([tmp_path], tmp_path / "out")]
    assert capsys.readouterr().out.strip() == str(tmp_path)
