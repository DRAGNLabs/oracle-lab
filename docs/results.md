# Results and artifacts reference

Every `oracle-bench run` creates a timestamped run directory under the configured
`output` path. It is the complete record of one agent attempt: its inputs, the
resolved dataset instance, the runtime, the agent's activity, the captured
submission, the reference check, the paired evaluations, and the report.

## Directory layout

```text
runs/<run-id>/
├── status.json
├── cost.json
├── report.md
├── inputs/
│   ├── config.resolved.yaml
│   ├── instance.json
│   ├── prompt.txt
│   ├── target.json (localized runs only)
│   └── target.log (localized runs only)
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

A run that stops early holds only the files written before the failure. For
example, a run whose image build failed has no generation or evaluation files.
When a file is missing, check `status.json` and the last stage's log.

Generation, judge, and classification turns are retried after a recognized
transient API or transport error. There are up to three retries, after 30, 90,
and 180 seconds. Each retry uses a fresh container and workspace. The earlier
attempt is kept under `generation-history/`, `judge-history/`, or a
classification attempt's `agent-history/`. Other harness failures and deadlines
are not retried; they end with their normal status.

## Classification attempts

Task classifications are stored centrally, not in run directories:

```text
classifications/<instance-id>/<timestamp>-<id>/
├── inputs/
│   ├── config.resolved.yaml
│   ├── instance.json
│   └── rubric.md
├── image-build/
├── agent/
├── agent-history/ (when a transient failure was retried)
├── workspace-spec.json
├── classification.raw.txt
├── classification.json
├── cost.json
└── status.json
```

Every `classify` call creates a new attempt, and attempts are never changed.

A run's report finds its classification like this:

1. It looks in the `classifications` directory beside the run's `runs` output.
2. It picks the newest attempt for the run's exact `instance_id`.
3. It checks that attempt's base commit and source record checksum.

The report reads the attempt in place; nothing is copied into the run. If the
newest attempt failed or is invalid, the report shows that. It does not fall back
to an older attempt.

`classification.json` holds the instance and rubric hashes and the normalized
result:

- A completed in-scope result has every rubric facet.
- An out-of-scope result has only the task nature and the rationale.
- `invalid_output`, `failed`, and `timed_out` are explicit states.

Beside it, for audit, are the raw final response, the frozen rubric, the harness
trace, the model and image provenance, and the manifest of what the workspace
exposed. `cost.json` records the classifier turn's spend, in the same format as a
run's [`cost.json`](#costjson).

## Judge workspace manifests

`workspace-spec.json` records:

- the prepared image ID and digests;
- the base commit;
- the hashes of the repair, the reference-test patch, and the rubric;
- how the repository views were built, and the commands to rebuild them;
- every artifact the judge can see.

`bundle-manifest.json` is the short list of each destination path and its
SHA-256.

The judge's disposable container receives:

- `rubric.md` and the rendered `instructions.md`;
- the `/oracle-judge/buggy` and `/oracle-judge/golden` repository views, each
  with the generated submission;
- the issue and the patches;
- the saved evaluation evidence.

Oracle Bench verifies the submission bundle before the transfer. It checks every
generated file again inside both views. The repository views are not copied back
to the host. The saved run files and the workspace specification are enough to
rebuild them.

[Judging](judging.md) covers running the judge and collecting human ratings.

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
- Files: links to the files that exist, grouped by stage. This includes the
  private ground truth, which is written on the host and never reaches the
  generation container.

`oracle-bench report runs/<run-id>` rebuilds this file from saved data. It runs
no tests and calls no model.

The judge shows as `disabled` when the run has no frozen rubric, and as `missing`
when it has a rubric but no `judgment.json`. A failed, timed-out, invalid, or
stale judgment shows its diagnostic fields, not labels.

### `evaluation/results.json`

The machine-readable summary of the generated tests. Each version's `tests.json`
is validated as it comes out of the container. Unreadable runner output
therefore becomes an explicit incomplete result, not a gap found later. Files
carry no schema version. A file written by an earlier revision fails to load; it
is never half-read.

| Field | Meaning |
|---|---|
| `instance_id` | Dataset instance evaluated |
| `agent` | Copy of `generation/result.json` |
| `artifact_sha256` | Checksum over the generated-file manifest entries |
| `submission_compliant` | Whether the agent made only permitted changes |
| `forbidden_changes` | Paths changed outside the allowed submission policy |
| `task_scope` | `localized` or `repository`, matching the generation assignment |
| `test_target` | The localized target the agent was shown, copied from `inputs/target.json`, or null for a repository run |
| `existing_tests` | Test-visibility policy; `keep` means existing tests were visible |
| `buggy_status`, `golden_status` | Overall execution status for each version |
| `matrix` | Counts and test IDs in each binary paired cell |
| `tests` | One paired row per test ID |
| `other_outcomes` | Tests excluded from the binary matrix |
| `failure_kinds` | Assertion, other-exception, and unknown failure counts for each version |
| `coverage` | Normalized coverage summaries for both versions |

Two values are computed, not stored. A run is diagnostic-only when
`submission_compliant` is false. It detected the bug when it is compliant, both
versions completed, and the `fail_on_buggy_pass_on_golden` count is above zero.

Each `tests` row has `test_id`, `buggy`, `golden`, and `cell`. A failing side
also has `buggy_failure` or `golden_failure`, with a failure kind, exception
type, and message. `cell` is null when either execution is incomplete or either
outcome is not a plain pass or fail.

| Matrix key | Buggy | Golden | Meaning |
|---|---|---|---|
| `pass_on_both` | pass | pass | Test accepts both implementations |
| `fail_on_buggy_pass_on_golden` | fail | pass | Test detects behavior repaired by the golden patch |
| `pass_on_buggy_fail_on_golden` | pass | fail | Test prefers buggy behavior or rejects the repair |
| `fail_on_both` | fail | fail | Test rejects both implementations |

The cells describe behavior. They do not prove whether a test's oracle is
correct. Skips, expected failures, setup or teardown errors, collection
failures, missing test IDs, and incomplete runs go in `other_outcomes`.

### `status.json`

The run's latest checkpoint: `stage`, `state`, `updated_at`, and an error if
there was one. The stages, in order:

1. `resolve`
2. `build`
3. `localize` (localized runs only)
4. `reference`
5. `generate`, which includes capturing the submission
6. `evaluate`
7. `judge` (when configured)
8. `cost`, the wait for the spend reading in [`cost.json`](#costjson)
9. `finished`

The final state is `completed` only when the agent and both evaluations
completed and the submission was compliant. Otherwise it is
`completed_with_errors`, and the results are still kept. An exception records
the stage it happened in and a failed state.

`judge_status` is separate. It is `disabled`, `completed`, `invalid_output`,
`failed`, `timed_out`, `skipped`, `stale`, or `missing`. A configured judge is
`skipped`, with no model call, when generation wrote no test files or neither
version collected a generated test (`no_tests`). Tests captured from a timed-out
or otherwise incomplete generation are still evaluated and judged. A judge
failure never changes a completed evaluation's state.

### `cost.json`

What the run's model stages cost. The host reads the OpenRouter key's total
spend (`GET /api/v1/key`) twice: just before generation, and after the judge (or
after evaluation, when there is no judge). The difference is the run's cost for
generation and the judge together. No other stage calls a model.

OpenRouter updates the total about a minute after a request, and readings taken
close together can disagree. So the final reading:

- polls every 5 seconds;
- waits at least 60 seconds;
- accepts the total once it has risen and three readings in a row match;
- gives up at 180 seconds.

| Field | Meaning |
|---|---|
| `status` | `measured`; `unsettled` (180 s passed without a steady reading, so the last reading is used); `unavailable` (a lookup failed); `not_measured` (a model stage does not use OpenRouter) |
| `cost_usd` | `after - before` in US dollars, or null |
| `before`, `after` | The key's total spend at each reading |
| `waited_seconds` | Time spent waiting for the final reading |
| `error` | The lookup error, when `unavailable` |

The difference counts everything charged to the key while the model stages ran.
Any other use of the key in that time inflates it, so give the benchmark its own
key. A run that fails after generation starts is still measured. An interrupted
run is not. A cost failure never changes the run's state.

## `judge/`

### `judge/judgment.json`

The normalized judgment. A completed one has `status`, `tests_issue`,
`attempt_detail`, `no_attempt_reason`, `cheating`, and `rationale`. A conditional
field that does not apply is saved as JSON `null`, even when the response used
the string `"null"`. The raw response is kept as well. The other states:

| Status | Meaning |
|---|---|
| `invalid_output` | The final response was not one valid rubric JSON object |
| `failed` | Harness or judge-workspace execution failed |
| `timed_out` | The judge exceeded its wall-time limit |
| `skipped` | No tests were collected, so no judge turn ran; `reason` says why |
| `stale` | Reevaluation replaced the execution evidence; the prior annotation is nested for audit |

### Judge model evidence

`judgment.raw.txt` is the model's final response before parsing. `prompt.md`
and `rubric.md` are the exact instructions it got. `agent/result.json` records
the status, usage, duration, harness, provider, model, CLI version, and wall-time
limit. The other files in `agent/` match their namesakes under `generation/`.

## Inputs and reproducibility

### `inputs/config.resolved.yaml`

The full configuration the run used. It adds to the input file the repository
`runtime`, the pinned `toolchain`, every default, the dataset revision, and
absolute host paths. `task.prompt` points to this run's frozen `prompt.txt`.
Credentials are not stored. See the [configuration reference](configuration.md).

### `inputs/instance.json`

The resolved dataset record and where it came from. It has the base commit, the
golden repair patch, the private reference-test patch and test IDs, the dataset
metadata, a hash of the record, and the original record. It reveals the bug and
the fix, so the generation agent must never see it.

### `inputs/prompt.txt`

The exact prompt sent to the agent, with its variables filled in. For a
localized run it includes the target from `target.json`. It never includes patch
text, changed line numbers, or expected behavior.

### `inputs/target.json`

The part of the buggy repository the agent is told to test. Only localized runs
have it. It names the existing code the golden repair changes. That code is
found by comparing the syntax trees of the buggy and repaired files. The file
holds no patch text or changed lines.

| Field | Meaning |
|---|---|
| `level` | `function`, `class` or `file`: the most specific level any changed file reaches |
| `files` | One entry per file, each with `path`, `names` and `lines` |
| `files[].names` | Qualified functions (`Outer.method`) or classes, in file order; empty for a whole-file entry |
| `files[].lines` | Lines the entry spans in the buggy file |
| `text` | Exactly what replaces `${test_target}` in the prompt |

One level is chosen for all changed production files. Changed existing
functions come first. Classes whose own body changed are used only when no
function changed. Whole files are used only when neither changed. Comment and
formatting edits never enter the target, and nor do definitions the repair adds.

`inputs/target.log` is the container output from reading the buggy files. The
stage first resets the checkout to `base_commit`, as the agent's workspace is, so
it reads the same code the agent sees.

## `image-build/`

### `image-build/runtime.json`

The ID of the locally built Docker image the run used. Reevaluation needs this
image to still exist. `images.json` describes the images it was built from.

### Build contexts and inputs

`image-build/harnesses/` and `image-build/runtime/` are the two build contexts.
They hold copies of the checked-in Dockerfiles, the harness package lock, and the
public Python helpers, and nothing else. No run data or credentials enter them.
The runtime recipe copies the locked CLI toolchain from its shared image onto the
prepared repository image.

These are written before anything is pulled or built:

- `requested.json`: the source and Node image references, the platform, every
  CLI pin, and the package-lock hash.
- `harnesses.inputs.json` and `runtime.inputs.json`: the build arguments (none
  secret), platform, recipe name, and the SHA-256 of every context file.

Cache tags are derived from these inputs, so a changed lockfile or helper makes a
new tag.

### `image-build/images.json`

The configured references and resolved IDs or digests for four images: the
repository, Node, the shared CLI toolchain, and the final runtime. It also
records the requested CLI pins, the lockfile hash, and each installed CLI's
actual `--version` output. It is updated as each image becomes ready, so an
interrupted build records only the finished steps.

### `image-build/output.log`

Raw SDK JSON events from pulls and builds, including any build failure. It may
be empty when every image was cached.

## `generation/`

### `generation/session.log`

The readable agent transcript, built from `trace.jsonl`. It shows the prompt,
the agent's messages, tool calls and their results, errors, standard error, and
the harness result. It is the best file for reviewing what the agent did.

It cannot show reasoning the provider hid. The CLI may already have truncated
tool output. It is written once, after generation. `oracle-bench report` never
rebuilds it.

### `generation/trace.jsonl`

The raw JSON Lines events from Codex, Claude Code, or OpenCode. Their shape
depends on the pinned CLI version. This is the source of truth: completion,
errors, usage, and `session.log` are all read from it. Cost is not; it comes from
the OpenRouter key (see [`cost.json`](#costjson)).

### `generation/command.json`

The exact agent CLI arguments: the model, isolation flags, provider overrides,
and the wall-time limit. No credential values.

### `generation/version.txt`

The selected CLI's `--version` output, run inside the container.

### `generation/final.txt`

The agent's final message. It may be empty when generation fails or times out.

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
tokens out of their input count. OpenCode leaves reasoning out of its output
count. Each adapter adds them back.

### Agent logs

| File | Contents |
|---|---|
| `stderr.log` | Agent CLI standard error; often empty on success |
| `setup.log` | Generation-container creation, repository reset/rebuild, test visibility, and initial snapshot diagnostics |
| `launch.log` | Agent launch and setup diagnostics |
| `capture.log` | Final snapshot, workspace diff, and generated-file copy diagnostics |

Agent stdout goes to `trace.jsonl`, so `launch.log` is not the transcript. File
transfers and snapshots are normally silent. Agent output is redacted before it
is written on the host, even when a credential is split across output chunks.

### Workspace preparation inputs

`generation/workspace.json`, and the same file in each evaluation directory,
records how the workspace was prepared: the repository path, generated
directory, test visibility, and test globs. It holds no private patches or
credentials.

### `generation/baseline.txt`

The Git commit made just before generation. It reflects the test-visibility
setting and the empty generated-test directory. The final workspace diff starts
from it.

### `generation/before.json` and `generation/after.json`

Snapshots of the repository's file metadata before and after generation. Each
path maps to its kind, Unix mode, size, and SHA-256. Comparing the two decides
which files are allowed and which changes are forbidden, without relying on Git.
They can be large, but they hold metadata, not file contents.

### `generation/workspace.diff`

The full Git diff from `baseline.txt` to the agent's final state, including
staged, unstaged, and untracked changes. Use it to inspect forbidden changes and
production edits.

## `submission/`

### `submission/files/`

The frozen submission. Each allowed new regular file is copied here at its
repository-relative path. Evaluation copies these exact files into fresh buggy
and golden workspaces. The directory always exists; an empty submission leaves
it empty.

### `submission/manifest.json`

What was captured, and whether the submission is compliant.

| Field | Meaning |
|---|---|
| `files` | Allowed paths mapped to SHA-256 and Unix mode |
| `forbidden_changes` | Changed paths that violated capture policy |
| `compliant` | Whether `forbidden_changes` is empty |
| `empty` | Whether no allowed files were captured |
| `sha256` | Hash of the canonical `files` mapping |

Evaluation and judging each verify the bundle once, at the start of the stage.
The check covers the manifest's hash, every file's hash, and that no file is
missing from the manifest. Any change to the frozen files fails it. Both code
versions and the judge then receive that one verified bundle.

## `ground-truth/`

`issue.md` is the SWE-bench problem statement, formatted for reading.
`fix.patch` is the exact golden patch that turns the buggy checkout into the
fixed one. Both are kept for people analyzing the run. Neither is ever inside the
generation container.

## Evaluation directories

`evaluation/buggy/`, `evaluation/golden/`, `reference-check/buggy/`, and
`reference-check/golden/` share one layout. The evaluation pair runs the
generated tests. The reference pair runs the private developer test before
generation. Golden directories also have `repair.patch`. Reference directories
also have `reference.patch`.

### `tests.json`

Normalized pytest results.

| Field | Meaning |
|---|---|
| `status` | Overall execution status |
| `exit_code` | pytest exit code |
| `collected` | Number of collected tests |
| `collection_errors` | Structured collection failures |
| `tests` | Pytest node IDs mapped to outcomes and phase records |

Each test records its outcome and each phase it reached (setup, call,
teardown), with the phase's outcome, duration, and failure details. Outcomes
keep passes, failures, skips, expected failures, unexpected passes, and setup or
teardown errors apart.

The incomplete statuses are `no_tests`, `collection_error`, `timeout`,
`runner_error`, and `infrastructure_error`. Each comes with an `error`
explanation. A failing assertion is an ordinary test failure. It does not make
the execution incomplete.

pytest runs with `--continue-on-collection-errors`. A file that fails to collect is
listed in `collection_errors`, and the other files still run. The status is
`completed` when any test ran, and `collection_error` only when collection errors
left no test to run. The report names the versions with files that failed to
collect.

### Coverage files

| File | Contents |
|---|---|
| `coverage.json` | The normalized status, totals, percentage, and per-file data Oracle Bench uses |
| `coverage.raw.json` | The unmodified JSON report from the configured coverage.py version |
| `.coverage` | coverage.py's binary data file; reading it needs a compatible coverage version |

When coverage is unavailable, `coverage.json` has `status: unavailable` and a
reason. A coverage failure never discards test results. A source file the
project's Python cannot parse is left out of coverage. The rest of the source
tree is still measured.

### Other execution files

| File | Contents |
|---|---|
| `imports.json` | Each configured module and the file it imported from; catches an import from another installed copy |
| `runner.json` | The in-container runner's inputs: workdir, output, targets, source roots, and imports |
| `command.json` | The pytest command (not the agent command) |
| `execution.json` | The runner's exit code, duration, and timeout flag |
| `output.log` | Output from workspace setup, patching, pytest, coverage, and Docker |
| `repair.patch` | The exact golden source patch; golden executions only |
| `reference.patch` | The private test patch; reference executions only |

When `tests.json` reports a runner or infrastructure failure, start with
`output.log`.

## `reference-check/results.json`

The paired result of the private developer test. Its matrix has the same shape
as the one in `evaluation/results.json`. Generation starts only when it has at
least one `fail_on_buggy_pass_on_golden` entry. This proves the base commit,
repair, private test, and environment work before any money is spent on a model.

## Reevaluation archives

Running:

```sh
uv run oracle-bench evaluate runs/<run-id>
```

does three things:

1. It moves the current `evaluation/` into `evaluation-history/<UTC timestamp>/`.
2. It verifies the frozen manifest.
3. It evaluates the frozen tests in fresh containers.

Generation files, provenance, the runtime image, and the frozen tests stay
unchanged. Every earlier evaluation stays available for comparison.
`generation/session.log` belongs to generation, so it is not archived.

Reevaluation marks an existing `judge/judgment.json` as `stale`, because the
evidence it rated has been replaced. Human judgments become stale for the same
reason. Each stale record keeps the previous judgment inside it. Reevaluation
never calls the judge model. Run `oracle-bench judge` to get a new LLM judgment.

## Empty, optional, and absent files

Some files are normally empty or absent:

- `generation/launch.log`, `generation/capture.log`, and often
  `generation/stderr.log` are empty when their commands succeed quietly.
- `image-build/output.log` may be short or absent when the runtime was cached.
- `generation/final.txt` may be empty after an agent failure.
- `submission/files/` is empty for an empty submission.
- The raw and binary coverage files may be absent when coverage fails.
- `evaluation/results.json` and `report.md` are absent when the pipeline stops
  before final evaluation.

To explain a missing file, read `status.json`, the relevant `execution.json`, and
the nearest stage log.

## Batch artifacts

`oracle-bench batch` writes a batch directory with the original manifest, the
resolved job list with its checksums, the batch status, `summary.json`, and
`report.md`.

The summary keeps each job's state, run-directory link, paired matrix counts,
coverage, and generation measurements. A job whose run has judge files also
records the judge's status, answer, conditional fields, model, token usage, and
duration. A run without a judge is recorded as `disabled`. A run with a judge
but no result is `missing`. Invalid, failed, timed-out, and stale judgments are
recorded as such. They never make a job with a completed evaluation count as
unfinished on `--resume`.

The Markdown job table shows each run ID as plain text beside the linked
instance name. You can then find the run under `runs/<run-id>` without following
a link. A job that failed before creating a run shows `—`.

`summary.json` distinguishes three denominators:

- `attempted`: jobs that started;
- `completed_evaluations`: jobs whose buggy and golden executions both completed;
- `judge.valid_judgments`: jobs with a normalized `completed` judgment.

`matrix_detection_rate` is the share of completed evaluations that are compliant
and have a fail-on-buggy/pass-on-golden result. A non-compliant submission stays
in the denominator and counts as not detected, because editing files outside the
generated directory is an agent failure. Runs without two completed evaluations
are left out, including runs that stopped before generation.

`judge_tests_issue_yes_rate` is the share of valid judgments whose `tests_issue`
answer is `yes`. The two rates answer different questions, so they are reported
separately.

The `judge` object has status counts, label counts for each field, and these
cross-tabulations:

- matrix detection by the `tests_issue` answer;
- attempt detail by the presence or absence of each matrix cell.

Each job record has its run's `cost_status` and `cost_usd` from `cost.json`. A
run that failed after generation started has them too. `total_cost_usd` sums
every job with a cost, and `costed_runs` counts those jobs. The total is `null`
when no job has a cost.

The Markdown report shows a short per-run comparison and the label count tables.
The full cross-tabulations are only in `summary.json`.

## Agreement artifacts

`oracle-bench agreement` writes `agreement/report.md`, `agreement/summary.json`,
and `agreement/calibration-sample.json` inside the batch directory.

Agreement is reported separately for each label (`tests_issue`,
`attempt_detail`, `no_attempt_reason`, and `cheating`). Within each label,
human–human and human–LLM agreement are also separate. Each result has:

- the number of observations;
- raw agreement;
- Cohen's kappa (agreement corrected for chance) for each rater pair, and their
  mean weighted by observations;
- a pooled confusion matrix;
- each disagreement.

Each rater pair also keeps its own expected agreement. A pair counts only when
both ratings are valid and complete. The possible, available, and missing pair
counts are all reported. A rater with no rating appears as `status: missing` in
the per-run records. Kappa is `null` when there are no observations, or when
expected agreement is one.

The report records the hash of every rubric, LLM prompt, human instruction, and
workspace specification, plus the judge models. Ratings made under different
conditions then show up as different, instead of being merged without notice.

`summary.json` holds the agreement figures and per-run ratings.
`calibration-sample.json` holds the calibration sample, and only it. The sample
takes up to a fixed number of runs from each group: F→P, F→F, P→P, invalid, and
submissions the LLM judged unrelated. (F→P means failed on buggy, passed on
golden.) Runs are ordered by a hash of the instance ID and directory name, so
moving a batch does not change the choice. A run may fall in more than one
group. The sample records whether every selected run has at least two completed
human ratings. Until it does, agreement is not reported as calibrated.
