"""The single-instance pipeline: resolve, build, localize, check reference, generate, evaluate, judge.

Every stage records itself in `status.json` before it starts, so an interrupted or
failed run names the stage it stopped in rather than leaving a silent directory.
`prepare` holds the stages before generation, which `preflight` also runs.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from docker import DockerClient

from oracle_lab.config import (
    RunConfig,
    render_prompt,
    validate_prompt_scope,
    validate_resolved_config,
)
from oracle_lab.container.images import prepare_image, require_saved_image
from oracle_lab.container.sandbox import docker_client
from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.judgment import (
    SkippedJudgment,
    mark_judgments_stale,
    read_judgment_state,
)
from oracle_lab.contracts.run import (
    EvaluationResult,
    InstanceRecord,
    read_instance_record,
    verify_bundle,
    write_status,
)
from oracle_lab.evaluation import check_reference, evaluate
from oracle_lab.generation import generate_submission
from oracle_lab.harnesses import require_credentials
from oracle_lab.harnesses.spend import measure_spend
from oracle_lab.instance import resolve_source, write_private_instance_artifacts
from oracle_lab.io import read_json, write_json
from oracle_lab.judge.generation.run import judge_stage
from oracle_lab.localization import localize
from oracle_lab.paths import OutputPaths, RunPaths
from oracle_lab.report import report


def completion_state(result: EvaluationResult) -> str:
    return "completed_with_errors" if result.completion_problems else "completed"


class Stages:
    """The stage a run is in, recorded in `status.json` as each one starts."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.current = "resolve"

    def start(self, stage: str) -> None:
        self.current = stage
        write_status(self.run_dir, stage)


def run(config: RunConfig) -> Path:
    require_credentials(config)
    prompt_template = Path(config.task.prompt).read_text()
    validate_prompt_scope(prompt_template, config.task.scope)
    paths = OutputPaths(Path(config.output)).new_run()
    run_dir = paths.root

    stages = Stages(run_dir)
    try:
        with docker_client() as client:
            instance, image = prepare(client, config, paths, stages, prompt_template)

            # Only generation and the judge call a model, so spend is measured around them.
            harnesses = [config.agent] + ([config.judge] if config.judge else [])
            with measure_spend(harnesses, paths.cost, lambda: write_status(run_dir, "cost")):
                stages.start("generate")
                submission = generate_submission(client, config, image, instance, paths)

                stages.start("evaluate")
                result = evaluate(client, config, image, instance, paths)

                judge_status = "disabled"
                if config.judge:
                    if submission.empty or result.no_tests:
                        judgment = SkippedJudgment(
                            reason="No generated tests were collected; no judge turn was run."
                        )
                        write_json(paths.judge.judgment, judgment.model_dump())
                    else:
                        stages.start("judge")
                        judgment = judge_stage(
                            client, config, paths, image, instance, verify_bundle(paths)
                        )
                    judge_status = judgment.status
        report(paths)
        write_status(
            run_dir,
            "finished",
            completion_state(result),
            judge_status=judge_status,
        )
    except KeyboardInterrupt:
        write_status(run_dir, stages.current, "interrupted")
        raise
    except Exception as exc:
        write_status(run_dir, stages.current, "failed", error=str(exc))
        raise
    return run_dir


def prepare(
    client: DockerClient,
    config: RunConfig,
    paths: RunPaths,
    stages: Stages,
    prompt_template: str,
) -> tuple[InstanceRecord, str]:
    """Run every stage before generation, and return the instance and image."""
    stages.start("resolve")
    instance = _resolve(config, paths)

    stages.start("build")
    image = prepare_image(client, config, paths.build)
    write_json(paths.runtime, {"image": image})

    # A localized run tells the agent which code to test: the code the
    # golden repair changes, read from the image. A repository run does not.
    target = None
    if config.task.scope == "localized":
        stages.start("localize")
        target = localize(client, config, image, instance, paths)
    paths.prompt.write_text(render_prompt(prompt_template, config, target.text if target else None))

    # A pair that cannot distinguish its own revisions cannot score an
    # agent, so prove it before spending a generation call.
    stages.start("reference")
    check_reference(client, config, image, instance, paths)
    return instance, image


def _resolve(config: RunConfig, paths: RunPaths) -> InstanceRecord:
    """Look up the instance, then freeze everything later stages read into the run."""
    instance, config.runtime = resolve_source(config.source)
    validate_resolved_config(config)

    # Point the config at the run's own copies, so a rerun uses the files this run
    # used. The prompt itself is written after localization.
    config.task.prompt = str(paths.prompt)
    if config.judge:
        paths.judge.rubric.write_bytes(Path(config.judge.rubric).read_bytes())
        # Absolute, because reloading a config joins its paths to the run directory.
        config.judge.rubric = str(paths.judge.rubric.resolve())

    # `evaluate` and `judge` read this saved config, not the user's.
    paths.config.write_text(yaml.safe_dump(config.model_dump(), sort_keys=False))

    # The answer key, which the agent must never see: the raw record, plus the
    # readable issue.md and fix.patch.
    write_result(paths.instance, instance)
    write_private_instance_artifacts(paths, instance)
    return instance


def reevaluate(config: RunConfig, paths: RunPaths) -> Path:
    """Re-run a saved run's tests against both versions. Costs nothing, no model runs.

    Use it after changing evaluation code, or to redo a run whose generation you
    already paid for.
    """
    image = read_json(paths.runtime)["image"]
    run_dir = paths.root
    write_status(run_dir, "evaluate")
    try:
        with docker_client() as client:
            require_saved_image(client, image, "reevaluation")
            result = evaluate(client, config, image, read_instance_record(paths.instance), paths)
        # Any judgment was based on the old results, so it no longer applies.
        mark_judgments_stale(paths)
        path = report(paths)
        write_status(
            run_dir,
            "finished",
            completion_state(result),
            judge_status=read_judgment_state(paths)["status"],
        )
        return path
    except KeyboardInterrupt:
        write_status(run_dir, "evaluate", "interrupted")
        raise
    except Exception as exc:
        write_status(run_dir, "evaluate", "failed", error=str(exc))
        raise
