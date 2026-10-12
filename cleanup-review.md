# Oracle Bench cleanup review

2026-09-30, branch `cleanup` at `1d6ff8f`. E4, E6, E7 and E8 are done and removed;
the remaining IDs keep their original numbers.

Findings have IDs so we can work through them one at a time:

- **E**: things that change experiment results.
- **T**: test gaps, and tests that check the wrong thing.
- **C**: code structure and duplication.
- **D**: documentation.

Evidence comes from reading every source and test file, a branch-coverage run
(92% overall; per-module numbers below), and scripts run against the cached
SWE-bench Verified dataset at the pinned revision. Nothing under `runs/` was
modified.

---

## E. Experiment-critical

### E1. The localized target names a function the repair did not touch

**Where:** `src/oracle_bench/instance/swebench.py:100`.

A hunk header names the definition *above* the hunk. The code falls back to it
whenever the hunk has not shown a definition yet. When the repair adds a new
function or method, the added lines hit that fallback, and the untouched
function above gets named.

- `pytest-dev__pytest-10051`, which is in the expanded-validation batch, gets
  `src/_pytest/logging.py (clear, reset)`. `LogCaptureHandler.reset` is
  unchanged. The repair adds `LogCaptureHandler.clear` below it.
- Across the 192 supported Verified instances that get a target, **9** name an
  unchanged definition this way. Examples: `pylint-dev__pylint-8898` names
  `_check_csv` when the repair adds `_check_regexp_csv` below it;
  `scikit-learn__scikit-learn-13439` names `_iter`.

**Effect:** the agent is pointed at code the bug is not in.

### E2. Most target names are bare and ambiguous

Counts are over the 192 supported Verified instances that get a target.

- 326 of the 342 symbols are bare names without a class.
- 30 of them are bare dunder methods, like `__init__` or `__repr__`.
- 13 instances name a bare `__init__`. In a file with several classes, that does
  not say which one.
- Whether a name gets its class depends on whether the hunk happens to show the
  `class` line. So target precision varies from instance to instance for reasons
  unrelated to the task.
- Example: `pydata__xarray-3095` lists both `PandasIndexAdapter.__init__` and a
  bare `__init__` for the same file. Three instances mix the two forms this way.

### E3. Other ways the diff-text heuristic goes wrong

These are rare in Verified today. Each gives a wrong or empty symbol.

| Patch shape | Target today | Should be |
|---|---|---|
| Change inside a nested function `inner` in `outer()` | `inner`, which a test cannot reach | `outer` |
| Decorator change above `target()` | `previous` (the hunk header) | `target` |
| Body change after a multi-line signature ending `) -> int:` | file only | the function |
| Docstring text at column 0 inside a function | file only | the function |
| Deleted file (`+++ /dev/null`) after another file | the deleted names, credited to the *previous* file | the deleted file |
| Non-Python file under a source root | included as a target | excluded |

The original probe script lived in a session scratchpad and is gone.

**Recommendation for E1–E3:** get the target from the buggy file's abstract
syntax tree (AST) instead of from diff text.

1. Take the changed old-file line numbers from each hunk header (`-a,b`).
2. Read the file at `base_commit` with `git show` in the runtime image.
3. Parse it with the host's `ast`. That also covers the Python 3.6 image. If
   parsing fails, fall back to file only.
4. Name the innermost `Class.method` that encloses each removed line, and the
   insertion point of each added block.
5. Added names never appear in the buggy file, so they still cannot leak.

The cost: the target can only be computed after the build stage, so prompt
rendering moves out of resolve.

**Cheaper alternative:** fix only E1. Do not fall back to the header inside a
definition the repair adds. That removes all 9 wrong names and changes no other
target. It leaves E2 and E3 as they are.

I recommend the AST route. It is about as much code as the current state machine,
and it can be tested locally with small source fixtures.

### E5. Nothing checks that the scope and the prompt agree

`task.scope` and `task.prompt` are set independently.

- `scope: localized` with `unit-tests.md`, which has no `${test_target}`: the run
  is saved as localized, with `task_scope` and `test_target` in `results.json`,
  but the agent never saw the target. The data is mislabeled.
- `scope: repository` with `localized-tests.md`: the prompt reads "the following
  area of this repository: `this repository`" (`run.py:105`).

**Fix:** at resolve, require `${test_target}` in the template exactly when scope is
`localized`. That is a few lines plus one test.

---

## T. Tests

Coverage of the experiment-critical modules:

| Module | Line + branch coverage |
|---|---:|
| `evaluation/__init__.py` | 47% |
| `container/helpers/prepare.py` | 63% |
| `container/images.py` | 67% |
| `generation.py` | 70% |
| `repository.py` | 75% |
| `instance/swebench.py` | 83% |
| `evaluation/outcomes.py`, `results.py`, `judge/*` | 90–100% |

### T1. The core of evaluation has no local test

`run_version`, `check_reference` and `evaluate`
(`evaluation/__init__.py:86–181`) run only in the Docker suite, and only against
a saved run. No local test checks the rules that define the experiment:

- Golden gets the golden patch and then a rebuild. Buggy gets nothing.
- The reference run keeps existing tests and runs `reference_test_ids`.
- Evaluation hides existing tests and runs only `generated_dir`.
- An empty manifest returns `no_tests` without starting pytest.
- Both versions receive the same verified bundle.

**Fix:** a fake sandbox that records calls, like `RecordingSandbox` in
`test_repo_classification_run.py`, and one test per rule.

### T2. Capturing the submission has no local test

`capture()` (`generation.py:113`) decides what counts as the submission. Only
`changed_files` is tested. These are not:

- the download,
- the hash re-check,
- the manifest fields,
- the "changed during capture" failure.

### T3. Workspace preparation is tested in pieces, never as a whole

`prepare()` in `container/helpers/prepare.py` never runs in a test. What matters
is the end state the agent sees:

- no Git history,
- the hidden tests are gone,
- the generated directory exists and is empty,
- exactly one commit.

It can run locally on a temporary Git repository in under a second.

### T4. Record validation is untested, including the branch every real run takes

In `swebench.py` `resolve()`, none of these paths has a test:

- missing keys,
- a mismatched instance ID,
- a base commit that is not a full SHA,
- the JSON-string `FAIL_TO_PASS` branch (line 188).

The dataset stores `FAIL_TO_PASS` as a string, so that last branch is the one
every real run takes.

### T5. No test checks the detection rule directly

`EvaluationResult.detected` (`results.py:199`) needs a compliant submission, both
versions completed, and at least one fail-on-buggy/pass-on-golden test. No test
checks that a non-compliant submission with such a test is not counted as
detected, either in `results.json` or in the batch summary.

### T6. The localized-target tests only cover cases that already work

- There are five hand-written cases (`test_instance_source.py:80–141`).
- `test_localized_target_names_the_edited_definition_not_the_hunk_header` uses
  the third hunk of the real pytest-10051 patch.
- The second hunk of that same patch produces the wrong `reset` (E1), and no
  test covers it.

**Fix:** once E1–E3 are decided, add a table test over about 15 real Verified
patches with hand-checked expected targets, stored as small fixtures.

### T7. Two cheap guards are missing

- **Rubric labels.** The label names in both rubrics match the parsers'
  `Literal` values today; I checked. Nothing keeps them matched. A test that
  reads both rubrics would.
- **Scope and prompt.** The check from E5.

### T8. Tests that check the wrong things, or duplicate each other

- **Tests tied to a sample config.** Many tests use `configs/smoke.yaml` as
  their fixture: `test_config.py`, `test_containers.py:31`,
  `test_evaluation_execution.py:16` and `test_lifecycle.py`.
  `test_smoke_config_uses_five_minute_limits_for_generation_and_judging` even
  asserts the smoke model's name. Editing the sample config breaks unrelated
  tests. Move the fixture config into `tests/fixtures.py`.
- **`test_paths.py`** repeats `paths.py` line by line.
- **`test_verbose_schema_is_rejected`** tests schema sections removed long ago.
  The `agent.typo` case already covers unknown fields.
- **Misplaced Codex tests.** Codex trace parsing is tested in
  `test_lifecycle.py:35–51`. There is `test_claude.py` and `test_opencode.py`,
  but no `test_codex.py`.
- **Repeated fakes.** `client_context` appears 7 times, `sandbox_context` 3 times,
  and there are two `config` fixtures. `test_lifecycle.py:142` also keeps a
  module-level `spent` list that tests share. All of these belong in
  `conftest.py`.

---

## C. Code structure and duplication

### C1. The judge and the classifier are parallel copies of each other

| Concern | Judge | Classification |
|---|---|---|
| Status writer | `run.py:37` `status` | `repo_classification/run.py:195` `_status` (same code) |
| JSON-from-model parsing | `judge/contracts.py:244`, `:314` | `repo_classification/contracts.py:168`, `:234` (verbatim) |
| `MAX_RATIONALE_LENGTH` and the non-blank rationale check | 1 copy | 2 copies |
| Failure result model | `UnsuccessfulJudgment` | `UnsuccessfulClassification` (identical) |
| Failure constructor | `failed_judgment` | `failed_classification` (identical) |
| Turn result with provenance | `JudgeAttempt` | `ClassificationAttempt` (identical) |
| `ExposedArtifact` | 1 copy | 1 copy (identical) |
| Base model | `JudgeModel` | `ClassificationModel` (plus `ArtifactModel` in `results.py`) |
| "Record a failed turn" block | `judge/run.py:123–148` | `repo_classification/run.py:105–122` |
| Last error message from a result | `judge/run.py:161–166` | `repo_classification/run.py:135–139` (a third variant is in `report.py:79`) |

**Proposal:** one of each, all in `results.py`:

- an `AgentTurnResult` model,
- one `parse_json_object(raw)`,
- a `Rationale` type,
- `last_error_message(result)`,
- a single status writer.

### C2. The harness adapters repeat the trace loop and the status rule

- Three copies of "read JSON Lines, skip blanks, count malformed lines".
- Three copies of the timeout/failed/completed decision: `claude.py:93`,
  `codex.py:110` and `opencode.py:96`.
- Both belong in `launch.py`. AGENTS.md says to share behavior once more than one
  harness needs it; all three do.

### C3. The retry loop is written twice, and classification has none

- `generation.py:35` and `judge/run.py:108` both retry transient failures after
  30, 90 and 180 s in a fresh container, but they are written differently. One
  returns a `None` in a tuple; the other carries a `retry` flag out of a `with`
  block.
- Classification never retries, and nothing says why.
- Either share one helper, or write down the reason for the difference.

### C4. One rule, two implementations

- **Run completion.** `completion_state` (`run.py:50`) and `_title_lines`
  (`report.py:47`) both decide it. They agree today.
- **"Localized means the agent saw `test_target`".** Written at `run.py:95` and
  again at `evaluation/__init__.py:170`.
- **Detection.** `EvaluationResult.detected` works out the
  `has_fail_on_buggy_pass_on_golden` check again inline instead of using the
  property.

### C5. The submission manifest is an untyped dict

- `manifest.json` is read and written as a plain dict: `submission["empty"]` at
  `run.py:139`, and `manifest["files"]` in several modules.
- AGENTS.md says every persisted contract has a model in `results.py`. Add a
  `SubmissionManifest` model.
- `verify_bundle` runs three times per evaluation: once in `evaluate` and once in
  each `run_version`.
- An optional `manifest=` parameter is passed from `judge_stage` into
  `build_judge_workspace` just to skip re-hashing a few KB.
- Verify once at the start of each stage and pass the model down. Then the
  optional parameter can go.

### C6. `run_version(reference: bool)` does two jobs

`evaluation/__init__.py:86` switches all of these on one flag:

- the output directory,
- the container profile,
- whether existing tests are hidden,
- which patch is applied,
- which tests run.

Two small setup functions sharing one runner would read top to bottom:

- **reference:** apply the test patch; targets are the reference IDs.
- **submission:** upload the bundle; the target is `generated_dir`.

### C7. Config duplication, and options that cannot vary

- **Classification config.** `ClassificationConfig` repeats `RunConfig`'s
  `limits`, `output`, `runtime`, `toolchain`, `require_runtime` and `to_dict`.
  `load_classification_config` repeats the path handling in `load_config`
  (`config.py:249–327`). Both could share a base.
- **`to_dict()`** only calls `model_dump()`.
- **`AgentConfig`** is an empty subclass (`config.py:150`).
- **Harness versions** are kept in four places: `HARNESS_VERSIONS`, three
  `ToolchainConfig` fields plus the `installed_harnesses` property, and
  `docker/package.json`. A test keeps them equal. Read `package.json` once
  instead, or store `toolchain.harnesses` as one dict.
- **`BatchExecution.concurrency`** only accepts `1`.
- **`SourceConfig.dataset_revision`** exists only to narrow a type, and carries a
  `pragma: no cover`.

### C8. `latest_classification` lives in the wrong module

- It is a report lookup that loads the run config, but it sits in
  `repo_classification/contracts.py:195`.
- The report calls it twice per render (`report.py:232` and `:354`).
- It also assumes `classifications/` sits next to the run's output directory, and
  nothing enforces that.

### C9. `run()` does the resolve stage inline

- `run.py:66–113` validates, copies the rubric and instructions, rewrites config
  paths, saves the config, renders the prompt and writes the private instance,
  all in one block.
- Pulling it out as `_resolve(config, paths)` would leave `run()` as a plain list
  of stages.
- The comment at `:73–76` repeats `validate_resolved_config`'s docstring, with a
  typo (`generate_dir`).

### C10. Small items

- **`open_sandbox`** (`sandbox.py:183`) re-implements what `preserve_failure`
  (`lifecycle.py:16`) already does.
- **`Repository.setup_timeout`** is set, but two of its four uses read
  `config.limits.setup_seconds` directly anyway.
- **Codex arguments.** `codex.py:77–97` inserts each `--config` flag at
  `argv[2:2]`, which reverses their order. Build the list first and insert it
  once.
- **`render_session`** only renders the generation trace, and its docstring says
  "either agent CLI" when there are three. The judge trace is never rendered.
- **Per-repository settings are in two forms.** `runtime_for` keeps some in a
  table of tuples and some in `if repo == "pytest-dev/pytest"` branches, plus a
  check hard-wired to sklearn-14710. Use one table with named fields, and one
  small table for per-instance overrides.
- **`smoke.Dockerfile`** is packaged as package data, but only tests use it.
- **`run_batch` writes the summary in `finally`** (`batch/__init__.py:134`). If
  any saved run fails validation there, that error replaces the original one,
  even an interrupt.
- **Naming.** The package `repo_classification/`, the command `classify` and the
  docs' "task classification" name one thing three ways.

---

## D. Documentation

### D1. Statements that are wrong

| Where | Says | Actually |
|---|---|---|
| `AGENTS.md:16–17` | See `docs/implementation-plan.md`, `docs/spec.md` | Both deleted in `ec276e2` |
| `AGENTS.md:32` | `RepositoryWorkspace.prepare()` | The class is `Repository` |
| `AGENTS.md:72` and its layout list | CLI list and code layout | Leave out `classify`, `repo_classification/` and `spend.py` |
| `AGENTS.md:157` | "bounded turns" | Only wall-time limits exist |
| `AGENTS.md:180–182` | History sanitization and bug taxonomy are gaps; "staged plan", "Stage 1 path" | Both are implemented; the plan is deleted |
| `AGENTS.md` contracts rule | Contracts live in `results.py` or `judge/contracts.py` | There is a third module, and the manifest has no model |
| `README.md:77` | `report` rebuilds the session log | It does not, and a test checks that it does not |
| `README.md:129–138` | Results table | Leaves out `status.json`, `cost.json` and `ground-truth/` |
| `docs/containers.md:38` | `RepositoryWorkspace(...)` | `Repository` |
| `docs/containers.md:73–76`, `docker/README.md:45` | The judge uploads its own helper, and the spec records its hash | The helper is built into the image; the spec has no helper hash |
| `docker/README.md:4` | Link `../container/images.py` | Broken; should be `../images.py` |
| `docs/results.md:115` | The spec records a workspace-layout version and helper hashes | It records neither |
| `docs/results.md:344` | "schema-versioned run lock" | Configs are not versioned |
| `docs/results.md:408` | `report` can rebuild `session.log` | It cannot |
| `docs/results.md:415` | The trace is used to extract "cost" | Not since the pricing change. I missed this one |
| `docs/results.md:494`, `:614` | `submission/files/` may be absent | Capture always creates it |
| `docs/results.md:579–580` | "Generation begins only when / At least one…" | Broken sentence |
| `docs/results.md:232–237` | Judgment status table | Leaves out `skipped` |
| `docs/results.md:677` | Agreement covers "the answer and both conditional fields" | It also covers `cheating` |
| `docs/configuration.md:23–25` | "mirrors the ten-task initial Haiku batch with OpenCode… no turn limit" | Muddled and stale |
| `docs/configuration.md:417` | `configs/batches/openrouter-three-harnesses.yaml` | Does not exist |
| `configs/expanded-validation/candidates.md` | "No candidate image or reference test has been run yet" | They have been run |

### D2. Two plan documents describe finished work in the future tense

- **`docs/generated-test-judge-plan.md`** (827 lines) lays out milestones 1–10,
  all built. Whatever in it is still true is also in `results.md` or the code.
  Delete it.
- **`docs/issue-classification.md`** (418 lines) is a literature review plus a
  "proposed" taxonomy. The taxonomy now lives in
  `prompts/judge/task-classification-rubric.md`. Keep a short rationale of about
  60 lines (why these facets, and the sources), or move it to a notes folder.

### D3. `results.md` mixes a field reference with how-to guides

- It is 700 lines long.
- The judge-rerun, human-judging and agreement workflows sit inside what is
  otherwise a field reference.
- Two top-level `#` headings appear mid-file: `# Batch artifacts` and
  `# Agreement artifacts`.

**Proposal:** keep `results.md` to the directory layout plus one table per file.
Move the judging workflows to `docs/judging.md`. That should bring it to about 350
lines.

### D4. `configuration.md` carries history and speculation

- The mention of "removed `dataset`, `environment`, and `harness` sections"
  (`:32–34`).
- "Future Git sources" (`:374–390`) sketches a feature that does not exist.
- The "Resolution model" section and its diagram add little beyond "the resolved
  file adds `runtime` and `toolchain`".

Cut it down to what a user actually sets.

### D5. Prose style

- The docs use long sentences framed as policy statements. An example from
  `results.md`: "This makes results from changed rating conditions visible
  instead of silently combining them." Rewrite for short sentences, one idea
  each.
- These code comments and docstrings run past the two-sentence rule:
  - `judge/run.py:124–127`,
  - `Repository.prepare`,
  - `remove_existing_tests` (four sentences),
  - `AgentTurnRequest`.

---

## Checked and fine

- **Outcome mapping.** `pair_results` and the runner handle pass, fail, skip,
  xfail, xpass, strict xpass, and setup and teardown errors correctly. The real
  runner is exercised by `test_evaluation_outcomes.py`.
- **Bundle integrity.** Manifest hashes, rejection of unmanifested files, and the
  in-container re-hash for the judge all work.
- **Credential redaction.** Secrets split across stream chunks are still
  redacted.
- **Judge contracts.** The conditional-field rules, the stale and skipped states,
  and marking judgments stale after reevaluation all hold.
- **Agreement analysis.** Kappa (chance-corrected agreement between raters), its
  undefined case, and the weighting of per-pair values across rater pairs are all
  correct and tested.
- **Dead code.** Every function and class in `src/` is referenced.
- **Sample configs.** All 43 load.
- **Rubrics.** Every label in both rubrics matches its parser.

## Suggested order

1. **Small fix that prevents bad runs:** E5.
2. **Safety-net tests on current behavior,** before any refactor: T1–T5.
3. **Target rewrite:** E1–E3, together with T6.
4. **Deduplication:** C1–C6. Then C7–C10.
5. **Docs last,** once the code settles. The D1 corrections can go in at any
   time.
