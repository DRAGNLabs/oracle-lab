# Judging

An LLM judge and blinded human raters can each rate a run's submission. A third
command measures how well they agree. This guide covers the commands. The
[results reference](results.md) describes the files each one writes.

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

## Human raters

Create a blinded human workspace from the same saved `workspace-spec.json` used
for model judging:

```sh
uv run oracle-lab judge-workspace create runs/<run-id> --rater rater_01
```

The command rebuilds the judge bundle. It stops if the rebuilt specification
differs from the saved one. It then leaves a named container running, with no
network and no model credential, and prints commands for opening a shell or
attaching VS Code.

Inside the container:

- The shared evidence is read-only. The non-root `oracle` user can write only to
  `/oracle-judge/output/`.
- LLM responses and other raters' ratings are never uploaded.
- The run's rubric is the one frozen when the run was created. Ratings made
  against an older rubric do not validate. To use a newer rubric, create a new
  run rather than mixing rubric versions.

To rate:

1. Read `/oracle-judge/instructions.md` alongside `rubric.md`. The instructions
   cover the workflow, a guide to the evidence files, and a summary of this
   task's test outcomes.
2. Open `/oracle-judge/output/judgment.json`. It starts with placeholders for
   `tests_issue`, `attempt_detail`, `no_attempt_reason`, `cheating`, and
   `rationale`.
3. Replace each placeholder with a rubric label. For a conditional field that
   does not apply, use JSON `null` or the string `"null"`.
4. Write a short rationale.

The answer is the rater's own. Collection validates it but never fills in or
changes a label. Collect the rating, then remove the container:

```sh
uv run oracle-lab judge-workspace collect <container-name>
uv run oracle-lab judge-workspace remove <container-name>
```

If the JSON or a label is invalid, collection rejects it and leaves the workspace
running so the rater can fix it. A valid rating is saved under
`judge/human/<opaque-rater-id>/`, as both the submitted JSON and the normalized
result. Its `provenance.json` records the hashes of the rubric and of the shared
workspace specification, which covers `instructions.md`.

Collection never overwrites a saved rating. To replace one, pass
`--archive-existing`. The previous files then move to
`judge/human-history/<opaque-rater-id>/<UTC timestamp>/`.

`remove` checks the container's profile label, so it cannot remove an unrelated
container. Rerunning the LLM judge leaves human ratings alone. Reevaluation marks
both LLM and human judgments stale, because the evidence they rated has changed.

## Agreement

After blinded human ratings have been collected, run the offline agreement
analysis for a batch:

```sh
uv run oracle-lab agreement batches/<batch-id> --sample-per-stratum 8
```

The command never loads credentials, starts containers, or calls a model. It
also picks a calibration sample of runs for raters to rate. Do not show raters
the sample file or any existing labels. They rate through the blinded workspace
only.
