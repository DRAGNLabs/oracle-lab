"""The command line: parse arguments, call the right thing, pick an exit code."""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

from oracle_bench.batch import run_batch
from oracle_bench.classification.run import classify
from oracle_bench.config import RunConfig, load_classification_config, load_config
from oracle_bench.io import read_json
from oracle_bench.judge.agreement import analyze_agreement
from oracle_bench.judge.human import (
    collect_human_judgment,
    create_human_workspace,
    remove_human_workspace,
)
from oracle_bench.judge.run import judge
from oracle_bench.paths import RunPaths
from oracle_bench.report import report
from oracle_bench.run import preflight, reevaluate, run


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        return _dispatch(args)
    except KeyboardInterrupt:
        print("oracle-bench: interrupted", file=sys.stderr)
        return 130
    except (RuntimeError, ValueError, OSError, TypeError) as exc:
        print(f"oracle-bench: {exc}", file=sys.stderr)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate tests and evaluate buggy/golden outcomes"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Generate tests for one instance, then evaluate them.
    run_parser = sub.add_parser("run", help="Run one configured instance")
    run_parser.add_argument("config", type=Path)

    # Check instances before any paid run: build, reference pair, environment.
    preflight_parser = sub.add_parser(
        "preflight", help="Check configured instances without calling a model"
    )
    preflight_parser.add_argument("configs", type=Path, nargs="+")

    # Run every job in a batch YAML, or pick up where a batch directory left off.
    batch_parser = sub.add_parser("batch", help="Run or resume an explicit batch")
    batch_parser.add_argument(
        "path", type=Path, help="Batch YAML, or batch directory with --resume"
    )
    batch_parser.add_argument("--resume", action="store_true", help="Resume pending jobs")

    # Re-run the buggy/golden evaluation on tests already on disk. No model calls.
    evaluate_parser = sub.add_parser(
        "evaluate", help="Reevaluate saved tests without calling a model"
    )
    evaluate_parser.add_argument("run_dir", type=Path)

    # LLM judge pass over a finished run.
    judge_parser = sub.add_parser("judge", help="Judge generated tests from a saved run")
    judge_parser.add_argument("run_dir", type=Path)

    # Classify one SWE-bench problem independently of the generation pipeline.
    classify_parser = sub.add_parser("classify", help="Classify one SWE-bench problem")
    classify_parser.add_argument("config", type=Path)

    # Human judging workspaces, with their own create/collect/remove subcommands.
    workspace_parser = sub.add_parser(
        "judge-workspace", help="Create, collect, or remove a human judge workspace"
    )
    workspace_actions = workspace_parser.add_subparsers(dest="workspace_action", required=True)

    create_parser = workspace_actions.add_parser("create", help="Create a blinded workspace")
    create_parser.add_argument("run_dir", type=Path)
    create_parser.add_argument("--rater", required=True, help="Opaque rater ID")

    collect_parser = workspace_actions.add_parser("collect", help="Collect and validate a rating")
    collect_parser.add_argument("container_name")
    collect_parser.add_argument(
        "--archive-existing",
        action="store_true",
        help="Archive an existing rating before collecting its replacement",
    )

    remove_parser = workspace_actions.add_parser("remove", help="Remove a human workspace")
    remove_parser.add_argument("container_name")

    # Compare human and LLM judgments that are already saved.
    agreement_parser = sub.add_parser(
        "agreement", help="Analyze saved human and LLM judgments without model calls"
    )
    agreement_parser.add_argument("batch_dir", type=Path)
    agreement_parser.add_argument(
        "--sample-per-stratum",
        type=int,
        default=8,
        help="Maximum calibration instances selected for each outcome stratum",
    )

    # Render an existing run directory. Reads only.
    report_parser = sub.add_parser("report", help="Render saved results without executing code")
    report_parser.add_argument("run_dir", type=Path)

    return parser


def _dispatch(args: argparse.Namespace) -> int:
    # Only these reach a model, so only these need secrets from .env.
    if args.command in {"run", "batch", "judge", "classify"}:
        load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)

    if args.command == "preflight":
        return _preflight_all(args.configs)
    if args.command == "run":
        output = run(load_config(args.config))
    elif args.command == "batch":
        output = run_batch(args.path, resume=args.resume)
    elif args.command == "evaluate":
        config, paths = _load_saved_run(args.run_dir)
        output = reevaluate(config, paths)
    elif args.command == "judge":
        config, paths = _load_saved_run(args.run_dir)
        judge(config, paths)
        output = report(paths)
    elif args.command == "classify":
        output = classify(load_classification_config(args.config))
    elif args.command == "judge-workspace":
        if args.workspace_action == "create":
            config, paths = _load_saved_run(args.run_dir)
            output = create_human_workspace(config, paths, args.rater)
        elif args.workspace_action == "collect":
            output = collect_human_judgment(
                args.container_name, archive_existing=args.archive_existing
            )
        else:  # remove
            output = remove_human_workspace(args.container_name)
    elif args.command == "agreement":
        output = analyze_agreement(args.batch_dir, sample_per_stratum=args.sample_per_stratum)
    else:  # report
        output = report(RunPaths.open(args.run_dir.resolve()))

    print(output)

    # The rest produce no run directory, so there is no status.json to grade.
    if args.command not in {"run", "batch", "evaluate", "judge", "classify"}:
        return 0

    # `run` and `batch` return the directory they created; the rest were given one.
    run_dir = Path(output if args.command in {"run", "batch", "classify"} else args.run_dir)
    return 2 if read_json(run_dir / "status.json")["state"] == "completed_with_errors" else 0


def _load_saved_run(run_dir: Path) -> tuple[RunConfig, RunPaths]:
    """Open a run and load its frozen resolved configuration once at the CLI boundary."""
    paths = RunPaths.open(run_dir.resolve())
    return load_config(paths.config, resolved=True), paths


def _preflight_all(configs: list[Path]) -> int:
    """Check every config, even after a failure, then list the outcome of each."""
    outcomes = []
    for path in configs:
        try:
            outcomes.append(("passed", path, preflight(load_config(path))))
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            outcomes.append(("failed", path, exc))
    print()
    for state, path, detail in outcomes:
        print(f"{state}  {path}  {detail}")
    return 1 if any(state == "failed" for state, _, _ in outcomes) else 0
