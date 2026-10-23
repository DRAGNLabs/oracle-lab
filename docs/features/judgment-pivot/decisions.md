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


## Pre (cycle 2: failure modes)

- **One judge turn answers both the submission questions and the failure modes.** One rubric, one `judgment.json`. The judge reads the tests and trace once, and the failure modes can lean on its reading of the submission.
- **The start list is the ten labels in `docs/research-framing.md`, as written**, including "Flagged the bug". Each becomes a fixed key with its apply and don't-apply rules in the rubric.
- **Raters' own labels are failure modes outside the start list only**, each with a name, a one-line description, and the trace steps it rests on. Disagreeing with a start label is a rating of its decision.
- **Each label the judge marks present carries a justification and the trace steps it rests on** (at least one step number, as the timeline shows them). Step links in the rating card open that event.
- **Ratings are agree or disagree everywhere; "defensible" is removed.** It was not well defined. This changes the submission and classification ratings too, and saved reviews stop validating.
- **The judge does not justify absent labels.** Absent decisions carry only the decision.
- **The judge gets the trace twice, with the same step numbers:** `evidence/agent-trace.md`, the summarized view a rater sees (one line per event with its tags, and the test file versions), and `evidence/agent-trace.json`, every event with its full input and output (the saved `judge/trace.json`). The session log stays.
- **The rail has sections: Submission, then Failure modes (present labels as full cards, absent labels as one-line rows), then the rater's own labels.** Keys and `j`/`k` run through every item in order.
- **If the trace cannot be built, the judge gets the raw harness trace instead** (`evidence/agent-trace.jsonl`) and judging continues. Its cited steps are then that file's line numbers.
- **Agreement reports each start label as its own question, the judge-added labels pooled as one question, and a list of every judge-added label with its ratings.** Judge-added names differ per run, so they cannot be grouped by name.

## Model-decided (cycle 2)

- **Failure-mode titles live in `judge/contracts.FAILURE_MODES`, and `FailureModeKey` repeats the keys as a `Literal`** for validation. `tests/test_rubrics.py` checks the rubric defines exactly those keys and titles (`### `key`: Title` headings).
- **A present label needs a justification and at least one step; an absent one is exactly `{"decision": "absent"}`** (`judge/contracts.py`). The submission `justifications` map still covers only Questions 1–4.
- **`save_trace` runs only when the workspace is persisted, and removes any earlier `judge/trace.json` first** (`judge/workspace.py`). It catches any exception from the build, since the builder parses agent output, and the judge then gets the raw trace.
- **`agent-trace.md` lists every event with its tags but no output, then the test file versions with their pytest runs and how many added tests detect the bug** (`trace/markdown.py`). The judge already sees the paired results, so the outcome counts add nothing hidden.
- **`judged_run` saves an empty `generation/trace.jsonl` and its `judge/trace.json`** (`tests/fixtures.py`), as every judged run now has. `judgment_ratings()` builds a full agreeing review.
- **The overview's rating items carry `section`, `answer`, and for failure modes `steps` and `justification` (null when absent)** (`web/session._rating_items`). A saved review is returned whole (`ratings` and `other_failure_modes`).
- **The rail sorts items into display order once, present start labels before absent ones** (`rail.startRail`), so the existing index-based keyboard and rating code works unchanged.
- **Drafts store the whole review, ratings and the rater's own labels** (`rail.js`). Drafts in the old shape are ignored.
- **The ordinal-distance agreement test was removed.** With two ratings there is no "one step apart".
- **The demo set's failure-mode decisions are stand-ins marked "Demo placeholder"**, since no judge has labeled that run.
- **The "How to review" text is Markdown, `web/templates/how-to-review.md`, filled with `string.Template` like the judge's workspace instructions** (`web/session.how_to_review`). Reviewer prose does not belong in page code (user). `${ratings}` takes the definitions from `RATING_DEFINITIONS`, which the rating buttons' tooltips also use.
