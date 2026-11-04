"""The rubrics a model reads and the parsers that read its answer must agree."""

import re
import typing
from pathlib import Path

import pytest

from oracle_lab.container.paths import CLASSIFICATION_ROOT, JUDGE_ROOT
from oracle_lab.contracts.classification import (
    ORACLE_SOURCES,
    InScopeClassification,
    OutOfScopeClassification,
    parse_classification,
)
from oracle_lab.contracts.judgment import FAILURE_MODES, JudgmentLabels, parse_judgment
from oracle_lab.contracts.turn import Answered
from oracle_lab.judge.classification.workspace import classification_evidence
from oracle_lab.paths import AttemptPaths

RUBRICS = Path(__file__).parents[1] / "prompts/judge"
JUDGE_RUBRIC = RUBRICS / "generated-test-evaluation-rubric.md"
CLASSIFICATION_RUBRIC = RUBRICS / "task-classification-rubric.md"
CLASSIFICATION_FIELDS = [
    "task_nature",
    "defect_mechanisms",
    "primary_assertion_target",
    "required_test_setup",
    "oracle_kind",
    "oracle_source",
    "required_test_scope",
    "benchmark_quality",
]


def questions(path: Path) -> dict[str, set[str]]:
    """Each `Question N: `field`` section's bulleted labels. A question is a level-2 or
    level-3 heading; any other heading at those levels ends it."""
    found: dict[str, set[str]] = {}
    field = None
    for line in path.read_text().splitlines():
        if re.match(r"#{2,3} ", line):
            match = re.fullmatch(r"#{2,3} Question \d+: `([a-z_]+)`", line)
            field = match.group(1) if match else None
            if field:
                found[field] = set()
        elif field and (match := re.match(r"- `([a-z_]+)`:", line)):
            found[field].add(match.group(1))
    return found


def allowed(model: type, field: str) -> set[str]:
    """Every answer string a field accepts, through `Answered`, lists, and optionals."""
    values: set[str] = set()

    def walk(annotation) -> None:
        if typing.get_origin(annotation) is typing.Literal:
            values.update(arg for arg in typing.get_args(annotation) if isinstance(arg, str))
        elif isinstance(annotation, type) and issubclass(annotation, Answered):
            walk(annotation.model_fields["answer"].annotation)
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
    defined = re.findall(r"^#### `([a-z_]+)`: (.+)$", JUDGE_RUBRIC.read_text(), re.MULTILINE)
    assert dict(defined) == FAILURE_MODES


def test_the_classification_rubric_groups_oracle_sources_by_kind():
    section = CLASSIFICATION_RUBRIC.read_text().split("`oracle_source`", 1)[1]
    section = section.split("\n## ", 1)[0]
    groups: dict[str, list[str]] = {}
    for line in section.splitlines():
        if match := re.fullmatch(r"For `([a-z_]+)`:", line):
            kind = groups.setdefault(match.group(1), [])
        elif match := re.match(r"- `([a-z_]+)`:", line):
            kind.append(match.group(1))
    assert {kind: tuple(sources) for kind, sources in groups.items()} == ORACLE_SOURCES


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


def workspace_paths(path: Path) -> set[str]:
    """Every absolute `/oracle-*` path a rubric names in backticks."""
    return set(re.findall(r"`(/oracle-[^`]*)`", path.read_text()))


def test_the_judge_rubric_names_paths_in_the_judge_workspace():
    named = workspace_paths(JUDGE_RUBRIC)
    assert named
    assert all(path.startswith(JUDGE_ROOT + "/") for path in named)


def test_the_classification_rubric_names_only_files_the_classifier_receives(tmp_path):
    named = workspace_paths(CLASSIFICATION_RUBRIC)
    exposed = {destination for _, destination in classification_evidence(AttemptPaths(tmp_path))}
    assert named
    assert named <= exposed
    assert all(path.startswith(CLASSIFICATION_ROOT + "/") for path in named)
