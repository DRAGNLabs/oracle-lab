# Judging

An LLM judge rates a run's submission. This guide covers the command. The
[results reference](results.md) describes the files it writes.

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

