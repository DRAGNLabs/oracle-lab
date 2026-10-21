import pytest

pytest.importorskip("tree_sitter_bash")

from oracle_lab.harnesses.steps import EditFile, WriteFile  # noqa: E402
from oracle_lab.trace.events import (  # noqa: E402
    ActionType,
    DefectOverlap,
    Event,
    FileCategory,
    PytestSummary,
    Touch,
)
from oracle_lab.trace.facts import (  # noqa: E402
    PatchedRegions,
    Repository,
    generated_files,
    grep_hits,
    parse_pytest,
)
from oracle_lab.trace.shell import PatchFile  # noqa: E402

PATCH = """\
diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -10,6 +10,6 @@ class Parser:
     def parse(self, text):
         value = text.strip()
-        return value.lower()
+        return value

     def other(self):
         pass
"""


def test_repository_relative_paths_and_categories():
    repository = Repository("/testbed", "oracle_tests")

    assert repository.relative("/testbed/pkg/mod.py") == "pkg/mod.py"
    assert repository.relative("./pkg/mod.py") == "pkg/mod.py"
    assert repository.relative("/tmp/probe.py") is None
    assert repository.category("/testbed/oracle_tests/test_a.py") is FileCategory.GENERATED_TEST
    assert repository.category("tests/test_mod.py") is FileCategory.TEST
    assert repository.category("CHANGES.rst") is FileCategory.CHANGELOG
    assert repository.category("changelog/123.bugfix.rst") is FileCategory.CHANGELOG
    assert repository.category("docs/usage.rst") is FileCategory.DOCS
    assert repository.category("pyproject.toml") is FileCategory.CONFIG
    assert repository.category("pkg/mod.py") is FileCategory.SOURCE
    assert repository.category("/opt/conda/lib/site.py") is FileCategory.OUTSIDE


def test_patched_regions_overlap_and_shown_lines():
    regions = PatchedRegions.from_patch(PATCH, [])

    assert regions.overlap("pkg/mod.py", (1, 9)) is DefectOverlap.FILE
    assert regions.overlap("pkg/mod.py", (14, 30)) is DefectOverlap.LINES
    assert regions.overlap("pkg/mod.py", None) is DefectOverlap.LINES
    assert regions.overlap("pkg/other.py", None) is DefectOverlap.NONE
    assert regions.lines_total == 1
    assert regions.shown_lines("pkg/mod.py", (1, 20), "12  return value.lower()") == {12}
    # Lines in range whose text is missing from the output (truncated) do not count.
    assert regions.shown_lines("pkg/mod.py", (1, 20), "... output truncated") == set()


def test_patched_names_qualify_methods_by_their_class():
    regions = PatchedRegions.from_patch(PATCH, ["Parser.parse"])

    assert regions.names == ["Parser", "Parser.parse", "Parser.other"]
    assert regions.names_in("p = Parser(); p.parse('x')") == ["Parser", "Parser.parse"]
    assert regions.names_in("parse it") == []


def test_grep_hits_reads_each_output_format():
    assert grep_hits("pkg/mod.py:12:  x\npkg/mod.py-13-  y", None) == [
        ("pkg/mod.py", 12),
        ("pkg/mod.py", 13),
    ]
    assert grep_hits("12:  x\n13-  y", "pkg/mod.py") == [("pkg/mod.py", 12), ("pkg/mod.py", 13)]
    assert grep_hits("Found 1 matches\n/testbed/pkg/mod.py:\n  Line 12: x", None) == [
        ("/testbed/pkg/mod.py", 12)
    ]


def test_parse_pytest_counts_and_failures():
    output = "FAILED oracle_tests/test_a.py::test_x - assert 1 == 2\n1 failed, 3 passed in 0.12s\n"

    summary = parse_pytest(output)

    assert summary == PytestSummary({"failed": 1, "passed": 3}, ["oracle_tests/test_a.py::test_x"])
    assert parse_pytest("collected 0 items\n\n=== no tests ran in 0.01s ===") == PytestSummary(
        {}, []
    )
    assert parse_pytest("hello") is None


def edit(step, path, change):
    return Event(
        step=step,
        part=0,
        action=ActionType.EDIT_TEST,
        description="",
        trace_line=step,
        touches=[Touch(path, FileCategory.GENERATED_TEST, None, DefectOverlap.NONE)],
        change=change,
    )


def run(step, summary):
    return Event(
        step=step,
        part=0,
        action=ActionType.RUN_TESTS,
        description="",
        trace_line=step,
        pytest=summary,
    )


def test_generated_files_rebuild_versions_and_attach_runs_and_outcomes():
    path = "oracle_tests/test_a.py"
    first = "def test_x():\n    assert f() == 1\n"
    failing = PytestSummary({"failed": 1}, [f"{path}::test_x"])
    events = [
        edit(1, path, WriteFile(path, first)),
        run(2, failing),
        edit(3, path, EditFile(path, "== 1", "== 2")),
        edit(
            4,
            path,
            PatchFile(path, "@@\n def test_x():\n-    assert f() == 2\n+    assert f() == 3\n"),
        ),
        run(5, PytestSummary({"passed": 1}, [])),
    ]
    final = "def test_x():\n    assert f() == 3\n"

    [history] = generated_files(events, {path: final}, {f"{path}::test_x": "pass_on_both"})

    assert [version.step for version in history.versions] == [1, 3, 4]
    assert [version.content for version in history.versions][1:] == [
        "def test_x():\n    assert f() == 2\n",
        final,
    ]
    assert history.versions[0].runs_after == [(2, failing)]
    assert history.versions[1].runs_after == []
    assert history.versions[0].outcomes == {f"{path}::test_x": "pass_on_both"}
    assert "-    assert f() == 1" in history.versions[1].diff
    assert history.matches_submission is True


def test_an_edit_without_a_known_base_is_partial():
    path = "oracle_tests/test_a.py"
    events = [edit(1, path, EditFile(path, "a", "b")), edit(2, path, EditFile(path, None, None))]

    [history] = generated_files(events, {}, None)

    assert [version.partial for version in history.versions] == [True, True]
    assert history.matches_submission is None


def test_edit_facts_record_failures_before_an_edit_skip_marks_and_absent_tests():
    path = "oracle_tests/test_a.py"
    first = "def test_x():\n    assert f() == 1\n\n\ndef test_y():\n    assert g()\n"
    # pytest run from inside the generated directory prints ids without it
    failing = PytestSummary({"failed": 2}, ["test_a.py::test_x", "tests/other.py::test_z"])
    events = [
        edit(1, path, WriteFile(path, first)),
        run(2, failing),
        edit(3, path, EditFile(path, "def test_x():\n    assert f() == 1\n", "")),
        edit(4, path, EditFile(path, "def test_y", "@pytest.mark.xfail\ndef test_y")),
    ]
    final = "\n\n\n@pytest.mark.xfail\ndef test_y():\n    assert g()\n"

    [history] = generated_files(events, {path: final}, None)

    first_version, after_failure, marked = history.versions
    assert not first_version.follows_failed_run
    assert after_failure.follows_failed_run
    assert after_failure.follows_failures == ["test_a.py::test_x"]
    assert not marked.follows_failed_run  # no run between the two edits
    assert marked.skip_marks_added == ["@pytest.mark.xfail"]
    assert history.failed_then_absent == ["test_a.py::test_x"]


def test_a_run_chained_after_an_edit_in_the_same_step_follows_it():
    path = "oracle_tests/test_a.py"
    chained_run = run(1, PytestSummary({"failed": 1}, [f"{path}::test_x"]))
    chained_run.part = 1
    events = [
        edit(1, path, WriteFile(path, "def test_x():\n    assert 0\n")),
        chained_run,
        edit(2, path, EditFile(path, "assert 0", "assert 1")),
    ]

    [history] = generated_files(events, {}, None)

    assert history.versions[0].runs_after == [(1, chained_run.pytest)]
    assert history.versions[1].follows_failures == [f"{path}::test_x"]
