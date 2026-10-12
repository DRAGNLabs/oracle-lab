# Five-repository validation shortlist

The selected five SWE-bench Verified instances form the 15-job batch in
`batch.yaml`: Claude, Codex, and OpenCode on each instance, using the same models
and judge as `configs/initial-validation/`.

| Repository | Selected instance | Behavior type |
|---|---|---|
| Matplotlib | `matplotlib__matplotlib-24627` | Object state and resource ownership |
| pytest | `pytest-dev__pytest-10051` | Stateful logging API and operation order |
| xarray | `pydata__xarray-6744` | Numeric window alignment |
| Sphinx | `sphinx-doc__sphinx-11510` | Document inclusion and extension event |
| scikit-learn | `scikit-learn__scikit-learn-14710` | Classifier fitting with string labels |

This shortlist favors behavioral bugs with a code-visible relationship that a
test-generation agent could assert: a method preserving its input, two equivalent
paths agreeing, a result satisfying its own defining operation, or related
repository behavior being consistent. No candidate relies solely on knowing an
external specification's exact output. The “oracle clue” column is a preliminary
assessment from each issue, repair, and reference test. It has **not** been
verified from the buggy checkout alone; the standalone classification step should
check that before committing to the full batch.

All candidates are in Oracle Bench's pinned Verified dataset, have a supported
repository layout and Python localization target, and have pytest-style
`FAIL_TO_PASS` reference IDs. Django and SymPy are omitted because their
reference IDs require repository-specific test runners; Oracle Bench supports
only repositories whose tests run under pytest. The dataset's “fix time” is a
repair-difficulty estimate, not a test-writing or runtime estimate. One candidate
per repository was run in batch `20260923T220231Z-087f21ff`:
`matplotlib__matplotlib-24627`, `pytest-dev__pytest-10051`, `pydata__xarray-6744`,
`sphinx-doc__sphinx-11510`, and `scikit-learn__scikit-learn-14710`. The other ten
have not been run.

## Matplotlib

| Instance | Behavior to test | Likely code-only oracle clue | Setup | Fix time |
|---|---|---|---|---|
| `matplotlib__matplotlib-23299` | Asking for the backend should not make previously created figures disappear. | A read-only backend query should preserve figure registry state. | Figure created inside `rc_context`; backend query. | 15 min–1 hour |
| `matplotlib__matplotlib-24026` | An explicit-color `stackplot()` should not change the Axes color cycle for later plots. | Compare the Axes color cycle before and after an operation with explicit colors. | Multiple plot calls on one Axes. | 15 min–1 hour |
| `matplotlib__matplotlib-24627` | Clearing an Axes should detach its former artists from the Axes and figure. | Clearing and explicitly removing an artist should agree on parent references. | Create artists, clear Axes, inspect former artists. | 15 min–1 hour |

## pytest

| Instance | Behavior to test | Likely code-only oracle clue | Setup | Fix time |
|---|---|---|---|---|
| `pytest-dev__pytest-6197` | Running pytest should not import an unrelated package's `__init__.py` during collection. | A directory with no tests should not affect collection. | Temporary test tree and nested pytest process. | 1–4 hours |
| `pytest-dev__pytest-10051` | `caplog.get_records("call")` should reflect `caplog.clear()` and later log events. | Two public views of the same captured records should agree. | Ordered log, clear, log sequence. | 15 min–1 hour |
| `pytest-dev__pytest-7490` | Adding an xfail marker during a test should handle a failure like an equivalent preexisting xfail marker. | Compare dynamic and static marker paths. | Nested pytest run with a dynamically marked test. | 15 min–1 hour |

## xarray

| Instance | Behavior to test | Likely code-only oracle clue | Setup | Fix time |
|---|---|---|---|---|
| `pydata__xarray-3095` | Copying a dataset with Unicode indices should preserve their dtype. | A copy should retain values and dtypes. | Dataset with Unicode coordinate index. | 15 min–1 hour |
| `pydata__xarray-6744` | Iterating a centered rolling window should use the same window alignment as the aggregate operation. | Compare two paths through the same rolling object. | DataArray, centered rolling window, manual iteration. | 15 min–1 hour |
| `pydata__xarray-6938` | `swap_dims()` should not mutate dimensions on its source dataset. | A derived dataset operation should preserve the original object's state. | Dataset with a data variable promoted to a dimension. | 15 min–1 hour |

## Sphinx

| Instance | Behavior to test | Likely code-only oracle clue | Setup | Fix time |
|---|---|---|---|---|
| `sphinx-doc__sphinx-11510` | A `source-read` extension should transform included source as it does top-level source. | Compare the same source content included versus read directly. | Small Sphinx project, include file, event handler. | 1–4 hours |
| `sphinx-doc__sphinx-11445` | Adding `rst_prolog` should not remove a top-level heading containing a domain role. | Compare the same document with and without a prolog. | Small Sphinx build with a role in the heading. | 15 min–1 hour |
| `sphinx-doc__sphinx-9461` | Autodoc should include a documented `@classmethod`/`@property` combination. | Compare autodoc output for closely related documented members. | Small importable class and autodoc build; Python-version sensitivity. | 1–4 hours |

## scikit-learn

| Instance | Behavior to test | Likely code-only oracle clue | Setup | Fix time |
|---|---|---|---|---|
| `scikit-learn__scikit-learn-14053` | Exporting a decision tree with one feature should not raise `IndexError`. | A one-feature tree should work like other valid feature counts. | Fit small decision tree, export as text. | 15 min–1 hour |
| `scikit-learn__scikit-learn-14710` | A classifier with string targets should still fit when early stopping is enabled. | Label handling should agree with the same estimator without early stopping. | Small string-labeled dataset and early stopping. | 15 min–1 hour |
| `scikit-learn__scikit-learn-26323` | `ColumnTransformer.set_output()` should apply to the `remainder` estimator too. | Other component transformers already receive the same output setting. | Column transformer with an estimator as `remainder`. | 15 min–1 hour |

The configs are ready for a batch run. The batch's private reference check will
stop a job before any model call if its reference tests cannot distinguish buggy
and golden revisions. Standalone classification is still optional and has not
been run for these five instances.
