# Trace viewer

A local page that shows one run's agent trace as a timeline of typed, annotated
events. It is a standalone demo for judging whether traces carry enough signal for
open coding. It is not a pipeline stage and writes nothing to the run directory.

Open coding is dropped. The viewer is moving into the generation judgment's review
page, where raters rate the LLM judge's failure-mode labels against the trace. See
[the judgment pivot plan](../judgment-pivot/implementation.md).

Decisions and their reasons are in `decisions.md`.

## What it shows

- **Timeline.** One row per event, in trace order. Each row shows the step number,
  the action type (as a color), the files it touches, and a mechanical one-line
  description built from the tool arguments, such as `Read requests/models.py
  L220–400` or `pytest oracle_tests -q → 47 passed, 1 failed`. Clicking a row opens
  the raw input and output.
- **Lanes.** By default, one column per action type. A toggle switches to one
  column per file: patched files first, then test files, then the rest. Files
  touched only once go into one "other" column.
- **Defect overlap.** A strong mark on events that overlap patched lines, and a
  faint mark on events that only touch a patched file. A key for both sits in the
  table header.
- **Reasoning.** Reasoning and message events show a short preview that expands to
  the full text.
- **Side panel.** One tab bar with two labeled groups. Run tabs: Summary, Test
  files, and Legend. Step tab: Event, the selected row in full. Selecting a row
  opens Event.
- **Summary.** A document about the run, read first. The instance (repository,
  dataset, base commit, agent, localized target), the issue as Markdown, the prompt
  the agent was given, the golden patch with its changed line ranges and how many
  of those lines the agent saw, the reference tests, the final results as the 2×2
  matrix plus a per-test table with failure messages, and the edit facts as a table
  with step links.
- **Test files.** Each file under the generated directory with its versions. Each
  version is a diff against the previous one, with the pytest results of runs
  between versions and the final result of each test it introduced.
- **Edit facts.** Always shown, as tags on edit rows and versions. (1) A run between
  the previous version and this one reported failures, and which FAILED ids are in
  this file. (2) The edit added a skip/xfail mark. (3) Tests reported as FAILED
  that the submitted file no longer defines. They are sequence facts, worded
  neutrally, and never named as failure modes.
- **Legend and hover text.** Every tag shows a description on hover. The Legend tab
  lists every color, tag, and fact with the same descriptions.
- **Run picker.** Runs grouped by instance, showing harness and model.

The viewer annotates facts only: where the agent was, what it saw, and what
happened. It never labels an event as a failure mode.

## Action types

`read`, `search`, `edit_test`, `edit_source`, `run_tests`, `probe_code`,
`run_other`, `reasoning`, `message`, `failed_call`.

- `edit_test` vs `edit_source`: does the path fall under `task.generated_dir` or
  satisfy `localization.is_test_path`?
- `probe_code`: a Python invocation that isn't pytest (`python -c`,
  `python - <<EOF`, `python script.py`).
- `failed_call`: tool calls the harness aborted or rejected (OpenCode's `unknown`
  and `invalid`). Shell commands that exit nonzero keep their own type and record
  the exit code.

## File categories

Every path an event touches gets one of these: `source`, `test`, `generated_test`,
`docs` (`docs/`, `*.rst`, `*.md`, `README*`), `changelog` (`HISTORY*`, `CHANGES*`,
`CHANGELOG*`), `config`, or `outside` (outside the repository root). Git history
commands (`git log`, `git show`, `git blame`) are marked as such on the event.

## Structure

### Harness adapters: `harnesses/steps.py`, `claude.py`, `codex.py`, `opencode.py`

`harnesses/steps.py` defines `Step` and the actions an adapter can report:
`ReadFile(path, start, end)`, `Search(pattern, path)`, `WriteFile(path, content)`,
`EditFile(path, old, new)`, `Shell(command)`, and `OtherTool(name, input)`. A `Step`
is a tool call, reasoning passage, or message, with its trace line, output, exit
code, failure flag, text, and timestamp when the harness gives one.

Each adapter gains `read_steps(path) -> list[Step]` and a `REASONING` constant
(`"raw"` or `"summary"`). The adapter maps its own tool names and argument keys onto
the shared actions and drops harness noise. `harnesses/__init__.py` dispatches by
harness name (`read_steps`, `reasoning_kind`).

- Claude: pairs `tool_use` and `tool_result` by id. `thinking` and `text` blocks
  become reasoning and message steps. `system/thinking_tokens` events are dropped.
- Codex: every `command_execution` is a `Shell` step. `reasoning` and
  `agent_message` items become reasoning and message steps.
- OpenCode: each `tool` part is one step. Its `reasoning_details` text becomes a
  reasoning step placed just before it. `text` parts become message steps.
  Aborted `unknown` calls and `invalid` retries are failed steps.

### `src/oracle_lab/trace/`

- `shell.py`: `split(command) -> list[Command]` parses with `tree-sitter-bash`,
  unwrapping `sh -c`/`sh -lc`. Pipelines stay whole. `classify(command) ->
  list[ShellAction]` returns the shared file actions, or shell-only ones:
  `PatchFile` (an `apply_patch` update), `RunTests`, `RunPython`, and
  `RunCommand(git_history)`.
- `facts.py`: `Repository` (workdir and generated directory, which give relative
  paths and file categories), `PatchedRegions` (hunks, changed lines, and names
  from the patch and target; overlap and name matching), `grep_hits`,
  `parse_pytest`, and `generated_files`.
- `events.py`: `ActionType`, `FileCategory`, `DefectOverlap`, `Touch`,
  `PytestSummary`, `Event` (with its mechanical `description` and the `change` an
  edit made), `FileVersion`, `GeneratedFile`, `DefectSummary`, `RunSummary`, and
  `RunTrace.to_json()`.
- `build.py`: `read_run_facts(paths)` reads the run summary, repository, and
  per-test outcomes **loosely from raw YAML/JSON. This is demo only**: most demo
  runs fail the current `RunConfig`/`EvaluationResult` contracts. Replace it with
  the strict readers when the viewer joins the pipeline. `build_run_trace(paths)`
  assembles the `RunTrace`. `to_events` maps steps to events.
- `server.py`: `serve(runs)` and `TraceServer` serve `static/`, `highlight.js` from
  `web/static/vendor/` under `/vendor/`, `GET /api/runs`, and `GET /api/run?id=`.
  Traces are built on first request and cached in memory.
- `static/`: `index.html`, `app.js` (picker, lane toggle, panel tabs, hover text,
  state), `timeline.js`, `detail.js`, `tests.js`, `summary.js`, `legend.js`,
  `html.js` (shared helpers and the description table), `app.css`.

### CLI

`oracle-lab trace <path> [--port N] [--no-browser]`. The path is a run, or a
directory of runs. `cli.py` builds a `RunPaths` for each run that has a
`generation/trace.jsonl`, and imports the viewer lazily so the main install works
without the `trace` extra.

### Dependency

A new optional extra `trace = ["tree-sitter>=0.23,<1", "tree-sitter-bash>=0.23,<1"]`.
`oracle-lab trace` prints an install hint when it is missing. Local tests that need
it are skipped when it is absent.

## Line ranges and overlap

- Ranges come from Read/read `offset`/`limit`, `sed -n 'a,bp'`, `head -n`, and
  `tail -n` (as the last N lines, resolved only when the line count is known from
  output). Grep and `rg -n` hits come from `N:` and `path:N:` output lines.
- `cat`, and a Read with no limit, cover the whole file. A whole file overlaps any
  hunk in it.
- Hunks use old-side (buggy) line numbers, which match the checkout the agent saw.
- The share of patched lines shown counts changed buggy-side lines that fall inside
  a read or grep range *and* whose text appears in that event's output. Truncated
  output does not count, so this is a lower bound, and the page says so on hover.
- Edits and searches without line numbers get at most the file-level mark.

## Test file versions

Full content comes from Write/write, `apply_patch` Add File, and
`cat > f <<EOF`/`tee`. Edit/edit (`old_string`→`new_string`) and `apply_patch`
Update File apply to the last known version. If no base is known, or an edit
doesn't apply, the version records only that edit's diff, and `FileVersion.unknown`
says why: a shell edit such as `sed -i`, an unknown earlier version, or old text
that isn't in the previous version. The page says which in a sentence, and shows
the command for shell edits.
The final version is compared with `submission/files/` and marked if it differs.

## Implementation steps

1. Add the `trace` extra and the `Step` type, and `read_steps` for the three
   harnesses, with tests on small fixture traces of each format.
2. `shell.py`: parsing, unwrapping, and classification. Tests on real commands
   taken from the demo runs, especially Codex's chained and heredoc commands.
3. `facts.py`: patched regions and overlap, pytest output parsing, and test
   file histories, with tests.
4. `events.py` and `build.py`: assemble a `RunTrace`. Run it over every demo run
   and check that it finishes and that no step falls through to an unknown
   type without being counted.
5. `server.py` and the CLI command.
6. The page: picker, timeline with both lane modes, detail panel, test panel,
   reasoning previews, outcome toggle.
7. Open several runs from each harness in the browser and fix what looks wrong.

## Cycle 2: edit facts

`FileVersion` gains `part`, `follows_failed_run`, `follows_failures`, and
`skip_marks_added`; `GeneratedFile` gains `failed_then_absent`. All are computed
in `facts.generated_files` from the versions, runs, and submission it already has.
No new trace parsing. Runs are ordered by `(step, part)`, so a run chained after an
edit in the same command follows it. Cycle 3 shows them always.

## Cycle 3: page layout

`DefectSummary` gains `hunks`. The run header and both fact toggles are removed,
the side panel gains Summary and Legend tabs, and every tag gets hover text from
one description table in `static/html.js`.

The Summary tab reads like the review page's Overview. `RunTrace` gains a
`TaskContext` (instance, issue, prompt, golden and reference patches, and
`OutcomeRow`s), read loosely from `inputs/instance.json`, `ground-truth/issue.md`,
`inputs/prompt.txt`, `inputs/target.json`, and `evaluation/results.json`.

## Out of scope

Notes or codes on events, side-by-side runs, LLM summaries, failure-mode labels,
literal provenance (where a test's expected value came from), assertion strength,
and any change to the run pipeline or its artifacts.
