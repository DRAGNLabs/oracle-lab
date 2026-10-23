# Judging

An LLM judge rates a run's submission and the agent's behavior in its trace, and
human raters then rate the judge's answers. This guide covers both commands. The [results reference](results.md)
describes the files they write.

## The LLM judge

A run with a `judge` section is judged automatically after paired evaluation.
To judge a saved run for the first time, or again:

```sh
uv run oracle-lab judge output/runs/<run-id>
```

The command needs the configured judge credential and the saved runtime image.

- It saves the exact prompt and the raw final response before it validates
  `judgment.json`.
- Invalid model output is kept as `status: invalid_output`. It is not retried.
- Transient API failures are retried, as for any model turn.
- A rerun first moves the whole previous attempt to
  `judge-history/<UTC timestamp>/`, then rebuilds the workspace.

Besides the four submission questions, the judge decides whether each failure
mode on a start list is present in the agent's trace, citing trace steps for each
present one, and may add failure modes of its own. The start list and its reasons
are in [research framing](research-framing.md#start-list). The workspace builds
the trace on the host and saves it as `judge/trace.json`; the judge reads a
summary of it and the full trace with the same step numbers.

## Human review

Raters measure the two LLM judges, the generated-test judge and the task
classifier, by rating each answer they gave. They do not answer the questions
themselves. Each answer gets one rating:

| Rating | Meaning |
|---|---|
| `agree` | The rater would give the same answer, given the evidence and the stage rubric. |
| `disagree` | The rater would give a different answer, given the evidence and the stage rubric. |

A question the judge answered `null` is not rated. A judgment is rated on up to
4 submission answers, every start-list failure-mode decision (present or
absent), and each failure mode the judge added; an in-scope classification on 8
answers, and an out-of-scope one on 1. A rater may also add failure modes the
judge missed that are outside the start list, each with a name, a description,
and the trace steps. Only a
completed judgment or classification can be reviewed; a failed, skipped, or
stale one is listed with the reason it cannot be.

[The human review workflow](human-review.md) describes how a review set is
shared with raters and how their reviews come back. Start the review page with:

```sh
uv run oracle-lab review <dir> --rater <id>
```

`<dir>` is a run, a classification attempt, or an output root holding `runs/`
and `classifications/`, such as `output/` or a review data repository. The command starts a local
web server on `127.0.0.1` and opens the browser. `--port` picks the port and
`--no-browser` prints the URL instead of opening it. Press Ctrl+C to stop.

The page needs no Docker, credentials, or model calls. It does need Git and
network access to GitHub: the repository views are built from a shallow fetch of
the instance's base commit, cached under `~/.cache/oracle-lab/repos/` (or
`$XDG_CACHE_HOME/oracle-lab/repos/`). Later reviews of the same commit reuse the
cache.

The page opens on a home page that lists every problem (task instance) with its
tasks in rating order: the task classification first, then each run's judgment,
labeled with harness, model, and scope. It shows each task's progress, including
ratings not saved yet, and filters by stage, harness, and unfinished tasks. Each
task opens its rating page, titled Problem Classification or Generation
Judgment.

A rating page has three columns: the questions to rate, tabs with what the judge
rated, and a sidebar with reference (the problem, the stage rubric, and for a
judgment the trace legend and the selected trace event). Drag the divider to
resize the sidebar. It shows the rater what the judge had:

- the judge's answers and justifications beside three rating buttons, and after
  saving, a link to the next unreviewed task;
- a Problem tab: the issue, the task record's other texts, the golden patch, and
  the reference tests;
- for a judgment, an Overview of the run: the agent, the localized target, the
  prompt, the paired-outcome matrix and per-test outcomes, which patched lines
  the agent saw, edit facts, and the generated tests;
- for a judgment, the agent trace as a timeline of typed events, with each
  event's input and output in the sidebar, and a Test files tab with each generated
  file's versions and the pytest runs between them. Both show facts only, such as
  which events touch the patched lines, never failure-mode labels. A legend
  explains every color and tag, and every tag describes itself on hover;
- the repository views the judge read, with a file finder: buggy and golden for
  a judgment, with a side-by-side view of the repair, and the buggy checkout
  only for a classification;
- the judge's evidence files, such as the agent session log or the task record;
- the stage rubric.

Unsaved ratings stay in the browser until the rater saves. Saving writes the
review under `judge/reviews/<rater>/` in the run or classification attempt, beside
the output it rates; saving again moves the earlier review to
`judge/reviews-history/<rater>/`. The
page reads only the current rater's reviews, never another rater's. Rerunning
the judge moves the reviews into `judge-history/` with the judgment they rated.

## Agreement

Once raters have reviewed, compute agreement over the reviewed set:

```sh
uv run oracle-lab agreement ../oracle-lab-data
```

It takes one or more runs, classification attempts, or output roots holding
`runs/` and `classifications/`, and reads every rater's review. It writes
`agreement.json` and `agreement.md` to `--output`, or by default to
`output/agreement/<UTC timestamp>/` in the current directory. It needs no Docker,
network, or credentials.

The two stages are reported separately. Each submission question, each
classification question, and each start-list failure mode is a question. The
failure modes judges added are named freely and differ by run, so they are
pooled into one question, and also listed one by one with their ratings. The
report counts the raters' own failure-mode labels by name. For each question it
reports:

- **Rating counts:** how many `agree` and `disagree` ratings the question
  received across every target and rater.
- **Judge agreed with:** the share of ratings that are `agree`.
- **Raw agreement:** of every pair of raters who rated the same target, the share
  that gave the same rating.
- **Krippendorff's alpha:** agreement beyond what chance would produce. 1 is
  perfect agreement and 0 is chance level.

Read alpha beside raw agreement. When nearly every rating is the same, alpha is
low or undefined even if raters agree, because there is little variation to
agree on; raw agreement shows that case. Alpha needs at least two raters on some
target and some variation in the ratings; otherwise it is reported as missing.

Every rater is expected to review every target, so the report also lists each
target that lacks a review from any rater who reviewed something, and each
target that could not be reviewed with the reason.
