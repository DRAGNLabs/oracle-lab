"""Interpret one version's execution and compare both versions of a submission."""

from pathlib import Path

from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.run import (
    FAILURE_KINDS,
    MATRIX_CELLS,
    VERSIONS,
    MatrixCell,
    MatrixCellName,
    PairedOutcome,
    PairedResult,
    VersionResult,
)
from oracle_lab.io import read_json

CELL_BY_OUTCOMES = {cell.outcomes: cell for cell in MATRIX_CELLS}


def incomplete_result(directory: Path, status: str, reason: str) -> VersionResult:
    """Record why a version produced no comparable outcomes, keeping partial evidence.

    This runs on failure paths, so unreadable partial evidence is set aside rather
    than raised: the runner's own file is kept as `tests.raw.json`.
    """
    path = directory / "tests.json"
    result = _partial_result(path)
    if result is None:
        path.replace(directory / "tests.raw.json")
        result = VersionResult(status="running")
    result = result.model_copy(update={"status": status, "error": reason})
    write_result(path, result)
    return result


def pair_results(buggy: VersionResult, golden: VersionResult) -> PairedResult:
    """Compare both executions, keeping non-binary outcomes out of the matrix."""
    both_completed = buggy.status == golden.status == "completed"
    cells: dict[MatrixCellName, list[str]] = {name: [] for name in MATRIX_CELLS}
    failure_kinds: dict[str, dict[str, int]] = {
        version: dict.fromkeys(FAILURE_KINDS, 0) for version in VERSIONS
    }
    rows = []
    for test_id in sorted(buggy.tests.keys() | golden.tests.keys()):
        cases = {"buggy": buggy.tests.get(test_id), "golden": golden.tests.get(test_id)}
        outcomes = {
            version: case.outcome if case else "not_collected" for version, case in cases.items()
        }
        failures = {}
        for version, case in cases.items():
            if outcomes[version] != "fail":
                continue
            failure = (case.failure if case else None) or {"kind": "unknown"}
            failures[version] = failure
            kind = failure.get("kind", "unknown")
            failure_kinds[version][kind if kind in FAILURE_KINDS else "unknown"] += 1
        # Only completed executions on both versions can earn a matrix cell.
        cell = (
            CELL_BY_OUTCOMES.get((outcomes["buggy"], outcomes["golden"]))
            if both_completed
            else None
        )
        if cell:
            cells[cell].append(test_id)
        rows.append(
            PairedOutcome(
                test_id=test_id,
                buggy=outcomes["buggy"],
                golden=outcomes["golden"],
                cell=cell,
                buggy_failure=failures.get("buggy"),
                golden_failure=failures.get("golden"),
            )
        )
    return PairedResult(
        matrix={name: MatrixCell(count=len(ids), test_ids=ids) for name, ids in cells.items()},
        other_outcomes=[row for row in rows if row.cell is None],
        tests=rows,
        failure_kinds=failure_kinds,
        buggy_status=buggy.status,
        golden_status=golden.status,
    )


def _partial_result(path: Path) -> VersionResult | None:
    """The saved result, a blank one when there is none, or None when it is unreadable."""
    if not path.exists():
        return VersionResult(status="running")
    try:
        return VersionResult.model_validate(read_json(path))
    except (OSError, ValueError):
        return None
