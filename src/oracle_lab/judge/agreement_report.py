"""Render the offline agreement analysis as Markdown."""

from __future__ import annotations


def render_agreement_report(summary: dict, calibration: dict) -> str:
    lines = [
        "# Judge agreement report",
        "",
        f"Runs: **{summary['runs']}**. Human raters: **{len(summary['human_raters'])}**.",
        "",
        "Agreement excludes pairs where either rating is missing or invalid. Missing pairs are "
        "reported separately and never enter a denominator. The headline Cohen's κ is the "
        "observation-weighted mean of the separately calculated rater-pair kappas.",
        "",
    ]
    for name, title in (
        ("human_human", "Human–human agreement"),
        ("human_llm", "Human–LLM agreement"),
    ):
        lines += _comparison_lines(summary["comparisons"][name], title)
    lines += _provenance_lines(summary["provenance_versions"])
    lines += _calibration_lines(calibration)
    lines += [
        "",
        "The machine-readable report contains every confusion matrix, disagreement, missing "
        "rating, rubric hash, prompt hash, and judge model identity.",
        "",
        "See [summary.json](summary.json) and [calibration-sample.json](calibration-sample.json).",
        "",
    ]
    return "\n".join(lines)


def _comparison_lines(group: dict, title: str) -> list[str]:
    lines = [
        f"## {title}",
        "",
        f"Available rating pairs: **{group['available_pairs']}** of "
        f"**{group['possible_pairs']}**; missing: **{group['missing_pairs']}**.",
        "",
        "| Facet | Observations | Raw agreement | Mean pairwise Cohen's κ |",
        "|---|---:|---:|---:|",
    ]
    for facet, result in group["facets"].items():
        lines.append(
            f"| `{facet}` | {result['observations']} | "
            f"{_format_number(result['raw_agreement'])} | "
            f"{_format_number(result['mean_pairwise_cohen_kappa'])} |"
        )
    lines.append("")
    return lines


def _provenance_lines(provenance: dict) -> list[str]:
    return [
        "## Rating provenance",
        "",
        "LLM models: " + _format_counts(provenance["llm_models"]) + ".",
        "",
        "Rubric hashes: " + _format_counts(provenance["rubric_sha256"]) + ".",
        "",
        "LLM prompt hashes: " + _format_counts(provenance["llm_prompt_sha256"]) + ".",
        "",
    ]


def _calibration_lines(calibration: dict) -> list[str]:
    lines = [
        "## Calibration sample",
        "",
        f"Unique selected runs: **{calibration['unique_selected_runs']}**. Ready with at "
        f"least two human ratings per run: "
        f"**{'yes' if calibration['ready_for_calibration'] else 'no'}**.",
        "",
        "| Stratum | Available | Selected |",
        "|---|---:|---:|",
    ]
    for stratum, values in calibration["strata"].items():
        lines.append(f"| `{stratum}` | {values['available']} | {len(values['selected'])} |")
    return lines


def _format_number(value: float | None) -> str:
    return "—" if value is None else f"{value:.3f}"


def _format_counts(values: dict[str, int]) -> str:
    if not values:
        return "none recorded"
    return ", ".join(f"`{value}` ({count})" for value, count in values.items())
