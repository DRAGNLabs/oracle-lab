# Judgment pivot, cycle 1: the trace in the review page

Cycle 1 covers steps 1–3 of `implementation.md`: the trace builder reads runs
strictly, the review page shows the trace, and raters get a home page that moves
them through one problem at a time. No persisted contract changes. Decisions are
in `decisions.md`.

## What the rater sees

```
Home → problem page → classification rating → run rating → run rating → next problem page → ...
```

- **Home** (`#/`). One section per problem, in instance order: its instance ID,
  repository, issue title, and progress. Under it, its tasks in order: the
  classification attempts, then the runs by run ID, each labeled with harness,
  model, and scope, with "n of m rated". Overall progress at the top. Filters for
  stage, harness, and "not finished only", kept in the hash. A Continue button,
  and "Can't be reviewed" at the bottom.
- **Problem page** (`#/problem/<instance>`). The problem view, then the
  problem's task list and a "Start rating" button that opens its first unrated
  task.
- **Rating page** (`#/review/<target>`). The rail as today, with "Next task" in
  its footer once every question is rated and saved. Tabs:
  - classification: Problem, Files, Task record, Stage rubric;
  - judgment: Problem, Overview, Agent trace, Test files, Files, Evidence, Stage
    rubric.
- **The trace viewer's UI is kept as refined in trace-viewer cycle 3**: the
  timeline, lanes, row tags, overlap key, event detail, Test files, legend
  descriptions, and hover text. It moves into the review page; only the run
  picker and the panel's tab bar go, since the rating page's tabs replace them.
  The Summary tab's sections are split between the problem view and Overview,
  with their formatting and styles.
- **Problem view** (shared by the problem page and the Problem tab). Repository,
  dataset, and base commit; the issue; the record's issue comments, pull request
  description, and interface; the golden patch; the reference tests.
- **Overview** (judgment only). The forbidden-changes banner, the agent, the
  localized target, the prompt given to the agent, the outcome matrix
  and per-test table, the patch coverage (each patched file's changed line
  ranges, and how many changed lines the agent saw), the edit facts table with
  step links, and the generated tests.
- **Agent trace.** The timeline at full tab width with the lane toggle. Clicking
  a row opens the event in a drawer over the right of the timeline; Escape or the
  close button shuts it. Arrow keys move between events while the tab is open.
  A Legend button opens the legend as a dialog.
- **Test files.** The trace viewer's version history, unchanged.
- **Next.** "Continue" on the home page and "Next task" on a rating page go to
  the next unrated task in order. When that task belongs to another problem, they
  open that problem's page first.
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

- `listing.js`: the home page, and `nextStop(afterId)`, the hash of the next
  problem page or rating page, used by Continue and Next task.
- `problem.js`: `renderProblem(container, problem)` (the shared view) and
  `openProblem(instanceId)` (the page). Built from the trace viewer's
  `summary.js` sections (header, issue, golden patch, reference tests).
- `overview.js`: the judgment's Overview, gaining `summary.js`'s agent, target,
  prompt, patch coverage, results, and edit facts sections.
- `review.js`: the new tab sets, Next task, and step links.
- `trace.js`: the Agent trace tab: timeline, drawer, lane toggle, and arrow
  keys. `openStep(step)`.
- Moved from `trace/static/`: `timeline.js`, `detail.js` (the drawer's
  content), `tests.js` (the Test files tab), and `legend.js` (now a dialog).
- `labels.js`: the trace's labels and descriptions, from `trace/static/html.js`
  (`ACTION_LABELS`, `CATEGORY_LABELS`, `DESCRIPTIONS`, `tag`, `editFacts`,
  `counts`, `testName`, `outcomeTag`). Its generic helpers merge into
  `render.js`; the trace's plain diff renderer becomes `render.plainDiffHtml`.
- `app.js`: routes `#/problem/`, and gains the `data-tip` hover text from the
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
4. Page: problem view and page, home page and `nextStop`, tabs.
5. Page: trace tab, drawer, Test files, legend dialog, Overview additions, step
   links, hover text.
6. Docs: `README.md`, `AGENTS.md`, `docs/judging.md`, `docs/human-review.md`.
   The trace-viewer spec is marked as superseded.
7. Drive the page in headless Chromium across both stages and the three
   harnesses, and fix what looks wrong.
