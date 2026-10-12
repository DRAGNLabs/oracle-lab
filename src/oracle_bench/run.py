"""The single-instance pipeline: resolve, build, localize, check reference, generate, evaluate, judge.

Every stage records itself in `status.json` before it starts, so an interrupted or
failed run names the stage it stopped in rather than leaving a silent directory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

from oracle_bench.config import (
    RunConfig,
    render_prompt,
    validate_prompt_scope,
    validate_resolved_config,
)
from oracle_bench.container.images import prepare_image, require_saved_image
from oracle_bench.container.sandbox import docker_client
from oracle_bench.evaluation import check_reference, evaluate
from oracle_bench.generation import generate_submission, verify_bundle
from oracle_bench.harnesses import require_credentials
from oracle_bench.instance import resolve_source, write_private_instance_artifacts
from oracle_bench.io import read_json, write_json
from oracle_bench.judge.contracts import (
    mark_judgments_stale,
    read_judgment_state,
    skipped_judgment,
)
from oracle_bench.judge.run import judge_stage
from oracle_bench.localization import localize
from oracle_bench.paths import RunPaths
from oracle_bench.report import report
from oracle_bench.results import (
    EvaluationResult,
    InstanceRecord,
    read_instance_record,
    write_result,
    write_status,
)
from oracle_bench.spend import measure_spend


def completion_state(result: EvaluationResult) -> str:
    return "completed_with_errors" if result.completion_problems else "completed"


def run(config: RunConfig) -> Path:
    require_credentials(config)
    prompt_template = Path(config.task.prompt).read_text()
    validate_prompt_scope(prompt_template, config.task.scope)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    run_dir = Path(config.output) / run_id
    run_dir.mkdir(parents=True)

    # Setup the run results directory from the given root.
    paths = RunPaths.create(run_dir)

    stage = "resolve"
    try:
        write_status(run_dir, stage)
        instance = _resolve(config, paths)
        with docker_client() as client:
            stage = "build"
            write_status(run_dir, stage)
            image = prepare_image(client, config, paths.build)
            write_json(paths.runtime, {"image": image})

            # A localized run tells the agent which code to test: the code the
            # golden repair changes, read from the image. A repository run does not.
            target = None
            if config.task.scope == "localized":
                stage = "localize"
                write_status(run_dir, stage)
                target = localize(client, config, image, instance, paths)
            paths.prompt.write_text(
                render_prompt(prompt_template, config, target.text if target else None)
            )

            # A pair that cannot distinguish its own revisions cannot score an
            # agent, so prove it before spending a generation call.
            stage = "reference"
            write_status(run_dir, stage)
            check_reference(client, config, image, instance, paths)

            # Only generation and the judge call a model, so spend is measured around them.
            harnesses = [config.agent] + ([config.judge] if config.judge else [])
            with measure_spend(harnesses, paths.cost, lambda: write_status(run_dir, "cost")):
                stage = "generate"
                write_status(run_dir, stage)
                submission = generate_submission(client, config, image, instance, paths)

                stage = "evaluate"
                write_status(run_dir, stage)
                result = evaluate(client, config, image, instance, paths)

                judge_status = "disabled"
                if config.judge:
                    if submission.empty or result.no_tests:
                        judgment = skipped_judgment(
                            "No generated tests were collected; no judge turn was run."
                        )
                        write_json(paths.judge.judgment, judgment.model_dump())
                    else:
                        stage = "judge"
                        write_status(run_dir, stage)
                        judgment = judge_stage(
                            client, config, paths, image, instance, result, verify_bundle(paths)
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
        write_status(run_dir, stage, "interrupted")
        raise
    except Exception as exc:
        write_status(run_dir, stage, "failed", error=str(exc))
        raise
    return run_dir


def _resolve(config: RunConfig, paths: RunPaths) -> InstanceRecord:
    """Look up the instance, then freeze everything later stages read into the run."""
    instance, config.runtime = resolve_source(config.source)
    validate_resolved_config(config)

    # Point the config at the run's own copies, so a rerun uses the files this run
    # used. The prompt itself is written after localization.
    config.task.prompt = str(paths.prompt)
    if config.judge:
        paths.judge.rubric.write_bytes(Path(config.judge.rubric).read_bytes())
        paths.judge.instructions.write_bytes(Path(config.judge.instructions).read_bytes())
        # Absolute, because reloading a config joins its paths to the run directory.
        config.judge.rubric = str(paths.judge.rubric.resolve())
        config.judge.instructions = str(paths.judge.instructions.resolve())

    # `evaluate`, `judge`, and `agreement` read this saved config, not the user's.
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
