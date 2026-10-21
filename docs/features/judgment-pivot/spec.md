# Judgment pivot, cycle 1: the trace in the review page

Cycle 1 covers steps 1–3 of `implementation.md`: the trace builder reads runs
strictly, the review page shows the trace, and raters get a home page that moves
them through one problem at a time. No persisted contract changes. Decisions are
in `decisions.md`.

## What the rater sees

```
Home → classification rating → run rating → run rating → next problem's classification → ...
```

- **Home** (`#/`). One section per problem, in instance order: its instance ID,
  repository, issue title, and progress. Under it, its tasks in order: the
  classification attempts, then the runs by run ID, each labeled with harness,
  model, and scope, with "n of m rated". Overall progress at the top. Filters for
  stage, harness, and "not finished only", kept in the hash. "Can't be reviewed"
  at the bottom. Each task opens its rating page. No help dialog opens here.
- **Rating page** (`#/review/<target>`), titled Problem Classification or
  Generation Judgment. The rail as today, with "Next task" in its footer once
  every question is rated and saved. The "How to review" button shows only here.
  Three columns: the rail (340px), the middle tabs with what the judge rated, and
  a sidebar with reference, its width set by dragging the divider and remembered
  in the browser (300–900px).
  - classification: middle Files, Task record; sidebar Problem, Stage rubric.
  - judgment: middle Overview, Agent trace, Test files, Files, Evidence; sidebar
    Problem, Stage rubric, Legend, and Event.
- **The trace viewer's UI is kept as refined in trace-viewer cycle 3**: the
  timeline, lanes, row tags, overlap key, event detail, Test files, legend
  descriptions, and hover text. It moves into the review page; only the run
  picker and the panel's tab bar go, since the rating page's tabs replace them.
  The Summary tab's sections are split between the problem view and Overview,
  with their formatting and styles.
- **Problem tab.** Repository,
  dataset, and base commit; the issue; the record's issue comments, pull request
  description, and interface; the golden patch; the reference tests.
- **Overview** (judgment only). The forbidden-changes banner, the agent, the
  localized target, the prompt given to the agent, the outcome matrix
  and per-test table, the patch coverage (each patched file's changed line
  ranges, and how many changed lines the agent saw), the edit facts table with
  step links, and the generated tests.
- **Agent trace.** The timeline at full tab width with the lane toggle. Clicking
  a row opens the event in the sidebar's Event tab, labeled "Event · step N".
  Arrow keys move between events while the tab is open, and Escape clears the
  selection. The legend is a sidebar tab.
- **Test files.** The trace viewer's version history, unchanged.
- **Next task** goes to the next unrated task in rating order, across problems.
- Step links (`data-open-step`) anywhere on a rating page open the Agent trace tab
  at that step.

## Structure

### Python

- `trace/build.py`: `build_run_trace(paths)` reads the run with the strict
  readers: `load_config(paths.config, resolved=True)`, `read_evaluation_result`,
  `read_instance_record`, and `read_localized_target`. `read_run_facts`,
  `RunFacts`, `_task_context`, and `_failure` are removed.
- `trace/events.py`: `RunSummary`, `TaskContext`, and `OutcomeRow` are removed;
  the problem view and Overview show that information. `RunTrace` keeps
  `reasoning`, `events`, `generated_files`, `defect`, and `outcomes_available`.
- `trace/server.py` and `trace/static/` are removed, with the `trace` CLI
  command. `tree-sitter` and `tree-sitter-bash` become main dependencies and the
  `trace` extra goes away.
- `review/targets.py`:
  - `Problem`: one instance and its targets. `instance_id`, `instance`,
    `classifications`, `judgments`, and `tasks` (classifications, then
    judgments). `issue()` is the first task's issue.
  - `find_problems(targets) -> list[Problem]`: groups by instance ID, ordered by
    instance ID; each list is ordered by target root.
  - `JudgmentTarget.config()`: the run's resolved `RunConfig`, for the task
    labels.
- `web/session.py`: `ReviewSession` gains `problems: dict[str, Problem]`.
  - `listing()` returns problems with their tasks and progress, and the
    unreviewable list.
  - `problem(instance_id)`: the problem view's data.
  - `overview(target_id)` drops the issue, patches, and record texts, and gains
    `instance_id` links to the problem.
  - `trace(target_id)`: `RunTrace.to_json()`, built on first use and cached.
    Judgment targets only.
- `web/server.py`: `/api/problem?instance=` and `/api/trace?id=`.

### Page (`web/static/`)

- `listing.js`: the home page, and `nextTask(afterId)`, used by Next task.
- `problem.js`: `renderProblem(container, problem, views)`, the Problem tab.
  Built from the trace viewer's `summary.js` sections (header, issue, golden
  patch, reference tests).
- `overview.js`: the judgment's Overview, gaining `summary.js`'s agent, target,
  prompt, patch coverage, results, and edit facts sections.
- `review.js`: the three columns, the middle and sidebar tab sets
  (`showTab`, `showSideTab`), the divider, Next task, and step links.
- `trace.js`: the Agent trace tab (timeline, lane toggle, arrow keys), the
  sidebar's Event tab (`renderEvent`), and `openStep(step)`.
- Moved from `trace/static/`: `timeline.js`, `detail.js` (the Event tab's
  content), `tests.js` (the Test files tab), and `legend.js` (the Legend tab).
- `labels.js`: the trace's labels and descriptions, from `trace/static/html.js`
  (`ACTION_LABELS`, `CATEGORY_LABELS`, `DESCRIPTIONS`, `tag`, `editFacts`,
  `counts`, `testName`, `outcomeTag`). Its generic helpers merge into
  `render.js`; the trace's plain diff renderer becomes `render.plainDiffHtml`.
- `app.js`: gains the `data-tip` hover text from the
  trace viewer's `app.js`. That file's selection, lane toggle, and key handling
  move into `trace.js`.
- `state.js`: `problems`, `problem`, `trace`, and the trace tab's selection and
  lane mode.
- `app.css`: the trace styles merged in.

## Steps

1. Strict trace build: `build.py` and `events.py` changes, the dependency move,
   removal of the standalone server, static files, and command. Trace tests use
   `tests/fixtures.py` runs.
2. `Problem` and `find_problems`, `JudgmentTarget.config()`, with tests.
3. Session and server: `listing`, `problem`, `overview`, and `trace`, with tests.
4. Page: Problem tab, home page and `nextTask`, tabs.
5. Page: trace tab, sidebar Event and Legend tabs, Test files, Overview additions, step
   links, hover text.
6. Docs: `README.md`, `AGENTS.md`, `docs/judging.md`, `docs/human-review.md`.
   The trace-viewer spec is marked as superseded.
7. Drive the page in headless Chromium across both stages and the three
   harnesses, and fix what looks wrong.

# Cycle 2: failure modes

Plan steps 4–5. The generation judge reads the agent's trace and decides each
start-list failure mode, and raters agree or disagree with every decision. Ratings
become agree or disagree everywhere. This changes three persisted contracts:
`judgment.json`, a new `judge/trace.json`, and `review.json`. Old judgments and
reviews stop validating; rejudge and re-review.

## What the judge does

One turn, one rubric, one `judgment.json`. Besides the four submission questions:

- **`failure_modes`**: every start-list label, each `present` or `absent`. A present
  label carries `steps` (trace step numbers, at least one) and a `justification`.
  An absent label carries nothing else.
- **`added_failure_modes`**: the judge's own labels for failure modes outside the
  start list, each with `name`, `definition`, `steps`, and `justification`. May
  be empty.

The start list is the ten labels in `docs/research-framing.md`, as keys:
`never_saw_bug`, `saw_bug_untested`, `copied_buggy_output`,
`copied_existing_tests`, `ignored_available_docs`, `contradicted_read_docs`,
`changed_test_to_pass`, `doubted_moved_on`, `explained_bug_away`, `flagged_bug`.
The rubric gives each its apply and don't-apply rules, the four tests a label
must pass, and says the trace's facts are evidence, not labels.

## What the judge sees

Workspace construction builds the trace on the host and saves it as
`judge/trace.json` (`RunTrace.to_json()`). The judge gets:

- `evidence/agent-trace.md`: the summarized view a rater sees: one line per event
  (step, action type, description, tags such as patched lines and edit facts,
  pytest counts) and the test file versions. Rendered from `judge/trace.json`.
- `evidence/agent-trace.json`: `judge/trace.json`, every event with its full
  input and output.
- `evidence/agent-session.log`, as before.

If the trace cannot be built, neither file exists; the judge gets the raw
harness trace as `evidence/agent-trace.jsonl`, and its steps are that file's line
numbers. The review page then shows the Agent trace tab as unavailable.

## What the rater does

- **Agree or disagree** with every answered submission question, every start
  label's decision, and every judge-added label. Keys `1` and `2`.
- **Other labels**: optional failure modes outside the start list the judge
  missed, each with a name, a one-line description, and the trace steps.
- **Rail**: a Submission section (full cards), a Failure modes section (present
  labels as full cards with step links, then absent labels as one-line rows), then
  "Your other labels" with an add form. `j`/`k` and `1`/`2` run through every item
  in order.

## Agreement

Each submission question and each start label is a question. Judge-added labels
are pooled into one question. The report also lists every judge-added label with
its ratings, and counts the raters' other labels by name. "Judge quality" is the
share of agree ratings; alpha is computed over the two ratings.

## Structure (cycle 2)

- `judge/contracts.py`: `FAILURE_MODES` (key → title), `PresentFailureMode`,
  `AbsentFailureMode`, `FailureModeDecision`, `AddedFailureMode`.
  `JudgmentLabels` gains `failure_modes` (exactly the start-list keys) and
  `added_failure_modes`.
- `paths.py`: `JudgePaths.trace` (`judge/trace.json`).
- `trace/markdown.py`: `trace_markdown(trace: dict) -> str`, the summarized view
  from saved trace JSON.
- `judge/workspace.py`: `save_trace(paths)` builds and saves `judge/trace.json`,
  or reports that it could not. `judge_evidence` exposes the two trace files, or
  the raw trace.
- `judge/templates/workspace-instructions.md` and
  `prompts/judge/generated-test-evaluation-rubric.md`: the trace files and the
  failure-mode section and output.
- `review/contracts.py`: `Rating` is `agree` or `disagree`. `RaterFailureMode`
  (name, description, steps). `Review` gains `other_failure_modes`.
- `review/targets.py`: `ReviewTarget.rating_keys()` replaces
  `answered_questions()` (submission keys, `failure_modes.<key>`,
  `added_failure_modes.<index>`); `ReviewTarget.rating_label(key)`.
- `review/agreement.py`: start labels as questions, pooled added labels, the
  added-label list, and rater label counts. `AgreementReport` gains
  `added_labels` and `rater_labels`.
- `web/session.py`: `overview` gives each rating item its `section`, decision,
  steps, and justification. `trace` reads `judge/trace.json`.
- `web/static/rail.js`: the sections, compact absent rows, the other-labels form,
  and two ratings.
- `report.py`: failure modes in the judgment section.

## Steps (cycle 2)

1. Contracts: judgment, `JudgePaths.trace`, review. Fixtures and contract tests.
2. `trace/markdown.py`, `save_trace`, and the evidence rows, with tests.
3. Rubric and workspace instructions.
4. Review targets, store, and agreement, with tests.
5. Session, rail, help text, and the trace tab reading `judge/trace.json`.
6. Report, `docs/results.md`, `docs/judging.md`, and the demo set.
