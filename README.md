# Oracle Lab

## Quickstart

Requirements:

- Python 3.11 or later and [uv](https://docs.astral.sh/uv/).
- Docker with a running Linux daemon.
- A credential for the provider the configuration selects.
- A Unix host (Linux or macOS). The CLI uses Unix signals to enforce pull and
  build deadlines.

Copy the local secrets file and add your API keys:

```sh
# Create the local secrets file if you don't already have one.
[ -f .env ] || cp .env.example .env
```

Run from the repository root:

```sh
uv sync --extra dev

uv run oracle-lab preflight configs/smoke.yaml
uv run oracle-lab run configs/smoke.yaml
```

## About

Oracle Lab measures how well coding agents write unit tests for code that
already contains a bug. The agent gets a buggy repository and a request to write
tests. It does not see the issue description or the fix. Oracle Lab then runs
the same tests against the buggy version and the fixed (golden) version. It
records the paired pass/fail outcomes.

A separate `classify` command labels SWE-bench and SWE-rebench V2 problems. The labels
cover defect mechanism, test setup, oracle availability, scope, and benchmark
quality. A run's report shows the newest classification for its instance, if
one exists.

80% of this repo is just coordinating containers and managing data in and out of them.

## Usage

Every command is `uv run oracle-lab <command>`, run from the repository root.
`uv run oracle-lab <command> --help` lists a command's arguments. Commands that
take a YAML file write into that file's `output` root, `output/` in the sample
configs.

A typical sequence: `preflight` each problem, then `run` or `batch`, then
`classify` each problem, then human review.

### `preflight`

Checks one or more problems without calling a model. For each config it
resolves the dataset record, builds the runtime image, finds the code the golden
repair changes (the localized target) and its fix scope, runs the private
reference test on the buggy and golden code, and runs one fixed test the way
generated tests run.

```sh
uv run oracle-lab preflight configs/smoke.yaml [more configs...]
```

Each config gets a directory in `output/preflight/`. It prints one line per
config, with the fix scope, and exits non-zero if any failed.

Run it before anything else. A localized `run`, `batch` job, or `classify` loads
its target from the newest passed preflight of the same problem, and stops if
there is none.

### `run`

Runs one config end to end: build, private reference check, the agent writing
tests in a buggy container, capture, evaluation on both versions, the LLM judge
(if the config has a `judge` section), and the report.

```sh
uv run oracle-lab run configs/smoke.yaml
```

The run is saved in `output/runs/<run-id>/`. Start with its `report.md`.

### `batch`

Runs several configs one after another, from a manifest that lists them. A
failed job does not stop the others. `configs/validation/batch.yaml` is an
example.

```sh
# Start a batch.
uv run oracle-lab batch configs/validation/batch.yaml

# Continue an unfinished batch. Completed jobs are not repeated.
uv run oracle-lab batch --resume output/batches/<batch-id>
```

The batch summary is saved in `output/batches/<batch-id>/`. Each job is an
ordinary run in `output/runs/`.

### `evaluate`

Reruns a saved run's captured tests in fresh buggy and golden containers. No
agent or model calls. It needs the run's runtime image to still exist.

```sh
uv run oracle-lab evaluate output/runs/<run-id>
```

The previous evaluation moves to `evaluation-history/`. An existing judgment is
marked `stale`, because the evidence it rated was replaced.

### `judge`

Runs the LLM judge on a saved run: for the first time, or again. It uses the
run's `judge` config, needs that judge's credential, and needs the saved runtime
image.

```sh
uv run oracle-lab judge output/runs/<run-id>
```

A rerun moves the previous judgment, and any human reviews of it, to
`judge-history/`.

### `report`

Rebuilds a run's `report.md` from its saved files. Runs no code.

```sh
uv run oracle-lab report output/runs/<run-id>
```

### `classify`

Labels one problem with the LLM task classifier, separately from any run. It
uses its own YAML format; `configs/classification/example.yaml` is an example.
The problem needs a passed preflight.

```sh
uv run oracle-lab classify configs/classification/example.yaml
```

Each attempt is saved in `output/classifications/<instance-id>/<attempt-id>/`.
A run's report shows the newest classification of its problem.

### `review`

Opens a local web page for rating the LLM judges' answers: each run's judgment
and each problem's classification. No Docker, API keys, or model calls. The page
fetches each repository from GitHub the first time it is needed. See
[Human review](#human-review) for the full workflow.

```sh
uv run oracle-lab review <path> --rater <your-id>
```

- `<path>`: one run, one classification attempt, or a directory holding
  `runs/` and `classifications/`.
- `--rater`: your rater ID: letters, digits, `-`, and `_`.
- `--port`: the port to serve on. Default: any free port.
- `--no-browser`: print the URL instead of opening a browser.

Stop it with Ctrl+C.

### `agreement`

Measures agreement between raters, and how often they found the LLM judges
right. Lists any target a rater has not reviewed yet.

```sh
uv run oracle-lab agreement ../oracle-lab-data [more paths...]
```

- `--output`: where to write `agreement.json` and `agreement.md`. Default:
  `output/agreement/<agreement-id>/` in the current directory.

### `export`

Writes every run, classification attempt, and review under an output root as a
dataset: Parquet tables, raw copies, and a dataset card.

```sh
# Write it locally.
uv run oracle-lab export output/ --out dataset/

# Upload it to a private Hugging Face dataset. Needs HF_TOKEN in the environment or .env.
uv run oracle-lab export output/ --repo <owner>/<name>
```

Give `--out`, `--repo`, or both.

## Human review

Raters rate the LLM judges' answers in the `review` page. Runs reach raters, and
reviews come back, through a separate private Git repository, the data
repository (`oracle-lab-data` below). It holds only runs and classification
attempts, no code. Keep it private: the runs contain the golden repairs and
reference tests.

- `main` holds the review set.
- Each rater pushes reviews to their own branch, `reviews/<rater-id>`.
- The maintainer merges the rater branches locally to compute agreement. Nobody
  merges reviews into `main`, so no rater sees another's reviews.

Raters must use the same `oracle-lab` revision that produced the runs, because
the review page reads them with strict contracts.

[docs/human-review.md](docs/human-review.md) has more detail and a
troubleshooting table.

### Setup (maintainer)

Create an empty private repository, for example `oracle-lab-data` on GitHub, and
give each rater write access. Then clone it beside `oracle-lab`:

```sh
cd ..
git clone <data-repository-url> oracle-lab-data
cd oracle-lab
```

Produce the runs and classifications in `oracle-lab` as usual (`preflight`,
`batch`, `classify`). Copy the ones to review into the data repository, keeping
the output layout:

```sh
mkdir -p ../oracle-lab-data/runs ../oracle-lab-data/classifications/<instance-id>
cp -r output/runs/<run-id> ../oracle-lab-data/runs/
cp -r output/classifications/<instance-id>/<attempt-id> ../oracle-lab-data/classifications/<instance-id>/
```

Check the set. The listing names anything that cannot be reviewed, and why. Only
a completed judgment or classification can be reviewed. Stop it with Ctrl+C.

```sh
uv run oracle-lab review ../oracle-lab-data --rater check --no-browser
```

Publish it:

```sh
cd ../oracle-lab-data
git add runs classifications
git commit -m "Add review set"
git push origin main
```

To add more later, copy, commit, and push to `main` again, then tell raters to
pull it. Do not rejudge a run raters have started on; add the new judgment as a
new run instead.

### Raters

You need Git with push access to GitHub, [uv](https://docs.astral.sh/uv/), and a
web browser. You do not need Docker or an API key.

Set up once. Clone both repositories side by side and create your branch:

```sh
git clone <oracle-lab-url> oracle-lab
git clone <data-repository-url> oracle-lab-data

cd oracle-lab
uv sync

cd ../oracle-lab-data
git switch -c reviews/<your-id>
```

Pick one rater ID (letters, digits, `-`, and `_`, such as `alice`) and use it
every time.

Review, from `oracle-lab`:

```sh
cd ../oracle-lab
uv run oracle-lab review ../oracle-lab-data --rater <your-id>
```

The page lists every problem with its tasks and your progress. Work one problem
at a time: its classification first, then each run. Read the Problem tab before
rating, and the "How to review" dialog on your first rating page. Saved reviews
are files in the data repository. Stop the page with Ctrl+C; you can resume any
time.

Hand in your reviews, whenever you want to save progress and when you finish:

```sh
cd ../oracle-lab-data
git add runs classifications
git commit -m "Reviews by <your-id>"
git push -u origin reviews/<your-id>
```

Only your own `judge/reviews/<your-id>/` and `judge/reviews-history/<your-id>/`
directories should show up in `git status`.

When the maintainer adds targets, merge `main` into your branch and restart the
page:

```sh
cd ../oracle-lab-data
git fetch origin
git merge origin/main
```

When the maintainer updates `oracle-lab`, run `git pull` and `uv sync` in
`oracle-lab`.

### Collecting (maintainer)

Merge every rater branch into a local `agreement` branch. Raters write to
different directories, so the merge has no conflicts.

```sh
cd ../oracle-lab-data
git fetch origin
git switch -c agreement origin/main
git merge origin/reviews/<rater-1> origin/reviews/<rater-2>
```

Compute agreement, from `oracle-lab`:

```sh
cd ../oracle-lab
uv run oracle-lab agreement ../oracle-lab-data
```

Optionally export the reviewed set, with every rater's reviews:

```sh
uv run oracle-lab export ../oracle-lab-data --repo <owner>/<name>
```

Never push the `agreement` branch or merge it into `main`. To refresh it, delete
it and repeat these steps:

```sh
cd ../oracle-lab-data
git switch main
git branch -D agreement
```

## Configuration and credentials

Choose a sample configuration, or copy one and edit it.

The smoke configuration uses Claude Haiku through OpenRouter for generation and
judging. Set `OPENROUTER_API_KEY` in `.env` before running it.

The YAML interface defines:

| Section  | Controls                                                                     |
| -------- | ---------------------------------------------------------------------------- |
| `source` | Source adapter and instance ID                                               |
| `agent`  | Claude Code, Codex, or OpenCode harness; provider, model, one stopping limit |
| `judge`  | Optional semantic-judge rubric, harness, model, and stopping limit           |
| `task`   | Repository/localized scope, prompt, generated directory, and test visibility |
| `limits` | Setup/evaluation timeouts and container CPU/memory limits                    |
| `output` | Output root that every command writes into                                   |

See the [complete configuration reference](docs/configuration.md) for every
setting, default, validation rule, and harness-specific behavior.

## Results

Everything is written under the output root, `output/` in the sample configs:
`runs/`, `preflight/`, `batches/`, `classifications/`, and `agreement/`.

| Artifact in `output/runs/<run-id>/` | Contents                                                  |
| ----------------------------------- | --------------------------------------------------------- |
| `report.md`                         | Human-readable run summary and artifact links             |
| `status.json`                       | The latest stage and its state, with any error            |
| `cost.json`                         | What the run's model stages spent on the OpenRouter key   |
| `inputs/`                           | Frozen prompt, resolved config, private instance, target  |
| `image-build/`                      | Runtime image, build inputs, provenance, and logs         |
| `reference-check/`                  | Private fail-on-buggy/pass-on-golden validation           |
| `generation/`                       | Agent command, transcript, raw events, and workspace diff |
| `submission/`                       | Captured generated tests, hashes, and violations          |
| `evaluation/`                       | Paired result plus buggy and golden executions            |
| `judge/`                            | Judge workspace manifest, evidence, judgment, and reviews |
| `ground-truth/`                     | Private issue text and golden repair, for readers         |

See the [complete results and artifacts reference](docs/results.md) for the full
directory layout, JSON fields, logs, evaluation artifacts, and reevaluation
behavior. [Judging](docs/judging.md) covers the LLM judges and the review page.

For initial batch plots, run `uv sync --extra dev --extra analysis`
and open [notebooks/initial-results.ipynb](notebooks/initial-results.ipynb) with
the project's `.venv` kernel. Set `BATCH_DIR` in its first code cell to choose
which saved batch to inspect.

## Tests

```sh
# Local suite: no Docker or API keys required.
uv run pytest -q

# Formatting and lint checks.
uv run ruff check src tests
uv run ruff format --check src tests

# Docker integration checks after a smoke run has prepared its image.
ORACLE_LAB_SMOKE_RUN=output/runs/<run-id> uv run pytest -q -m docker
```

Local tests cover configuration, credentials, agent traces, artifacts, pytest
outcomes, and reports. Docker checks run handwritten tests on the
Requests task. They reach all four matrix cells and both test-visibility
settings. They do not call a model.

Docker may report a socket permission error after you join the `docker` group.
Run `newgrp docker`, or log out and back in. Then check `docker info` again.

## Misc notes

- The private regression check must pass before generation starts.
- The agent runs as a non-root user with only the selected credential. The host
  Docker socket and private run artifacts are not mounted.
- Generation, reference, and evaluation containers all have outbound network
  access, so repository tests run in the same environment each time.
- The agent gets a fresh Git repository with one commit: the prepared buggy
  snapshot. Internet use is not audited.
- Only new tests and fixtures under `oracle_tests/` are evaluated. Changes
  outside that directory are recorded as violations, and the results become
  diagnostic only. Configuration decides whether existing repository tests stay
  visible.

## License

Copyright 2026 Brigham Young University. Licensed under the
[Apache License, Version 2.0](LICENSE). See [NOTICE](NOTICE) for attribution and
bundled third-party software.
