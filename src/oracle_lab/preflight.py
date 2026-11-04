"""Check an instance end to end without an agent, before any paid run.

Runs every stage a run runs before generation, then an environment check: one
fixed test, run the way an agent's tests run. Calls no model and needs no
credentials. The built image is cached, so a later `run` of the same
configuration reuses it.
"""

from __future__ import annotations

from pathlib import Path

from docker import DockerClient

from oracle_lab.config import RunConfig, load_config, validate_prompt_scope
from oracle_lab.container.repository import Repository
from oracle_lab.container.sandbox import Profile, Sandbox, docker_client
from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.run import (
    InstanceRecord,
    VersionResult,
    read_localized_target,
    write_status,
)
from oracle_lab.evaluation import run_version
from oracle_lab.evaluation.outcomes import pair_results
from oracle_lab.paths import OutputPaths, RunPaths
from oracle_lab.run import Stages, prepare

# Stands in for an agent's submission. It cannot fail, so any failure comes from
# the environment.
ENVIRONMENT_TEST = '''"""Written by Oracle Lab preflight, not by an agent."""


def test_generated_tests_can_run():
    pass
'''


def preflight(config: RunConfig) -> Path:
    """Check one instance, and return its directory under `<output>/preflight/`."""
    prompt_template = Path(config.task.prompt).read_text()
    validate_prompt_scope(prompt_template, config.task.scope)
    paths = OutputPaths(Path(config.output)).new_preflight()
    run_dir = paths.root

    stages = Stages(run_dir)
    try:
        with docker_client() as client:
            instance, image = prepare(
                client, config, paths, stages, prompt_template, preflight=True
            )
            stages.start("environment")
            check_environment(client, config, image, instance, paths)
        write_status(run_dir, "finished", "passed")
    except KeyboardInterrupt:
        write_status(run_dir, stages.current, "interrupted")
        raise
    except Exception as exc:
        write_status(run_dir, stages.current, "failed", error=str(exc))
        raise
    return run_dir


def preflight_all(configs: list[Path]) -> bool:
    """Check every config, even after a failure, list each outcome, and say if all passed."""
    outcomes: list[tuple[str, Path, Path | Exception]] = []
    for path in configs:
        try:
            outcomes.append(("passed", path, preflight(load_config(path))))
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            outcomes.append(("failed", path, exc))
    for state, path, detail in outcomes:
        print(f"{state}  {path}  {detail}{_scope_note(detail)}")
    return all(state == "passed" for state, _, _ in outcomes)


def _scope_note(detail: Path | Exception) -> str:
    """A localized preflight's fix scope, which says how much of the fix its target names."""
    if isinstance(detail, Exception) or not RunPaths(detail).target.is_file():
        return ""
    return f"  fix scope: {read_localized_target(RunPaths(detail).target).scope}"


def check_environment(
    client: DockerClient,
    config: RunConfig,
    image: str,
    instance: InstanceRecord,
    paths: RunPaths,
) -> None:
    """Run one fixed test the way generated tests run, and require it to pass on both."""
    files = paths.environment / "files"
    test = files / config.task.generated_dir / "test_oracle_lab_preflight.py"
    test.parent.mkdir(parents=True, exist_ok=True)
    test.write_text(ENVIRONMENT_TEST)

    def setup(sandbox: Sandbox, workspace: Repository, directory: Path) -> list[str]:
        sandbox.upload(files, config.require_runtime().workdir, contents=True)
        return [config.task.generated_dir]

    def run(version: str) -> VersionResult:
        directory = paths.environment / version
        return run_version(
            client, config, image, instance, version, directory, Profile.EVALUATION, setup
        )

    paired = pair_results(run("buggy"), run("golden"))
    write_result(paths.environment / "results.json", paired)
    if not (paired.both_completed and paired.matrix["pass_on_both"].count == 1):
        raise RuntimeError(
            "Environment check test did not pass on both versions; inspect environment-check/"
        )
