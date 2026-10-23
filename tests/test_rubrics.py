"""The rubrics a model reads and the parsers that read its answer must agree."""

import re
import typing
from pathlib import Path

import pytest

from oracle_lab.classification.contracts import (
    InScopeClassification,
    OutOfScopeClassification,
    parse_classification,
)
from oracle_lab.judge.contracts import FAILURE_MODES, JudgmentLabels, parse_judgment

RUBRICS = Path(__file__).parents[1] / "prompts/judge"
JUDGE_RUBRIC = RUBRICS / "generated-test-evaluation-rubric.md"
CLASSIFICATION_RUBRIC = RUBRICS / "task-classification-rubric.md"
CLASSIFICATION_FIELDS = [
    "task_nature",
    "defect_mechanisms",
    "primary_assertion_target",
    "required_test_setup",
    "code_only_oracle_availability",
    "required_test_scope",
    "benchmark_quality",
]


def questions(path: Path) -> dict[str, set[str]]:
    """Each `## Question N: `field`` section's bulleted labels, without `null`."""
    found: dict[str, set[str]] = {}
    field = None
    for line in path.read_text().splitlines():
        if line.startswith("## "):
            match = re.fullmatch(r"## Question \d+: `([a-z_]+)`", line)
            field = match.group(1) if match else None
            if field:
                found[field] = set()
        elif field and (match := re.match(r"- `([a-z_]+)`:", line)):
            found[field].add(match.group(1))
    return {field: labels - {"null"} for field, labels in found.items()}


def allowed(model: type, field: str) -> set[str]:
    """Every string a field's `Literal` types accept, through lists and optionals."""
    values: set[str] = set()

    def walk(annotation) -> None:
        if typing.get_origin(annotation) is typing.Literal:
            values.update(arg for arg in typing.get_args(annotation) if isinstance(arg, str))
        else:
            for arg in typing.get_args(annotation):
                walk(arg)

    walk(model.model_fields[field].annotation)
    return values


@pytest.mark.parametrize(
    "field", ["tests_issue", "attempt_detail", "no_attempt_reason", "cheating"]
)
def test_judge_rubric_labels_are_exactly_what_the_parser_accepts(field):
    assert questions(JUDGE_RUBRIC)[field] == allowed(JudgmentLabels, field)


@pytest.mark.parametrize("field", CLASSIFICATION_FIELDS)
def test_classification_rubric_labels_are_exactly_what_the_parser_accepts(field):
    labels = questions(CLASSIFICATION_RUBRIC)[field]
    accepted = allowed(InScopeClassification, field)
    if field == "task_nature":
        accepted |= allowed(OutOfScopeClassification, field)
    assert labels == accepted


def test_both_rubrics_ask_exactly_the_fields_their_parsers_read():
    assert list(questions(JUDGE_RUBRIC)) == [
        "tests_issue",
        "attempt_detail",
        "no_attempt_reason",
        "cheating",
        "failure_modes",
        "added_failure_modes",
    ]
    assert list(questions(CLASSIFICATION_RUBRIC)) == CLASSIFICATION_FIELDS


def test_the_judge_rubric_defines_exactly_the_start_list():
    defined = re.findall(r"^### `([a-z_]+)`: (.+)$", JUDGE_RUBRIC.read_text(), re.MULTILINE)
    assert dict(defined) == FAILURE_MODES


def output_examples(path: Path) -> list[str]:
    after_heading = path.read_text().split("## Required output", 1)[1]
    return re.findall(r"```json\n(.*?)\n```", after_heading, re.DOTALL)


def test_the_judge_rubric_output_example_parses():
    (example,) = output_examples(JUDGE_RUBRIC)
    assert parse_judgment(example).status == "completed"


def test_every_classification_rubric_output_example_parses():
    examples = output_examples(CLASSIFICATION_RUBRIC)
    assert len(examples) == 2
    for example in examples:
        assert parse_classification(example).status == "completed"
