import json

from fixtures import TRACE_TEST_ID, traced_run

from oracle_lab.io import read_json
from oracle_lab.trace.build import build_run_trace
from oracle_lab.trace.markdown import trace_markdown


def test_build_run_trace_annotates_a_codex_run(tmp_path):
    trace = build_run_trace(traced_run(tmp_path / "run"))

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
    assert history.versions[0].outcomes == {TRACE_TEST_ID: "pass_on_buggy_fail_on_golden"}
    assert trace.reasoning == "summary" and trace.outcomes_available
    data = json.loads(json.dumps(trace.to_json()))
    assert "change" not in data["events"][4]


def test_the_judge_reads_the_same_steps_a_rater_sees(tmp_path):
    paths = traced_run(tmp_path / "run")

    text = trace_markdown(read_json(paths.judge.trace))

    assert "- 1 · Read · Read pkg/mod.py L1–40 [patched lines]" in text
    assert "- 1.1 · Run other · ls" in text
    assert "- 2 · Probe code · " in text and "[mentions: parse]" in text
    assert "### oracle_tests/test_mod.py" in text
    assert "1 version; the last matches the submitted file." in text
    assert "  - pytest at step 4: 1 passed" in text
