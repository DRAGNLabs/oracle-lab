# Localization prototype

A standalone prototype of a new way to choose the localized test target. It is
not used by Oracle Bench yet.

The target is the existing functions whose syntax tree differs between the buggy
file and the repaired file. When no existing function changes, it falls back to
the classes whose own body changed, then to the changed files.

## Files

- `localize.py`: the method.
- `patches.py`: reads a unified diff and applies it to the buggy file.
- `crosscheck.py`: an independent check that maps diff line numbers onto
  function spans. It shares only the patch parser with `localize.py`.
- `evaluate.py`: runs the method, the cross-check, and Oracle Bench's current
  heuristic over SWE-bench Verified.
- `coverage_check.py`: reads saved Oracle Bench reference-check coverage and
  checks that the failing reference tests run the target on the buggy version.
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
uv run --no-project --python 3.14 --with datasets --with ../.. python coverage_check.py ../../runs/<run-id> ...
```
