"""Check an instance end to end without an agent, before any paid run.

Runs every stage a run runs before generation, then an environment check: one
fixed test, run the way an agent's tests run. Calls no model and needs no
credentials. The built image is cached, so a later `run` of the same
configuration reuses it.
"""

from __future__ import annotations

from pathlib import Path

from docker import DockerClient

from oracle_bench.config import RunConfig, validate_prompt_scope
from oracle_bench.container.sandbox import Profile, Sandbox, docker_client
from oracle_bench.evaluation import run_version
from oracle_bench.evaluation.outcomes import pair_results
from oracle_bench.paths import RunPaths
from oracle_bench.repository import Repository
from oracle_bench.results import InstanceRecord, VersionResult, write_result, write_status
from oracle_bench.run import Stages, create_run_dir, prepare

# Stands in for an agent's submission. It cannot fail, so any failure comes from
# the environment.
ENVIRONMENT_TEST = '''"""Written by Oracle Bench preflight, not by an agent."""


def test_generated_tests_can_run():
    pass
'''


def preflight(config: RunConfig) -> Path:
    """Check one instance, and return its directory under `<output>/preflight/`."""
    prompt_template = Path(config.task.prompt).read_text()
    validate_prompt_scope(prompt_template, config.task.scope)
    run_dir = create_run_dir(Path(config.output) / "preflight")
    paths = RunPaths.create(run_dir)

    stages = Stages(run_dir)
    try:
        with docker_client() as client:
            instance, image = prepare(client, config, paths, stages, prompt_template)
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


def check_environment(
    client: DockerClient,
    config: RunConfig,
    image: str,
    instance: InstanceRecord,
    paths: RunPaths,
) -> None:
    """Run one fixed test the way generated tests run, and require it to pass on both."""
    files = paths.environment / "files"
    test = files / config.task.generated_dir / "test_oracle_bench_preflight.py"
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
