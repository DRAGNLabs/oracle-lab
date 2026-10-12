"""The detection rule: what counts as a run whose tests found the bug."""

import pytest
from fixtures import detecting_result, evaluation_result

from oracle_bench.results import EvaluationResult


def test_a_compliant_completed_run_with_a_distinguishing_test_is_detected():
    assert EvaluationResult.model_validate(detecting_result()).detected


@pytest.mark.parametrize(
    "changes",
    [
        {"submission_compliant": False, "forbidden_changes": ["package/core.py"]},
        {"buggy_status": "timeout"},
        {"golden_status": "collection_error"},
    ],
    ids=["non-compliant", "buggy-incomplete", "golden-incomplete"],
)
def test_a_distinguishing_test_is_not_a_detection_without_compliance_and_completion(changes):
    result = EvaluationResult.model_validate(detecting_result(**changes))

    assert result.matrix["fail_on_buggy_pass_on_golden"].count == 1
    assert not result.detected


def test_a_compliant_completed_run_without_a_distinguishing_test_is_not_detected():
    assert not EvaluationResult.model_validate(evaluation_result()).detected
