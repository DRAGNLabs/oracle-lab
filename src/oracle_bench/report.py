"""Present validated, machine-readable run artifacts as a Markdown audit report."""

from pathlib import Path

from oracle_bench.classification.contracts import (
    CLASSIFICATION_LABELS,
    latest_classification,
)
from oracle_bench.config import RunConfig, load_config
from oracle_bench.io import read_json
from oracle_bench.judge.contracts import (
    JUDGMENT_LABELS,
    read_judgment_state,
)
from oracle_bench.paths import RunPaths
from oracle_bench.results import (
    MATRIX_CELLS,
    VERSIONS,
    AgentAttempt,
    EvaluationResult,
    HarnessResult,
    LocalizedTarget,
    MatrixCellName,
    Usage,
    last_error_message,
    read_agent_attempt,
    read_evaluation_result,
    read_instance_record,
    read_paired_result,
    read_run_cost,
    read_version_result,
)


def report(paths: RunPaths) -> Path:
    results = read_evaluation_result(paths.results)
    judgment = read_judgment_state(paths)
    judge_attempt = read_agent_attempt(paths.judge.result) if paths.judge.result.is_file() else None
    config = load_config(paths.config, resolved=True) if paths.config.is_file() else None
    classification, attempt = _classification(paths)
    lines = _title_lines(paths, results)
    lines.extend(_pipeline_lines(paths, config, results, judgment, judge_attempt))
    lines.extend(_outcome_lines(results))
    lines.extend(_judge_lines(judgment))
    lines.extend(_classification_lines(classification, attempt))
    lines.extend(_usage_lines(results, judge_attempt))
    lines.extend(_file_lines(paths, attempt))
    paths.report.write_text("\n".join(lines))
    return paths.report


def _title_lines(paths: RunPaths, results: EvaluationResult) -> list[str]:
    reasons = results.completion_problems
    state = "completed with errors" if reasons else "completed"
    lines = [
        f"# {results.instance_id}",
        "",
        f"**{state}**" + (": " + "; ".join(reasons) if reasons else ""),
        "",
    ]
    if results.forbidden_changes:
        lines += [
            "Forbidden changes:",
            "",
            *[f"- `{name}`" for name in results.forbidden_changes],
            "",
        ]
    # Files that failed to collect do not stop the other files from counting.
    uncollected = [
        f"{version} {len(errors)}"
        for version in VERSIONS
        if (errors := _collection_errors(paths.evaluation / version / "tests.json"))
    ]
    if uncollected:
        lines += ["Files that failed to collect: " + ", ".join(uncollected) + ".", ""]
    if results.agent.errors:
        lines += [f"Agent message: {last_error_message(results.agent.errors)}", ""]
    return lines


def _collection_errors(path: Path) -> list[dict]:
    return read_version_result(path).collection_errors if path.is_file() else []


def _pipeline_lines(
    paths: RunPaths,
    config: RunConfig | None,
    results: EvaluationResult,
    judgment: dict,
    judge_attempt: AgentAttempt | None,
) -> list[str]:
    rows = []
    if config:
        source = config.source
        rows.append(("Resolve", f"`{source.dataset_name}`, {source.split} split", "ok"))
    if paths.runtime.is_file():
        image = read_json(paths.runtime)["image"].removeprefix("sha256:")[:12]
        rows.append(("Build", f"image `{image}`", "ok"))
    reference = paths.reference / "results.json"
    if reference.is_file():
        paired = read_paired_result(reference)
        distinguishing = paired.matrix[MatrixCellName.FAIL_ON_BUGGY_PASS_ON_GOLDEN].count
        count = len(paired.tests)
        rows.append(
            (
                "Reference",
                f"{count} reference test{'' if count == 1 else 's'}",
                f"{distinguishing} fail buggy, pass golden",
            )
        )

    agent = results.agent
    target = _target_summary(results.test_target) if results.test_target else "whole repository"
    visible = "visible" if results.existing_tests == "keep" else "hidden"
    rows.append(
        (
            "Generate",
            _harness_text(agent) + f"; target: {target}; existing tests {visible}",
            f"{agent.status}, {_seconds(agent.duration_seconds)}",
        )
    )
    generated_dir = config.task.generated_dir if config else "generated tests"
    rows.append(
        (
            "Evaluate",
            f"`{generated_dir}/` on buggy and golden",
            f"buggy {results.buggy_status}, golden {results.golden_status}",
        )
    )

    status = judgment["status"]
    if judge_attempt:
        judge_params = _harness_text(judge_attempt)
    elif config and config.judge:
        judge = config.judge
        judge_params = (
            f"{judge.harness}, {judge.model}, {judge.provider}, {_limit(judge.limit.model_dump())}"
        )
    else:
        judge_params = "—"
    judge_result = status
    if status == "completed":
        judge_result += f", tests issue: {judgment['tests_issue']}"
    elif status == "skipped":
        judge_result += " (no tests)"
    rows.append(("Judge", judge_params, judge_result))
    rows.append(("Cost", "OpenRouter key spend, generation and judge", _cost(paths.cost)))

    return [
        "## Pipeline",
        "",
        "| Stage | Parameters | Result |",
        "|---|---|---|",
        *[f"| {stage} | {params} | {result} |" for stage, params, result in rows],
        "",
    ]


def _target_summary(target: LocalizedTarget) -> str:
    """The prompt's one-line-per-file target, on a single line for a table cell."""
    return "; ".join(line.removeprefix("- ") for line in target.text.splitlines())


def _harness_text(result: HarnessResult) -> str:
    # Generation results carry these as adapter extras; judge and classifier declare them.
    harness = getattr(result, "harness", None) or "unknown harness"
    if version := getattr(result, "harness_version", None):
        harness += f" {version}"
    parts = [harness, getattr(result, "model", None), getattr(result, "provider", None)]
    if limit := getattr(result, "limit", None):
        parts.append(_limit(limit))
    return ", ".join(part for part in parts if part)


def _limit(limit: dict) -> str:
    if limit["kind"] == "wall_seconds":
        return f"{limit['value']:g} s limit"
    return f"{limit['kind']}={limit['value']}"


def _outcome_lines(results: EvaluationResult) -> list[str]:
    lines = [
        "## Test outcomes",
        "",
        "| Outcome | Tests |",
        "|---|---:|",
        *[f"| {cell.label} | {results.matrix[cell].count} |" for cell in MATRIX_CELLS],
        f"| Other (skip, error, incomplete) | {len(results.other_outcomes)} |",
        "",
        "| Version | Assertion failures | Other exceptions | Unknown failures |",
        "|---|---:|---:|---:|",
    ]
    for version in VERSIONS:
        kinds = results.failure_kinds[version]
        lines.append(
            f"| {version} | {kinds['assertion']} | {kinds['exception']} | {kinds['unknown']} |"
        )
    return [*lines, ""]


def _judge_lines(judgment: dict) -> list[str]:
    status = judgment["status"]
    lines = ["## Judgment", ""]
    if status == "completed":
        lines += [
            "| Tests issue? | Attempt detail | No-attempt reason | Cheating |",
            "|---|---|---|---|",
            "| " + " | ".join(judgment[key] or "—" for key in JUDGMENT_LABELS) + " |",
            "",
            *_blockquote(judgment["rationale"]),
            "",
        ]
    elif status == "stale":
        lines += [f"Stale: {judgment['reason']}", ""]
        previous = judgment.get("previous_judgment", {})
        if previous.get("status") == "completed":
            lines += [f"Previous tests-issue answer: `{previous['tests_issue']}`.", ""]
    elif status == "skipped":
        lines += [f"Skipped: {judgment['reason']}", ""]
    elif status in {"invalid_output", "invalid_artifact", "failed", "timed_out"}:
        lines += [f"{status}: {judgment['error']}", ""]
    elif status == "missing":
        lines += ["Missing: the judge was configured, but no judgment is saved.", ""]
    else:
        lines += ["Disabled.", ""]
    return lines


def _classification(paths: RunPaths) -> tuple[dict, Path | None]:
    """The newest classification for this run's instance.

    Classifications live in a `classifications` directory beside the runs
    directory, and a run lives at `<runs>/<run-id>`.
    """
    # Early or synthetic runs may have no frozen instance; classification is optional.
    if not paths.instance.is_file():
        return {"status": "missing"}, None
    classifications = paths.root.parent.parent / "classifications"
    return latest_classification(classifications, read_instance_record(paths.instance))


def _classification_lines(classification: dict, attempt: Path | None) -> list[str]:
    status = classification["status"]
    lines = ["## Task classification", ""]
    attempt_result = attempt / "agent/result.json" if attempt is not None else None
    if attempt_result is not None and attempt_result.is_file():
        classifier = read_agent_attempt(attempt_result)
        lines += [
            f"Classifier: {_harness_text(classifier)}. Cost: {_cost(attempt / 'cost.json')}.",
            "",
        ]
    if status == "completed":
        if classification["task_nature"] == "out_of_scope":
            lines += [f"Task nature: `{classification['task_nature']}`.", ""]
        else:
            lines += [
                "| Facet | Label |",
                "|---|---|",
                *[
                    f"| {label} | `{_classification_value(classification[key])}` |"
                    for key, label in CLASSIFICATION_LABELS.items()
                ],
                "",
            ]
        lines += [*_blockquote(classification["rationale"]), ""]
    elif status == "missing":
        lines += ["Missing: no classification is saved for this problem.", ""]
    else:
        lines += [f"{status}: {classification['error']}", ""]
    return lines


def _classification_value(value: object) -> str:
    return ", ".join(value) if isinstance(value, list) else str(value)


def _usage_lines(results: EvaluationResult, judge_attempt: AgentAttempt | None) -> list[str]:
    rows = [("Generate", results.agent)]
    if judge_attempt:
        rows.append(("Judge", judge_attempt))
    return [
        "## Usage",
        "",
        "| Stage | Wall time | Input tokens | Cached input tokens | Output tokens |",
        "|---|---:|---:|---:|---:|",
        *[
            f"| {stage} | {_seconds(result.duration_seconds)} | "
            f"{_tokens(result.usage, 'input_tokens')} | "
            f"{_tokens(result.usage, 'cached_input_tokens')} | "
            f"{_tokens(result.usage, 'output_tokens')} |"
            for stage, result in rows
        ],
        "",
    ]


def _seconds(value: float | None) -> str:
    return "—" if value is None else f"{value:.0f} s"


def _tokens(usage: Usage | None, key: str) -> str:
    return "—" if usage is None else f"{getattr(usage, key):,}"


def _cost(path: Path) -> str:
    cost = read_run_cost(path)
    if cost is None:
        return "—"
    if cost.status == "not_measured":
        return "not measured: a model stage does not use OpenRouter"
    if cost.status == "unavailable":
        return f"unavailable: {cost.error}"
    text = f"${cost.cost_usd:.4f}"
    if cost.status == "unsettled":
        text += f" (unsettled after {cost.waited_seconds:g} s)"
    return text


def _file_lines(paths: RunPaths, attempt: Path | None) -> list[str]:
    groups = [
        (
            "Ground truth",
            [
                ("issue", paths.ground_truth / "issue.md"),
                ("fix", paths.ground_truth / "fix.patch"),
            ],
        ),
        ("Build", [("images", paths.build / "images.json")]),
        ("Cost", [("cost", paths.cost)]),
        ("Reference", [("results", paths.reference / "results.json")]),
        (
            "Generate",
            [
                ("target", paths.target),
                ("prompt", paths.prompt),
                ("session", paths.generation / "session.log"),
                ("trace", paths.generation / "trace.jsonl"),
                ("diff", paths.generation / "workspace.diff"),
                ("manifest", paths.submission / "manifest.json"),
            ],
        ),
        (
            "Evaluate",
            [
                ("results", paths.results),
                ("buggy tests", paths.evaluation / "buggy/tests.json"),
                ("buggy log", paths.evaluation / "buggy/output.log"),
                ("golden tests", paths.evaluation / "golden/tests.json"),
                ("golden log", paths.evaluation / "golden/output.log"),
            ],
        ),
        (
            "Judge",
            [
                ("judgment", paths.judge.judgment),
                ("raw", paths.judge.judgment_raw),
                ("prompt", paths.judge.prompt),
                ("rubric", paths.judge.rubric),
                ("workspace", paths.judge.workspace_spec),
                ("trace", paths.judge.agent / "trace.jsonl"),
                ("stderr", paths.judge.agent / "stderr.log"),
            ],
        ),
    ]
    if attempt is not None:
        groups.append(
            (
                "Classification",
                [
                    ("result", attempt / "classification.json"),
                    ("raw", attempt / "classification.raw.txt"),
                    ("rubric", attempt / "inputs/rubric.md"),
                    ("trace", attempt / "agent/trace.jsonl"),
                ],
            )
        )
    lines = ["## Files", ""]
    for group, entries in groups:
        links = [f"[{label}]({_link(paths, path)})" for label, path in entries if path.is_file()]
        if links:
            lines.append(f"- {group}: " + ", ".join(links))
    return [*lines, ""]


def _link(paths: RunPaths, path: Path) -> str:
    return (
        path.relative_to(paths.root).as_posix()
        if path.is_relative_to(paths.root)
        else (path.as_posix())
    )


def _blockquote(text: str) -> list[str]:
    return ["> " + line for line in text.splitlines()]
