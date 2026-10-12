# Localization prototype

The prototype and validation for how Oracle Bench chooses a localized run's test
target. The method now ships as `src/oracle_bench/localization/`, which is the
source of truth. This directory keeps the evidence: the evaluation over SWE-bench
Verified, the independent cross-check, the stage check, and their results.
`localize.py` and `patches.py` here are the prototype copies the results were
produced with; `results/stage_check.json` confirms the shipped stage gives the
same targets.

The target is the existing functions whose syntax tree differs between the buggy
file and the repaired file. When no existing function changes, it falls back to
the classes whose own body changed, then to the changed files.

## Files

- `localize.py`: the method.
- `patches.py`: reads a unified diff and applies it to the buggy file.
- `crosscheck.py`: an independent check that maps diff line numbers onto
  function spans. It shares only the patch parser with `localize.py`.
- `evaluate.py`: runs the method, the cross-check, and Oracle Bench's old
  heuristic over SWE-bench Verified.
- `old_heuristic.py`: the old diff-text heuristic, frozen from before its removal.
- `check_stage.py`: runs Oracle Bench's real `localize` stage against freshly
  built runtime images and compares it with the prototype. Needs Docker.
- `test_localize.py`: the method's rules on small hand-written sources.
- `results/`: per-instance results, the summary, and manual verdicts.

## Run

`evaluate.py` downloads each changed buggy file from GitHub at its `base_commit`
into `.cache/`, which is ignored by Git. It needs Python 3.14 or earlier, because
the dataset library does not install on 3.15.

```sh
cd experiments/localization
uv run --no-project --python 3.14 --with pytest python -m pytest -q test_localize.py
uv run --no-project --python 3.14 --with datasets --with ../.. python evaluate.py
```
