"""The rubrics a model reads and the parsers that read its answer must agree."""

import re
import typing
from pathlib import Path

import pytest

from oracle_bench.classification.contracts import (
    InScopeClassification,
    OutOfScopeClassification,
    parse_classification,
)
from oracle_bench.judge.contracts import JudgmentLabels, parse_judgment

RUBRICS = Path(__file__).parents[1] / "prompts/judge"
JUDGE_RUBRIC = RUBRICS / "generated-test-evaluation-rubric.md"
CLASSIFICATION_RUBRIC = RUBRICS / "task-classification-rubric.md"
CLASSIFICATION_FACETS = {
    "Facet A": "task_nature",
    "Facet B": "defect_mechanisms",
    "Facet C": "primary_assertion_target",
    "Facet D": "required_test_setup",
    "Facet E": "code_only_oracle_availability",
    "Facet F": "required_test_scope",
    "Facet G": "benchmark_quality",
}


def sections(path: Path) -> dict[str, set[str]]:
    """Each `## ` heading's bulleted labels, without the `null` placeholder."""
    found: dict[str, set[str]] = {}
    heading = None
    for line in path.read_text().splitlines():
        if line.startswith("## "):
            heading = line[3:]
            found[heading] = set()
        elif heading and (match := re.match(r"- `([a-z_]+)`:", line)):
            found[heading].add(match.group(1))
    return {heading: labels - {"null"} for heading, labels in found.items() if labels}


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
    (labels,) = [
        labels for heading, labels in sections(JUDGE_RUBRIC).items() if f"`{field}`" in heading
    ]
    assert labels == allowed(JudgmentLabels, field)


@pytest.mark.parametrize("facet,field", CLASSIFICATION_FACETS.items())
def test_classification_rubric_labels_are_exactly_what_the_parser_accepts(facet, field):
    (labels,) = [
        labels
        for heading, labels in sections(CLASSIFICATION_RUBRIC).items()
        if heading.startswith(facet + ":")
    ]
    accepted = allowed(InScopeClassification, field)
    if field == "task_nature":
        accepted |= allowed(OutOfScopeClassification, field)
    assert labels == accepted


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
