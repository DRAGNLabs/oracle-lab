# Oracle Lab task-classification rubric

Use this rubric to classify one Oracle Lab task.

This rubric classifies the task itself: its nature, defect mechanism, the setup
needed to reproduce it, and the evidence available to infer correct behavior.
It does not judge whether a particular generated test is good.

## Task record

The buggy repository checkout is your working directory. The task record is
available at `/oracle-classification/instance.json`. Its top-level fields are:

- `instance_id`: the task identifier.
- `repo`: the source repository.
- `base_commit`: the buggy revision. Your working directory is a checkout
  prepared from this revision.
- `golden_patch`: the gold repair.
- `reference_test_patch`: the developer's reference-test changes.
- `reference_test_ids`: the reference tests expected to fail before the repair
  and pass after it.
- `source`: the dataset, its revision, and its split.
- `original_record`: the dataset's own record, unchanged.

The fields of `original_record` depend on `source.kind`. Both supported datasets
share these:

- `problem_statement`: the issue text given to repair agents.
- `patch` and `test_patch`: the same gold repair and reference-test changes as
  above.
- `FAIL_TO_PASS`: tests expected to fail before the repair and pass after it.
- `PASS_TO_PASS`: existing tests expected to pass both before and after the
  repair.
- `created_at`: the task date.

SWE-bench (`source.kind` is `swebench`) adds:

- `hints_text`: issue comments written before the repair.
- `version` and `environment_setup_commit`: environment metadata.
- `difficulty`: an estimated time to fix, in SWE-bench Verified only.

SWE-rebench V2 (`source.kind` is `swerebench_v2`) adds:

- `pr_description`: the pull request's text.
- `interface`: a description of the repaired code's signatures and expected
  behavior, written from the repair.
- `meta`: pull request metadata. `meta.llm_metadata` holds the dataset's own
  model-generated labels, such as `pr_categories`, `intent_completeness`, and
  `detected_issues`, with that model's reasoning.
- `install_config`, `image_name`, `language`, and `license`: environment and
  repository metadata.

Inspect any of these fields to understand the task and assess benchmark quality.
Treat `meta.llm_metadata` as another rater's opinion: reach your own labels
from the evidence, and do not copy its labels. Question 5 has a stricter
evidence rule stated in that question.

## Procedure

1. Read all task data available to you, including the issue, gold repair,
   reference-test changes, buggy/golden repository checkouts, etc.
2. Answer the questions below.
3. Return the JSON object in the required format. Use the exact label strings.

## Question 1: `task_nature`

What kind of task is this?

Choose one label.

- `behavioral_bug`: existing behavior violates its intended contract.
- `performance_problem`: existing behavior violates a time, memory, or other
  resource requirement.
- `out_of_scope`: anything else, including a new feature, refactoring,
  documentation-only change, or compatibility request without a faulty existing
  behavior.

If the label is `out_of_scope`, stop here and return only `task_nature` and its
justification.

## Question 2: `defect_mechanisms`

What is wrong in the buggy code?

Choose every label that directly describes the defect. You may use the provided
issue to answer. Do not add labels merely because they are downstream effects.

- `control_logic`: wrong branch, condition, ordering, or loop.
- `computation_algorithm`: wrong calculation, transformation, or algorithm.
- `data_state`: wrong initialization, mutation, caching, or state transition.
- `validation_checking`: missing or incorrect validation, bounds, type, or
  precondition check.
- `error_handling`: wrong exception, missing recovery, or swallowed failure.
- `interface_contract`: disagreement between functions, modules, APIs,
  callbacks, or protocols.
- `parsing_serialization`: parsing, formatting, encoding, schema, or round-trip
  error.
- `resource_lifecycle`: incorrect handling of files, connections, cleanup,
  transactions, or context managers.
- `concurrency_timing`: race, ordering, timeout, synchronization, or timing
  error.
- `configuration_environment`: dependency, build, packaging, configuration, or
  platform behavior is wrong.
- `compatibility`: version-specific behavior is wrong.
- `performance_resource_use`: excessive time, memory, calls, or allocation.
- `presentation_output`: diagnostics, messages, rendering, or representation is
  wrong.
- `other`: none of the above fits.

## Question 3: `primary_assertion_target`

What does a minimal valid fail-to-pass test directly inspect to prove that
buggy and repaired behavior differ?

Choose one label. Classify the direct subject of the assertion, not a
downstream consequence.

- `return_value_or_status`: a returned value, response, result object, or
  status is wrong.
- `exception_behavior`: an expected exception or failure status is absent or
  wrong, or an unexpected exception or crash occurs.
- `state_mutation`: in-memory or persistent state is wrong after the operation.
- `produced_output_or_artifact`: rendered text, serialized data, a log entry,
  file, or other produced artifact is wrong.
- `external_interaction`: a call, request, event, command, or other interaction
  with a dependency is missing, incorrect, or in the wrong order.
- `resource_or_performance`: execution exceeds a defined resource limit.
- `nondeterminism`: equivalent repeated executions produce inconsistent
  results.
- `import_build_or_collection`: importing, building, or collecting the code
  fails.

For example, an incorrect HTTP request might eventually return the wrong value.
Use `external_interaction` if the test asserts the request, and
`return_value_or_status` if it asserts only the returned value.

## Question 4: `required_test_setup`

What special setup is needed before the test can exercise the bug?

Choose one label.

- `none`: a direct call or normal use of the feature is enough.
- `special_input`: a particular value, boundary case, invalid value, or unusual
  object is needed.
- `existing_state`: data or object state must be created beforehand.
- `sequence`: multiple operations must occur in a particular order.
- `environment_or_dependency`: a filesystem condition, configuration,
  platform, version, or controlled dependency is needed.
- `concurrency_or_timing`: coordinated execution, timing, or repetition is
  needed.

If several labels fit, choose the most distinctive prerequisite: the condition
without which the test cannot reach the bug. For a duplicate-email test that
needs both an existing user and a duplicate value, choose `existing_state`.

## Question 5: `code_only_oracle_availability`

From the buggy checkout and generation prompt alone, where can the test author
learn what to assert?

Choose one label. Do not use anything from the task record, such as the issue,
repair, reference test, pull request text, or `interface`, as evidence. Do not
use existing tests either. Only consider the buggy repository state without
tests.

- `local_contract`: a signature, type, docstring, error message, or nearby
  production code states the expected behavior.
- `repository_pattern`: analogous production code, callers, or another
  implementation in the repository shows the expected behavior.
- `repository_documentation`: repository documentation states the expected
  behavior.
- `api_convention`: a well-known library or language convention makes the
  expected behavior clear.
- `general_property`: idempotence, preservation, or a relationship between two
  executions gives the test its assertion.
- `domain_knowledge`: knowledge outside the repository is needed to know what
  should happen.
- `unavailable_or_ambiguous`: the agent-visible evidence does not support one
  reasonable assertion.

Choose the strongest source available. A task can be easy to trigger but still
be `unavailable_or_ambiguous` if the buggy checkout does not reveal what the
test should assert.

## Question 6: `required_test_scope`

What is the broadest scope a valid test must exercise?

Choose one label. You may refer to the provided reference test.

- `unit`: one function or object can be tested in isolation.
- `component`: related behavior within one component is required.
- `multi_module`: behavior across multiple repository modules is required.
- `integration`: the test requires a filesystem, process, framework, or similar
  integration boundary.
- `environment_or_platform`: the test depends on a particular platform,
  installed version, or environment.
- `performance_or_concurrency`: the test must measure resource use or exercise
  concurrent behavior.

## Question 7: `benchmark_quality`

Is this task sound enough to use?

Choose one label. Use any available information, including your previous
answers.

- `usable`: the issue, repair, and reference tests describe one coherent task,
  and its execution is reliable.
- `unclear_expected_behavior`: the issue, repair, and reference test changes
  together do not establish one clear expected behavior.
- `reference_test_or_repair_misaligned`: the repair or reference test changes
  contradict the issue, or test behavior unrelated to the issue.
- `unreliable_environment_or_execution`: repeated preparation or execution of
  the task gives inconsistent results, or depends on unavailable services.
- `solution_or_oracle_leakage`: material visible to the test writer—the buggy
  checkout or generation prompt—reveals the gold repair or the required
  assertion.
- `unfair_without_issue`: the correct behavior is only stated in the issue or
  its discussion. Nothing in the buggy code suggests it, so a test writer
  without the issue cannot know what to assert.

## Required output

Return exactly one JSON object with these exact fields and label strings. Do
not wrap it in a Markdown fence and do not include any other text.

`justifications` holds one justification for each question, keyed by the
question's field name. Each justification is at least three sentences and cites
the concrete evidence for that answer: a field of the task record, a code path,
or an execution result. For a `benchmark_quality` label other than `usable`,
name the specific evidence that supports it.

For an in-scope task, return:

```json
{
  "task_nature": "behavioral_bug",
  "defect_mechanisms": ["validation_checking"],
  "primary_assertion_target": "exception_behavior",
  "required_test_setup": "special_input",
  "code_only_oracle_availability": "local_contract",
  "required_test_scope": "unit",
  "benchmark_quality": "usable",
  "justifications": {
    "task_nature": "...",
    "defect_mechanisms": "...",
    "primary_assertion_target": "...",
    "required_test_setup": "...",
    "code_only_oracle_availability": "...",
    "required_test_scope": "...",
    "benchmark_quality": "..."
  }
}
```

For an out-of-scope task, return:

```json
{
  "task_nature": "out_of_scope",
  "justifications": {
    "task_nature": "..."
  }
}
```

## Before you answer

Your final message is the JSON object alone: no text before or after it, and no
Markdown fence.
