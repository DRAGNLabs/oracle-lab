import json

import pytest

pytest.importorskip("tree_sitter_bash")

from oracle_lab.paths import RunPaths  # noqa: E402
from oracle_lab.trace.build import build_run_trace  # noqa: E402
from oracle_lab.trace.server import TraceServer  # noqa: E402

PATCH = """\
diff --git a/pkg/mod.py b/pkg/mod.py
--- a/pkg/mod.py
+++ b/pkg/mod.py
@@ -10,3 +10,3 @@ def parse(text):
     value = text.strip()
-    return value.lower()
+    return value
"""
TEST = "from pkg.mod import parse\n\n\ndef test_parse():\n    assert parse(' A ') == 'a'\n"


def make_run(root):
    """A small Codex run in the shape the demo runs were saved in."""
    paths = RunPaths.create(root)
    paths.config.write_text(
        "source: {instance: org__pkg-1}\n"
        "agent: {harness: codex, model: openai/model}\n"
        "task: {generated_dir: oracle_tests}\n"
        "runtime: {image: example}\n"
    )
    (paths.ground_truth / "fix.patch").write_text(PATCH)
    files = paths.submission / "files" / "oracle_tests"
    files.mkdir(parents=True)
    (files / "test_mod.py").write_text(TEST)
    write = "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: oracle_tests/test_mod.py\n"
    write += "".join(f"+{line}\n" for line in TEST.splitlines()) + "*** End Patch\nPATCH"
    items = [
        {"type": "reasoning", "text": "**Reading**"},
        {
            "type": "command_execution",
            "command": "/bin/sh -lc \"sed -n '1,40p' pkg/mod.py; ls\"",
            "aggregated_output": "    return value.lower()\n",
            "exit_code": 0,
        },
        {
            "type": "command_execution",
            "command": "python -c 'from pkg.mod import parse; print(parse(\" A \"))'",
            "aggregated_output": "a\n",
            "exit_code": 0,
        },
        {"type": "command_execution", "command": write, "aggregated_output": "", "exit_code": 0},
        {
            "type": "command_execution",
            "command": "python -m pytest -q oracle_tests",
            "aggregated_output": "1 passed in 0.01s\n",
            "exit_code": 0,
        },
    ]
    (paths.generation / "trace.jsonl").write_text(
        "\n".join(
            json.dumps({"type": "item.completed", "item": {"id": str(index), **item}})
            for index, item in enumerate(items)
        )
    )
    paths.results.write_text(
        json.dumps(
            {
                "tests": [
                    {
                        "test_id": "oracle_tests/test_mod.py::test_parse",
                        "buggy": "pass",
                        "golden": "fail",
                        "cell": "pass_on_buggy_fail_on_golden",
                    }
                ]
            }
        )
    )
    return paths


def test_build_run_trace_annotates_a_codex_run(tmp_path):
    trace = build_run_trace(make_run(tmp_path / "run"))

    assert [(event.step, event.action.value) for event in trace.events] == [
        (0, "reasoning"),
        (1, "read"),
        (1, "run_other"),
        (2, "probe_code"),
        (3, "edit_test"),
        (4, "run_tests"),
    ]
    read, probe, edit = trace.events[1], trace.events[3], trace.events[4]
    assert read.description == "Read pkg/mod.py L1–40"
    assert read.touches[0].overlap.value == "lines"
    assert probe.patched_names == ["parse"]
    assert edit.patched_names == ["parse"]
    assert trace.defect.first_touch == 1
    assert (trace.defect.lines_shown, trace.defect.lines_total) == (1, 1)
    [history] = trace.generated_files
    assert history.matches_submission is True
    assert history.versions[0].outcomes == {
        "oracle_tests/test_mod.py::test_parse": "pass_on_buggy_fail_on_golden"
    }
    assert trace.reasoning == "summary" and trace.outcomes_available
    data = json.loads(json.dumps(trace.to_json()))
    assert "change" not in data["events"][4]


def test_server_lists_runs_and_builds_each_once(tmp_path):
    paths = make_run(tmp_path / "run")
    server = TraceServer([paths])
    try:
        assert server.listing() == [
            {"run_id": "run", "instance": "org__pkg-1", "harness": "codex", "model": "openai/model"}
        ]
        first = server.run_trace("run")
        assert server.run_trace("run") is first
        with pytest.raises(LookupError):
            server.run_trace("missing")
    finally:
        server.server_close()
