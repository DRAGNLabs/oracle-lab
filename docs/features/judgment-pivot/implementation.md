# Judgment pivot

This plan folds the trace viewer into the generation judgment, has the LLM judge
label failure modes from the trace, and gives raters one home page for every
task they have to rate. The reasons are in
[research framing](../../research-framing.md).

## What changes

- **Two judgment stages, not three.** Classification (per task) and generation
  (per run). There is no separate human open-coding stage.
- **The generation judge reads the trace.** It still answers the four existing
  questions about the submission. It also marks each start-list failure mode
  present or absent, and may add labels of its own.
- **Raters rate both parts on one page.** The review page for a run gains the
  trace timeline, and the question rail gains the judge's failure-mode labels.
- **One home page.** `oracle-lab review` opens a list of every rating task,
  grouped by problem, that takes the rater to each task and on to the next.
- **The standalone viewer goes away.** `oracle-lab trace` and its server are
  removed once the review page shows traces. `trace/` keeps building the facts.

## Current state

- `oracle-lab review <dir> --rater <id>` already serves one page for both stages
  (`web/`). Its home is a target list grouped by instance, with reviewed or not
  reviewed per target (`web/static/listing.js`). A target page is a question
  rail beside tabs: Overview, Files, Agent session (or Task record), and Stage
  rubric.
- Ratings are `correct`, `defensible`, or `incorrect`, one per answered question
  (`review/contracts.py`). Agreement reports counts, judge quality, raw
  agreement, and Krippendorff's alpha per question.
- The generation judge sees `evidence/agent-session.log`, but uses it only for
  the cheating question.
- `oracle-lab trace` is a separate demo server (`trace/server.py`,
  `trace/static/`). It reads runs loosely, against `AGENTS.md`'s no-shims rule,
  and its Summary tab repeats much of the review page's Overview.

## Steps

Each step is one mergeable change with its own tests. Steps 1 to 3 change only
the page and the code behind it. Steps 4 and 5 change persisted contracts.

### 1. Trace facts read runs strictly

The trace builder becomes ordinary pipeline code.

- Replace `trace.build.read_run_facts`'s loose YAML and JSON reading with the
  strict readers (`RunPaths`, `RunConfig`, `EvaluationResult`, the instance
  record). Runs that fail the current contracts stop loading, as for every other
  reader.
- Move `tree-sitter` and `tree-sitter-bash` from the `trace` extra into the main
  dependencies. The judge workspace (step 4) builds the trace on the host, so a
  judged run needs them.
- Trace fixtures move into `tests/fixtures.py` next to the other run fixtures.
- Remove the demo-only notes from `trace/` docstrings and `AGENTS.md`.

### 2. The review page shows the trace

A judgment target's page gains an Agent trace tab. No rating changes yet.

- Move `timeline.js`, `detail.js`, `tests.js`, and `legend.js` from
  `trace/static/` into `web/static/`, and the trace styles into `app.css`.
  Shared helpers merge into `render.js`.
- `ReviewSession.trace(target_id)` returns `RunTrace.to_json()`, built on first
  use and cached like the repository views. `server.py` serves it at
  `/api/trace`.
- Tabs for a judgment: Overview, Agent trace, Test files, Files, Evidence,
  Stage rubric. Agent trace is the timeline with the selected event beside it.
  Test files is the trace viewer's version history. The raw session log stays
  under Evidence.
- Drop the trace viewer's Summary tab. Move what it adds into Overview: each
  patched file's changed line ranges, how many changed lines the agent saw, and
  the edit facts table with step links.
- The legend becomes a dialog opened from the trace tab, beside the existing
  "How to review" dialog.
- Step links: `data-open-step` opens the trace tab at that event, like
  `data-open-file` does for files.
- Keys: `j`/`k` move between rail questions today and between events in the
  viewer. Keep them on the rail; the trace tab uses the arrow keys.
- Remove `oracle-lab trace`, `trace/server.py`, and `trace/static/`.

### 3. The annotator home page

The target list becomes a work queue a rater can move through quickly.

- **Grouped by problem.** Raters work one problem at a time. One section per
  instance: its repository, a one-line issue title, and its tasks in order: the
  classification first, then each run's generation judgment (harness, model,
  prompt condition).
- **Progress at every level.** Overall, per problem, and per task (for example,
  "3 of 7 rated" for a task left half done, read from the saved review and the
  browser draft).
- **Continue.** One button opens the next unrated task in that order: the rest
  of the current problem before the next problem. After a save, the target page
  offers "Next task".
- **Filters.** Stage, harness, and "not finished only". Kept in the URL hash so
  a reload keeps them.
- The "Can't be reviewed" section stays, at the bottom.
- Keep the command name `oracle-lab review`.

### 4. The judge labels failure modes

The generation judge gets the trace and a new question. This changes the
`judgment.json` contract.

- **Bundle.** `judge/workspace.py` adds `evidence/agent-trace.md`: the numbered
  events with their descriptions, patch overlap, pytest summaries, test file
  versions, and edit facts, rendered on the host from `RunTrace`. The session
  log stays.
- **Saved trace.** The same build also saves `judge/trace.json`
  (`RunTrace.to_json()`), and the review page shows that file rather than
  rebuilding the trace. The step numbers the judge cites then always match the
  page, even after the trace code changes.
- **Rubric.** `prompts/judge/generated-test-evaluation-rubric.md` gains a section
  on agent behavior: the start list from
  [research framing](../../research-framing.md#start-list) with each label's
  apply and don't-apply rules, the four tests a label must pass, and how to add
  a label of its own. It says signals are evidence, not labels. The judge
  decides every start label independently, present or absent.
- **Contract.** `judge/contracts.py`:
  - `JudgmentLabels.failure_modes` maps every start-list key to a decision:
    `present` (with `steps`, the trace step numbers, at least one; `tests`, test
    IDs, may be empty; and `justification`) or `absent` (with a short
    `justification`).
  - `JudgmentLabels.added_failure_modes` is a list of the judge's own labels:
    `name`, `definition`, `steps`, `tests`, and `justification`. It may be empty.
- Update the workspace instructions template, `report.py`, `docs/results.md`,
  `docs/judging.md`, and the judgment fixtures. Old judgments fail validation;
  rejudge the runs that matter.

### 5. Raters rate failure modes

Raters check the judge's work; they do not label from scratch. The rail gains
one card per failure-mode decision, plus a space for labels the judge missed.
This changes the review contract.

- **Rating decisions.** Every start label's present or absent decision, and
  every label the judge added, gets the usual three ratings. `defensible` covers
  a decision that is reasonable but rests on weak or partial evidence.
- **Other labels.** The rater can add labels they think apply that the judge did
  not give, from the start list or their own, each with steps and a note.
- **Contract.** `review/contracts.py`: `Review.ratings` keys extend to
  `failure_modes.<label>` for each start label and `added_failure_modes.<index>`
  for each judge-added label, so `parse_review` still requires exactly one
  rating per decision. `Review` gains `other_failure_modes`, the rater's list of
  labels.
- **Rail.** Present labels come first. A card shows the label, its definition,
  the decision, its steps as links into the trace, and its justification. Absent
  labels are compact cards, so rating them stays quick.
- **Agreement.** `review/agreement.py` reports each start label as its own
  question, and judge-added labels pooled as one. It counts the raters' other
  labels per label, as a sign of what the judge misses. Docs and fixtures update
  with it.

### 6. Classification additions

Placeholder. Something to add to the classification judgment, likely from the
oracle state and recognition signal ideas in
[research framing](../../research-framing.md#oracle-state-per-problem). Its shape
is not decided yet; come back to it after step 5.

### 7. Docs

- `README.md`, `AGENTS.md`, `docs/judging.md`, and `docs/human-review.md`
  describe the two stages, the trace tab, and the home page.
- Move `docs/features/trace-viewer/` content that still applies into
  `docs/judging.md`, and keep the decision record as history.

## Decided

- **Failure-mode rating.** The judge decides each start label independently,
  present or absent. Raters rate their agreement with each decision and may add
  labels the judge missed. They check the judge's work rather than labeling
  from scratch.
- **Expected-value provenance** is left out of the judge for now.
- **Step numbers.** The review page shows the trace saved at judging time, so
  the judge's cited steps match the page.
- **Order.** Raters move per problem: its classification, then its runs.
