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

### Build

- **"Patched lines shown" needs the line's text in the output, not just a covering range** (`facts.PatchedRegions.shown_lines`). Line ranges alone would count truncated output. Changed lines are removed lines, plus the lines either side of a pure insertion. Blank lines are left out.
- **Edits and hitless searches never get the strong "patched lines" mark** (`build._event.touch`). Where they land in the file is unknown, so they get the file-level mark at most. Whole-file reads do get it.
- **Patched names are qualified `Class.method` when the patch shows the class** (`facts._patched_names`). Git strips indentation from hunk headers, so a header like `def reset` usually can't be placed in its class, and names such as `clear` or `messages` stay bare and match loosely.
- **A script the agent wrote with `cat > x.py` and then ran with `python x.py` is shown as that probe's code** (`build._script_file`).
- **`python --version` and `pytest --version`/`--help` are "run other", not probe code or test runs** (`shell._python`, `shell._pytest`).
- **Writes to files outside the repository (scratch scripts in `/tmp`) are "run other", not "edit source"** (`build._event`).
- **A chained command's exit code is shown once, on its last part** (`static/timeline.js`). It belongs to the whole chain.
- **`cd` is not tracked** (`shell.split`). Relative paths after a `cd` are read as relative to the repository root.
- **Renamed `TestFileHistory`/`TestFileVersion`/`test_histories` to `GeneratedFile`/`FileVersion`/`generated_files`.** Pytest collects `Test*`/`test_*` names imported into test modules. The new names also fit better, since the directory holds `conftest.py` and `.rst` fixtures too. `FileVersion.runs_before` became `runs_after`: a version is followed by the runs before the next one.
- **`Event.description` is a field set by `build`, not a property** (`events.Event`). It needs the search pattern and edit sizes, which only the builder has.
- **The right panel has two tabs, Event and Test files** (`static/app.js`), and `static/html.js` holds shared helpers. Selected reasoning rows expand inline. `j`/`k` move between events.


## Pre (cycle 2: edit facts)

- **Add three sequence facts behind an off-by-default toggle:** failures reported before an edit, skip/xfail marks added, and failed tests absent from the submission. They are cheap and low-noise, built from data the viewer already has, and they miss cases rather than invent them. Reworded as facts, they separate signal from failure-mode labels.
- **Leave out literal provenance** (a test literal that also appears in earlier output). Output formats rarely match test literals, and short values match everywhere, so it would be noisy without tuning.
- **Assertion strength and "noticed" keywords stay out.** They judge, even when reworded.

## Model-decided (cycle 2)

- **`FileVersion.part`, and runs ordered by `(step, part)`** (`facts._attach_runs`). Codex chains `apply_patch ... && pytest` in one step; step-only ordering dropped those runs.
- **A failure is attributed to a file when the FAILED id's path equals the file or is a suffix of it** (`facts._in_file`), so runs from inside the generated directory still match.
- **A first version's skip marks count as "added"** (`facts._attach_edit_facts`). It's literally true; the toggle's tag doesn't claim the mark came after a failure.
- **Fixed: `_defines` now matches diff lines** (`+def name`). Before, outcomes for partial versions always fell through to the file's last version.

## Review
