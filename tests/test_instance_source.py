import json
from pathlib import Path

import pytest
import yaml

from oracle_bench.config import load_config
from oracle_bench.instance.swebench import image_for, resolve, runtime_for


def test_image_is_derived_from_instance_id():
    assert image_for("psf__requests-2148") == (
        "swebench/sweb.eval.x86_64.psf_1776_requests-2148:latest"
    )


def test_local_record_resolves_instance_and_runtime(tmp_path):
    record = {
        "instance_id": "psf__requests-2148",
        "repo": "psf/requests",
        "base_commit": "a" * 40,
        "patch": """diff --git a/requests/a.py b/requests/a.py
--- a/requests/a.py
+++ b/requests/a.py
@@ -1,2 +1,2 @@
 def send(value):
-    return value
+    return str(value)
""",
        "test_patch": "diff --git a/test_requests.py b/test_requests.py\n",
        "FAIL_TO_PASS": ["test_requests.py::test_example"],
    }
    (tmp_path / "record.json").write_text(json.dumps(record))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "source": {"instance": record["instance_id"], "record": "record.json"},
                "agent": {"model": "test-model"},
                "task": {"prompt": str(Path(__file__).parents[1] / "prompts/agent/unit-tests.md")},
            }
        )
    )

    config = load_config(config_path)
    instance, runtime = resolve(config.source)

    assert instance.instance_id == record["instance_id"]
    assert instance.source.dataset == "verified"
    assert runtime.image == image_for(record["instance_id"])
    assert runtime.source_roots == ["requests"]
    assert runtime.import_modules == ["requests"]
    assert runtime.existing_test_globs == ["test_requests.py", "tests"]
    assert runtime.rebuild == ":"


def test_unknown_repository_has_no_guessed_runtime():
    with pytest.raises(ValueError, match="No tested SWE-bench runtime profile"):
        runtime_for({"instance_id": "example__project-1", "repo": "example/project"})


def test_pytest_source_checkout_takes_precedence_over_installed_distribution():
    runtime = runtime_for({"instance_id": "pytest-dev__pytest-5221", "repo": "pytest-dev/pytest"})
    assert runtime.pythonpath == "/testbed/src"
    assert runtime.pytest_plugins == ["pytester"]


def test_python_36_instance_uses_compatible_evaluation_tools():
    runtime = runtime_for(
        {
            "instance_id": "scikit-learn__scikit-learn-14710",
            "repo": "scikit-learn/scikit-learn",
        }
    )
    assert runtime.pytest_version == "6.2.5"


def resolve_record(tmp_path, *, instance="psf__requests-2148", **changes):
    record = {
        "instance_id": "psf__requests-2148",
        "repo": "psf/requests",
        "base_commit": "a" * 40,
        "patch": "diff --git a/requests/a.py b/requests/a.py\n",
        "test_patch": "diff --git a/test_requests.py b/test_requests.py\n",
        "FAIL_TO_PASS": ["test_requests.py::test_example"],
    }
    record.update(changes)
    record = {key: value for key, value in record.items() if value is not None}
    (tmp_path / "record.json").write_text(json.dumps(record))
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "source": {"instance": instance, "record": "record.json"},
                "agent": {"model": "test-model"},
                "task": {"prompt": str(Path(__file__).parents[1] / "prompts/agent/unit-tests.md")},
            }
        )
    )
    return resolve(load_config(config_path).source)


def test_fail_to_pass_stored_as_a_json_string_is_decoded(tmp_path):
    # The dataset stores FAIL_TO_PASS this way, so every real run takes this branch.
    encoded = json.dumps(["test_requests.py::test_a", "test_requests.py::test_b"])
    instance, _ = resolve_record(tmp_path, FAIL_TO_PASS=encoded)
    assert instance.reference_test_ids == ["test_requests.py::test_a", "test_requests.py::test_b"]


@pytest.mark.parametrize("key", ["instance_id", "repo", "base_commit", "patch", "test_patch"])
def test_a_record_missing_a_required_key_is_rejected(tmp_path, key):
    with pytest.raises(ValueError, match=f"missing {key}"):
        resolve_record(tmp_path, **{key: None})


def test_a_record_for_another_instance_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="does not match"):
        resolve_record(tmp_path, instance="psf__requests-1142")


@pytest.mark.parametrize("commit", ["a" * 39, "main", "A" * 40])
def test_a_base_commit_that_is_not_a_full_sha_is_rejected(tmp_path, commit):
    with pytest.raises(ValueError, match="base_commit must be immutable"):
        resolve_record(tmp_path, base_commit=commit)


@pytest.mark.parametrize("value", ['"test_requests.py::test_a"', [1, 2], {"id": "x"}])
def test_fail_to_pass_that_is_not_a_list_of_ids_is_rejected(tmp_path, value):
    with pytest.raises(ValueError, match="FAIL_TO_PASS must be a list"):
        resolve_record(tmp_path, FAIL_TO_PASS=value)
