# Configuration reference

Oracle Bench runs one benchmark instance from a YAML file:

```sh
uv run oracle-bench run configs/smoke.yaml
```

A batch manifest runs several independent configurations:

```yaml
name: initial-nex
jobs:
  - config: ../initial-nex/astropy.yaml
  - config: ../initial-nex/flask.yaml
execution:
  continue_on_error: true
output: ../../batches
```

Run it with `oracle-bench batch <manifest>`. Paths are relative to the manifest.
`configs/batches/initial-nex.yaml` runs one localized task from each of the ten
supported repositories. It uses OpenCode with the free
`nex-agi/nex-n2.5-pro:free` OpenRouter model.

How a batch runs:

- Every job configuration is validated and hashed before any job starts.
- Jobs run one at a time. A failed job does not stop the others.
- The aggregate JSON and Markdown are rewritten after every job.
- `oracle-bench batch --resume <batch-directory>` continues an unfinished batch.

A run configuration has six top-level sections: `source`, `agent`, `judge`
(optional), `task`, `limits`, and `output`. Only `source.instance`,
`agent.model`, and `task.prompt` are required. Unknown settings are rejected.

## Minimal configuration

```yaml
source:
  instance: psf__requests-1142

agent:
  model: gpt-5.4

task:
  prompt: ../prompts/agent/unit-tests.md
```

For a localized SWE-bench assignment:

```yaml
task:
  prompt: ../prompts/agent/localized-tests.md
  scope: localized
  existing_tests: hide_all
```

For a Claude run:

```yaml
source:
  instance: psf__requests-1142

agent:
  harness: claude
  model: haiku
  auth: api_key
  limit:
    kind: wall_seconds
    value: 300

task:
  prompt: ../prompts/agent/smoke-tests.md
```

Paths in the input file are resolved relative to that file, not the shell's
current directory.

Pydantic schemas check the file when it loads. Unknown fields and wrong value
types are rejected. Some checks happen later: path resolution, and the runtime
checks that need the SWE-bench instance. Configurations are not versioned. A file
written for an earlier revision fails to load; it is never half-read.

## `source`

The source selects one SWE-bench Verified record by default.

### `source.instance`

- **Type:** string
- **Required:** yes

The SWE-bench `instance_id`, such as `psf__requests-1142`. Exactly one record
must match. The official prepared image name is derived from this ID.

### `source.kind`

- **Type:** `swebench`
- **Default:** `swebench`

Selects the source adapter. SWE-bench is the only one.

### `source.dataset`

- **Type:** dataset alias
- **Default:** `verified`

`lite` resolves to `princeton-nlp/SWE-bench_Lite`; `verified` resolves to
`princeton-nlp/SWE-bench_Verified`.

### `source.revision`

- **Type:** 40-character commit SHA
- **Default:** Oracle Bench's pinned revision for the selected dataset

The commit of the Hugging Face dataset repository. Leave it unset unless you
need a different dataset version. It is not the buggy code's commit; that is the
record's `base_commit`. Every run saves the resolved value and the record's
checksum.

### `source.split`

- **Type:** string
- **Default:** `test`

The dataset split searched for `source.instance`. SWE-bench Verified runs use
the default.

### `source.record`

- **Type:** host path or null
- **Default:** null

A local SWE-bench JSON record, for offline runs and fixtures. When it is set,
Oracle Bench does not contact Hugging Face. The record must have:

- nonempty `instance_id`, `repo`, `base_commit`, `patch`, and `test_patch`
  strings;
- an `instance_id` equal to `source.instance`;
- a `base_commit` that is a full commit SHA;
- a `FAIL_TO_PASS` that is a list of test IDs, or a JSON-encoded list.

The resolved record, golden repair, and reference tests are saved privately in
`instance.json`. The generation agent never sees them.

## `agent`

This section selects the agent under test. The supported combinations are:

| `harness` | `provider` | Credential |
|---|---|---|
| `codex` | `openrouter` (default) | `OPENROUTER_API_KEY` |
| `codex` | `openai` | `OPENAI_API_KEY` |
| `claude` | `openrouter` (default) | `OPENROUTER_API_KEY` |
| `claude` | `anthropic` | `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` |
| `opencode` | `openrouter` (default) | `OPENROUTER_API_KEY` |

The credential follows from the provider and `auth`. Through OpenRouter:

- Claude Code uses OpenRouter's Anthropic-compatible endpoint.
- Codex uses OpenRouter's Responses-compatible endpoint.
- OpenCode uses its built-in OpenRouter provider.

Put secret values in the environment or the gitignored `.env`, never in YAML.

### `agent.model`

- **Type:** string
- **Required:** yes

The model ID passed to the selected CLI. Use the provider's full model ID when
the exact model matters.

### `agent.harness`

- **Type:** `codex`, `claude`, or `opencode`
- **Default:** `codex`

The agent CLI launched in the generation container. OpenCode works only with
OpenRouter here.

### `agent.provider`

- **Type:** `openai`, `openrouter`, or `anthropic`
- **Default:** `openrouter` for every harness

Codex supports OpenRouter and OpenAI. Claude supports OpenRouter and Anthropic.
OpenCode supports OpenRouter. To use OpenAI or Anthropic directly, name it here.

### `agent.limit`

- **Type:** `{kind: wall_seconds, value: <positive number>}`
- **Default:** `{kind: wall_seconds, value: 300}`

The longest the model turn may run. The same container deadline applies to
every harness. Files written before a timeout are still captured and evaluated.
`judge.limit` has the same shape and default.

### `agent.multi_agent`

- **Type:** boolean
- **Default:** `true`

Whether the agent under test may use its harness's subagents. The judge never
may, so `judge.multi_agent` accepts only `false`.

### `agent.auth`

- **Type:** `oauth` or `api_key`
- **Default:** `oauth`
- **Applies to:** Claude

`oauth` reads `CLAUDE_CODE_OAUTH_TOKEN`. `api_key` reads `ANTHROPIC_API_KEY`.
Codex picks its credential from the provider alone.

## `judge`

The optional judge rates the generated tests, with access to private evidence.
When this section is present, `run` calls the judge after paired evaluation.
`oracle-bench judge runs/<run-id>` reruns the same stage from saved evidence.
Without this section, a run makes no judge model call.

```yaml
judge:
  rubric: ../prompts/judge/generated-test-evaluation-rubric.md
  instructions: ../prompts/judge/generated-test-instructions.md
  harness: claude
  provider: anthropic
  model: haiku
  auth: api_key
  limit:
    kind: wall_seconds
    value: 300
```

A run with a judge checks for the judge credential before generation starts. A
missing credential fails the run before it spends anything.

`judge.rubric` and `judge.instructions` are both required. Their paths are
relative to the configuration file. A run copies both files to `judge/rubric.md`
and `judge/instructions.md` when it starts. A later `oracle-bench judge` then
uses the same text.

The instructions tell the judge what the workspace holds and what to return.
`$root` in them becomes the workspace path inside the container. The rubric
defines the labels. The two are joined, instructions first, into
`judge/prompt.md`, the exact text sent to the model.

Every other field works as it does under `agent`, except `multi_agent`, which is
always `false`.

## `task`

### `task.prompt`

- **Type:** host path
- **Required:** yes

The prompt template given to the agent. Everything the agent is told comes from
this file. It may use these variables:

- `${generated_dir}`: repository-relative directory where new tests are allowed.
- `${project_python}`: absolute path to the resolved project Python.
- `${workdir}`: repository root inside the container.
- `${test_target}`: a localized run's target, one line per file, as saved in
  `inputs/target.json`. See [`task.scope`](#taskscope).

Oracle Bench fills them in and saves the exact text sent as `prompt.txt`. Write
`$$` for a literal dollar sign.

### `task.scope`

- **Type:** `localized` or `repository`
- **Default:** `repository`

`localized` points the agent at the existing code the golden repair changes.
After the image build, a `localize` stage:

1. reads each changed production Python file from the image;
2. applies the golden patch to it;
3. compares the buggy and repaired syntax trees.

The target is the most specific level any changed file reaches:

1. Existing functions and methods whose syntax tree changed, qualified by class
   (`Outer.method`). A change inside a nested function counts for the function
   that encloses it.
2. If no function changed: classes whose own body changed, such as a new method
   or a changed attribute.
3. If no class changed either: the changed files.

Comment and formatting edits never enter the target. Nor do definitions the
repair adds; a new function counts only through the existing code that calls it.
The prompt gets the target through `${test_target}`, for example:

```text
- `src/_pytest/logging.py`: `LogCaptureFixture.clear`
- `sklearn/pipeline.py`: class `Pipeline`
```

Patch contents, changed lines, reference tests, and expected behavior stay
private.

`repository` gives the agent no location and skips the `localize` stage. It
suits small projects, where tests for the whole repository make sense.

The scope and the prompt must agree. A `localized` run's prompt must contain
`${test_target}`, and any other run's prompt must not. A mismatch stops the run
before it creates a run directory.

### `task.existing_tests`

- **Type:** `keep` or `hide_all`
- **Default:** `hide_all`

Whether the repository's existing tests stay visible to the agent and present
in the evaluation workspace. Evaluation runs only the generated tests either way.

`hide_all` removes the test files and test directories that the SWE-bench
adapter lists for the repository. Inside those directories it removes only the
modules pytest would collect. Package initializers, fixtures, runners, helpers,
and data stay, because some projects import them at runtime.

The private reference check always keeps every existing test. With either
setting, the agent gets a fresh Git repository with one commit. Earlier commits
therefore cannot reveal removed tests or other project history.

### `task.generated_dir`

- **Type:** repository-relative path
- **Default:** `oracle_tests`

The only directory where the agent may add tests and fixtures. It cannot be
absolute, contain `..`, name `.git`, or overlap the repository's production
source roots.

## `limits`

The whole section is optional.

| Setting | Default | Meaning |
|---|---:|---|
| `evaluation_seconds` | `600` | Limit for each buggy or golden pytest execution |
| `setup_seconds` | `1800` | Limit for each image/setup operation |
| `memory` | `4g` | Docker memory limit |
| `cpus` | `2` | Docker CPU allocation |

Numeric limits must be positive. Docker validates the memory string when the
container starts.

## `output`

- **Type:** host directory path
- **Default:** `runs`

Parent directory for timestamped run directories.

## Resolved-only sections

`config.resolved.yaml` adds `runtime` and `toolchain` sections. Oracle Bench
fills them in from the SWE-bench instance and its own pinned versions. An input
file that sets either is rejected.

- `runtime`: the source image, platform, workdir, Python, optional source import
  path, rebuild command, source roots, import probes, required pytest plugins,
  the repository's existing-test globs, and any test-tool versions that image
  needs.
- `toolchain`: the Node image, `harnesses` (the pinned Codex, Claude Code, and
  OpenCode versions), and the pytest version.

A run cannot pin its own CLI versions. They are read from
`src/oracle_bench/container/docker/package.json`, so upgrading a CLI means
changing that file and its lockfile. The SWE-bench adapter overrides pytest when
the project's Python cannot run the default.

The resolved file also spells out every default. `oracle-bench evaluate` reads
it. Edit the small input file for new runs. Treat the resolved file as a record
of the run, not as input.

## Standalone task classification

Task classification has its own YAML format, because it labels a problem once
rather than running an agent on it. Run it with:

```sh
uv run oracle-bench classify configs/classification/example.yaml
```

Its top-level fields:

- `source`: as above.
- `classifier`: the same harness, provider, `auth`, model, and limit settings
  as `judge`. `multi_agent` is always `false`.
- `rubric`: the path to the task-classification rubric.
- `limits`: setup time and container memory and CPU. `evaluation_seconds` is
  unused.
- `output`: defaults to `classifications`.

[`configs/classification/example.yaml`](../configs/classification/example.yaml)
is a complete example. Paths are relative to the YAML file. As for runs, Oracle
Bench generates `runtime` and `toolchain` and saves them with the attempt; they
are not accepted as input.

## Sample configurations

- [`configs/smoke.yaml`](../configs/smoke.yaml): Claude Code generation and
  judging with Haiku through OpenRouter. Both stages have five-minute limits.
- [`configs/batches/smoke-validation.yaml`](../configs/batches/smoke-validation.yaml):
  one localized Requests task each for Claude 3 Haiku/Claude Code, GPT-5
  Mini/Codex, and Gemini 2.5 Flash Lite/OpenCode, all routed through OpenRouter.
