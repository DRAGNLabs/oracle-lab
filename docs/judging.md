# Judging

An LLM judge rates a run's submission, and human raters then rate the judge's
answers. This guide covers both commands. The [results reference](results.md)
describes the files they write.

## The LLM judge

A run with a `judge` section is judged automatically after paired evaluation.
To judge a saved run for the first time, or again:

```sh
uv run oracle-lab judge runs/<run-id>
```

The command needs the configured judge credential and the saved runtime image.

- It saves the exact prompt and the raw final response before it validates
  `judgment.json`.
- Invalid model output is kept as `status: invalid_output`. It is not retried.
- Transient API failures are retried, as for any model turn.
- A rerun first moves the whole previous attempt to
  `judge-history/<UTC timestamp>/`, then rebuilds the workspace.

## Human review

Raters measure the two LLM judges, the generated-test judge and the task
classifier, by rating each answer they gave. They do not answer the questions
themselves. Each answer gets one rating:

| Rating | Meaning |
|---|---|
| `correct` | The rater would give the same answer. |
| `defensible` | The rater would answer differently, but the judge's answer is reasonable given the evidence and the stage rubric. |
| `incorrect` | The evidence or the stage rubric contradicts the judge's answer. |

A question the judge answered `null` is not rated, so a judgment has up to 4
ratings, an in-scope classification 7, and an out-of-scope one 1. Only a
completed judgment or classification can be reviewed; a failed, skipped, or
stale one is listed with the reason it cannot be.

[The human review workflow](human-review.md) describes how a review set is
shared with raters and how their reviews come back. Start the review page with:

```sh
uv run oracle-lab review <dir> --rater <id>
```

`<dir>` is a run, a classification attempt, or a directory holding `runs/` and
`classifications/`, such as a review data repository. The command starts a local web
server on `127.0.0.1` and opens the browser. `--port` picks the port and
`--no-browser` prints the URL instead of opening it. Press Ctrl+C to stop.

The page needs no Docker, credentials, or model calls. It does need Git and
network access to GitHub: the repository views are built from a shallow fetch of
the instance's base commit, cached under `~/.cache/oracle-lab/repos/` (or
`$XDG_CACHE_HOME/oracle-lab/repos/`). Later reviews of the same commit reuse the
cache.

The page shows the rater what the judge had:

- the judge's answers and justifications beside three rating buttons;
- for a judgment, the issue, the paired-outcome matrix and per-test outcomes,
  the generated tests, the repair, and the reference tests;
- for a classification, the task record's issue, issue comments or pull request
  texts, the repair, and the reference tests;
- the repository views the judge read, with a file finder: buggy and golden for
  a judgment, with a side-by-side view of the repair, and the buggy checkout
  only for a classification;
- the judge's evidence files, such as the agent session log or the task record;
- the stage rubric.

Unsaved ratings stay in the browser until the rater saves. Saving writes the
review under `reviews/<rater>/` beside the output it rates (`judge/reviews/` in a
run); saving again moves the earlier review to `reviews-history/<rater>/`. The page reads only the current rater's
reviews, never another rater's. Rerunning the judge moves the reviews into
`judge-history/` with the judgment they rated.
