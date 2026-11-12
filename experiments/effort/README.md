# Effort experiment

Does maximum reasoning effort change how well an agent tests for a hidden bug?
This reruns 12 of the study's problems with each harness at maximum effort and
compares the results with the study's runs at each harness's default effort.

## Baseline

The study batch `20261109T181437Z-c6fd1a2e` (`configs/study/`): 50 problems,
each run once with Claude Code, Codex, and OpenCode at the harness's default
effort. Those defaults are in
[docs/configuration.md](../../docs/configuration.md#default-reasoning-effort):
Claude Code sends effort `high`; Codex and OpenCode send none, so the model
provider's default applies.

## Problems

[`draw.py`](draw.py) chose the problems; [`selection.json`](selection.json) is
its output, with the seed, the groups, and every study problem's baseline
labels. Each problem's group comes from its three baseline judgments:

| Group | Rule, checked in this order | Eligible | Drawn |
|---|---|---|---|
| excluded | any run attempts the issue with a correct assertion | 12 | — |
| A | any run attempts the issue with a wrong assertion | 12 | cfn-lint-3900, requests-2931, pystac-1337 |
| B | any run attempts the issue with another flaw | 4 | modin-6769, babel-1196, sphinx-8269 |
| C | any run does not attempt it for a reason other than a missed trigger | 3 | dulwich-1160, matplotlib-24627, pytest-7571 |
| D | every run missed the trigger | 19 | mpmath-926, markdown-1426, sphinx-11510 |

Three problems were drawn at random from each group, seed
`1359832674075934700` from the operating system. Group C has exactly three.
Every drawn problem runs with all three harnesses: 36 runs.

## What changed from the baseline

Each config in [`configs/effort/`](../../configs/effort/) is the problem's
`configs/study/` config with only these changes:

| Setting | Baseline | Effort run |
|---|---|---|
| `agent.effort` | unset (harness default) | `max` |
| `agent.limit` | 900 s | 1800 s |
| `judge.limit` | 600 s | 900 s |

Everything else is the same: models, pinned CLI versions, prompt
(`localized-tests.md`), localized scope, hidden existing tests, the DeepSeek
host pin, runtime images, and the judge's model and rubric.

How `max` reaches each model, checked from the request bodies and with real
calls on 2026-11-12:

| Harness | Model | Sent as |
|---|---|---|
| Claude Code 2.1.280 | `anthropic/claude-opus-5.5` | `--effort max` → `output_config.effort: "max"` |
| Codex 0.153.4 | `openai/gpt-6-sol` | `model_reasoning_effort="max"` → `reasoning.effort: "max"` |
| OpenCode 1.18.32 | `deepseek/deepseek-v4-pro-0813` | model options → `reasoning.effort: "max"` on every request |

## Caveats

- **Selected on failure.** Every problem was chosen because no baseline run
  asserted the correct behavior. Each baseline is one run, so some of those
  failures are chance, and a rerun at the default effort would succeed on some
  of them. Part of any improvement is therefore run-to-run variation, not
  effort. No default-effort rerun was made to measure it.
- **Two agent settings changed.** The agent time limit doubled along with the
  effort, so a difference may come from either.
- **DeepSeek's `max` is a smaller step.** On 2026-11-12, six calls per setting
  on each of three harder questions, through DeepSeek's own endpoint, gave these
  median reasoning tokens:

  | Question | No effort sent (baseline) | `high` | `max` |
  |---|---|---|---|
  | Code bug | 2,375 | 2,391 | 4,492 |
  | Logic puzzle | 1,598 | 1,402 | 4,661 |
  | Combinatorics | 9,832 | 13,062 | 12,830 |

  DeepSeek's default behaves like `high`, and `max` thinks more on two of the
  three questions. The combinatorics question varied too much between calls at
  the same setting to show a difference. A trivial question used about 240
  tokens at every setting. Claude and GPT-6 Sol showed a clearer step to `max`.
- **OpenCode side requests.** At default effort they sent `effort: low`; here
  they send `max`. They name the session and do not write tests.
- **Judge limit.** The judge's limit only bounds how long the judge may run, not
  the agent. 25 baseline runs were also rejudged at 900 s.

## Running and comparing

```sh
uv run oracle-lab batch configs/effort/batch.yaml
```

Compare each effort run with the baseline run for the same problem and harness,
listed under `problems.<instance>.baseline` in `selection.json`.
