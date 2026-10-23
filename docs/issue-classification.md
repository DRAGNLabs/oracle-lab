# Classifying SWE-bench issue types for test-generation analysis

There is substantial prior work on software-defect classification, but no single
existing taxonomy quite matches what Oracle Bench needs. The most promising
approach is a faceted classification rooted in established defect taxonomies,
with additional dimensions describing what makes a defect observable by a newly
generated test.

## Existing classifications worth building on

The strongest general foundation is IBM's Orthogonal Defect Classification
(ODC). It classifies defects into types such as:

- Assignment
- Checking
- Algorithm
- Function
- Interface
- Timing/serialization
- Build/package/merge
- Documentation

ODC is well established and was explicitly designed so defect distributions can
be analyzed quantitatively. It also distinguishes the defect itself from the
_trigger_ that caused it to be observed, which is especially relevant to test
generation. See the [original ODC paper from IBM
Research](https://research.ibm.com/publications/orthogonal-defect-classificationa-concept-for-in-process-measurements).

Other useful foundations are:

- **IEEE 1044**, a general standard for classifying software anomalies. It is
  comprehensive, but probably too process-oriented and cumbersome to use
  directly as the Oracle Bench taxonomy.
- **Hayes et al.'s language-independent code-fault taxonomy**, which includes
  control/logic, data flow, interface, error handling, and configuration faults.
  This is close to intuitive categories such as logic and branching errors.
- **Sobreira et al.'s Defects4J dissection**, probably the best methodological
  precedent for analyzing an existing bug benchmark. It annotates patch size,
  spreading, repair actions, and repair patterns. Its categories include changes
  to conditionals, method calls, assignments, null checks, and similar repair
  operations. However, these describe the _developer's repair_, not necessarily
  the semantic nature of the defect or why it is difficult to test. See the
  [paper and annotated dataset](https://arxiv.org/abs/1801.06393).

Sobreira et al. explicitly conclude that there is no universally adopted method
for characterizing benchmark bugs. Developing an adapted taxonomy is therefore
academically defensible rather than merely reinventing an existing standard.

A particularly relevant recent benchmark is **TestExplora**, which evaluates
the broad capability Oracle Bench is interested in: generating tests that fail
on a buggy repository and pass on its repaired version. It reports that
cross-module interactions are an important source of difficulty and that current
models have low defect-discovery rates. See the [TestExplora
paper](https://arxiv.org/abs/2602.10471) and [Microsoft project
page](https://www.microsoft.com/en-us/research/publication/testexplora-benchmarking-llms-for-proactive-bug-discovery-via-repository-level-test-generation/).

## What SWE-bench already classifies

The original SWE-bench paper does **not** provide a useful semantic taxonomy of
issue or defect types. Its main analysis concerns:

- Repository
- Issue and context length
- Patch size
- Number of files and functions involved
- Retrieval and context strategy
- Model resolution rate

It emphasizes that tasks can require coordination across functions, classes,
and files, but does not annotate instances as control-flow bugs, API-contract
bugs, and so on. See the [original SWE-bench
paper](https://arxiv.org/abs/2310.06770).

SWE-bench Verified adds human annotations for:

- Problem-statement quality
- Test correctness
- Estimated human completion time
- Miscellaneous benchmark problems

Its difficulty label is essentially estimated developer time, not defect type.
Verified contains 500 tasks selected from 1,699 reviewed instances, and the full
annotation set is publicly available. See the [SWE-bench Verified methodology
and downloads](https://openai.com/index/introducing-swe-bench-verified/).

There is an important warning here: a later audit found residual benchmark
problems such as tests that were too narrow, tests that demanded unspecified
functionality, and underspecified task descriptions. These are not defect types,
but they can easily masquerade as LLM difficulty. See the [later SWE-bench
Verified audit](https://openai.com/index/why-we-no-longer-evaluate-swe-bench-verified/).

SWE-bench has therefore partially classified difficulty and benchmark quality,
but has not supplied the semantic defect taxonomy needed here.

## Proposed Oracle Bench taxonomy

Each problem should not be forced into one exclusive label. Real defects often
span several categories, and a single-label taxonomy will conflate unrelated
sources of difficulty. Instead, annotate every instance along independent
facets.

### Facet A: Task nature

This prevents treating every SWE-bench record as a bug.

- `behavioral_bug`: documented existing behavior is incorrect
- `missing_behavior`: a required case is unimplemented
- `feature_or_enhancement`: intentionally adds or changes behavior
- `compatibility_change`: behavior must adapt to another version or platform
- `performance_problem`
- `refactoring_or_maintenance`
- `documentation_only`
- `ambiguous_or_mixed`

SWE-bench is commonly described as a bug-fixing benchmark, but its issue/PR
mining process also captures enhancements and behavioral changes.

We would want to eliminate missing_behavior, feature_or_enhancement, refactoring_or_maintenance, documentation_only, and ambiguous_or_mixed. We would possibly want to eliminate compatibility_change. That leaves use with behavioral_bug and performance_problem. We are specifically targeting instances where there is a problem in the code, and the model needs to write a F->P test.

So if it is not behavioral_bug or performance_problem, just stop here.

### Facet B: Defect mechanism

This is the ODC-derived core. Multiple labels should be permitted.

- `control_logic`: wrong branch, condition, ordering, or loop behavior
- `computation_algorithm`: incorrect formula, transformation, or algorithm
- `data_state`: wrong assignment, initialization, mutation, caching, or state
  transition
- `validation_checking`: missing or incorrect validation, bounds, type, or
  precondition check
- `error_handling`: wrong exception, missing recovery, or swallowed failure
- `interface_contract`: disagreement across functions, modules, APIs, callbacks,
  or protocols
- `parsing_serialization`: parsing, formatting, encoding, schema, or round-trip
  errors
- `resource_lifecycle`: files, connections, cleanup, transactions, or context
  managers
- `concurrency_timing`: race, ordering, timeout, synchronization, or
  nondeterminism
- `configuration_environment`: platform, dependency, build, packaging, or
  configuration behavior
- `compatibility`: Python, library, operating-system, or version-specific
  behavior
- `performance_resource_use`: excessive time, memory, calls, or allocation
- `presentation_output`: diagnostics, messages, rendering, or representation
- `other`

"Syntactically incorrect" should probably not be a prominent SWE-bench
category. SWE-bench base revisions generally install and execute. Syntax errors
are more likely to be failures in an agent-generated patch or test than the
latent issue being benchmarked.

### Facet C: Primary assertion target

This records the one kind of behavior that a minimal valid fail-to-pass test
directly asserts. In other words: **what does the test inspect to establish
that the buggy and repaired versions behave differently?**

Choose exactly one label. Classify the assertion's direct subject, rather than
every downstream consequence of the defect. For example, if an incorrect HTTP
request eventually produces a wrong return value, classify it as
`external_interaction` when the test asserts the request; classify it as
`return_value_or_status` when the test asserts only the returned value.

- `return_value_or_status`: a returned value, response, result object, or
  status is wrong.
- `exception_behavior`: an expected exception or failure status is absent or
  wrong, or an unexpected exception or crash occurs.
- `state_mutation`: in-memory or persistent state is missing or incorrect after
  the operation.
- `produced_output_or_artifact`: rendered text, serialized data, a log entry,
  a file, or another produced artifact is wrong.
- `external_interaction`: a call, request, event, command, or other observable
  interaction with a dependency is missing, incorrect, or occurs in the wrong
  order.
- `resource_or_performance`: execution exceeds a defined limit on time,
  memory, allocation, calls, or another resource.
- `nondeterminism`: equivalent repeated executions produce inconsistent
  results.
- `import_build_or_collection`: importing, building, or collecting the code
  fails.

The bug itself may be silent: it can return normally while violating its
contract. The assertion is what makes that discrepancy observable. This facet
therefore describes the test's observation point, not whether the defect
naturally emits an error.

Mechanism and assertion target should remain separate. An interface-contract
defect might be tested through an exception, incorrect output, or a missing
side effect.

### Facet D: Required test setup

This records what special setup a test needs before it can exercise the bug.
Choose exactly one label.

- `none`: a direct call or normal use of the feature is enough.
- `special_input`: the test needs a particular value, boundary case, invalid
  value, or unusual object.
- `existing_state`: the test needs data or object state created beforehand.
- `sequence`: the test needs multiple operations in a particular order.
- `environment_or_dependency`: the test needs a filesystem condition,
  configuration, platform, version, or controlled dependency.
- `concurrency_or_timing`: the test needs coordinated execution, timing, or
  repetition.

When more than one label appears applicable, choose the most distinctive
prerequisite: the condition without which the test cannot reach the bug. For
example, when a duplicate-email test needs both a pre-existing user and a
duplicate value, choose `existing_state`; the duplicate value merely exercises
that setup. When filesystem configuration is required before a sequence of API
calls, choose `environment_or_dependency`.

### Facet E: Code-only oracle availability

This records whether a test author can infer the needed assertion from the
information available in the buggy checkout. The generation agent does not see
the SWE-bench issue, the repair, or existing tests, so none of those can count
as evidence here.

Choose exactly one label: the strongest source available in the agent-visible
repository.

- `local_contract`: a function signature, type, docstring, error message, or
  nearby production code states the expected behavior.
- `repository_pattern`: analogous production code, callers, or another
  implementation in the repository shows the expected behavior.
- `repository_documentation`: repository documentation states the expected
  behavior.
- `api_convention`: a well-known library or language convention makes the
  expected behavior clear.
- `general_property`: a property such as idempotence, preservation, or a
  relationship between two executions gives the test its assertion.
- `domain_knowledge`: knowledge outside the repository is needed to know what
  should happen.
- `unavailable_or_ambiguous`: the buggy checkout does not provide enough
  evidence for one reasonable assertion.

Annotate this facet in two passes:

1. Read the issue, repair, and reference test only to identify the behavior a
   valid generated test must establish.
2. Set them aside. Inspect only the information the generation agent may see,
   then choose the strongest source that supports that assertion.

An LLM can locate and trigger a bug but still fail because the buggy checkout
does not reveal what the test should assert. This should not be classified
merely as a logic bug.

### Facet F: Required test scope

- Pure or unit-level
- Component-level
- Multi-module integration
- Filesystem or process integration
- Framework or application integration
- Environment or platform dependent
- Performance or concurrency test

Useful numeric measures include:

- Minimum relevant modules
- Call-graph distance from a public entry point to the defect
- Fixture complexity
- Setup steps
- Assertions required
- Existing nearby test examples
- Whether mocking is necessary

### Facet G: Specification and benchmark quality

This is a quality screen, not a measure of defect difficulty or agent ability.
It records whether an instance can provide a trustworthy result for code-only
test generation.

Choose exactly one label:

- `usable`: the instance appears suitable for evaluation.
- `unclear_expected_behavior`: the available evidence does not establish one
  clear expected behavior.
- `reference_test_or_repair_misaligned`: the reference test or repair does not
  appear to represent the stated problem.
- `unreliable_environment_or_execution`: the instance depends on an unstable
  environment or produces flaky results.
- `solution_or_oracle_leakage`: agent-visible material reveals the repair or
  the assertion the test should make.
- `not_reasonably_testable_from_code_only_context`: even with a sound repair,
  the buggy checkout does not give a test author a reasonable way to construct
  a valid test.

Use this facet to filter questionable instances and interpret results. Do not
use it to explain an agent's capability. SWE-bench Verified's existing quality
annotations can supply evidence for this screen, but do not replace it: Oracle
Bench also needs to judge whether a task is testable without the issue,
existing tests, or repair.

## Why a faceted approach matters

Consider a defect described as "a parser accepts an invalid nested form and later
produces a misleading error." A single-label system might call it any of:

- Parser bug
- Validation bug
- Logic bug
- Error-handling bug

The faceted representation is more precise:

- Nature: `behavioral_bug`
- Mechanisms: `parsing_serialization`, `validation_checking`
- Manifestation: wrong exception
- Trigger: invalid nested input
- Oracle: explicit issue statement
- Scope: component-level
- Test complexity: short setup, structured input required

This description is considerably more useful for explaining agent performance.

## Proposed SWE-bench analysis process

Use a two-stage annotation process that respects Oracle Bench's trust
boundaries.

### Stage 1: Public-view annotation

Annotate using only information the generation agent could access:

- Problem statement
- Buggy checkout
- Public documentation
- Existing tests
- Repository structure

Record task nature, apparent mechanism, trigger characteristics, oracle source,
required scope, and specification clarity. These are features that might
genuinely explain model difficulty.

### Stage 2: Privileged post hoc annotation

On the host side, after evaluation, use:

- Golden patch
- Reference test patch
- Buggy/golden execution differences
- Evaluation results

Record the actual mechanism, manifestation, minimal trigger, repair pattern, and
patch footprint. Also compare the public-view prediction with the privileged
determination. A mismatch is itself informative: it measures how discoverable
the defect was from the evidence available to the agent.

Begin with approximately 50--100 stratified instances, sampled across
repositories and success/failure outcomes:

1. Have two annotators label them independently.
2. Allow multiple defect-mechanism labels.
3. Adjudicate disagreements.
4. Revise definitions and add concrete positive and negative examples.
5. Measure agreement per facet. Krippendorff's alpha is more appropriate than
   raw agreement when there are multiple annotators or missing labels.
6. Freeze taxonomy version 1 before applying it to the full dataset.
7. Use an LLM to propose labels at scale, but have humans review a stratified
   sample and every low-confidence case.

Avoid constructing the taxonomy only from agent failures. Otherwise it will
encode one model's failure modes rather than the dataset's properties.

## Analyzing what is actually hard

Do not immediately collapse all annotations into a handcrafted difficulty
score. First model the observed result directly:

```text
distinguishing_test_success
    ~ defect mechanisms
    + trigger characteristics
    + oracle source
    + test scope
    + specification quality
    + patch locality
    + model/harness
    + repository
```

A mixed-effects logistic model would let repository, model, and possibly
instance act as random effects. With repeated attempts per instance, this could
estimate relationships such as:

- Are cross-module triggers harder after controlling for patch size?
- Are implicit oracles harder than explicit ones?
- Are stateful bugs harder than ordinary boundary-value bugs?
- Does agentic exploration particularly help interface defects?
- Does model failure correlate more strongly with semantic distance or physical
  patch size?
- Are apparent hard bugs actually underspecified or test-misaligned tasks?

This is more scientifically useful than deciding beforehand that, for example,
concurrency has difficulty 5 and conditionals have difficulty 2.

## Recommended foundation

The initial classification should combine:

- **ODC** as the scholarly foundation for defect mechanisms.
- **Defects4J Dissection** as the precedent for benchmark-wide patch
  characterization.
- **SWE-bench Verified annotations** for specification quality, test quality,
  and human-time difficulty.
- **A new Oracle Bench layer** for trigger complexity, oracle availability,
  observability, and required test scope.
- **TestExplora** as the closest contemporary comparison for repository-level
  LLM defect discovery.

The useful contribution would not merely be another bug taxonomy. It would be a
taxonomy connecting semantic defect type to the information and effort required
to produce a distinguishing test, which existing classifications largely do not
capture.
