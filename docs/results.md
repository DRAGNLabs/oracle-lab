# Results and artifacts reference

Every `oracle-bench run` creates a timestamped attempt directory beneath the
configured `output` path. The directory is the complete record of one agent
attempt: its inputs, resolved dataset instance, runtime, agent activity, captured
submission, reference check, paired evaluations, and report.

## Directory layout

Standalone task classifications use a parallel central store rather than the run
directory:

```text
classifications/<instance-id>/<timestamp>-<id>/
├── inputs/
│   ├── config.resolved.yaml
│   ├── instance.json
│   └── rubric.md
├── image-build/
├── agent/
├── workspace-spec.json
├── classification.raw.txt
├── classification.json
├── cost.json
└── status.json
```

Every invocation creates a new immutable attempt. Reports search the
`classifications` directory beside their configured `runs` output, select the newest
attempt for the exact SWE-bench `instance_id`, and verify its base commit and source
record checksum. The central artifact is read in place and is not copied into a run.
The newest failed or invalid attempt remains visible rather than falling back silently.

`classification.json` contains immutable instance/rubric hashes and a normalized
result. Completed in-scope results contain every rubric facet; out-of-scope results
contain task nature and rationale only. `invalid_output`, `failed`, and `timed_out`
are explicit result states. The raw final response, frozen rubric, harness trace,
model provenance, image provenance, and workspace exposure manifest remain alongside
it for audit. `cost.json` records the classifier turn's spend in the same format
as a run's [`cost.json`](#costjson).

```text
runs/<run-id>/
├── status.json
├── cost.json
├── report.md
├── inputs/
│   ├── config.resolved.yaml
│   ├── instance.json
│   ├── prompt.txt
│   └── target.json (localized runs only)
├── image-build/
│   ├── runtime.json
│   ├── harnesses/{harnesses.Dockerfile,package.json,package-lock.json}
│   ├── runtime/runtime.Dockerfile
│   ├── runtime/container_helpers/*.py
│   ├── requested.json
│   ├── harnesses.inputs.json
│   ├── runtime.inputs.json
│   ├── images.json
│   └── output.log (when a pull or build runs)
├── reference-check/
│   ├── results.json
│   ├── buggy/<execution artifacts>
│   └── golden/<execution artifacts>
├── generation/
│   ├── command.json
│   ├── version.txt
│   ├── trace.jsonl
│   ├── session.log
│   ├── final.txt
│   ├── result.json
│   ├── stderr.log
│   ├── setup.log
│   ├── launch.log
│   ├── capture.log
│   ├── baseline.txt
│   ├── before.json
│   ├── after.json
│   └── workspace.diff
├── submission/
│   ├── manifest.json
│   └── files/<generated test paths>
├── ground-truth/
│   ├── issue.md
│   └── fix.patch
├── evaluation/
│   ├── results.json
│   ├── buggy/<execution artifacts>
│   └── golden/<execution artifacts>
├── judge/
│   ├── rubric.md (when judge configuration is present)
│   ├── workspace.log
│   ├── image.json
│   ├── workspace-spec.json
│   ├── bundle-manifest.json
│   ├── prompt.md
│   ├── judgment.raw.txt
│   ├── judgment.json
│   ├── human/<opaque-rater-id>/{judgment.raw.json,judgment.json,provenance.json}
│   ├── human-history/<opaque-rater-id>/<archived rating>/
│   ├── human-workspaces/<workspace metadata and VS Code files>
│   ├── human-workspace.log
│   └── agent/{command.json,version.txt,trace.jsonl,stderr.log,final.txt,result.json}
├── judge-history/<archived judge attempts>/
├── generation-history/<archived interrupted generation attempts>/
└── evaluation-history/<archived reevaluations>/
```

A run that stops early contains only the files produced before the failure. For
example, an image-build failure will not have generation or evaluation artifacts.
Consult `status.json` and the last stage log when expected files are absent.

## Judge workspace manifests

`workspace-spec.json` records the workspace-layout version, prepared image ID
and digests, base commit, patch, rubric and container-helper hashes,
repository-view construction method, reconstruction commands, and every exposed
artifact. The separate
`bundle-manifest.json` is the compact destination-to-SHA-256 inventory.

The disposable container receives `rubric.md` and the rendered `instructions.md`
alongside `/oracle-judge/buggy` and
`/oracle-judge/golden` repository views, the generated submission in both views,
the issue and patches, and saved evaluation evidence. Oracle Bench verifies the
submission bundle before transfer and checks every generated file again inside
both views. Repository views are not copied back to the host; the saved run
artifacts and workspace specification are the reproducible inputs.

Run the judge independently after paired evaluation:

```sh
uv run oracle-bench judge runs/<run-id>
```

The command requires the configured judge credential and the saved runtime image.
It writes the exact prompt and raw final response before validating
`judgment.json`. Invalid model output is retained as `status: invalid_output` and
is not retried automatically. Generation and judgment turns that fail with a
recognized transient API or transport error are retried up to three times, after
30, 90, and 180 seconds. Each retry uses a fresh container and workspace; prior
attempt evidence is preserved under `generation-history/` or `judge-history/`.
Other harness failures and deadlines are not retried and produce their normal
explicit status. A manual judge rerun archives the complete prior attempt under
`judge-history/<UTC timestamp>/` before rebuilding the workspace.

## Interactive human judging

Create a blinded human workspace from the same saved `workspace-spec.json` used
for model judging:

```sh
uv run oracle-bench judge-workspace create runs/<run-id> --rater rater_01
```

The command reconstructs the bundle and refuses to continue if its specification
differs from the saved one. It leaves a named container running without network
access or model credentials and prints commands for opening a shell or attaching
VS Code. The common evidence is read-only; the non-root `oracle` user can write
only `/oracle-judge/output/`. LLM responses and earlier human ratings are never
uploaded into this container.

Each run freezes the rubric used when it was created. Judgments from earlier
rubrics do not satisfy the current contract; create a new run to use this rubric
rather than mixing ratings across versions.

Read `/oracle-judge/instructions.md` alongside `rubric.md`; it combines the
human workflow, evidence-file guide, and task-specific outcome summary. The
workspace opens with `/oracle-judge/output/judgment.json` already populated
with placeholders for `tests_issue`, `attempt_detail`, `no_attempt_reason`,
`cheating`, and `rationale`. Replace each with the appropriate rubric label, JSON `null`, or
the string `"null"` for an inapplicable conditional field;
then write a short rationale. The answer is the rater's own
judgment; collection validates but does not derive or override it. Then collect it:

```sh
uv run oracle-bench judge-workspace collect <container-name>
uv run oracle-bench judge-workspace remove <container-name>
```

Invalid JSON or invalid labels do not stop or remove the workspace, allowing the
rater to correct the draft. A valid rating is saved under
`judge/human/<opaque-rater-id>/` with both the submitted JSON and normalized
result. Its `provenance.json` records the hashes of the rubric and common
workspace specification, which includes `instructions.md`. Collection refuses to overwrite
that result. To replace it deliberately,
pass `--archive-existing`; Oracle Bench first moves the previous files under
`judge/human-history/<opaque-rater-id>/<UTC timestamp>/`. The `remove` operation
checks the container's profile label and refuses to remove unrelated containers.
Rerunning the automated judge preserves these human artifacts. Reevaluation marks
both automated and human judgments stale because their execution evidence has
changed.

## Primary results

### `report.md`

The human-readable summary, in this order:

- Title line: `completed` or `completed with errors`, followed by the reasons
  (generation status, `no tests collected`, an incomplete evaluation, or
  diagnostic-only), and any forbidden changes.
- Pipeline: one row per stage (resolve, build, reference, generate, evaluate,
  judge) with its parameters and result, then the run's cost. Generation and
  judge rows name the harness, version, model, provider, and limit.
- Test outcomes: the paired matrix plus other outcomes, then coverage and failure
  causes per version.
- Judgment: the labels and rationale, or the judge's non-completed state.
- Task classification: the central classification for the problem.
- Usage: wall time and input, cached input, and output tokens for generation
  and judge.
- Files: links to the artifacts that exist, grouped by stage, including the
  private ground truth. Ground truth is written on the host and never mounted or
  copied into the generation container.

`oracle-bench report runs/<run-id>` regenerates this file from saved data. It does
not execute tests or call a model.

A run with no frozen rubric renders the judge as `disabled`; a frozen rubric
without `judgment.json` renders as `missing`. Failed, timed-out, invalid, and
stale judgments render their diagnostic fields without assuming successful label
fields are present.

## `judge/`

### `judge/judgment.json`

The normalized semantic annotation. A completed judgment contains `status`,
`tests_issue`, `attempt_detail`, `no_attempt_reason`, `cheating`, and `rationale`. The
conditional fields are saved as JSON `null` when inapplicable, even if the
submitted response used the string `"null"`. The raw response is retained.
Other states are explicit:

| Status | Meaning |
|---|---|
| `invalid_output` | The final response was not one valid rubric JSON object |
| `failed` | Harness or judge-workspace execution failed |
| `timed_out` | The judge exceeded its wall-time limit |
| `stale` | Reevaluation replaced the execution evidence; the prior annotation is nested for audit |

### Judge model evidence

`judgment.raw.txt` preserves the final assistant response before parsing.
`prompt.md` and `rubric.md` preserve the exact instructions. `agent/result.json`
records status, usage, duration, harness, provider, model, CLI version, and
the stopping limit that bounded the turn;
the neighboring command, version, trace, stderr, and final files have the same
roles as their generation counterparts.

### `evaluation/results.json`

The machine-readable aggregate for the generated tests. It is a validated
contract: per-version `tests.json` is checked as it enters the host from the
container, so unreadable runner output becomes an explicit incomplete result
rather than a missing outcome discovered later. Artifacts carry no schema
version; a file written by an earlier revision fails to load rather than being
partially interpreted.

| Field | Meaning |
|---|---|
| `instance_id` | Dataset instance evaluated |
| `agent` | Copy of `generation/result.json` |
| `artifact_sha256` | Checksum over the generated-file manifest entries |
| `submission_compliant` | Whether the agent made only permitted changes |
| `forbidden_changes` | Paths changed outside the allowed submission policy |
| `task_scope` | `localized` or `repository`, matching the generation assignment |
| `test_target` | Sanitized localized code area delivered to the agent, or null |
| `existing_tests` | Test-visibility policy; `keep` means existing tests were visible |
| `buggy_status`, `golden_status` | Overall execution status for each version |
| `matrix` | Counts and test IDs in each binary paired cell |
| `tests` | One paired row per test ID |
| `other_outcomes` | Tests excluded from the binary matrix |
| `failure_kinds` | Assertion, other-exception, and unknown failure counts for each version |
| `coverage` | Normalized coverage summaries for both versions |

Diagnostic-only status is derived from `submission_compliant`. Detection in the
expected direction is derived from the `fail_on_buggy_pass_on_golden` matrix count;
neither value is duplicated as a separately persisted field.

Each `tests` row contains `test_id`, `buggy`, `golden`, and `cell`. Failed sides
also include `buggy_failure` or `golden_failure` with a diagnostic kind, exception
type, and message. `cell` is null when either execution is incomplete or an
outcome is not binary.

| Matrix key | Buggy | Golden | Meaning |
|---|---|---|---|
| `pass_on_both` | pass | pass | Test accepts both implementations |
| `fail_on_buggy_pass_on_golden` | fail | pass | Test detects behavior repaired by the golden patch |
| `pass_on_buggy_fail_on_golden` | pass | fail | Test prefers buggy behavior or rejects the repair |
| `fail_on_both` | fail | fail | Test rejects both implementations |

These cells describe behavior; they do not prove whether an oracle is correct.
Skips, expected failures, setup or teardown errors, collection failures, missing
test IDs, and incomplete runs remain in `other_outcomes`.

### `status.json`

The latest lifecycle checkpoint, containing `stage`, `state`, `updated_at`, and an
error when applicable. Stages progress through `resolve`, `build`, `reference`,
`generate`, `evaluate`, optional `judge`, `cost`, and `finished`. Capturing the
submission is part of the `generate` stage. `cost` is the wait for the spend
reading described in [`cost.json`](#costjson).

The final state is `completed` only when the agent and both evaluations completed
and the submission was compliant. `completed_with_errors` means the run retained
results but one of those conditions failed. The independent `judge_status` is
`disabled`, `completed`, `invalid_output`, `failed`, `timed_out`, `skipped`,
`stale`, or `missing`. A configured judge is `skipped` without a model call when
generation produces no test files, or when neither version collects a generated
test (`no_tests`). Tests captured from a timed-out or otherwise
incomplete generation are still evaluated and judged. Judge failure does not
replace a completed evaluation state. An early exception records its stage and a
failed state.

### `cost.json`

What the run's model stages cost. The host reads the OpenRouter key's total spend
(`GET /api/v1/key`) just before generation, and again after the judge, or after
evaluation when no judge runs. The difference is the run's cost, covering
generation and judge together. Resolve, build, the reference check, and
evaluation call no model.

OpenRouter updates the total about a minute after a request, and consecutive
readings can briefly disagree. The final reading polls every 5 s, waits at least
60 s, and accepts the total once it has risen and three readings in a row match.
It stops at 180 s.

| Field | Meaning |
|---|---|
| `status` | `measured`; `unsettled` (180 s passed without a steady reading, so the last reading is used); `unavailable` (a lookup failed); `not_measured` (a model stage does not use OpenRouter) |
| `cost_usd` | `after - before` in US dollars, or null |
| `before`, `after` | The key's total spend at each reading |
| `waited_seconds` | Time spent waiting for the final reading |
| `error` | The lookup error, when `unavailable` |

The difference counts everything charged to the key while the run's model stages
ran. Another use of the same key during that time inflates it; a key used only by
the benchmark avoids this. A run that fails after generation starts is still
measured. An interrupted run is not. A cost failure never changes the run's state.
Runs made before this file existed have no cost.

## Inputs and reproducibility

### `inputs/config.resolved.yaml`

The schema-versioned run lock. It expands the small input configuration with the
source adapter's repository `runtime`, Oracle Bench's pinned `toolchain`, agent
defaults, dataset revision, and absolute host paths. During a run, `task.prompt`
points to this attempt's frozen `prompt.txt`. Credentials and credential values
are not stored. See the [configuration reference](configuration.md).

### `inputs/instance.json`

The resolved dataset record and provenance. It includes the base commit, golden
repair patch, private reference-test patch and IDs, source dataset metadata, a
record hash, and the original record. This is private evaluator data that may
reveal the defect and repair; it must not be exposed to the generation agent.

### `inputs/prompt.txt`

The exact prompt delivered to the agent after resolving its environment
variables. Agent-facing instructions remain in the selected prompt template.
For localized tasks this contains the target from `target.json`, but no private
patch text, changed line numbers, or expected behavior.

### `inputs/target.json`

The localized target: the part of the buggy repository the agent is told to test.
Only localized runs have it. It names the existing code the golden repair
changes, found by comparing the syntax trees of the buggy and repaired files. It
contains no patch text or changed lines.

| Field | Meaning |
|---|---|
| `level` | `function`, `class` or `file`: the most specific level any changed file reaches |
| `files` | One entry per file, each with `path`, `names` and `lines` |
| `files[].names` | Qualified functions (`Outer.method`) or classes, in file order; empty for a whole-file entry |
| `files[].lines` | Lines the entry spans in the buggy file |
| `text` | Exactly what replaces `${test_target}` in the prompt |

The level is chosen across all changed production files. Changed existing
functions win. Classes whose own body changed are used only when no function
changed, and whole files only when neither did. Comment and formatting edits, and
definitions the repair adds, never enter the target.

### `image-build/runtime.json`

The exact locally built Docker image ID used by the run. Reevaluation requires
this image to remain available. Its source images are detailed in
`image-build/images.json`.

## `image-build/`

### Build contexts and inputs

`image-build/harnesses/` and `image-build/runtime/` contain copies of the
checked-in Dockerfiles, the harness package lock, and public Python helpers.
These directories are isolated build contexts containing only approved assets.
Host run data and credentials do not enter either context. The runtime recipe
copies the complete locked harness toolchain from its reusable image and extends
the prepared repository image.

`requested.json` records source/Node references, platform, all installed harness
pins, and the package-lock hash before any pull. `harnesses.inputs.json` and
`runtime.inputs.json` record non-secret arguments, platform, recipe name, and
SHA-256 hashes of all context files before their builds. Cache tags depend on
these inputs, including lockfile and helper contents.
### `image-build/images.json`

Configured references and resolved IDs or digests for the repository, Node,
shared harness-toolchain, and final runtime images. It also records the requested
harness pins, lockfile hash, and each installed CLI's observed `--version`
output. This record is updated as each image becomes available, so interrupted
builds can contain only the completed stages.

### `image-build/output.log`

Raw SDK JSON events from pulls and builds, including build failure evidence.
It may be empty when all images are cached.

## `generation/`

### `generation/session.log`

The readable agent transcript generated from `trace.jsonl`: prompt, agent
messages, exposed tool calls and results, errors, standard error, and the harness
result. This is the best file for reviewing what the agent did.

It cannot include hidden reasoning that the provider did not expose. Tool output
may already be truncated by the CLI. `oracle-bench report` can rebuild it without
a model call.

### `generation/trace.jsonl`

Raw newline-delimited events emitted by Codex, Claude Code, or OpenCode. Event shapes depend
on the pinned CLI version. This is the authoritative low-level transcript used to
extract completion, errors, usage, cost, and `session.log`.

### `generation/command.json`

The exact agent CLI argument vector, including model, isolation flags, provider
overrides, and the configured stopping limit. Credential values are absent.

### `generation/version.txt`

Output of the selected harness's `--version` command inside the container.

### `generation/final.txt`

The agent's final natural-language response. It may be empty when generation
fails or times out.

### `generation/result.json`

Normalized generation metadata.

| Field | Meaning |
|---|---|
| `status` | `completed`, `failed`, or `timeout` |
| `exit_code` | Agent process exit code |
| `duration_seconds` | Agent process wall time |
| `timed_out` | Whether the harness timeout terminated it |
| `turn_completed`, `turn_failed` | Terminal event flags parsed from the trace |
| `errors` | Structured errors extracted from the trace |
| `unparsed_trace_lines` | Lines that were not valid JSON |
| `usage` | Token counts with one meaning for every harness, or null |

`usage` has the same fields for every harness:

| Field | Meaning |
|---|---|
| `input_tokens` | Every prompt token, read from cache or not |
| `cached_input_tokens` | Prompt tokens read from cache |
| `output_tokens` | Every generated token, reasoning included |
| `reasoning_tokens` | Reasoning tokens, or null when the CLI does not report them |
| `raw_usage` | The CLI's own usage object. OpenCode reports usage per step, so this is `{"steps": [...]}` |

Each CLI counts tokens differently. Claude Code and OpenCode leave cached prompt
tokens out of their input count, and OpenCode leaves reasoning out of its output
count. Each adapter adds them back.

### Agent logs

| File | Contents |
|---|---|
| `stderr.log` | Agent CLI standard error; often empty on success |
| `setup.log` | Generation-container creation, repository reset/rebuild, test visibility, and initial snapshot diagnostics |
| `launch.log` | Agent launch and setup diagnostics |
| `capture.log` | Final snapshot, workspace diff, and generated-file copy diagnostics |

Agent stdout goes to `trace.jsonl`, so `launch.log` is not the transcript. SDK
transfers and snapshot tools are normally silent. Streamed agent output is
redacted before host writes, including credentials split across output chunks.

### Workspace preparation inputs

`generation/workspace.json` (and the corresponding file in each evaluation directory)
records public workspace preparation inputs: repository path, generated directory,
test visibility, and test globs. It contains no private patches or credentials.

### `generation/baseline.txt`

The Git commit created immediately before generation. It includes the selected
existing-test visibility and empty generated-test directory and anchors the final
workspace diff.

### `generation/before.json` and `generation/after.json`

Complete filesystem metadata snapshots before and after generation. Every
repository-relative path maps to its entry kind, Unix mode, size, and SHA-256.
Their comparison determines allowed files and forbidden changes independently of
Git status. These can be large, but contain metadata rather than every file's
contents.

### `generation/workspace.diff`

The complete Git-oriented diff from `baseline.txt` through the final agent state,
including staged, unstaged, and untracked changes. Use it to inspect policy
violations and production edits.

## `submission/`

### `submission/files/`

The frozen submission. Each allowed new regular file is copied here while
preserving its repository-relative path. Evaluations copy these exact files into
fresh buggy and golden workspaces. The directory may be absent for an empty
submission.

### `submission/manifest.json`

The artifact inventory and compliance decision.

| Field | Meaning |
|---|---|
| `files` | Allowed paths mapped to SHA-256 and Unix mode |
| `forbidden_changes` | Changed paths that violated capture policy |
| `compliant` | Whether `forbidden_changes` is empty |
| `empty` | Whether no allowed files were captured |
| `sha256` | Hash of the canonical `files` mapping |

Before evaluation, Oracle Bench verifies the manifest checksum, every captured
file's checksum, and that the bundle contains no unmanifested files. It rejects
silent modification of the frozen artifact.

## `ground-truth/`

`issue.md` contains the SWE-bench problem statement in readable form.
`fix.patch` is the exact golden patch applied to transform the buggy checkout.
Both are private evaluator evidence retained for human analysis; neither is
available inside the generation container.

## Evaluation directories

`evaluation/buggy/`, `evaluation/golden/`, `reference-check/buggy/`, and
`reference-check/golden/` use the same layout. The evaluation pair evaluates generated tests; the reference pair checks
the private developer regression before generation. Golden directories also have
`repair.patch`; reference directories have `reference.patch`.

### `tests.json`

Normalized pytest results.

| Field | Meaning |
|---|---|
| `status` | Overall execution status |
| `exit_code` | pytest exit code |
| `collected` | Number of collected tests |
| `collection_errors` | Structured collection failures |
| `tests` | Pytest node IDs mapped to outcomes and phase records |

Each test records its normalized outcome and available setup, call, and teardown
phases, including phase outcome, duration, and failure details. Outcomes preserve
passes, failures, skips, expected failures, unexpected passes, and setup or
teardown errors.

Incomplete statuses include `no_tests`, `collection_error`, `timeout`,
`runner_error`, and `infrastructure_error`, with an `error` explanation. A failing
assertion is an ordinary per-test failure and does not make the overall execution
incomplete.

pytest runs with `--continue-on-collection-errors`. A file that fails to collect is
listed in `collection_errors`, and the other files still run. The status is
`completed` when any test ran, and `collection_error` only when collection errors
left no test to run. The report names the versions with files that failed to
collect.

### Coverage files

| File | Contents |
|---|---|
| `coverage.json` | Stable normalized status, totals, percentage, and per-file data used by Oracle Bench |
| `coverage.raw.json` | Detailed unmodified JSON emitted by the configured coverage.py version |
| `.coverage` | Binary coverage.py data file, requiring a compatible coverage version to inspect |

When coverage is unavailable, `coverage.json` contains `status: unavailable` and
a reason. Coverage failure does not discard test results. Source files that the
task's selected Python interpreter cannot parse are omitted from coverage rather
than making coverage unavailable for the rest of the source tree.

### Other execution files

| File | Contents |
|---|---|
| `imports.json` | Configured modules mapped to imported file paths, detecting accidental use of another installed copy |
| `runner.json` | Inputs to the in-container runner: workdir, output, targets, source roots, and imports |
| `command.json` | Concrete pytest command; distinct from the agent command |
| `execution.json` | Runner exit code, duration, and timeout flag |
| `output.log` | Workspace setup, patching, pytest, coverage, and Docker diagnostics |
| `repair.patch` | Exact golden source patch, only in golden executions |
| `reference.patch` | Private test patch, only in reference executions |

Start with `output.log` when `tests.json` reports a runner or infrastructure
failure.

## `reference-check/results.json`

The paired private-regression result. It has the same matrix shape as the paired
portion of `evaluation/results.json`. Generation begins only when
At least one `fail_on_buggy_pass_on_golden` matrix entry is required, verifying
the base commit, repair, private test, and environment before model spending.

## Reevaluation archives

Running:

```sh
uv run oracle-bench evaluate runs/<run-id>
```

moves the current contents of `evaluation/` into
`evaluation-history/<UTC timestamp>/`, verifies the frozen manifest, and evaluates it in
fresh containers. Generation evidence, provenance, runtime identity, and frozen
tests remain unchanged. Earlier reevaluations therefore remain available for
comparison.

Reevaluation marks an existing `judge/judgment.json` as `stale` because its saved
execution evidence has been replaced. Human judgments are marked stale for the
same reason. Each stale record retains the previous normalized judgment for audit.
Reevaluation never calls the judge model; invoke `oracle-bench judge` explicitly
to produce a new automated annotation.

`generation/session.log` is rendered from the original trace when generation
finishes. It is generation evidence rather than an evaluation result and is not archived.

## Empty, optional, and absent files

Several files are expected to be empty or absent in normal circumstances:

- `generation/launch.log`, `generation/capture.log`, and sometimes `generation/stderr.log` are
  empty when normally silent commands succeed;
- `image-build/output.log` may be minimal or absent when the runtime is cached;
- `generation/final.txt` may be empty after an agent failure;
- `submission/files/` may be absent for an empty submission;
- raw and binary coverage files may be absent when coverage fails;
- `evaluation/results.json` and `report.md` are absent if the pipeline stops before final
  evaluation.

Interpret absence together with `status.json`, the relevant `execution.json`, and
the closest stage log.

# Batch artifacts

`oracle-bench batch` writes a batch directory containing the original manifest,
the resolved and checksum-locked job list, batch status, `summary.json`, and
`report.md`. The summary preserves each job's state, run-directory link, paired
matrix counts, coverage, and generation measurements. When a run has judge
artifacts, its job record also contains the judge status, answer, conditional
details, model provenance, token usage, and duration. Runs without judge
configuration are recorded as `disabled`; configured runs without a result are
`missing`. Invalid, failed, timed-out, and stale judgments remain explicit and do
not make an otherwise completed evaluation resumable.

The Markdown job table shows each run ID as plain text alongside the linked
instance name, so runs can be located under `runs/<run-id>` without following a
Markdown link. Jobs that failed before creating a run show `—` instead.

`summary.json` distinguishes three denominators:

- `attempted`: jobs that started;
- `completed_evaluations`: jobs whose buggy and golden executions both completed;
- `judge.valid_judgments`: jobs with a normalized `completed` judgment.

`matrix_detection_rate` is the fraction of completed evaluations with a compliant
fail-on-buggy/pass-on-golden result. A non-compliant submission stays in the
denominator and counts as not detected: editing files outside the generated
directory is an agent failure. Runs without two completed evaluations, including
runs that stopped before generation, are left out. `judge_tests_issue_yes_rate` is the fraction of valid judgments whose
`tests_issue` answer is `yes`. These metrics answer different questions and are
reported separately.

The `judge` object contains status counts, per-facet label frequencies, and these
cross-tabs:

- matrix detection by the `tests_issue` answer;
- attempt detail by the presence or absence of each matrix cell.

Each job record carries its run's `cost_status` and `cost_usd` from `cost.json`,
including for a run that failed after generation started. `total_cost_usd` sums
every job with a cost, and `costed_runs` counts them; the total is `null` when
none has one. The Markdown report
presents a compact per-run comparison and the facet frequency tables; the
complete cross-tabs remain in `summary.json`.

# Agreement artifacts

After blinded human ratings have been collected, run the offline agreement
analysis for a batch:

```sh
uv run oracle-bench agreement batches/<batch-id> --sample-per-stratum 8
```

The command writes `agreement/report.md`, `agreement/summary.json`, and
`agreement/calibration-sample.json` inside the batch directory. It never loads
credentials, starts containers, or calls a model.

Agreement is reported independently for the answer and both conditional fields,
and separately
for human–human and human–LLM comparisons. Each result contains its observation
count, raw agreement, an observation-weighted mean of the per-rater-pair Cohen's
kappas, a pooled confusion matrix, and individual disagreements. Each rater pair
also retains its own expected agreement and Cohen's kappa. A pair enters these calculations
only when both ratings are valid and complete. Possible, available, and missing
pair counts are explicit, and every absent rater is retained as `status:
missing` in the per-run records. Kappa is `null` when no observations exist or
when expected agreement is one.

The report records every rubric, LLM prompt, human instruction, and workspace
specification hash together with the judge model identities. This makes results
from changed rating conditions visible instead of silently combining them.

The calibration sample lives only in `calibration-sample.json`; `summary.json`
carries the agreement figures and per-run ratings. The sample selects a
deterministic maximum per stratum for F→P, F→F, P→P, invalid, and LLM-judged
unrelated submissions, ordered by a hash of each run's instance ID and directory
name, so relocating a batch does not change the selection. A run may appear in
more than one stratum. The sample records whether every unique selected run has
at least two completed human ratings; agreement is not presented as calibrated
until that condition is met. Do not give the sample manifest or existing labels
to raters—the interactive workspace remains the blinded rating interface.
