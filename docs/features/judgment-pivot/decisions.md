# Judgment pivot decisions

The plan is `implementation.md`. Research reasons are in `docs/research-framing.md`.

## Pre (plan discussion)

- **Failure modes: the judge decides each start label independently, present or absent.** Raters rate agreement with each decision and may add labels the judge missed. Raters check the LLM's work, not label from scratch.
- **Expected-value provenance is left out.** It came from the dropped open-coding protocol.
- **The review page shows the trace saved at judging time (`judge/trace.json`).** The judge's cited steps then match the page after the trace code changes.
- **Classification additions are a placeholder step at the end.** The shape is not decided yet.
- **Raters move per problem: its classification, then its runs.**

## Pre (cycle 1)

- **Cycle 1 covers plan steps 1–3** (strict trace reading, the trace tab, the home page). Steps 4–5 change the judge and review contracts, and get their own cycle and review.
- **Trace tab: the timeline at full width, the event in a drawer over it.** Keeps the rail visible beside the trace. The user wants to see it before settling.
- **A problem page between the home page and the rating pages.** Raters must understand the problem well before rating.
- **The problem information is one view, shown on the problem page and as the first tab of every rating page.** Overview keeps only what is particular to the target.
- **Moving to a new problem always passes through its problem page.** Within a problem, Next task goes straight to the next task.
- **`Problem` lives in `review/targets.py`, built by grouping targets.** Grouping and task order are Python, and tested.
- **Keep the trace viewer's refined UI; merge it into the review page rather than rebuild it.** The Summary tab's content and formatting carry over into the problem view and Overview.
- **Rating only.** Runs without a completed judgment are not listed; there is no separate way to browse their traces.

## Model-decided (cycle 1)

- **No "read" state for problem pages** (Q7, unanswered). Passing through the page on the way to a new problem is enough; progress counts rated tasks only.
- **`TaskContext`, `OutcomeRow`, and `RunSummary` are removed from `trace/events.py`.** The problem view and Overview show the same information from the strict contracts; the run picker is gone.
- **The problem view has no repository browser** (`web/static/problem.js`). The Files tab on each rating page already browses the repository the judge read; the problem page would need its own tree API keyed by instance.
- **The problem speaks through its first task** (`review/targets.Problem.issue`). Classification and run instance records come from the same dataset record.
- **Runs in a problem are ordered by run ID, which starts with a UTC timestamp** (`review/targets.find_problems`). Labels show harness, model, and scope.
- **Classification rating pages get a Problem tab and lose the Overview tab.** Everything their Overview showed is problem information.
- **Arrow keys move between trace events; `j`/`k` stay on the rail** (`web/static/trace.js`).
- **The trace is built on demand in cycle 1** (`web/session.ReviewSession.trace`). Saving it at judging time is step 4.
- **The trace's own labels move into `web/static/labels.js`;** generic helpers merge into `render.js`.

### Build (cycle 1)

- **`JudgmentTarget.load` validates the run's config** (`review/targets.py`). The home page labels every run from it, so a run with a stale config is listed under "Can't be reviewed" rather than breaking the listing.
- **`listing()` gives each task its question keys** (`web/session._task`), so the page counts unsaved browser ratings toward "n of m rated" without opening the task.
- **`problem()` does not repeat the tasks** (`web/session.ReviewSession.problem`). The problem page reads them, with their progress, from the listing it already has.
- **The problem view comes from the classification's record when there is one** (`Problem.issue`): the `problem_statement` text rather than the run's `ground-truth/issue.md`. They are the same issue.
- **A judgment's Overview opens with a Run section** (agent, scope, localized target, prompt) from `summary.js`, and keeps the review page's own outcome table, whose test IDs open the test in Files.
- **The problem view uses the review page's diff renderer**, with line numbers and highlighting. Its file names open files only on a rating page, where there are views to open them in (`render.diffHtml` with no views).
- **Trace styles are scoped under `.trace-ui`, and the Summary document's under `.doc`** (`web/static/app.css`). The two pages shared class names such as `.row`, `.tabs`, `.panel`, and `.message`; a timeline row of action type `message` picked up the page's centered-notice margins until scoped.
- **Step links scroll to the row's step cell** (`web/static/trace.js`). Rows are `display: contents`, so the row itself cannot be scrolled into view.
- **The hover tooltip skips the rating buttons**, which already have their own CSS tooltip (`web/static/app.js`).
- **`judged_run` writes a real resolved config, and `traced_run` adds a short Codex trace** (`tests/fixtures.py`). The trace build reads the strict contracts, so its fixture lives with the others.

## Pre (cycle 1, demo feedback)

- **No problem page and no Continue.** Continue went to a problem page while a task row went straight to its rating page; two ways in to different pages confused the flow. Raters pick tasks from the home page, and read the problem in the Problem tab. Replaces the problem-page and "pass through the problem page" decisions above.
- **No help dialog on the home page.** "How to review" shows only on rating pages, when the rater asks for it.
- **Rating pages are titled Problem Classification and Generation Judgment.** The same names label tasks on the home page.

## Pre (cycle 1, layout)

- **Three columns: rail, middle, right sidebar.** The middle holds what the judge rated for this target; the sidebar holds reference: Problem, Stage rubric, and, on Generation Judgment pages, Legend and Event. The classification page's middle is Files and Task record.
- **The selected trace event opens as the sidebar's Event tab, not a drawer.** Nothing covers the timeline, as in the standalone viewer. The legend is a sidebar tab again, not a dialog.
- **A draggable divider sets the sidebar width, remembered in the browser.** The rail narrows to 340px.
- **Files stays in the middle.** It is the repository the judge browsed; the classification's oracle question is checked against it.

### Build (cycle 1, layout)

- **The trace selection resets when a rating page opens** (`review.openTarget`), not when the trace loads. A step link clicked before the trace had loaded lost its step otherwise.
- **The Event tab is always shown on Generation Judgment pages** (`review.showSideTab`), reading "Event" with a hint until an event is selected. A tab that appeared on selection and never left was jarring (demo feedback).
- **The sidebar starts at 480px** (`review.sidebarWidth`).

