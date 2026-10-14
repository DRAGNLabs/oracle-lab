import json
from pathlib import Path

import pytest
import yaml

from oracle_bench.config import load_config
from oracle_bench.instance import resolve_source, swerebench_v2
from oracle_bench.instance.swerebench_v2 import runtime_for

RECORD = {
    "instance_id": "wtforms__wtforms-614",
    "repo": "wtforms/wtforms",
    "base_commit": "a" * 40,
    "language": "python",
    "image_name": "docker.io/swerebenchv2/wtforms-wtforms:614-aaaaaaa",
    "patch": "diff --git a/src/wtforms/validators.py b/src/wtforms/validators.py\n",
    "test_patch": "diff --git a/tests/test_fields.py b/tests/test_fields.py\n",
    "FAIL_TO_PASS": ["tests/test_fields.py::TestFlags::test_existing_values"],
    "PASS_TO_PASS": [],
}


def load(tmp_path, record=RECORD, instance=RECORD["instance_id"]):
    (tmp_path / "record.json").write_text(json.dumps(record))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "source": {"kind": "swerebench_v2", "instance": instance, "record": "record.json"},
                "agent": {"model": "test-model"},
                "task": {"prompt": str(Path(__file__).parents[1] / "prompts/agent/unit-tests.md")},
            }
        )
    )
    return load_config(config_path).source


def test_v2_record_resolves_instance_and_runtime(tmp_path):
    instance, runtime = resolve_source(load(tmp_path))

    assert instance.reference_test_ids == RECORD["FAIL_TO_PASS"]
    assert instance.source.kind == "swerebench_v2"
    assert instance.source.name == "nebius/SWE-rebench-V2"
    assert instance.source.split == "train"
    assert runtime.image == RECORD["image_name"]
    assert runtime.workdir == "/wtforms"
    assert runtime.python == "/usr/local/bin/python"
    assert runtime.source_roots == ["src/wtforms"]
    assert runtime.import_modules == ["wtforms"]
    assert runtime.existing_test_globs == ["tests"]
    assert runtime.rebuild == ":"


def test_v2_workdir_keeps_the_repository_name_case(monkeypatch):
    monkeypatch.setitem(
        swerebench_v2.REPOSITORY_LAYOUTS,
        "Backblaze/B2_Command_Line_Tool",
        swerebench_v2.RepositoryLayout("b2", "b2", ("test",)),
    )
    runtime = runtime_for({**RECORD, "repo": "Backblaze/B2_Command_Line_Tool"})
    assert runtime.workdir == "/B2_Command_Line_Tool"


def test_v2_record_for_another_instance_is_refused(tmp_path):
    with pytest.raises(ValueError, match="does not match configured instance ID"):
        resolve_source(load(tmp_path, instance="wtforms__wtforms-615"))


@pytest.mark.parametrize(
    "changes,message",
    [
        ({"repo": "example/project"}, "No tested SWE-rebench V2 runtime profile"),
        ({"language": "go"}, "not a Python task"),
        ({"image_name": ""}, "missing image_name"),
    ],
)
def test_v2_runtime_needs_a_known_python_repository_and_an_image(changes, message):
    with pytest.raises(ValueError, match=message):
        runtime_for({**RECORD, **changes})
