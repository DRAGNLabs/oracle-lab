"""The single-instance pipeline: resolve, build, localize, check reference, generate, evaluate, judge.

Every stage records itself in `status.json` before it starts, so an interrupted or
failed run names the stage it stopped in rather than leaving a silent directory.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
from oracle_bench.generation import generate_submission
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
from oracle_bench.results import EvaluationResult, read_instance_record, write_result
from oracle_bench.spend import measure_spend


def status(run_dir: Path, stage: str, state: str = "running", **details: Any) -> None:
    write_json(
        run_dir / "status.json",
        {
            "stage": stage,
            "state": state,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            **details,
        },
    )
    print(f"[{stage}] {state}: {run_dir}", flush=True)


def completion_state(result: EvaluationResult) -> str:
    if result.agent.status == "completed" and result.both_completed and result.submission_compliant:
        return "completed"
    return "completed_with_errors"


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
        status(run_dir, stage)

        # Look up the dataset record and the settings for running this repo.
        instance, config.runtime = resolve_source(config.source)

        # Now that we know the repo layout, check the agent's test directory is not
        # inside the repo's source code.
        # this is done because generate_dir can be set by the user, who might be tempted
        # to set it to something inside src
        validate_resolved_config(config)

        # Copy the prompt and rubric into the run directory and point the config at
        # the copies, so a rerun later uses the same files this run used.
        config.task.prompt = str(paths.prompt)
        if config.judge:
            paths.judge.rubric.write_bytes(Path(config.judge.rubric).read_bytes())
            paths.judge.instructions.write_bytes(Path(config.judge.instructions).read_bytes())
            # Absolute, because reloading a config joins its paths to the run directory.
            config.judge.rubric = str(paths.judge.rubric.resolve())
            config.judge.instructions = str(paths.judge.instructions.resolve())

        # Save the config. `evaluate`, `judge`, and `agreement` read this file, not
        # the user's original.
        paths.config.write_text(yaml.safe_dump(config.to_dict(), sort_keys=False))

        # Save the answer key: the bug fix and the real tests. The agent must never
        # see either. instance.json is the raw data, ground truth is the readable
        # issue.md and fix.patch.
        write_result(paths.instance, instance)
        write_private_instance_artifacts(paths, instance)
        with docker_client() as client:
            stage = "build"
            status(run_dir, stage)
            image = prepare_image(client, config, paths.build)
            write_json(paths.runtime, {"image": image})

            # A localized run tells the agent which code to test: the code the
            # golden repair changes, read from the image. A repository run does not.
            target = None
            if config.task.scope == "localized":
                stage = "localize"
                status(run_dir, stage)
                target = localize(client, config, image, instance, paths)
            paths.prompt.write_text(
                render_prompt(prompt_template, config, target.text if target else None)
            )

            # A pair that cannot distinguish its own revisions cannot score an
            # agent, so prove it before spending a generation call.
            stage = "reference"
            status(run_dir, stage)
            check_reference(client, config, image, instance, paths)

            # Only generation and the judge call a model, so spend is measured around them.
            harnesses = [config.agent] + ([config.judge] if config.judge else [])
            with measure_spend(harnesses, paths.cost, lambda: status(run_dir, "cost")):
                stage = "generate"
                status(run_dir, stage)
                submission = generate_submission(client, config, image, instance, paths)

                stage = "evaluate"
                status(run_dir, stage)
                result = evaluate(client, config, image, instance, paths)

                judge_status = "disabled"
                if config.judge:
                    if submission["empty"] or result.no_tests:
                        judgment = skipped_judgment(
                            "No generated tests were collected; no judge turn was run."
                        )
                        write_json(paths.judge.judgment, judgment.model_dump())
                    else:
                        stage = "judge"
                        status(run_dir, stage)
                        judgment = judge_stage(client, config, paths, image, instance, result)
                    judge_status = judgment.status
        report(paths)
        status(
            run_dir,
            "finished",
            completion_state(result),
            judge_status=judge_status,
        )
    except KeyboardInterrupt:
        status(run_dir, stage, "interrupted")
        raise
    except Exception as exc:
        status(run_dir, stage, "failed", error=str(exc))
        raise
    return run_dir


def reevaluate(config: RunConfig, paths: RunPaths) -> Path:
    """Re-run a saved run's tests against both versions. Costs nothing, no model runs.

    Use it after changing evaluation code, or to redo a run whose generation you
    already paid for.
    """
    image = read_json(paths.runtime)["image"]
    run_dir = paths.root
    status(run_dir, "evaluate")
    try:
        with docker_client() as client:
            require_saved_image(client, image, "reevaluation")
            result = evaluate(client, config, image, read_instance_record(paths.instance), paths)
        # Any judgment was based on the old results, so it no longer applies.
        mark_judgments_stale(paths)
        path = report(paths)
        status(
            run_dir,
            "finished",
            completion_state(result),
            judge_status=read_judgment_state(paths)["status"],
        )
        return path
    except KeyboardInterrupt:
        status(run_dir, "evaluate", "interrupted")
        raise
    except Exception as exc:
        status(run_dir, "evaluate", "failed", error=str(exc))
        raise
