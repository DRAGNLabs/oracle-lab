# Oracle Lab task-classification rubric

Use this rubric to classify one Oracle Lab task. It is written for either a
human rater or an LLM judge.

Oracle Lab studies a different version of a SWE-bench task. A test writer
receives the buggy repository checkout and a general instruction to write
tests. It does not receive the issue, gold repair, reference test, or existing
tests. The evaluation checks whether a new test fails on the buggy checkout and
passes after the hidden gold repair is applied.

This rubric classifies the SWE-bench task itself: its nature, defect mechanism,
the setup needed to reproduce it, and the evidence available to infer correct
behavior. It does not judge whether a particular generated test is good.

## SWE-bench instance fields

The buggy repository checkout is your working directory. The complete SWE-bench
instance record is available at `/oracle-classification/instance.json`. Its fields
are:

- `instance_id`: the task identifier.
- `repo`: the source repository.
- `issue_id`, `issue_url`, and `pr_url`: links between the task, its issue, and
  its pull request.
- `base_commit`: the buggy revision. The judge also receives a checkout prepared
  from this repository revision.
- `problem_statement`: the issue text used by standard SWE-bench repair agents.
- `patch`: the gold repair.
- `test_patch`: the developer's reference-test changes.
- `FAIL_TO_PASS`: tests expected to fail before the repair and pass after it.
- `PASS_TO_PASS`: existing tests expected to pass both before and after the
  repair.
- `version` and `created_at`: repository-version and task-date metadata.
- `difficulty`: an additional SWE-bench Verified field, when present.

The judge may inspect all of these fields to understand the task and assess
benchmark quality. Facet E has a stricter evidence rule stated in that facet.

## Procedure

1. Use the issue, gold repair, and reference-test changes to identify the
   behavior that a valid test must fail on in the buggy checkout and pass on
   after the repair.
2. You must inspect the buggy repository checkout, including the production
   code relevant to that behavior. Do not classify the task from the instance
   record alone.
3. When deciding Facet E, base the label only on evidence you found in the buggy
   checkout without tests. Do not use the issue, repair, reference test, or
   existing tests for that facet.
4. Classify the task using the facets below.
5. Return the JSON object in the required format. Use the exact label strings.

## Facet A: In-scope task

Choose one label that classifies the kind of task this issue is.

- `behavioral_bug`: existing behavior violates its intended contract.
- `performance_problem`: existing behavior violates a time, memory, or other
  resource requirement.
- `out_of_scope`: anything else, including a new feature, refactoring,
  documentation-only change, or compatibility request without a faulty existing
  behavior.

If the label is `out_of_scope`, stop here and return only `task_nature` and a
short `rationale`.

## Facet B: Defect mechanism

Choose every label that directly describes what is wrong in the buggy code. You may use the provided issue to answer.
Do not add labels merely because they are downstream effects.

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

## Facet C: Primary assertion target

Choose one label that describes the primary assertion target. Ask: **what does a minimal valid fail-to-pass test directly inspect to prove that buggy and repaired behavior differ?** Classify the direct subject of the assertion, not a downstream consequence.

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

## Facet D: Required test setup

Choose one label that describes the setup requires to replicate the issue.
Ask: **what special setup is needed before the test can exercise the bug?**

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

## Facet E: Code-only oracle availability

Choose one label that describes where the test oracle can be ascertained.
Ask: **from the buggy checkout and generation prompt alone,
where can the test author learn what to assert?**

NOTE: Do not use the issue, repair, reference test, or existing tests as
evidence. Only consider the buggy repository state without tests.

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

## Facet F: Required test scope

Choose one label: the broadest scope a valid test must exercise. You may refer to the
provided golden test.

- `unit`: one function or object can be tested in isolation.
- `component`: related behavior within one component is required.
- `multi_module`: behavior across multiple repository modules is required.
- `integration`: the test requires a filesystem, process, framework, or similar
  integration boundary.
- `environment_or_platform`: the test depends on a particular platform,
  installed version, or environment.
- `performance_or_concurrency`: the test must measure resource use or exercise
  concurrent behavior.

## Facet G: Benchmark-quality screen

Choose one label that describes the quality of the provided SWE-Bench instance.
Use the `problem_statement`, `patch`, `test_patch`, `FAIL_TO_PASS`, and
`PASS_TO_PASS` fields to judge whether the issue, repair, and reference tests
describe one coherent task. Use the buggy checkout and generation prompt only
for the two code-only labels below.

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
- `not_reasonably_testable_from_code_only_context`: after using the issue,
  repair, and reference tests to establish the target behavior, the buggy
  checkout and generation prompt give no reasonable basis for writing a valid
  test.

In the rationale, name the specific field, code path, or execution result that
supports a label other than `usable`.

## Required output

For an in-scope task, return JSON in this form. Keep `rationale` to one or two
sentences and cite the concrete evidence that drove the difficult judgment.

```json
{
  "task_nature": "behavioral_bug",
  "defect_mechanisms": ["validation_checking"],
  "primary_assertion_target": "exception_behavior",
  "required_test_setup": "special_input",
  "code_only_oracle_availability": "local_contract",
  "required_test_scope": "unit",
  "benchmark_quality": "usable",
  "rationale": "..."
}
```

For an out-of-scope task, return:

```json
{
  "task_nature": "out_of_scope",
  "rationale": "..."
}
```
