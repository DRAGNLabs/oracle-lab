import json
from pathlib import Path

import pytest
import yaml
from fixtures import DOCKERFILES, RUN_CONFIG, run_config

from oracle_bench.batch import load_batch_config
from oracle_bench.config import (
    HARNESS_VERSIONS,
    SOURCE_DATASETS,
    HarnessConfig,
    RuntimeConfig,
    load_classification_config,
    load_config,
    validate_prompt_scope,
    validate_resolved_config,
)

SAMPLE = RUN_CONFIG


def config_file(tmp_path, section, key, value):
    raw = yaml.safe_load(SAMPLE.read_text())
    raw.setdefault(section, {})[key] = value
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    return path


@pytest.mark.parametrize("visibility", ["keep", "hide_all"])
def test_test_visibility_is_explicit_and_paths_are_config_relative(tmp_path, visibility):
    config = load_config(config_file(tmp_path, "task", "existing_tests", visibility))
    assert config.task.existing_tests == visibility
    assert config.task.prompt == str((tmp_path / "../prompts/agent/smoke-tests.md").resolve())
    assert config.output == str((tmp_path / "../runs").resolve())


PROMPTS = Path(__file__).parents[1] / "prompts" / "agent"


@pytest.mark.parametrize(
    "prompt,scope",
    [
        ("localized-tests.md", "localized"),
        ("unit-tests.md", "repository"),
        ("smoke-tests.md", "repository"),
    ],
)
def test_each_prompt_matches_its_scope(prompt, scope):
    validate_prompt_scope((PROMPTS / prompt).read_text(), scope)


@pytest.mark.parametrize(
    "prompt,scope",
    [
        ("localized-tests.md", "repository"),
        ("unit-tests.md", "localized"),
    ],
)
def test_prompt_and_scope_must_agree_on_showing_the_target(prompt, scope):
    with pytest.raises(ValueError, match="test_target"):
        validate_prompt_scope((PROMPTS / prompt).read_text(), scope)


CONFIGS = Path(__file__).parents[1] / "configs"


@pytest.mark.parametrize(
    "path", sorted(CONFIGS.rglob("*.yaml")), ids=lambda path: str(path.relative_to(CONFIGS))
)
def test_every_sample_config_loads(path):
    """Each sample is a run, classification, or batch config that validates as written."""
    raw = yaml.safe_load(path.read_text())
    if "jobs" in raw:
        load_batch_config(path)
    elif "classifier" in raw:
        load_classification_config(path)
    else:
        load_config(path)


def test_agent_limit_defaults_to_five_minutes(tmp_path):
    raw = yaml.safe_load(SAMPLE.read_text())
    raw["agent"].pop("limit")
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    config = load_config(path)

    assert config.agent.limit.model_dump() == {"kind": "wall_seconds", "value": 300.0}
    assert config.agent.timeout_seconds == 300


def test_claude_judge_accepts_wall_time_limit(tmp_path):
    raw = yaml.safe_load(SAMPLE.read_text())
    raw["judge"] = {
        "rubric": "rubric.md",
        "instructions": "instructions.md",
        "harness": "claude",
        "model": "judge-model",
        "limit": {"kind": "wall_seconds", "value": 30},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    config = load_config(path)

    assert config.judge.limit.kind == "wall_seconds"
    assert config.judge.rubric == str((tmp_path / "rubric.md").resolve())


@pytest.mark.parametrize("kind", ["tokens", "turns"])
def test_judge_rejects_removed_limit_kinds(tmp_path, kind):
    raw = yaml.safe_load(SAMPLE.read_text())
    raw["judge"] = {
        "rubric": "rubric.md",
        "instructions": "instructions.md",
        "harness": "claude",
        "model": "judge-model",
        "limit": {"kind": kind, "value": 1},
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize(
    "limit",
    [
        {"kind": "tokens", "value": 10},
        {"kind": "turns", "value": 1, "wall_seconds": 30},
        {"kind": "turns"},
        {"kind": "turns", "value": 1.5},
    ],
)
def test_judge_rejects_invalid_stopping_policy(tmp_path, limit):
    raw = yaml.safe_load(SAMPLE.read_text())
    raw["judge"] = {
        "rubric": "rubric.md",
        "instructions": "instructions.md",
        "harness": "claude",
        "model": "judge-model",
        "limit": limit,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError):
        load_config(path)


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"unexpected": True}, "Extra inputs are not permitted"),
        ({"harness": "opencode", "provider": "anthropic"}, "opencode with openrouter"),
        ({"version": "2.1.263"}, "judge.version"),
        ({"multi_agent": True}, "multi_agent"),
    ],
)
def test_judge_rejects_unknown_or_incompatible_harness_settings(tmp_path, changes, error):
    raw = yaml.safe_load(SAMPLE.read_text())
    judge = {
        "rubric": "rubric.md",
        "instructions": "instructions.md",
        "harness": "claude",
        "model": "judge-model",
        "limit": {"kind": "wall_seconds", "value": 30},
    }
    judge.update(changes)
    raw["judge"] = judge
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match=error):
        load_config(path)


@pytest.mark.parametrize(
    "harness,provider,credential",
    [
        ("claude", "openrouter", "OPENROUTER_API_KEY"),
        ("codex", "openrouter", "OPENROUTER_API_KEY"),
        ("opencode", "openrouter", "OPENROUTER_API_KEY"),
    ],
)
def test_openrouter_is_supported_by_every_harness(tmp_path, harness, provider, credential):
    raw = yaml.safe_load(SAMPLE.read_text())
    raw["agent"].update(harness=harness, provider=provider)
    if harness != "claude":
        raw["agent"]["limit"] = {"kind": "wall_seconds", "value": 30}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    config = load_config(path)

    assert config.agent.provider == provider
    assert config.agent.credential_env == credential


def test_opencode_rejects_non_openrouter_provider(tmp_path):
    raw = yaml.safe_load(SAMPLE.read_text())
    raw["agent"].update(harness="opencode", provider="openai")
    raw["agent"]["limit"] = {"kind": "wall_seconds", "value": 30}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match="opencode with openrouter"):
        load_config(path)


@pytest.mark.parametrize("section", ["agent", "judge"])
def test_harness_version_is_not_selectable_per_run(tmp_path, section):
    """One locked toolchain installs every CLI, so a run cannot pin its own."""
    raw = yaml.safe_load(SAMPLE.read_text())
    raw.setdefault(
        "judge",
        {"model": "judge-model", "harness": "claude", "rubric": "r.md", "instructions": "i.md"},
    )
    raw[section]["version"] = HARNESS_VERSIONS["codex"]
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))

    with pytest.raises(ValueError, match=f"{section}.version"):
        load_config(path)


def test_resolved_toolchain_records_every_harness_version():
    config = load_config(SAMPLE)

    assert config.toolchain.harnesses == HARNESS_VERSIONS


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("task", "existing_tests", "sometimes"),
        ("task", "scope", "package"),
        ("task", "generated_dir", "../outside"),
        ("task", "generated_dir", "/outside"),
        ("task", "generated_dir", ".git/hooks"),
        ("source", "revision", "main"),
        ("source", "kind", "git"),
        ("agent", "limit", {"kind": "turns", "value": 1.5}),
        ("agent", "limit", {"kind": "unbounded", "value": 1}),
        ("agent", "multi_agent", "false"),
        ("limits", "cpus", float("inf")),
        ("agent", "typo", 123),
        ("agent", "provider", "unknown"),
    ],
)
def test_invalid_config_fails_before_starting_containers(tmp_path, section, key, value):
    with pytest.raises(ValueError):
        load_config(config_file(tmp_path, section, key, value))


@pytest.mark.parametrize("section", ["runtime", "toolchain"])
def test_resolved_sections_cannot_be_supplied_as_input(tmp_path, section):
    raw = yaml.safe_load(SAMPLE.read_text())
    raw[section] = {}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="generated by Oracle Bench"):
        load_config(path)


def test_generated_directory_is_checked_against_resolved_source_roots():
    config = load_config(SAMPLE)
    config.task.generated_dir = "requests/tests"
    config.runtime = RuntimeConfig(
        image="example/image:latest",
        source_roots=["requests"],
        import_modules=["requests"],
        existing_test_globs=["test_requests.py", "tests"],
    )
    with pytest.raises(ValueError, match="outside production source roots"):
        validate_resolved_config(config)


def test_classification_config_is_independent_and_resolves_paths():
    path = Path(__file__).parents[1] / "configs/classification/example.yaml"

    config = load_classification_config(path)

    assert config.source.dataset == "verified"
    assert config.classifier.multi_agent is False
    assert config.rubric.endswith("/prompts/judge/task-classification-rubric.md")
    assert config.output.endswith("/classifications")
    assert config.runtime is None


def test_each_swebench_dataset_resolves_its_own_pinned_revision(tmp_path):
    path = config_file(tmp_path, "source", "dataset", "lite")

    config = load_config(path)

    assert config.source.dataset == "lite"
    assert config.source.revision == "6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2"


def test_resolved_run_lock_can_be_reloaded(tmp_path):
    config = run_config(
        "requests", image="example/image:latest", existing_test_globs=["test_requests.py", "tests"]
    )
    path = tmp_path / "config.resolved.yaml"
    path.write_text(yaml.safe_dump(config.model_dump()))

    reloaded = load_config(path, resolved=True)

    assert reloaded.require_runtime().image == "example/image:latest"
    assert reloaded.toolchain.pytest_version


def test_resolved_configuration_contains_no_credential_value(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "secret-test-value")
    config = load_config(SAMPLE)
    assert "secret-test-value" not in yaml.safe_dump(config.model_dump())


@pytest.mark.parametrize(
    "field,value",
    [
        ("source_roots", []),
        ("source_roots", ["../escape"]),
        ("existing_test_globs", ["/outside"]),
        ("python", "relative/python"),
        ("import_modules", [123]),
    ],
)
def test_runtime_schema_checks_external_adapter_data(field, value):
    values = {
        "image": "example/image",
        "source_roots": ["requests"],
        "import_modules": ["requests"],
        "existing_test_globs": ["tests"],
    }
    values[field] = value
    with pytest.raises(ValueError):
        RuntimeConfig(**values)


@pytest.mark.parametrize("harness", ["codex", "claude", "opencode"])
def test_openrouter_is_the_default_provider(harness):
    """A config that names no provider routes through OpenRouter."""
    config = HarnessConfig(model="vendor/model", harness=harness)
    assert config.provider == "openrouter"
    assert config.credential_env == "OPENROUTER_API_KEY"


@pytest.mark.parametrize("dataset", ["lite", "verified"])
def test_each_dataset_defaults_to_its_own_pinned_revision(tmp_path, dataset):
    config = load_config(config_file(tmp_path, "source", "dataset", dataset))
    assert config.source.revision == SOURCE_DATASETS[dataset].revision


def test_swerebench_v2_kind_pins_its_dataset_revision_and_split(tmp_path):
    config = load_config(config_file(tmp_path, "source", "kind", "swerebench_v2"))
    assert config.source.dataset == "swerebench_v2"
    assert config.source.dataset_name == "nebius/SWE-rebench-V2"
    assert config.source.revision == "10483de0f50fe5da545942705a76c6150171af7f"
    assert config.source.split == "train"


def test_a_dataset_from_another_source_kind_is_refused(tmp_path):
    path = config_file(tmp_path, "source", "kind", "swerebench_v2")
    raw = yaml.safe_load(path.read_text())
    raw["source"]["dataset"] = "verified"
    path.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="not a 'swerebench_v2' source"):
        load_config(path)


def test_an_explicit_dataset_revision_is_kept(tmp_path):
    config = load_config(config_file(tmp_path, "source", "revision", "f" * 40))
    assert config.source.revision == "f" * 40


def test_harness_versions_are_the_pins_the_image_installs():
    package = json.loads((DOCKERFILES / "package.json").read_text())["dependencies"]
    assert sorted(HARNESS_VERSIONS.values()) == sorted(package.values())
