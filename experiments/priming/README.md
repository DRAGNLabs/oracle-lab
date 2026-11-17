# Priming experiment

Does an agent test for a bug better when told one may exist, or told what it is?
The default prompt asks for unit tests and never mentions bugs, so a test that
matches the code's current behavior is following instructions. This reruns 12
of the study's problems with two primed prompts and compares the results with
the study's runs at the default prompt.

## Baseline

The study batch `20261109T181437Z-c6fd1a2e` (`configs/study/`), with the prompt
[`localized-tests.md`](../../prompts/agent/localized-tests.md).

## Problems

The effort experiment's 12 problems, drawn in
[`experiments/effort/selection.json`](../effort/selection.json): three from each
baseline group (A: a wrong assertion, B: another flawed attempt, C: no attempt
for another reason, D: every run missed the trigger). None of them had a
baseline run with a correct assertion. Each problem runs with all three
harnesses: 36 runs per condition.

## Conditions

| Condition | Prompt | Batch |
|---|---|---|
| Baseline | `localized-tests.md` | study batch |
| Bug prior | [`localized-tests-bug-prior.md`](../../prompts/agent/localized-tests-bug-prior.md) | [`configs/priming-bug-prior/`](../../configs/priming-bug-prior/) |
| Issue | [`localized-tests-issue.md`](../../prompts/agent/localized-tests-issue.md) | [`configs/priming-issue/`](../../configs/priming-issue/) |

Each condition adds to the previous one, so each comparison changes one thing:

- **Bug prior** is the baseline prompt plus, after the target:

  > This code may contain bugs. Tests should check the code's intended behavior,
  > even where the current code does not follow it.

- **Issue** is the bug-prior prompt plus the task's issue text from the dataset,
  inside `<issue>` tags. It is an upper reference: the agent knows what to look
  for, though not the fix. Some issues describe the expected behavior or suggest
  a fix themselves.

Each config is the problem's `configs/study/` config with only the prompt and
the judge's limit changed (600 to 900 seconds, which only bounds the judge).
Models, CLI versions, default effort, the 900-second agent limit, localized
scope, hidden existing tests, the DeepSeek host pin, runtime images, and the
judge's model and rubric are the same.

## Caveats

- **Selected on failure.** Every problem was chosen because no baseline run
  asserted the correct behavior, and each baseline is one run. Part of any
  improvement is run-to-run variation, not the prompt.
- **The judge is not told the condition.** It judges the tests against the
  issue and the fix, as for any run.
- **Issue text varies.** Lengths range from 73 characters (modin-6769) to over
  5,000 (sphinx-11510).

## Running and comparing

```sh
uv run oracle-lab batch configs/priming-bug-prior/batch.yaml
uv run oracle-lab batch configs/priming-issue/batch.yaml
```

Compare each run with the baseline run for the same problem and harness, listed
under `problems.<instance>.baseline` in `experiments/effort/selection.json`.
