# Trace viewer decisions

## Pre

- **Delivery: local server, following `web/`'s pattern.** Consistent with the review page and closer to the eventual pipeline step than a static file.
- **Lanes: both layouts behind a toggle, lanes by action type as the default.** Lanes by file are the alternative. It's unclear which is more useful until real traces are viewed.
- **Chained shell commands split into one node per sub-command.** Each lands in its own lane so harnesses look comparable. Siblings share one output panel and are grouped visually.
- **Test history is per file.** Each version is diffed against the previous one, with pytest summaries of runs in between. Per-function tracking breaks where a version can't be rebuilt.
- **"Probe code" is its own action type.** It covers Python invocations other than pytest (`python -c`, heredoc scripts). This is where implementation-as-oracle would show up. Base set: read, search, edit test, edit source, run tests, probe code, run other, reasoning, message, failed call.
- **"Touches the defect" is measured by patched lines, with the patched file as a weaker level.** Ranged reads, grep hits, and edits are checked for overlap with hunks. Whole-file reads count as overlapping.
- **Automatic annotations are facts only, never failure-mode labels.** Kept: patched-line and patched-file overlap, first touch, share of patched lines shown, target read, file category per node, references to names defined in patched hunks, and parsed pytest results. Dropped: probe-then-hardcode, assertion strength, edit-after-failure, dropped tests, skip/xfail added, and keyword flags. Labels that name a failure mode would set the codebook before open coding does. They may return later as detectors that check codes the coding produced.
- **Final evaluation results are hidden behind a toggle, off by default.** Coders read the trace before knowing the outcome, which avoids outcome bias.
- **One run at a time, with a run picker grouped by instance.** Side-by-side timelines are cramped, and aligning them by step misleads when step counts differ widely.
- **Viewing only, no notes.** The demo checks whether traces carry enough signal. Coding storage gets designed when this becomes the open-coding step.
- **Patched function and class names come from the patch alone.** Sources: hunk headers, `def`/`class` lines inside hunks, and `target.json`. No network or checkout needed.
- **Shell commands are parsed with `tree-sitter-bash`, not a hand-written splitter.** It handles heredocs, pipelines, and lists, and tolerates broken input. It is an optional extra.
- **Run metadata and outcomes are read loosely from raw YAML/JSON, in one function in `trace/build.py`.** 18 of 19 demo runs fail the current `RunConfig`/`EvaluationResult` contracts (reshaped usage, toolchain versions, `test_target`), though not in any field the viewer needs. This is a deliberate demo-only exception to `AGENTS.md`'s no-shims rule: **throw it away and use the strict readers when the viewer joins the pipeline.**

## Model-decided

- **Own command and page.** `oracle-lab trace runs/<id>` starts a small server and page separate from the review page. Per-harness event parsing lives in each harness adapter, per `AGENTS.md`.
- **Adapters map tool calls onto shared actions** (`harnesses/steps.py`: read, search, write, edit, shell, other), not raw tool names and inputs. Argument keys like `file_path` vs `filePath` are harness-specific, so per `AGENTS.md` they belong in the adapter. `trace/` never sees a harness format.
- **Shell-only actions live in `trace/shell.py`** (`PatchFile`, `RunTests`, `RunPython`, `RunCommand`). Adapters never produce them.
- **The viewer is imported lazily in `cli.py`**, so the main install works without the `trace` extra.
- **Nothing is persisted.** The view is computed when a run is opened, so there is no new artifact contract.
- **Test file versions.** `Write`, `apply_patch` Add File, and `cat > f <<EOF` give full content. Edits and patch updates apply to the last known version. With no known base, the node shows that edit's diff alone.
- **Read ranges.** They come from `offset`/`limit`, `sed -n 'a,bp'`, `head`/`tail`, and grep `N:` output. `cat` counts as the whole file.
- **Page uses plain ES modules and a CSS grid, and reuses the vendored `highlight.js`.**

## Review
