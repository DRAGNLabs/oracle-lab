import json

import pytest
from fixtures import in_scope_classification, out_of_scope_classification

from oracle_lab.classification.contracts import parse_classification


def test_parses_in_scope_classification():
    result = parse_classification(json.dumps(in_scope_classification()))

    assert result.status == "completed"
    assert result.defect_mechanisms == ["validation_checking"]


def test_parses_out_of_scope_classification_in_fence():
    raw = "```json\n" + json.dumps(out_of_scope_classification()) + "\n```"

    result = parse_classification(raw)

    assert result.status == "completed"
    assert result.task_nature == "out_of_scope"


@pytest.mark.parametrize(
    "value",
    [
        in_scope_classification(defect_mechanisms=[]),
        in_scope_classification(defect_mechanisms=["control_logic", "control_logic"]),
        in_scope_classification(unexpected=True),
        out_of_scope_classification(justifications={"task_nature": ""}),
        out_of_scope_classification(benchmark_quality="usable"),
        in_scope_classification(justifications={"task_nature": "Only one answer is justified."}),
    ],
)
def test_invalid_semantic_output_is_explicit(value):
    result = parse_classification(json.dumps(value))

    assert result.status == "invalid_output"
    assert result.error


def test_rejects_multiple_or_non_object_outputs():
    assert parse_classification("{}\n{}").status == "invalid_output"
    assert parse_classification("[]").status == "invalid_output"
