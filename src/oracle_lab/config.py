"""Typed YAML schemas, harness defaults, and configuration path resolution."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from string import Template
from typing import Annotated, Literal, NamedTuple, TypeVar

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator


class ConfigModel(BaseModel):
    """Schemas reject unknown fields and coercion at the configuration boundary."""

    model_config = ConfigDict(
        extra="forbid",
        strict=True,
        validate_default=True,
        hide_input_in_errors=True,
    )


PositiveNumber = Annotated[float, Field(strict=True, gt=0, allow_inf_nan=False)]
Version = Annotated[str, Field(strict=True, pattern=r"^\d+\.\d+(?:\.\d+)?(?:[a-zA-Z0-9.+-]*)?$")]
ImageReference = Annotated[str, Field(strict=True, pattern=r"^[A-Za-z0-9_./:@-]+$")]
NonBlank = Annotated[str, Field(strict=True, pattern=r"\S")]


class SourceDataset(NamedTuple):
    kind: str
    name: str
    revision: str
    split: str


SOURCE_DATASETS = {
    "lite": SourceDataset(
        "swebench",
        "princeton-nlp/SWE-bench_Lite",
        "6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2",
        "test",
    ),
    "verified": SourceDataset(
        "swebench",
        "princeton-nlp/SWE-bench_Verified",
        "c104f840cc67f8b6eec6f759ebc8b2693d585d4a",
        "test",
    ),
    "swerebench_v2": SourceDataset(
        "swerebench_v2",
        "nebius/SWE-rebench-V2",
        "10483de0f50fe5da545942705a76c6150171af7f",
        "train",
    ),
}
# The dataset a source kind uses when the config names none.
DEFAULT_DATASETS = {"swebench": "verified", "swerebench_v2": "swerebench_v2"}
# Every runtime image installs all three CLIs from this one package, so its pins
# are the versions. Nothing selects a version per run.
_HARNESS_PACKAGE = Path(__file__).parent / "container/docker/package.json"
_HARNESS_NPM_NAMES = {
    "codex": "@openai/codex",
    "claude": "@anthropic-ai/claude-code",
    "opencode": "opencode-ai",
}
_PINNED = json.loads(_HARNESS_PACKAGE.read_text())["dependencies"]
HARNESS_VERSIONS = {harness: _PINNED[name] for harness, name in _HARNESS_NPM_NAMES.items()}
# First provider listed is the default. Everything goes through OpenRouter unless
# a config asks for the other one.
HARNESS_PROVIDERS = {
    "codex": ("openrouter", "openai"),
    "claude": ("openrouter", "anthropic"),
    "opencode": ("openrouter",),
}
NODE_IMAGE = "node:22.14.0-bookworm-slim"
PYTEST_VERSION = "7.4.4"


def repo_path(value: str) -> str:
    """Confine paths supplied by YAML, source adapters, and captured manifests."""
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or str(path) == "."
        or ".git" in path.parts
        or value.startswith("-")
    ):
        raise ValueError(f"Expected a relative repository path, got {value!r}")
    return str(path)


def container_path(value: str) -> str:
    if not value.startswith("/") or "\n" in value:
        raise ValueError("Container Python and workdir must be absolute paths")
    return value


RepositoryPath = Annotated[str, AfterValidator(repo_path)]
ContainerPath = Annotated[str, AfterValidator(container_path)]


class SourceConfig(ConfigModel):
    instance: NonBlank
    kind: Literal["swebench", "swerebench_v2"] = "swebench"
    # The dataset, revision, and split default to the pinned values for the kind.
    dataset: Literal["lite", "verified", "swerebench_v2"]
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]
    split: str
    record: str | None = None

    @property
    def dataset_name(self) -> str:
        return SOURCE_DATASETS[self.dataset].name

    @model_validator(mode="before")
    @classmethod
    def pin_dataset(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        kind = data.get("kind", "swebench")
        dataset = data.get("dataset") or DEFAULT_DATASETS.get(kind)
        if dataset not in SOURCE_DATASETS:
            return data
        pinned = SOURCE_DATASETS[dataset]
        return {
            **data,
            "dataset": dataset,
            "revision": data.get("revision") or pinned.revision,
            "split": data.get("split") or pinned.split,
        }

    @model_validator(mode="after")
    def check_dataset_kind(self) -> SourceConfig:
        if SOURCE_DATASETS[self.dataset].kind != self.kind:
            raise ValueError(f"Dataset {self.dataset!r} is not a {self.kind!r} source")
        return self


class WallTimeLimit(ConfigModel):
    kind: Literal["wall_seconds"]
    value: PositiveNumber


def default_limit() -> WallTimeLimit:
    """Bound every model turn with the same five-minute default."""
    return WallTimeLimit(kind="wall_seconds", value=300)


class HarnessConfig(ConfigModel):
    """One wall-clock-bounded agent turn and the model used to run it."""

    model: NonBlank
    harness: Literal["codex", "claude", "opencode"] = "codex"
    provider: Literal["openai", "openrouter", "anthropic"] | None = None
    auth: Literal["oauth", "api_key"] = "oauth"
    limit: WallTimeLimit = Field(default_factory=default_limit)
    multi_agent: Annotated[bool, Field(strict=True)] = True

    @property
    def timeout_seconds(self) -> float:
        return self.limit.value

    @model_validator(mode="after")
    def resolve_harness(self) -> HarnessConfig:
        """Resolve harness defaults together with the permitted providers."""
        providers = HARNESS_PROVIDERS[self.harness]
        self.provider = self.provider or providers[0]
        if self.provider not in providers:
            raise ValueError(
                "Use codex with openrouter/openai, claude with openrouter/anthropic, "
                "or opencode with openrouter"
            )
        return self

    @property
    def credential_env(self) -> str:
        if self.provider == "openrouter":
            return "OPENROUTER_API_KEY"
        if self.harness == "claude":
            return "ANTHROPIC_API_KEY" if self.auth == "api_key" else "CLAUDE_CODE_OAUTH_TOKEN"
        return "OPENAI_API_KEY"


class JudgeConfig(HarnessConfig):
    """Settings for the optional, privileged generated-test judge."""

    rubric: NonBlank
    # The judge never spawns subagents. The type checker complains because this
    # narrows an inherited field, which is exactly what we want here.
    multi_agent: Literal[False] = False  # pyright: ignore[reportIncompatibleVariableOverride]


class ClassifierConfig(HarnessConfig):
    """Settings for one task-classification turn."""

    multi_agent: Literal[False] = False  # pyright: ignore[reportIncompatibleVariableOverride]


class TaskConfig(ConfigModel):
    prompt: str
    scope: Literal["localized", "repository"] = "repository"
    existing_tests: Literal["keep", "hide_all"] = "hide_all"
    generated_dir: RepositoryPath = "oracle_tests"

    @property
    def hides_existing_tests(self) -> bool:
        return self.existing_tests == "hide_all"


class LimitsConfig(ConfigModel):
    evaluation_seconds: PositiveNumber = 600
    setup_seconds: PositiveNumber = 1800
    memory: str = "4g"
    cpus: PositiveNumber = 2


class RuntimeConfig(ConfigModel):
    """Resolved source runtime generated by a source adapter."""

    image: ImageReference
    source_roots: Annotated[list[RepositoryPath], Field(min_length=1)]
    import_modules: list[str]
    existing_test_globs: list[RepositoryPath]
    platform: str = "linux/amd64"
    workdir: ContainerPath = "/testbed"
    python: ContainerPath = "/opt/miniconda3/envs/testbed/bin/python"
    pythonpath: ContainerPath | None = None
    pytest_plugins: list[str] = Field(default_factory=list)
    pytest_version: Version | None = None
    rebuild: str = "python -m pip install --no-deps --no-build-isolation -e ."


class ToolchainConfig(ConfigModel):
    """Oracle Lab implementation dependencies recorded with resolved runs."""

    node_image: ImageReference = NODE_IMAGE
    # Every CLI the image installs, whichever one the run selects.
    harnesses: dict[str, Version] = Field(default_factory=lambda: dict(HARNESS_VERSIONS))
    pytest_version: Version = PYTEST_VERSION


class ResolvedConfig(ConfigModel):
    """What a run and a classification share: an instance, its runtime, and limits.

    `runtime` and `toolchain` are filled in by Oracle Lab, never by the user.
    """

    source: SourceConfig
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    # The output root, relative to the config file. Each command writes into its own
    # directory there; see `OutputPaths`.
    output: str
    runtime: RuntimeConfig | None = None
    toolchain: ToolchainConfig = Field(default_factory=ToolchainConfig)

    def require_runtime(self) -> RuntimeConfig:
        if self.runtime is None:
            raise RuntimeError("The source adapter has not resolved the repository runtime")
        return self.runtime


class RunConfig(ResolvedConfig):
    # Subagents are permitted here; the judge and classifier forbid them.
    agent: HarnessConfig
    judge: JudgeConfig | None = None
    task: TaskConfig

    def require_judge(self) -> JudgeConfig:
        if self.judge is None:
            raise ValueError("This run has no judge configuration")
        return self.judge


class ClassificationConfig(ResolvedConfig):
    """Standalone settings for classifying one SWE-bench problem."""

    classifier: ClassifierConfig
    rubric: NonBlank


def validate_resolved_config(config: RunConfig) -> None:
    """Make sure the agent's test directory is not inside the repo's source code.

    If it were, the agent could edit production code and have it counted as a test.
    """
    runtime = config.require_runtime()
    generated = PurePosixPath(config.task.generated_dir)
    for source in map(PurePosixPath, runtime.source_roots):
        if generated == source or generated in source.parents or source in generated.parents:
            raise ValueError("Generated tests must be outside production source roots")


def validate_prompt_scope(template: str, scope: str) -> None:
    """Make sure a localized run's prompt shows the target, and only a localized one."""
    shows_target = "test_target" in Template(template).get_identifiers()
    if shows_target != (scope == "localized"):
        raise ValueError(
            "task.scope localized requires ${test_target} in the prompt, and only localized "
            f"may use it; scope is {scope!r}"
        )


def render_prompt(template: str, config: RunConfig, test_target: str | None) -> str:
    """Fill in the prompt variables. A run without a target says "this repository"."""
    runtime = config.require_runtime()
    return Template(template).substitute(
        generated_dir=config.task.generated_dir,
        project_python=runtime.python,
        workdir=runtime.workdir,
        test_target=test_target or "this repository",
    )


def _read_settings(path: Path, resolved: bool) -> dict:
    """Keep the public YAML interface separate from saved runtime metadata."""
    raw = yaml.safe_load(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("Configuration must be a YAML mapping")
    generated_fields = {"runtime", "toolchain"} & raw.keys()
    if generated_fields and not resolved:
        raise ValueError(
            f"Settings are generated by Oracle Lab and cannot be input: {sorted(generated_fields)}"
        )
    return raw


C = TypeVar("C", bound=ResolvedConfig)


def _load(model: type[C], path: Path, resolved: bool) -> tuple[C, Callable[[str], str]]:
    """Validate a config file and resolve the host paths every config has.

    Returns the config and a resolver for the caller's own file paths, which are
    relative to the config file's directory.
    """
    config = model.model_validate(_read_settings(path, resolved))
    base = path.resolve().parent

    def resolve(value: str) -> str:
        return str((base / value).resolve())

    config.output = resolve(config.output)
    if config.source.record:
        config.source.record = resolve(config.source.record)
    if resolved and config.runtime is None:
        raise ValueError("Resolved configuration is missing runtime")
    return config, resolve


def load_config(path: Path, *, resolved: bool = False) -> RunConfig:
    """Parse the schema once, resolve paths, then check the repository boundary."""
    config, resolve = _load(RunConfig, path, resolved)
    config.task.prompt = resolve(config.task.prompt)
    if config.judge:
        config.judge.rubric = resolve(config.judge.rubric)
    if config.runtime is not None:
        validate_resolved_config(config)
    return config


def load_classification_config(path: Path, *, resolved: bool = False) -> ClassificationConfig:
    """Parse standalone classification settings and resolve their host paths."""
    config, resolve = _load(ClassificationConfig, path, resolved)
    config.rubric = resolve(config.rubric)
    return config
