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
