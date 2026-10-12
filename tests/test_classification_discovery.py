from pathlib import Path

from fixtures import instance_record

from oracle_bench.classification.contracts import latest_classification
from oracle_bench.io import write_json


def prepared_run(tmp_path: Path) -> tuple[Path, Path]:
    classifications = tmp_path / "classifications"
    return classifications, classifications / "owner__project-1"


def latest(classifications: Path):
    return latest_classification(classifications, instance_record())


def classification(result=None, **identity):
    value = {
        "instance_id": "owner__project-1",
        "base_commit": "a" * 40,
        "source_record_sha256": "d" * 64,
        "rubric_sha256": "e" * 64,
        "result": result
        or {
            "status": "completed",
            "task_nature": "out_of_scope",
            "rationale": "This is a feature request.",
        },
    }
    value.update(identity)
    return value


def test_returns_newest_matching_attempt(tmp_path):
    paths, root = prepared_run(tmp_path)
    write_json(root / "20260101T000000Z-old" / "classification.json", classification())
    write_json(
        root / "20260201T000000Z-new" / "classification.json",
        classification(result={"status": "failed", "error": "model unavailable"}),
    )

    result, attempt = latest(paths)

    assert result == {"status": "failed", "error": "model unavailable"}
    assert attempt.name == "20260201T000000Z-new"


def test_rejects_mismatched_provenance(tmp_path):
    paths, root = prepared_run(tmp_path)
    write_json(
        root / "20260101T000000Z-attempt" / "classification.json",
        classification(base_commit="b" * 40),
    )

    result, _ = latest(paths)

    assert result["status"] == "mismatched"
    assert "base_commit" in result["error"]


def test_missing_classification_is_explicit(tmp_path):
    paths, _ = prepared_run(tmp_path)

    result, attempt = latest(paths)

    assert result == {"status": "missing"}
    assert attempt is None


def test_incomplete_latest_attempt_does_not_fall_back(tmp_path):
    paths, root = prepared_run(tmp_path)
    write_json(root / "20260101T000000Z-old" / "classification.json", classification())
    write_json(
        root / "20260201T000000Z-new" / "status.json",
        {"stage": "build", "state": "failed", "error": "image build failed"},
    )

    result, attempt = latest(paths)

    assert result == {"status": "invalid_artifact", "error": "image build failed"}
    assert attempt.name == "20260201T000000Z-new"


def test_corrupt_latest_artifact_is_explicit(tmp_path):
    paths, root = prepared_run(tmp_path)
    artifact = root / "20260201T000000Z-new" / "classification.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("not JSON")

    result, attempt = latest(paths)

    assert result["status"] == "invalid_artifact"
    assert result["error"]
    assert attempt == artifact.parent
