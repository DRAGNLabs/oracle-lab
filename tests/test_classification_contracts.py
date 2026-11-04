import json
import typing

import pytest
from fixtures import in_scope_classification, out_of_scope_classification

from oracle_lab.contracts.classification import (
    ORACLE_SOURCES,
    OracleKind,
    OracleSource,
    parse_classification,
)


def test_parses_in_scope_classification():
    result = parse_classification(json.dumps(in_scope_classification()))

    assert result.status == "completed"
    assert result.defect_mechanisms.answer == ["validation_checking"]


def test_parses_out_of_scope_classification_in_fence():
    raw = "```json\n" + json.dumps(out_of_scope_classification()) + "\n```"

    result = parse_classification(raw)

    assert result.status == "completed"
    assert result.task_nature.answer == "out_of_scope"


@pytest.mark.parametrize(
    "value",
    [
        in_scope_classification(defect_mechanisms=[]),
        in_scope_classification(defect_mechanisms=["control_logic", "control_logic"]),
        in_scope_classification(unexpected=True),
        out_of_scope_classification(justifications={"task_nature": ""}),
        out_of_scope_classification(benchmark_quality="usable"),
        in_scope_classification(justifications={"task_nature": "Only one answer is justified."}),
        in_scope_classification(oracle_kind="implicit", oracle_source="docstring_or_comment"),
        in_scope_classification(oracle_kind="human", oracle_source="code_logic"),
    ],
)
def test_invalid_semantic_output_is_explicit(value):
    result = parse_classification(json.dumps(value))

    assert result.status == "invalid_output"
    assert result.error


@pytest.mark.parametrize(
    "kind,source",
    [(kind, source) for kind, sources in ORACLE_SOURCES.items() for source in sources],
)
def test_every_oracle_source_is_accepted_under_its_kind(kind, source):
    value = in_scope_classification(oracle_kind=kind, oracle_source=source)

    assert parse_classification(json.dumps(value)).status == "completed"


def test_every_oracle_source_belongs_to_exactly_one_kind():
    sources = [source for group in ORACLE_SOURCES.values() for source in group]
    assert set(ORACLE_SOURCES) == set(typing.get_args(OracleKind))
    assert sorted(sources) == sorted(typing.get_args(OracleSource))


def test_rejects_multiple_or_non_object_outputs():
    assert parse_classification("{}\n{}").status == "invalid_output"
    assert parse_classification("[]").status == "invalid_output"
