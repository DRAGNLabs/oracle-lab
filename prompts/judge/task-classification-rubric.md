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

## How to answer

1. Read the task record and the buggy checkout.
2. Answer each question in order. For each one, choose the label, then write its
   justification.
3. Return the JSON object in the required format, using the exact label strings.

A justification is at least three sentences. It cites the concrete evidence for
the answer, such as a field of the task record, a code path, or an execution
result.

## Question 1: `task_nature`

What kind of task is this?

Choose one label.

- `behavioral_bug`: existing behavior violates its intended contract.
- `performance_problem`: existing behavior violates a time, memory, or other
  resource requirement.
- `out_of_scope`: anything else, including a new feature, refactoring,
  documentation-only change, or compatibility request without a faulty existing
  behavior.

Justify your answer.

If the label is `out_of_scope`, stop here and return only `task_nature`.

## Question 2: `defect_mechanisms`

What is wrong in the buggy code?

Choose every label that directly describes the defect, not its downstream
effects.

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

Justify your answer.

## Question 3: `primary_assertion_target`

What does a minimal valid fail-to-pass test directly inspect to prove that
buggy and repaired behavior differ?

Choose one label for the direct subject of the assertion, not a downstream
consequence.

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

Justify your answer.

## Question 4: `required_test_setup`

What special setup is needed before the test can exercise the bug?

Choose one label: the condition without which the test cannot reach the bug.

- `none`: a direct call or normal use of the feature is enough.
- `special_input`: a particular value, boundary case, invalid value, or unusual
  object is needed.
- `existing_state`: data or object state must be created beforehand.
- `sequence`: multiple operations must occur in a particular order.
- `environment_or_dependency`: a filesystem condition, configuration,
  platform, version, or controlled dependency is needed.
- `concurrency_or_timing`: coordinated execution, timing, or repetition is
  needed.

Justify your answer.

## Question 5: `oracle_kind`

From the buggy checkout and generation prompt alone, what kind of test oracle
can a test use to detect the bug? A test oracle is what tells the test the
correct outcome.

Choose one label. If more than one fits, choose the first in this order.

- `implicit`: the bug crashes, raises an uncaught exception, or hangs, so
  triggering it is enough to detect it.
- `specified`: the repository or a specification it follows states the correct
  behavior.
- `derived`: nothing states the correct behavior, but it can be reasoned out from
  the code.
- `human`: the repository does not settle the correct behavior; it takes the
  maintainer's intent or outside knowledge.

Justify your answer.

## Question 6: `oracle_source`

Where exactly does that oracle come from?

Choose one label listed under your `oracle_kind`.

For `implicit`:

- `crash_or_exception`: the bug raises an uncaught exception or crashes.
- `hang_or_resource`: the bug hangs, times out, or exhausts a resource.

For `specified`:

- `docstring_or_comment`: a docstring or comment states the behavior.
- `signature_or_type`: a signature, type hint, or declared interface states it.
- `repository_documentation`: the repository's documentation states it.
- `external_specification`: a standard, protocol, or library documentation the
  code follows states it.

For `derived`:

- `code_logic`: the surrounding logic implies it, such as other branches,
  invariants the function keeps, or how its result is used.
- `analogous_code`: a sibling implementation, caller, or similar code in the
  repository shows it.
- `property_relation`: a relation between executions implies it, such as a
  round trip, idempotence, or equivalence.
- `api_convention`: a well-known language or library convention implies it.

For `human`:

- `domain_knowledge`: knowledge outside the repository is needed.
- `intent_only`: only the issue or the maintainer's intent settles it.

Justify your answer.

## Question 7: `required_test_scope`

What is the broadest scope a valid test must exercise?

Choose one label.

- `unit`: one function or object can be tested in isolation.
- `component`: related behavior within one component is required.
- `multi_module`: behavior across multiple repository modules is required.
- `integration`: the test requires a filesystem, process, framework, or similar
  integration boundary.
- `environment_or_platform`: the test depends on a particular platform,
  installed version, or environment.
- `performance_or_concurrency`: the test must measure resource use or exercise
  concurrent behavior.

Justify your answer.

## Question 8: `benchmark_quality`

Is this task sound enough to use?

Choose one label.

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

Justify your answer.

## Required output

Return exactly one JSON object with these exact fields and label strings. Each
question is an object holding its `answer` and its `justification`. Do not wrap
it in a Markdown fence and do not include any other text.

For an in-scope task, return:

```json
{
  "task_nature": { "answer": "behavioral_bug", "justification": "..." },
  "defect_mechanisms": {
    "answer": ["validation_checking"],
    "justification": "..."
  },
  "primary_assertion_target": {
    "answer": "exception_behavior",
    "justification": "..."
  },
  "required_test_setup": { "answer": "special_input", "justification": "..." },
  "oracle_kind": { "answer": "specified", "justification": "..." },
  "oracle_source": {
    "answer": "docstring_or_comment",
    "justification": "..."
  },
  "required_test_scope": { "answer": "unit", "justification": "..." },
  "benchmark_quality": { "answer": "usable", "justification": "..." }
}
```

For an out-of-scope task, return:

```json
{
  "task_nature": { "answer": "out_of_scope", "justification": "..." }
}
```

## Before you answer

Your final message is the JSON object alone: no text before or after it, and no
Markdown fence.
