# Data layout and dataset export

Temporary design notes. Delete this directory once the work is merged and the
permanent docs (`docs/results.md`, `docs/configuration.md`, `docs/human-review.md`)
describe the result.

## Goal

1. Every output lives under one `output/` directory, with one fixed layout.
2. Every LLM judge's output and every human review of it sits in the same place
   relative to what was judged: `<target>/judge/`, with reviews in
   `<target>/judge/reviews/<rater>/`.
3. One command, `oracle-lab export`, turns an output directory into a Hugging Face
   dataset: the raw files plus flat Parquet tables.

## Target layout

```text
output/
├── runs/
│   └── <run-id>/
│       ├── status.json, cost.json, report.md
│       ├── inputs/  image-build/  reference-check/  generation/
│       ├── submission/  ground-truth/  evaluation/
│       ├── judge/                        # generation judge (unchanged)
│       │   ├── rubric.md, prompt.md, workspace-spec.json, bundle-manifest.json, ...
│       │   ├── trace.json
│       │   ├── agent/
│       │   ├── judgment.raw.txt
│       │   ├── judgment.json
│       │   ├── reviews/<rater>/{review.json,provenance.json}
│       │   └── reviews-history/<rater>/<timestamp>/
│       └── judge-history/  generation-history/  evaluation-history/
├── classifications/
│   └── <instance-id>/
│       └── <attempt-id>/
│           ├── status.json, cost.json
│           ├── inputs/{config.resolved.yaml,instance.json}
│           ├── image-build/
│           └── judge/                    # classifier, same shape as a run's judge/
│               ├── rubric.md, workspace-spec.json, workspace.log
│               ├── agent/  agent-history/
│               ├── classification.raw.txt
│               ├── classification.json
│               ├── reviews/<rater>/{review.json,provenance.json}
│               └── reviews-history/<rater>/<timestamp>/
├── batches/
│   └── <batch-id>/{batch.input.yaml,batch.resolved.json,status.json,summary.json,report.md}
└── preflight/
    └── <run-id>/
```

The review data repository mirrors this tree. It holds only `runs/` and
`classifications/`.

## Part 1: one output root (done)

A config's `output` field names the output root, and it is now required. Each
command adds its own fixed subfolder.

| Command | Before | After |
|---|---|---|
| `run` | `<output>/<run-id>` (default `runs`) | `<output>/runs/<run-id>` |
| `preflight` | `<output>/preflight/<run-id>` | `<output>/preflight/<run-id>` |
| `batch` | `<batch output>/<batch-id>`, jobs to each job config's `output` | `<output>/batches/<batch-id>`, jobs to `<output>/runs/` |
| `classify` | `<output>/<instance>/<attempt>` (default `classifications`) | `<output>/classifications/<instance>/<attempt>` |
| `agreement` | `agreement/<stamp>` in the current directory | `output/agreement/<stamp>` in the current directory |

- `paths.py`: `OutputPaths` names the root's directories and creates new run,
  preflight, batch, and attempt directories (`new_run()` and so on), with one
  `new_id()` for every ID. `RunPaths.output` is the root a run is saved in.
- Batch manifests have no `output`. Every job config must name the same root;
  the batch directory goes in that root's `batches/`.
- Sample configs point at the repository's `output/`. `.gitignore` ignores
  `output/` and `archive/`. Earlier results were moved to `archive/` unchanged.

## Part 2: classification judge folder (done)

A classification attempt's classifier files and reviews are in `judge/`, so
reviews are at `<target>/judge/reviews/` for both stages.

- `paths.py`: `JudgePaths` is the shared shape of a judge directory (rubric,
  workspace log and spec, `agent/`, `reviews/`, `reviews-history/`).
  `JudgmentPaths` adds the generated-test judge's files and `judge-history/`;
  `ClassifierPaths` adds `classification.json`, `classification.raw.txt`, and
  `agent-history/`. `AttemptPaths` is the handle for an attempt.
- `classification/run.py`, `workspace.py`, and `contracts.py` use `AttemptPaths`.
  The frozen rubric moved from `inputs/rubric.md` to `judge/rubric.md`.
- `review/targets.py`: each target exposes its `judge` directory; the store and
  the page read reviews, rubric, workspace spec, and judge result from it.
  `ReviewProvenance.reviewed_output` is `judge/classification.json` for an
  attempt.
- Run detection now looks for `generation/`, since an attempt also has a
  resolved config and a `judge/` directory.

## Part 3: export to tables (done)

**What gets built:** `oracle-lab export <root> --out <dir>` reads an output
directory (or the merged review data repository) and writes a dataset directory.
It does not upload. Part 4 adds the upload.

New package `src/oracle_lab/export/`:

- `tables.py`: one function per table. Each reads artifacts through the existing
  contracts and returns rows.
- `writer.py`: writes the tables as Parquet, copies the raw files, and writes the
  manifest and the dataset card.

Discovery walks the root's `runs/` and `classifications/` directly, because
`find_targets` returns only reviewable targets and the export needs every run.
Reviews are read through `ReviewTarget` and the review store. A run or attempt
that fails validation is listed in the manifest as skipped, with the error. An
attempt without a classification is skipped too. Batches and preflight
directories are not exported.

### Output directory

```text
<out>/
├── README.md                    # dataset card, with the `configs:` YAML header
├── export-manifest.json
├── data/
│   ├── runs.parquet
│   ├── tests.parquet
│   ├── judgments.parquet
│   ├── failure_modes.parquet
│   ├── classifications.parquet
│   ├── reviews.parquet
│   ├── rater_failure_modes.parquet
│   └── trace_steps.parquet
└── raw/
    ├── runs/<run-id>/...
    └── classifications/<instance-id>/<attempt-id>/...
```

### Tables

Every row carries the keys needed to join back: `run_id` or `attempt_id`, and
`instance_id`.

| Table | One row per | Columns (from) |
|---|---|---|
| `runs` | run | dataset, instance, base commit, agent harness and model, prompt, localized or not (`inputs/config.resolved.yaml`); stage and state (`status.json`); cost (`cost.json`); generation status, duration, tokens (`generation/result.json`); test file count, forbidden changes, diagnostic-only (`submission/manifest.json`); matrix cell counts (`evaluation/results.json`) |
| `tests` | generated test in a run | test ID, buggy outcome, golden outcome, matrix cell (`evaluation/results.json` paired outcomes) |
| `judgments` | run with a judge | judge harness and model, status, `tests_issue`, `attempt_detail`, `no_attempt_reason`, `cheating`, justifications, rubric SHA-256 (`judge/judgment.json`, `judge/agent/result.json`) |
| `failure_modes` | run × label | label key or added name, source (`start_list` or `judge_added`), present, cited steps, justification (`judge/judgment.json`) |
| `classifications` | attempt | classifier harness and model, status, every rubric label and justification (`judge/classification.json`) |
| `reviews` | target × rater × item | stage, rater, item key, `agree` or `disagree`, reviewed output SHA-256, rubric SHA-256 (`judge/reviews/<rater>/`) |
| `rater_failure_modes` | label a rater added | stage, target, rater, name, description, steps |
| `trace_steps` | agent trace step | step number, kind, command, text, output (`judge/trace.json`) |

The current review only, not `reviews-history/`. The raw layer keeps the
history.

### Dataset card

`README.md` starts with a YAML header that tells the Hugging Face viewer and
`datasets.load_dataset` which file is which table:

```yaml
configs:
  - config_name: runs
    data_files: data/runs.parquet
  - config_name: reviews
    data_files: data/reviews.parquet
  # one entry per table
```

The body is generated: what the dataset is, the table list with one line each,
the export time and oracle-lab commit, and how to read a table:
`load_dataset("<repo>", "reviews")`.

### Manifest

`export-manifest.json`: the export time, the oracle-lab version (`git describe
--always --dirty`), the source root, every exported run and attempt ID, every
skipped target with its error, and the row count of each table. Its contract and
one row model per table are in `export/contracts.py`; the Parquet schemas are
derived from the row models.

### Dependencies

`pyarrow` and `huggingface_hub` are core dependencies, imported at module top
level. The `dataset` extra is gone; `experiments/localization` installs `datasets`
itself with `uv run --with datasets`.

## Part 4: upload (done)

**What gets built:** `oracle-lab export <root> --repo <owner/name>` builds the
dataset directory (in a temporary directory unless `--out` is given) and uploads
it.

- Authentication: `huggingface_hub` reads `HF_TOKEN` from the environment. The
  token is never a config field or argument, matching the credential rules. A
  missing token is an error before any work starts.
- `HfApi.create_repo(repo, repo_type="dataset", private=True, exist_ok=True)`.
  The repository is created private. Changing it to public is a manual step on
  the Hugging Face website.
- Upload with `HfApi.upload_large_folder`, which handles many files, resumes
  after an interruption, and splits the work across commits.
- Each export replaces the repository contents with the new directory, so a run
  removed from the source disappears from the dataset. Hugging Face dataset
  repositories are Git repositories, so earlier exports stay in the history.

## Decisions to confirm

Recommendations are listed first.

1. **Source of the published dataset.** Export from the merged `agreement`
   checkout of the review data repository: it is the only place that holds the
   runs, classifications, and every rater's reviews together. Export also works
   on `output/` for runs that were never reviewed.
2. **Visibility.** Private. The runs hold golden repairs and reference tests. They
   come from public benchmarks, but publishing them beside agent traces makes
   contamination easier. A public release would drop `ground-truth/` and the
   reference patches from `raw/`, or use gated access.
3. **Invalid targets.** Skip and list them in the manifest. The alternative is to
   fail the whole export.
4. **Raw scope.** Upload everything. Most of a run's ~50 MB is execution
   artifacts under `evaluation/` and `reference-check/`. That is fine for Hugging
   Face, and `tests` already has the outcomes. A `--no-execution-artifacts` flag
   can be added later if size matters.
5. **Rater IDs.** Publish as they are. Raters choose their own IDs, and the
   review guide can tell them that IDs are public.

## Order of work

1. Part 1: done.
2. Part 2: done.
3. Part 3: done.
4. Part 4: done. A real upload is a manual step the user runs.
5. Docs: done.
