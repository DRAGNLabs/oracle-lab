"""The command line: parse arguments, call the right thing, pick an exit code."""

import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from dotenv import load_dotenv

from oracle_lab.batch import run_batch
from oracle_lab.classification.run import classify
from oracle_lab.config import RunConfig, load_classification_config, load_config
from oracle_lab.export.hub import require_token, upload_dataset
from oracle_lab.export.writer import export_dataset
from oracle_lab.io import read_json
from oracle_lab.judge.run import judge
from oracle_lab.paths import RunPaths
from oracle_lab.preflight import preflight
from oracle_lab.report import report
from oracle_lab.review.agreement import agreement
from oracle_lab.run import reevaluate, run
from oracle_lab.web.server import serve


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)

    try:
        return _dispatch(args)
    except KeyboardInterrupt:
        print("oracle-lab: interrupted", file=sys.stderr)
        return 130
    except (RuntimeError, ValueError, OSError, TypeError) as exc:
        print(f"oracle-lab: {exc}", file=sys.stderr)
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

    # Render an existing run directory. Reads only.
    report_parser = sub.add_parser("report", help="Render saved results without executing code")
    report_parser.add_argument("run_dir", type=Path)

    # Rate the LLM judges' answers in a local web page. No model calls, no Docker.
    review_parser = sub.add_parser("review", help="Review LLM judge answers in the browser")
    review_parser.add_argument(
        "path", type=Path, help="A run, a classification attempt, or a directory of both"
    )
    review_parser.add_argument("--rater", required=True, help="Your rater ID")
    review_parser.add_argument("--port", type=int, default=0, help="Default: any free port")
    review_parser.add_argument(
        "--no-browser", action="store_true", help="Print the URL without opening it"
    )

    # Inter-rater agreement and judge quality over saved human reviews.
    agreement_parser = sub.add_parser(
        "agreement", help="Compute agreement between human reviews of the LLM judges"
    )
    agreement_parser.add_argument(
        "paths", type=Path, nargs="+", help="Runs, classification attempts, or directories of both"
    )
    agreement_parser.add_argument(
        "--output", type=Path, help="Default: output/agreement/<UTC timestamp>/ here"
    )

    # Runs, classifications, and reviews as a dataset, saved locally or uploaded.
    export_parser = sub.add_parser(
        "export", help="Export runs, classifications, and reviews as a dataset"
    )
    export_parser.add_argument(
        "root", type=Path, help="An output root, such as output/ or a review data repository"
    )
    export_parser.add_argument("--out", type=Path, help="Write the dataset to this new directory")
    export_parser.add_argument(
        "--repo", help="Upload to this private Hugging Face dataset, as <owner>/<name>"
    )

    return parser


def _dispatch(args: argparse.Namespace) -> int:
    # Only these use a credential, so only these read secrets from .env.
    if args.command in {"run", "batch", "judge", "classify", "export"}:
        load_dotenv(Path.cwd() / ".env", override=False, interpolate=False)

    if args.command == "preflight":
        return _preflight_all(args.configs)
    if args.command == "export":
        return _export(args.root, args.out, args.repo)
    if args.command == "review":
        return serve(args.path, args.rater, port=args.port, open_browser=not args.no_browser)
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
    elif args.command == "agreement":
        output = agreement(args.paths, args.output)
    else:  # report
        output = report(RunPaths(args.run_dir.resolve()))

    print(output)

    # The rest produce no run directory, so there is no status.json to grade.
    if args.command not in {"run", "batch", "evaluate", "judge", "classify"}:
        return 0

    # `run` and `batch` return the directory they created; the rest were given one.
    run_dir = Path(output if args.command in {"run", "batch", "classify"} else args.run_dir)
    return 2 if read_json(run_dir / "status.json")["state"] == "completed_with_errors" else 0


def _export(root: Path, out: Path | None, repo: str | None) -> int:
    """Write the dataset to `out`, upload it to `repo`, or both."""
    if out is None and repo is None:
        raise ValueError("Give --out, --repo, or both")
    if repo is not None:
        require_token()
    with TemporaryDirectory(prefix="oracle-lab-export-") as staging:
        directory = out or Path(staging)
        manifest = export_dataset(root, directory)
        for skipped in manifest.skipped:
            print(f"Skipped {skipped.path}: {skipped.error}", file=sys.stderr)
        print(f"{len(manifest.runs)} runs, {len(manifest.attempts)} classification attempts")
        print(upload_dataset(directory, repo) if repo else directory)
    return 0


def _load_saved_run(run_dir: Path) -> tuple[RunConfig, RunPaths]:
    """Open a run and load its frozen resolved configuration once at the CLI boundary."""
    paths = RunPaths(run_dir.resolve())
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
