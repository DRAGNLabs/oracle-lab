# Research framing

This doc holds the research questions behind Oracle Lab and the ideas that shape
its judge rubrics. It is context for design work. It does not describe how the
code behaves today; [judging](judging.md) does that.

## Questions

- **Q1: which kinds of problems** do agents fail to write a detecting test for?
  The classification judge answers this, one task at a time.
- **Q2: what agents do**, in process and in output, that leads to the bug going
  undetected. The generation judgment answers this, one run at a time.

The two meet in the analysis: failure modes cross-tabulated by problem class.

## Two judgment stages

Each stage has an LLM judge, and human raters rate how far they agree with each
answer it gave.

1. **Classification** (per task): what kind of problem the task is, and what a
   test needs to detect it. See [task classification](#task-classification).
2. **Generation** (per run): did the generated tests target the oracle problem,
   and what failure modes did the agent show? The judge reads the final tests and
   the agent's trace together and answers both.

An earlier plan had a third stage: humans open coding traces into a taxonomy,
adapted from MAST (Cemri et al., "Why Do Multi-Agent LLM Systems Fail?"). That
plan is dropped. The LLM judge now applies a start list of failure modes, and
may add its own labels. Humans rate those labels instead of coding traces
themselves.

## Task classification

The classifier describes each task through several independent questions,
called facets, instead of one label. Real defects span categories, and a single
label would mix unrelated sources of difficulty. The labels themselves live in
`prompts/judge/task-classification-rubric.md`. This section records where the
questions come from and why each one is asked.

### Prior work

- **Orthogonal Defect Classification (ODC)**, from IBM Research (Chillarege et
  al., 1992). It sorts defects into types such as assignment, checking,
  algorithm, interface, and timing. It also separates a defect from the trigger
  that exposed it. It is the basis for `defect_mechanisms`.
- **IEEE 1044**, a standard for classifying software anomalies. It is thorough
  but too process-oriented to use directly.
- **Hayes et al.**, a language-independent code-fault taxonomy: control and
  logic, data flow, interface, error handling, and configuration.
- **Sobreira et al. (2018), "Dissection of a Bug Dataset"**, which annotates
  the Defects4J benchmark. It is the closest precedent for characterizing an
  existing bug benchmark. Its categories describe the developer's repair, not
  the defect or why it is hard to test. It concludes that there is no standard
  way to characterize benchmark bugs, which justifies an adapted taxonomy.
- **TestExplora (2026)**, a benchmark of the same capability: generating tests
  that fail on a buggy repository and pass on its repair. It finds cross-module
  interactions a major source of difficulty.

SWE-bench has no taxonomy of defect types. Its paper analyzes repository, issue
length, patch size, and the files and functions touched. SWE-bench Verified adds
human annotations for problem-statement quality, test correctness, and
estimated human time. A later audit of Verified found reference tests that were
too narrow, tests that demanded behavior the issue never specified, and
underspecified issues. Problems like these can pass for model difficulty, so the
classifier screens for them.

### Why each question

- `task_nature`: SWE-bench's mining also captures enhancements, refactors, and
  documentation changes. Only a behavioral bug or a performance problem calls for
  a fail-to-pass test. Anything else is `out_of_scope`, and classification stops
  there.
- `defect_mechanisms`: what is wrong in the code, adapted from ODC. Several can
  apply.
- `primary_assertion_target`: what a minimal fail-to-pass test inspects to tell
  the two versions apart. It is separate from the mechanism, because one
  interface defect can surface as an exception, a wrong output, or a missing
  call.
- `required_test_setup`: the one prerequisite without which a test cannot reach
  the bug.
- `oracle_kind` and `oracle_source`: whether the buggy repository tells a test
  author what to assert, and where. See [oracle kind](#oracle-kind-per-problem).
  An agent can find and trigger a bug yet still fail because nothing tells it
  the correct outcome. That is a different difficulty from the defect's
  mechanism.
- `required_test_scope`: how much of the system a test has to assemble, from a
  single unit up to the environment or platform.
- `benchmark_quality`: whether the task can give a trustworthy result at all.
  It filters tasks and qualifies results. It does not measure agent ability.

The classifier also receives the fix scope (`function`, `class`, or `module`),
which preflight computes from the golden repair rather than asking a model.

Build the taxonomy from the dataset, not only from agent failures. Otherwise it
encodes one model's failure modes instead of the tasks' properties.

### Analysis

Model detection directly before building any difficulty score:

```text
detects_bug
    ~ defect mechanisms + assertion target + test setup + oracle kind
    + test scope + fix scope + benchmark quality
    + model/harness + repository
```

A mixed-effects logistic regression, with repository, model, and instance as
random effects, uses repeated runs of each task. It can answer questions such
as:

- Are cross-module bugs harder once patch size is controlled for?
- Are derived oracles harder than specified ones?
- Are stateful bugs harder than boundary-value bugs?
- Do apparently hard bugs turn out to be underspecified or misaligned tasks?

This is more useful than deciding in advance that, for example, concurrency
bugs have difficulty 5 and conditionals difficulty 2.

## Recognition, retrieval, derivation

An agent writing a detecting test without the issue has to do three things:

1. **Recognize** that something is wrong.
2. **Retrieve** the correct behavior, if the repository states it.
3. **Derive** the correct behavior, if the repository only implies it.

A bug that crashes needs neither retrieval nor derivation: triggering it is
enough.

The candidate headline: if agents handle stated oracles but fail on implied ones,
the bottleneck is derivation, not context management.

### Oracle kind (per problem)

A test oracle is what tells a test the correct outcome. The kinds follow Barr et
al., "The Oracle Problem in Software Testing: A Survey" (IEEE TSE, 2015), judged
from the buggy repository alone:

- **Implicit:** the bug crashes, raises an uncaught exception, or hangs.
  Triggering it is enough; no expected value is needed.
- **Specified:** the repository, or a specification it follows, states the
  correct behavior (docs, docstrings, types). Succeeding needs retrieval.
- **Derived:** nothing states it, but it can be reasoned out from the code:
  surrounding logic, sibling code, invariants, or relations between executions.
  Succeeding needs reasoning.
- **Human:** the repository does not settle it; it lives in the issue, the
  maintainer's intent, or outside knowledge. Failing here is not a model failure.

The classification rubric asks this as `oracle_kind`, and names the exact source
in `oracle_source`. When several kinds fit, the judge takes the first in the
order above: the one a test author can use with the least reasoning.

### Recognition signal (per problem)

Is there any sign in the repository that something is wrong, such as a visible
inconsistency, a docstring that disagrees with the code, or a crash?

| | Signal present | No signal |
|---|---|---|
| Implicit oracle | Triggering the bug is enough | Not applicable: the crash is the signal |
| Specified oracle | Retrieval should be enough | Agent has to stumble onto the bug |
| Derived oracle | **Target cell** | Nearly hopeless without localization |
| Human oracle | Not a fair test | Not a fair test |

The target cell is where the key evidence lives: the problem is recognizable and
the oracle derivable, yet agents fail, and fail across repeated runs.

### Condition ladder

Each prompt condition removes one stage, so the drop between two adjacent
conditions measures that stage's cost. They are diagnostic controls, not a
comparison of prompts.

1. **Open prompt:** recognize, locate, derive.
2. **Localized prompt** (names the function, says nothing about a bug):
   recognize, derive. This is `inputs/target.json` today.
3. **Told there is a bug in the function:** derive only. Prior work gave the
   agent the issue, which removed recognition; this explains why it found the
   task easier.

A **reproducible derivation failure** is a derived-oracle problem that agents
fail consistently even in condition 3.

### Calling an oracle derivable

This label is the one reviewers will challenge, because a problem looks
derivable once you have seen the fix. Operational test: a person given only the
repository and the bug's location (no issue, no patch) can state the correct
behavior and point to the evidence for it. Humans should run this check on every
problem labeled derived.

## Failure modes

A **failure mode** is an observable behavior in the trace that moved the agent
away from a detecting test, at a point where the evidence available to it
allowed one.

Every label must be:

1. **Observable:** tied to specific steps in the trace.
2. **Behavioral, not an outcome:** it says what the agent did, not how its tests
   scored.
3. **Decidable from the evidence the judge has:** no deep repository knowledge
   needed.
4. **Distinct:** an exclusion rule separates it from its nearest neighbor.

### Signals are not failure modes

The trace viewer computes **signals**: mechanical facts such as patch overlap,
the share of patched lines shown, edit after a failing run, a skip mark added,
or a failing test missing from the submission. A signal is evidence. A failure
mode is the interpretation that evidence may support. An edit after a failure
can be a legitimate fix to a broken test; a probed value copied into an assert
can be correct. The judge reads the episode and decides. The viewer keeps showing
signals only.

### Unit and multiplicity

The unit is the run. A run gets every label that fits, not one, as in MAST:
modes co-occur, and forcing one label makes raters argue over which mode wins.
The judge decides every start label, present or absent, and each present label
points to its evidence: trace step numbers, test names, or both. Runs that
detect the bug are labeled too; their other tests can still show failure modes,
and they are the contrast cases.

A single "breakdown point" per run, if analysis wants one, is derived rather than
asked for: order the modes along the detection chain (reach, exercise, assert,
source the expectation, keep the assertion) and take the earliest one present.

### Start list

These are hypotheses, written before labeling begins, not a validated codebook.
The judge may apply them or add its own labels with a name and definition. In a
paper, this is provisional coding with an open category (Saldaña; Miles &
Huberman's start list), a hybrid deductive and inductive design (Fereday &
Muir-Cochrane).

| Label | Apply when | Don't apply when | Supporting signals |
|---|---|---|---|
| Never saw the bug | The agent never viewed any of the buggy lines. | It saw at least one buggy line, even in search results. | No patched-line overlap; low share of patched lines shown |
| Saw the bug, didn't test it | The agent viewed the buggy lines, but none of its tests call that code. | A test calls the buggy code. | Patched-line overlap; no test references a patched name |
| Copied the buggy output | The agent ran the code, saw a wrong result, and wrote that result into a test as the expected value. | The value came from docs or existing tests, or is correct. | A probe run before the test was written |
| Copied existing tests | A test expects the buggy behavior because it mirrors a repository test the agent read. | No test the agent read has that expectation. | Reads of existing tests |
| Ignored available docs | Docs or a docstring describe the correct behavior, and the agent never read them. | The agent read them, or none exist. | No reads of docs |
| Read docs, contradicted them | The agent read docs describing the correct behavior, then wrote a test expecting the buggy behavior. | No test touches the bug. | Reads of docs before the test was written |
| Changed test to pass | A test about the bug failed, and the agent edited or deleted it to make it pass without flagging a possible bug. | The agent flagged it, or the edit fixed an unrelated mistake. | Edit after failures; failing test absent from submission |
| Doubted, moved on | The agent said something looked off, then wrote a passing test without flagging it. | The agent decided the behavior was intended. | Reasoning or messages near patched-line reads |
| Explained the bug away | The agent argued the buggy behavior is correct or intended. | It voiced doubt but never concluded. | Reasoning or messages |
| Flagged the bug (good) | The agent marked the behavior as possibly wrong: an xfail or skip with a reason, a test comment, or a message. | A skip or xfail with no reason. | Skip mark added |

### Expected-value provenance

For each assertion on defect-affected behavior, correct or not, where did the
expected value come from?

- **Observed output:** earlier output from running the current code.
- **Specification:** docs or a docstring the agent read.
- **Existing tests:** a repository test the agent viewed.
- **No visible source:** none of these.

This is not a failure mode, but its distribution is a finding in itself.
It came from the earlier open-coding protocol and is not planned for the judge.

### Guarding against the start list

A start list makes the judge likelier to find what is on it. Two checks:

- **Watch the judge-defined rate.** A near-zero share of judge-defined labels
  suggests the judge forces runs into the list.
- **Track each start label's fate:** confirmed, revised, merged, or dropped, from
  the human ratings. If every hypothesis survives unchanged, reviewers will
  suspect the list drove the results.

## Open issues

- **Xfail scoring.** A non-strict xfail is xfailed on buggy and XPASS on golden,
  so a correctly flagged bug may not count as detected. Check how the pipeline
  scores xfail and skip.
- **Prompt confound.** "Write unit tests" rewards passing tests, so silently
  conforming a test may be instruction-following. A prompt that primes the agent
  to look for bugs is the diagnostic control.

## References

- Chillarege et al. (1992), "Orthogonal Defect Classification: A Concept for
  In-Process Measurements" (IEEE TSE).
- IEEE 1044, Standard Classification for Software Anomalies.
- Hayes et al., a language-independent code-fault taxonomy.
- Sobreira et al. (SANER 2018), "Dissection of a Bug Dataset: Anatomy of 395
  Patches from Defects4J" (arXiv:1801.06393).
- TestExplora (2026), repository-level test generation for bug discovery
  (arXiv:2602.10471).
- Jimenez et al. (ICLR 2024), "SWE-bench: Can Language Models Resolve Real-World
  GitHub Issues?" (arXiv:2310.06770).
- OpenAI, "Introducing SWE-bench Verified" and "Why we no longer evaluate
  SWE-bench Verified".
- Barr et al. (IEEE TSE 2015), "The Oracle Problem in Software Testing: A
  Survey".
- Cemri et al. (2025), "Why Do Multi-Agent LLM Systems Fail?" (MAST).
- Saldaña, *The Coding Manual for Qualitative Researchers*: provisional coding.
- Miles & Huberman (1994), *Qualitative Data Analysis*: the start list.
- Fereday & Muir-Cochrane (2006): hybrid deductive and inductive coding.
- Hsieh & Shannon (2005): directed content analysis.
- Stol, Ralph & Fitzgerald (ICSE 2016), "Grounded Theory in Software Engineering
  Research".
- Ammann & Offutt, *Introduction to Software Testing*: the RIPR (reach, infect,
  propagate, reveal) fault model behind the detection chain.
