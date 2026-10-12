"""Execute the actual container runner in separate local Python processes."""

import json
import os
import subprocess
import sys

import pytest
from fixtures import HELPERS

from oracle_bench.evaluation.outcomes import incomplete_result, pair_results
from oracle_bench.io import read_json
from oracle_bench.results import CoverageResult, MatrixCellName, VersionResult, read_version_result


def execute(tmp_path, source, tests, *, modules=None, extra=None, source_roots=None):
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    (root / "subject.py").write_text(source)
    (root / "oracle_tests").mkdir()
    (root / "oracle_tests" / "test_subject.py").write_text(tests)
    for name, content in (extra or {}).items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    output = tmp_path / "results"
    config = {
        "workdir": str(root),
        "output": str(output),
        "targets": ["oracle_tests"],
        "source_roots": source_roots or ["subject.py"],
        "import_modules": modules or [],
    }
    settings = tmp_path / "runner.json"
    settings.write_text(json.dumps(config))
    proc = subprocess.run(
        [sys.executable, str(HELPERS / "pytest_runner.py"), str(settings)],
        capture_output=True,
        text=True,
        timeout=30,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
    )
    assert (output / "tests.json").exists(), proc.stderr
    # Validating here also asserts that real runner output matches the host contract.
    coverage = CoverageResult.model_validate(read_json(output / "coverage.json"))
    return read_version_result(output / "tests.json"), coverage, output


def test_four_cells_come_from_real_test_executions(tmp_path):
    tests = """import subject
def test_both_pass(): assert subject.answer() in (1, 2)
def test_finds_bug(): assert subject.answer() == 2
def test_buggy_oracle(): assert subject.answer() == 1
def test_both_fail(): assert subject.answer() == 3
"""
    buggy, bcov, _ = execute(tmp_path / "buggy", "def answer(): return 1\n", tests)
    golden, gcov, _ = execute(tmp_path / "golden", "def answer(): return 2\n", tests)
    paired = pair_results(buggy, golden)
    assert [cell.count for cell in paired.matrix.values()] == [1, 1, 1, 1]
    assert paired.has_fail_on_buggy_pass_on_golden
    assert "has_fail_on_buggy_pass_on_golden" not in paired.model_dump()
    for coverage in [bcov, gcov]:
        assert coverage.status == "available"
        assert coverage.covered_lines == coverage.executable_lines == 1
    finds_bug = buggy.tests["oracle_tests/test_subject.py::test_finds_bug"]
    assert finds_bug.phases[1]["phase"] == "call"
    assert finds_bug.failure["kind"] == "assertion"
    assert finds_bug.failure["exception_type"] == "builtins.AssertionError"
    assert paired.failure_kinds == {
        "buggy": {"assertion": 2, "exception": 0, "unknown": 0},
        "golden": {"assertion": 2, "exception": 0, "unknown": 0},
    }


def test_coverage_omits_unparseable_files_without_discarding_valid_results(tmp_path):
    result, coverage, output = execute(
        tmp_path,
        "x = 1\n",
        "import package\ndef test_value(): assert package.VALUE == 1\n",
        modules=["package"],
        source_roots=["package"],
        extra={
            "package/__init__.py": "VALUE = 1\n",
            "package/platform_only.py": r'BROKEN = "\u"' + "\n",
        },
    )

    assert result.status == "completed"
    assert coverage.status == "available"
    assert coverage.covered_lines == coverage.executable_lines == 1
    assert any(name.endswith("package/__init__.py") for name in coverage.files)
    assert not any(name.endswith("platform_only.py") for name in coverage.files)
    assert (output / "coverage.raw.json").is_file()


def test_call_exceptions_are_distinguished_from_assertion_failures(tmp_path):
    tests = """def test_assertion(): assert False, 'wrong value'
def test_exception(): raise ValueError('changed ground')
"""
    result, _, _ = execute(tmp_path, "x = 1\n", tests)

    assertion = result.tests["oracle_tests/test_subject.py::test_assertion"].failure
    exception = result.tests["oracle_tests/test_subject.py::test_exception"].failure
    assert assertion == {
        "kind": "assertion",
        "exception_type": "builtins.AssertionError",
        "message": "wrong value\nassert False",
    }
    assert exception == {
        "kind": "exception",
        "exception_type": "builtins.ValueError",
        "message": "changed ground",
    }


@pytest.mark.parametrize(
    "test_source,expected",
    [
        ("", "no_tests"),
        ("def test_invalid(:\n", "collection_error"),
        ("import nonexistent_oracle_test_dependency\n", "collection_error"),
    ],
)
def test_empty_and_collection_failures_are_not_binary(tmp_path, test_source, expected):
    result, _, _ = execute(tmp_path, "x = 1\n", test_source)
    assert result.status == expected
    paired = pair_results(result, result)
    assert all(cell.count == 0 for cell in paired.matrix.values())


def test_a_file_that_fails_to_collect_does_not_void_the_other_files(tmp_path):
    broken = {"oracle_tests/test_broken.py": "import nonexistent_oracle_test_dependency\n"}
    tests = "import subject\ndef test_finds_bug(): assert subject.answer() == 2\n"
    buggy, _, _ = execute(tmp_path / "buggy", "def answer(): return 1\n", tests, extra=broken)
    golden, _, _ = execute(tmp_path / "golden", "def answer(): return 2\n", tests, extra=broken)

    assert buggy.status == golden.status == "completed"
    assert [error["nodeid"] for error in buggy.collection_errors] == ["oracle_tests/test_broken.py"]
    paired = pair_results(buggy, golden)
    assert paired.matrix[MatrixCellName.FAIL_ON_BUGGY_PASS_ON_GOLDEN].test_ids == [
        "oracle_tests/test_subject.py::test_finds_bug"
    ]


def test_skip_xfail_xpass_and_fixture_errors(tmp_path):
    tests = """import pytest
@pytest.mark.skip(reason='unavailable')
def test_skip(): pass
@pytest.mark.xfail(reason='known')
def test_xfail(): assert False
@pytest.mark.xfail(reason='known')
def test_xpass(): pass
@pytest.mark.xfail(reason='known', strict=True)
def test_strict_xpass(): pass
@pytest.fixture
def broken(): raise RuntimeError('setup broke')
def test_setup(broken): pass
@pytest.fixture
def broken_teardown():
    yield
    raise RuntimeError('teardown broke')
def test_teardown(broken_teardown): pass
"""
    result, _, _ = execute(tmp_path, "x = 1\n", tests)
    outcomes = {key.split("::")[-1]: case.outcome for key, case in result.tests.items()}
    assert outcomes == {
        "test_skip": "skip",
        "test_xfail": "xfail",
        "test_xpass": "xpass",
        "test_strict_xpass": "xpass",
        "test_setup": "setup_error",
        "test_teardown": "teardown_error",
    }
    assert len(pair_results(result, result).other_outcomes) == 6


def test_existing_tests_and_default_addopts_are_not_executed(tmp_path):
    result, _, _ = execute(
        tmp_path,
        "x = 1\n",
        "def test_generated(): pass\n",
        extra={
            "test_existing.py": "raise RuntimeError('must not be collected')\n",
            "pytest.ini": "[pytest]\naddopts = --deliberately-unknown-option\ntestpaths = .\n",
        },
    )
    assert result.status == "completed"
    assert len(result.tests) == 1


def test_wrong_import_path_is_reported(tmp_path):
    result, _, _ = execute(tmp_path, "x = 1\n", "def test_pass(): pass\n", modules=["json"])
    assert result.status == "runner_error"
    assert "outside checkout" in result.error


def test_incomplete_run_cannot_earn_bug_detection(tmp_path):
    buggy, _, directory = execute(tmp_path, "x = 1\n", "def test_fail(): assert False\n")
    golden = VersionResult(
        status=buggy.status, tests={key: {"outcome": "pass"} for key in buggy.tests}
    )
    incomplete = incomplete_result(directory, "timeout", "Time limit")
    assert not pair_results(incomplete, golden).has_fail_on_buggy_pass_on_golden


def test_unmatched_test_ids_stay_outside_matrix():
    buggy = VersionResult(status="completed", tests={"test_a": {"outcome": "pass"}})
    golden = VersionResult(status="completed", tests={"test_b": {"outcome": "fail"}})
    result = pair_results(buggy, golden)
    assert len(result.other_outcomes) == 2
    assert all(cell.count == 0 for cell in result.matrix.values())
