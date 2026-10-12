"""Typed YAML schemas, harness defaults, and configuration path resolution."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from string import Template
from typing import Annotated, Literal

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

SWEBENCH_DATASETS = {
    "lite": "princeton-nlp/SWE-bench_Lite",
    "verified": "princeton-nlp/SWE-bench_Verified",
}
SWEBENCH_DATASET_REVISIONS = {
    "lite": "6ec7bb89b9342f664a54a6e0a6ea6501d3437cc2",
    "verified": "c104f840cc67f8b6eec6f759ebc8b2693d585d4a",
}
# Every runtime image installs all three CLIs from one lockfile, so these pins
# must stay equal to docker/package.json. Nothing selects a version per run.
HARNESS_VERSIONS = {
    "codex": "0.153.4",
    "claude": "2.1.280",
    "opencode": "1.18.32",
}
# First provider listed is the default. Everything goes through OpenRouter unless
# a config asks for the other one.
HARNESS_PROVIDERS = {
    "codex": ("openrouter", "openai"),
    "claude": ("openrouter", "anthropic"),
    "opencode": ("openrouter",),
}
NODE_IMAGE = "node:22.14.0-bookworm-slim"
PYTEST_VERSION = "7.4.4"
COVERAGE_VERSION = "7.6.1"


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
    kind: Literal["swebench"] = "swebench"
    dataset: Literal["lite", "verified"] = "verified"
    revision: Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")] | None = None
    split: str = "test"
    record: str | None = None

    @property
    def dataset_name(self) -> str:
        return SWEBENCH_DATASETS[self.dataset]

    @model_validator(mode="after")
    def resolve_dataset_revision(self) -> SourceConfig:
        self.revision = self.revision or SWEBENCH_DATASET_REVISIONS[self.dataset]
        return self

    @property
    def dataset_revision(self) -> str:
        """The immutable revision resolved during model validation."""
        if self.revision is None:  # pragma: no cover - guarded by model validation
            raise RuntimeError("Dataset revision was not resolved")
        return self.revision


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


class AgentConfig(HarnessConfig):
    """Generation settings. Subagents are permitted; the judge's are not."""


class JudgeConfig(HarnessConfig):
    """Settings for the optional, privileged generated-test judge."""

    rubric: NonBlank
    instructions: NonBlank
    # The judge never spawns subagents. The type checker complains because this
    # narrows an inherited field, which is exactly what we want here.
    multi_agent: Literal[False] = False  # pyright: ignore[reportIncompatibleVariableOverride]


class ClassifierConfig(HarnessConfig):
    """Settings for one repository-classification turn."""

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
    coverage_version: Version | None = None
    rebuild: str = "python -m pip install --no-deps --no-build-isolation -e ."


class ToolchainConfig(ConfigModel):
    """Oracle Bench implementation dependencies recorded with resolved runs."""

    node_image: ImageReference = NODE_IMAGE
    codex_version: Version = HARNESS_VERSIONS["codex"]
    claude_version: Version = HARNESS_VERSIONS["claude"]
    opencode_version: Version = HARNESS_VERSIONS["opencode"]
    pytest_version: Version = PYTEST_VERSION
    coverage_version: Version = COVERAGE_VERSION

    @property
    def installed_harnesses(self) -> dict[str, str]:
        """Return the complete CLI toolchain independently of the selected agent."""
        return {
            "codex": self.codex_version,
            "claude": self.claude_version,
            "opencode": self.opencode_version,
        }


class RunConfig(ConfigModel):
    source: SourceConfig
    agent: AgentConfig
    judge: JudgeConfig | None = None
    task: TaskConfig
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    output: str = "runs"
    runtime: RuntimeConfig | None = None
    toolchain: ToolchainConfig = Field(default_factory=ToolchainConfig)

    def to_dict(self) -> dict:
        return self.model_dump()

    def require_runtime(self) -> RuntimeConfig:
        if self.runtime is None:
            raise RuntimeError("The source adapter has not resolved the repository runtime")
        return self.runtime

    def require_judge(self) -> JudgeConfig:
        if self.judge is None:
            raise ValueError("This run has no judge configuration")
        return self.judge


class ClassificationConfig(ConfigModel):
    """Standalone settings for classifying one SWE-bench problem."""

    source: SourceConfig
    classifier: ClassifierConfig
    rubric: NonBlank
    limits: LimitsConfig = Field(default_factory=LimitsConfig)
    output: str = "classifications"
    runtime: RuntimeConfig | None = None
    toolchain: ToolchainConfig = Field(default_factory=ToolchainConfig)

    def to_dict(self) -> dict:
        return self.model_dump()

    def require_runtime(self) -> RuntimeConfig:
        if self.runtime is None:
            raise RuntimeError("The source adapter has not resolved the repository runtime")
        return self.runtime


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
            f"Settings are generated by Oracle Bench and cannot be input: {sorted(generated_fields)}"
        )
    return raw


def _resolve_paths(config: RunConfig, base: Path) -> None:
    config.task.prompt = str((base / config.task.prompt).resolve())
    if config.judge:
        config.judge.rubric = str((base / config.judge.rubric).resolve())
        config.judge.instructions = str((base / config.judge.instructions).resolve())
    config.output = str((base / config.output).resolve())
    if config.source.record:
        config.source.record = str((base / config.source.record).resolve())


def load_config(path: Path, *, resolved: bool = False) -> RunConfig:
    """Parse the schema once, resolve paths, then check the repository boundary."""
    raw = _read_settings(path, resolved)
    config = RunConfig.model_validate(raw)
    _resolve_paths(config, path.resolve().parent)
    if resolved and config.runtime is None:
        raise ValueError("Resolved configuration is missing runtime")
    if config.runtime is not None:
        validate_resolved_config(config)
    return config


def load_classification_config(path: Path, *, resolved: bool = False) -> ClassificationConfig:
    """Parse standalone classification settings and resolve their host paths."""
    raw = _read_settings(path, resolved)
    config = ClassificationConfig.model_validate(raw)
    base = path.resolve().parent
    config.rubric = str((base / config.rubric).resolve())
    config.output = str((base / config.output).resolve())
    if config.source.record:
        config.source.record = str((base / config.source.record).resolve())
    if resolved and config.runtime is None:
        raise ValueError("Resolved classification configuration is missing runtime")
    return config
