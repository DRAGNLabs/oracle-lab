"""Check one problem end to end without an agent or a model.

Resolves the problem, builds its image, finds its localized target, runs the
private reference pair, and then an environment check: one fixed test, run the
way an agent's tests run. Needs no credentials. The built image is cached, so
later runs of the problem reuse it.
"""

from __future__ import annotations

from pathlib import Path

from docker import DockerClient

from oracle_lab.config import ProblemConfig
from oracle_lab.container.images import prepare_image
from oracle_lab.container.repository import Repository
from oracle_lab.container.sandbox import Profile, Sandbox, docker_client
from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.run import InstanceRecord, VersionResult, write_status
from oracle_lab.evaluation import check_reference, run_version
from oracle_lab.evaluation.outcomes import pair_results
from oracle_lab.io import write_json
from oracle_lab.localization import localize
from oracle_lab.paths import OutputPaths, RunPaths
from oracle_lab.run import Stages, resolve

# Stands in for an agent's submission. It cannot fail, so any failure comes from
# the environment.
ENVIRONMENT_TEST = '''"""Written by Oracle Lab preflight, not by an agent."""


def test_generated_tests_can_run():
    pass
'''


def preflight(config: ProblemConfig) -> Path:
    """Check one problem, and return its directory, `<output>/preflight/<instance-id>/`.

    A rerun replaces the directory: only the latest preflight of a problem counts.
    """
    paths = OutputPaths(Path(config.output)).new_preflight(config.source.instance)
    stages = Stages(paths.root)
    try:
        with docker_client() as client:
            stages.start("resolve")
            instance = resolve(config, paths)

            stages.start("build")
            image = prepare_image(client, config, paths.build)
            write_json(paths.runtime, {"image": image})

            # Task classification and localized runs copy the target from here.
            stages.start("localize")
            localize(client, config, image, instance, paths)

            stages.start("reference")
            check_reference(client, config, image, instance, paths)

            stages.start("environment")
            check_environment(client, config, image, instance, paths)
        write_status(paths.root, "finished", "passed")
    except KeyboardInterrupt:
        write_status(paths.root, stages.current, "interrupted")
        raise
    except Exception as exc:
        write_status(paths.root, stages.current, "failed", error=str(exc))
        raise
    return paths.root


def check_environment(
    client: DockerClient,
    config: ProblemConfig,
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
