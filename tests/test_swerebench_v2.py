import json
from pathlib import Path

import pytest
import yaml
from fixtures import swerebench_v2_record

from oracle_lab.config import load_config
from oracle_lab.instance import resolve_source, swerebench_v2
from oracle_lab.instance.swerebench_v2 import runtime_for

RECORD = swerebench_v2_record()


def load(tmp_path, record=RECORD, instance=RECORD["instance_id"]):
    (tmp_path / "record.json").write_text(json.dumps(record))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "source": {"kind": "swerebench_v2", "instance": instance, "record": "record.json"},
                "agent": {"model": "test-model"},
                "task": {"prompt": str(Path(__file__).parents[1] / "prompts/agent/unit-tests.md")},
                "output": "output",
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


def test_v2_record_with_a_truncated_test_id_is_refused(tmp_path):
    record = swerebench_v2_record(
        FAIL_TO_PASS=["tests/test_a.py::test_ok", "tests/test_a.py::test_length[a-longer"]
    )
    with pytest.raises(ValueError, match=r"truncated test IDs: \['tests/test_a.py::test_length"):
        resolve_source(load(tmp_path, record=record))


@pytest.mark.parametrize(
    "categories",
    [["core_feat"], ["minor_bug", "core_feat"], ["major_bug", "documentation_enh"], []],
)
def test_v2_record_not_labeled_as_only_a_bug_fix_is_refused(tmp_path, categories):
    record = swerebench_v2_record(meta={"llm_metadata": {"pr_categories": categories}})
    with pytest.raises(ValueError, match="not labeled as a bug fix only"):
        resolve_source(load(tmp_path, record=record))


def test_v2_record_is_read_from_the_pinned_dataset_file(tmp_path, monkeypatch):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    huggingface_hub = pytest.importorskip("huggingface_hub")
    other = swerebench_v2_record(instance_id="wtforms__wtforms-615")
    data = tmp_path / "train.parquet"
    pq.write_table(pa.Table.from_pylist([other, RECORD]), data)
    downloads = []

    def download(repo, filename, *, repo_type, revision):
        downloads.append((repo, filename, repo_type, revision))
        return str(data)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", download)
    source = load(tmp_path)
    source.record = None

    assert swerebench_v2.load_record(source) == RECORD
    assert downloads == [
        ("nebius/SWE-rebench-V2", "data/train-00000-of-00001.parquet", "dataset", source.revision)
    ]
