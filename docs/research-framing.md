# Research framing

This doc holds the research questions behind Oracle Lab and the ideas that shape
its judge rubrics. It is context for design work. It does not describe how the
code behaves today; [judging](judging.md) does that. The pivot it supports is
planned in [the judgment pivot plan](features/judgment-pivot/implementation.md).

## Questions

- **Q1: which kinds of problems** do agents fail to write a detecting test for?
  The classification judge answers this, one task at a time.
- **Q2: what agents do**, in process and in output, that leads to the bug going
  undetected. The generation judgment answers this, one run at a time.

The two meet in the analysis: failure modes cross-tabulated by problem class.

## Two judgment stages

Each stage has an LLM judge, and human raters rate how far they agree with each
answer it gave.

1. **Classification** (per task): the task's nature, defect mechanism, required
   setup, and where the correct behavior can be learned.
2. **Generation** (per run): did the generated tests target the oracle problem,
   and what failure modes did the agent show? The judge reads the final tests and
   the agent's trace together and answers both.

An earlier plan had a third stage: humans open coding traces into a taxonomy,
adapted from MAST (Cemri et al., "Why Do Multi-Agent LLM Systems Fail?"). That
plan is dropped. The LLM judge now applies a start list of failure modes, and
may add its own labels. Humans rate those labels instead of coding traces
themselves.

## Recognition, retrieval, derivation

An agent writing a detecting test without the issue has to do three things:

1. **Recognize** that something is wrong.
2. **Retrieve** the correct behavior, if the repository states it.
3. **Derive** the correct behavior, if the repository only implies it.

The candidate headline: if agents handle stated oracles but fail on implied ones,
the bottleneck is derivation, not context management.

### Oracle state (per problem)

- **Explicit:** the repository states the correct behavior (docs, docstrings,
  surrounding code). Succeeding needs retrieval.
- **Implicit:** the repository states it nowhere, but it can be derived from
  sibling behavior, invariants, contracts, or logic. Succeeding needs reasoning.
- **Absent:** the repository does not reveal it; it lives only in the issue or
  the maintainer's intent. Failing here is not a model failure.

The classification rubric's `code_only_oracle_availability` question is close to
this. `repository_documentation` maps to explicit. `local_contract` and
`repository_pattern` are mostly implicit. `unavailable_or_ambiguous` and
`domain_knowledge` are mostly absent. Reworking the question into these three
states is a possible later change.

### Recognition signal (per problem)

Is there any sign in the repository that something is wrong, such as a visible
inconsistency, a docstring that disagrees with the code, or a crash?

| | Signal present | No signal |
|---|---|---|
| Explicit oracle | Retrieval should be enough | Agent has to stumble onto the bug |
| Implicit oracle | **Target cell** | Nearly hopeless without localization |
| Absent oracle | Not a fair test | Not a fair test |

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

A **reproducible derivation failure** is an implicit-oracle problem that agents
fail consistently even in condition 3.

### Calling an oracle derivable

This label is the one reviewers will challenge, because a problem looks
derivable once you have seen the fix. Operational test: a person given only the
repository and the bug's location (no issue, no patch) can state the correct
behavior and point to the evidence for it. Humans should run this check on every
problem labeled implicit.

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

- Cemri et al. (2025), "Why Do Multi-Agent LLM Systems Fail?" (MAST).
- Saldaña, *The Coding Manual for Qualitative Researchers*: provisional coding.
- Miles & Huberman (1994), *Qualitative Data Analysis*: the start list.
- Fereday & Muir-Cochrane (2006): hybrid deductive and inductive coding.
- Hsieh & Shannon (2005): directed content analysis.
- Stol, Ralph & Fitzgerald (ICSE 2016), "Grounded Theory in Software Engineering
  Research".
- Ammann & Offutt, *Introduction to Software Testing*: the RIPR (reach, infect,
  propagate, reveal) fault model behind the detection chain.
