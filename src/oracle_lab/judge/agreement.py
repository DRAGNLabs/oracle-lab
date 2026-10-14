"""Offline agreement analysis for LLM and blinded human judgments."""

from __future__ import annotations

import hashlib
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

from oracle_lab.io import digest, read_json, write_json
from oracle_lab.judge.agreement_report import render_agreement_report
from oracle_lab.judge.contracts import (
    JUDGMENT_LABEL_KEYS,
    CompletedJudgment,
    HumanWorkspaceMetadata,
    LlmJudgeIdentity,
    Rating,
    Ratings,
    RunEvaluation,
    RunProvenance,
    RunRatings,
    validate_judgment,
)
from oracle_lab.paths import RunPaths
from oracle_lab.results import read_evaluation_result

# Strata taken directly from the paired matrix.
MATRIX_STRATA = ("fail_on_buggy_pass_on_golden", "fail_on_both", "pass_on_both")
# Strata derived from execution state and the LLM's issue-tested answer.
DERIVED_STRATA = ("invalid", "unrelated")
CALIBRATION_STRATA = MATRIX_STRATA + DERIVED_STRATA


def analyze_agreement(batch_dir: Path, *, sample_per_stratum: int = 8) -> Path:
    """Analyze saved ratings for one batch without executing code or calling a model."""
    if sample_per_stratum < 1:
        raise ValueError("sample_per_stratum must be at least 1")
    batch_dir = batch_dir.resolve()
    resolved_path = batch_dir / "batch.resolved.json"
    if not resolved_path.is_file():
        raise FileNotFoundError(f"Batch manifest does not exist: {resolved_path}")

    manifest = read_json(resolved_path)
    jobs = manifest.get("jobs") if isinstance(manifest, dict) else None
    if not isinstance(jobs, list) or not all(isinstance(job, dict) for job in jobs):
        raise ValueError("batch.resolved.json must contain a jobs array")

    # 1. Collect every rating the batch produced. Unreadable evidence counts as
    #    absent throughout, so one damaged run cannot end the analysis.
    instances: list[RunRatings] = []
    for job in jobs:
        if not job.get("run_dir"):
            continue
        run_dir = Path(job["run_dir"]).resolve()
        paths = RunPaths.open(run_dir)
        result = None
        if paths.results.is_file():
            try:
                result = read_evaluation_result(paths.results)
            except (OSError, ValueError):
                result = None
        humans = {}
        if paths.judge.human.judgments.is_dir():
            for directory in sorted(paths.judge.human.judgments.iterdir()):
                if directory.is_dir() and not directory.is_symlink():
                    humans[directory.name] = _load_rating(directory / "judgment.json")
        try:
            attempt = read_json(paths.judge.result) if paths.judge.result.is_file() else {}
        except (OSError, ValueError):
            attempt = {}
        instances.append(
            RunRatings(
                run_dir=str(run_dir),
                instance_id=result.instance_id if result else run_dir.name,
                matrix={key: cell.count for key, cell in result.matrix.items()} if result else {},
                evaluation=RunEvaluation(
                    submission_compliant=result.submission_compliant if result else None,
                    buggy_status=result.buggy_status if result else None,
                    golden_status=result.golden_status if result else None,
                ),
                ratings=Ratings(llm=_load_rating(paths.judge.judgment), humans=humans),
                provenance=RunProvenance(
                    rubric_sha256=_optional_digest(paths.judge.rubric),
                    prompt_sha256=_optional_digest(paths.judge.prompt),
                    # Anything the attempt did not record as text stays unknown.
                    llm=LlmJudgeIdentity.model_validate(
                        {
                            key: value
                            for key in ("harness", "provider", "model", "harness_version")
                            if isinstance(value := attempt.get(key), str)
                        }
                    ),
                ),
            )
        )

    # 2. Everyone who rated anything is expected everywhere, so a rater who skipped
    #    a run is recorded as missing rather than quietly dropping out of the counts.
    raters = sorted({rater for instance in instances for rater in instance.ratings.humans})
    for instance in instances:
        for rater in raters:
            instance.ratings.humans.setdefault(rater, Rating(status="missing"))

    # 3. Compare every pair of humans, then every human against the LLM.
    comparisons = {
        "human_human": _comparison_group(
            instances,
            [(f"human:{left}", f"human:{right}") for left, right in combinations(raters, 2)],
        ),
        "human_llm": _comparison_group(instances, [(f"human:{rater}", "llm") for rater in raters]),
    }
    calibration = _calibration_sample(instances, sample_per_stratum)
    summary = {
        "batch": str(batch_dir),
        "runs": len(instances),
        "human_raters": raters,
        "comparisons": comparisons,
        "ratings_by_run": [instance.model_dump(exclude_none=True) for instance in instances],
        "provenance_versions": _provenance_versions(instances),
    }
    output = batch_dir / "agreement"
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "summary.json", summary)
    write_json(output / "calibration-sample.json", calibration)
    (output / "report.md").write_text(render_agreement_report(summary, calibration))
    return output / "report.md"


def _load_rating(path: Path) -> Rating:
    if not path.is_file():
        return Rating(status="missing")
    try:
        judgment = validate_judgment(read_json(path))
    except (OSError, ValueError):
        return Rating(status="invalid_artifact")
    if not isinstance(judgment, CompletedJudgment):
        return Rating(status=judgment.status)

    # Only human ratings are saved beside a provenance file.
    provenance = None
    provenance_path = path.parent / "provenance.json"
    if provenance_path.is_file():
        try:
            provenance = HumanWorkspaceMetadata.model_validate(read_json(provenance_path))
        except (OSError, ValueError):
            provenance = None
    return Rating(
        status="completed",
        labels={key: getattr(judgment, key) for key in JUDGMENT_LABEL_KEYS},
        provenance=provenance,
    )


def _optional_digest(path: Path) -> str | None:
    return digest(path.read_bytes()) if path.is_file() else None


def _provenance_versions(instances: list[RunRatings]) -> dict:
    rubric_hashes = Counter()
    prompt_hashes = Counter()
    models = Counter()
    human_workspaces = Counter()
    for instance in instances:
        provenance = instance.provenance
        if provenance.rubric_sha256:
            rubric_hashes[provenance.rubric_sha256] += 1
        if provenance.prompt_sha256:
            prompt_hashes[provenance.prompt_sha256] += 1
        identity = provenance.llm
        if any(identity.model_dump().values()):
            model_key = "/".join(
                str(getattr(identity, key) or "unknown")
                for key in ("harness", "provider", "model", "harness_version")
            )
            models[model_key] += 1
        for rating in instance.ratings.humans.values():
            if rating.provenance:
                human_workspaces[rating.provenance.workspace_spec_sha256] += 1
    return {
        "rubric_sha256": dict(sorted(rubric_hashes.items())),
        "llm_prompt_sha256": dict(sorted(prompt_hashes.items())),
        "llm_models": dict(sorted(models.items())),
        "human_workspace_spec_sha256": dict(sorted(human_workspaces.items())),
    }


def _comparison_group(instances: list[RunRatings], pairs: list[tuple[str, str]]) -> dict:
    """Score one set of rater pairs across every run, pooled and pair by pair."""
    observations = []
    by_pair_observations = defaultdict(list)
    for instance in instances:
        # "llm" and "human:<id>" are the rater labels the saved report uses.
        rated = {"llm": instance.ratings.llm}
        rated.update(
            (f"human:{rater}", rating) for rater, rating in instance.ratings.humans.items()
        )
        for left, right in pairs:
            if rated[left].status != "completed" or rated[right].status != "completed":
                continue
            observation = {
                "instance_id": instance.instance_id,
                "run_dir": instance.run_dir,
                "left_rater": left,
                "right_rater": right,
                "left": rated[left].labels,
                "right": rated[right].labels,
            }
            observations.append(observation)
            by_pair_observations[f"{left}|{right}"].append(observation)

    by_pair = {
        pair: {
            facet: _agreement_for_facet(pair_observations, facet) for facet in JUDGMENT_LABEL_KEYS
        }
        for pair, pair_observations in sorted(by_pair_observations.items())
    }
    possible = len(instances) * len(pairs)
    return {
        "possible_pairs": possible,
        "available_pairs": len(observations),
        "missing_pairs": possible - len(observations),
        "facets": _summarize_pairwise_agreement(observations, by_pair),
        "by_pair": by_pair,
    }


def _summarize_pairwise_agreement(observations: list[dict], by_pair: dict) -> dict:
    """Pool raw outcomes while keeping chance correction specific to each rater pair.

    Cohen's kappa is defined for one pair of raters, so a pooled kappa over
    mixed pairs would not be interpretable. Report the observation-weighted mean
    of the per-pair kappas instead, alongside each pair's own value.
    """
    summary = {}
    for facet in JUDGMENT_LABEL_KEYS:
        pooled = _agreement_for_facet(observations, facet)
        weighted = [
            (values[facet]["cohen_kappa"], values[facet]["observations"])
            for values in by_pair.values()
            if values[facet]["cohen_kappa"] is not None
        ]
        summary[facet] = {
            key: value
            for key, value in pooled.items()
            if key not in {"cohen_kappa", "expected_agreement"}
        }
        summary[facet]["mean_pairwise_cohen_kappa"] = (
            sum(kappa * count for kappa, count in weighted) / sum(count for _, count in weighted)
            if weighted
            else None
        )
        summary[facet]["pairwise_cohen_kappa"] = {
            pair: values[facet]["cohen_kappa"] for pair, values in by_pair.items()
        }
    return summary


def _agreement_for_facet(observations: list[dict], facet: str) -> dict:
    confusion = defaultdict(Counter)
    left_counts = Counter()
    right_counts = Counter()
    disagreements = []
    matches = 0
    for observation in observations:
        # Keep JSON null distinct from every rubric label in confusion tables.
        left = observation["left"][facet] if observation["left"][facet] is not None else "null"
        right = observation["right"][facet] if observation["right"][facet] is not None else "null"
        confusion[left][right] += 1
        left_counts[left] += 1
        right_counts[right] += 1
        if left == right:
            matches += 1
        else:
            disagreements.append(
                {
                    "instance_id": observation["instance_id"],
                    "run_dir": observation["run_dir"],
                    "left_rater": observation["left_rater"],
                    "right_rater": observation["right_rater"],
                    "left_label": left,
                    "right_label": right,
                }
            )

    count = len(observations)
    raw_agreement = None
    expected = None
    kappa = None
    if count:
        raw_agreement = matches / count
        labels = set(left_counts) | set(right_counts)
        expected = sum(left_counts[label] * right_counts[label] for label in labels) / count**2
        # If agreement was certain anyway, kappa means nothing, so leave it None.
        if expected < 1:
            kappa = (raw_agreement - expected) / (1 - expected)
    return {
        "observations": count,
        "matches": matches,
        "raw_agreement": raw_agreement,
        "expected_agreement": expected,
        "cohen_kappa": kappa,
        "confusion": {
            label: dict(sorted(counts.items())) for label, counts in sorted(confusion.items())
        },
        "disagreements": disagreements,
    }


def _calibration_sample(instances: list[RunRatings], sample_per_stratum: int) -> dict:
    candidates = {stratum: [] for stratum in CALIBRATION_STRATA}
    for instance in instances:
        matrix = instance.matrix
        for stratum in MATRIX_STRATA:
            if matrix.get(stratum, 0) > 0:
                candidates[stratum].append(instance)
        issue_tested = (instance.ratings.llm.labels or {}).get("tests_issue")
        evaluation = instance.evaluation
        if (
            evaluation.submission_compliant is False
            or evaluation.buggy_status != "completed"
            or evaluation.golden_status != "completed"
        ):
            candidates["invalid"].append(instance)
        if issue_tested == "no":
            candidates["unrelated"].append(instance)

    strata = {}
    selected_runs = {}
    for stratum, available in candidates.items():
        ordered = sorted(available, key=_sample_order)
        selected = ordered[:sample_per_stratum]
        strata[stratum] = {
            "available": len(available),
            "selected": [instance.run_dir for instance in selected],
        }
        selected_runs.update((instance.run_dir, instance) for instance in selected)

    readiness = []
    for run_dir, instance in sorted(selected_runs.items()):
        completed = sorted(
            rater
            for rater, rating in instance.ratings.humans.items()
            if rating.status == "completed"
        )
        readiness.append(
            {
                "run_dir": run_dir,
                "instance_id": instance.instance_id,
                "completed_human_raters": completed,
                "ready": len(completed) >= 2,
            }
        )
    return {
        "sample_per_stratum": sample_per_stratum,
        "strata": strata,
        "unique_selected_runs": len(selected_runs),
        "readiness": readiness,
        "ready_for_calibration": bool(readiness) and all(item["ready"] for item in readiness),
    }


def _sample_order(instance: RunRatings) -> str:
    # Keyed on the run's own directory name, not its full path, so moving a batch
    # does not reshuffle the sample drawn from identical evidence.
    value = f"{instance.instance_id}\0{Path(instance.run_dir).name}"
    return hashlib.sha256(value.encode()).hexdigest()
