import json

import pytest

from oracle_bench.classification.contracts import parse_classification


def valid_classification(**changes):
    value = {
        "task_nature": "behavioral_bug",
        "defect_mechanisms": ["validation_checking"],
        "primary_assertion_target": "exception_behavior",
        "required_test_setup": "special_input",
        "code_only_oracle_availability": "local_contract",
        "required_test_scope": "unit",
        "benchmark_quality": "usable",
        "rationale": "The docstring and repair identify the missing validation.",
    }
    value.update(changes)
    return value


def test_parses_in_scope_classification():
    result = parse_classification(json.dumps(valid_classification()))

    assert result.status == "completed"
    assert result.defect_mechanisms == ["validation_checking"]


def test_parses_out_of_scope_classification_in_fence():
    raw = '```json\n{"task_nature":"out_of_scope","rationale":"Adds a feature."}\n```'

    result = parse_classification(raw)

    assert result.status == "completed"
    assert result.task_nature == "out_of_scope"


@pytest.mark.parametrize(
    "value",
    [
        valid_classification(defect_mechanisms=[]),
        valid_classification(defect_mechanisms=["control_logic", "control_logic"]),
        valid_classification(unexpected=True),
        {"task_nature": "out_of_scope", "rationale": "", "benchmark_quality": "usable"},
    ],
)
def test_invalid_semantic_output_is_explicit(value):
    result = parse_classification(json.dumps(value))

    assert result.status == "invalid_output"
    assert result.error


def test_rejects_multiple_or_non_object_outputs():
    assert parse_classification("{}\n{}").status == "invalid_output"
    assert parse_classification("[]").status == "invalid_output"
