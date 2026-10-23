---
$header---

# Oracle Lab results

Tests that coding agents generated for buggy SWE-bench repositories, evaluated on
the buggy and repaired versions; the LLM judges' answers about each run and task;
and human raters' reviews of those answers. Produced by Oracle Lab.

Exported $created_at from Oracle Lab $version: $runs runs and $attempts
classification attempts. `export-manifest.json` lists them, and anything left out
with the reason.

## Tables

$tables

Load one table with `datasets.load_dataset("<this repository>", "<table>")`.
Rows join on `run_id`, `attempt_id`, or `target_id`, and on `instance_id`. A
review's `item` matches a `failure_modes` row's `item`, or names a judgment or
classification column.

## Raw files

`raw/runs/<run-id>/` and `raw/classifications/<instance-id>/<attempt-id>/` hold
every saved file, including agent traces, execution logs, and the private ground
truth. Their layout is documented in Oracle Lab's `docs/results.md`.
