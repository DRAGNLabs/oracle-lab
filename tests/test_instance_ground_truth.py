from fixtures import instance_record

from oracle_lab.instance import write_private_instance_artifacts
from oracle_lab.paths import RunPaths


def test_ground_truth_preserves_issue_and_exact_fix(tmp_path):
    paths = RunPaths.create(tmp_path)
    instance = instance_record(
        instance_id="owner__repo-123",
        golden_patch="diff --git a/module.py b/module.py\n+fixed",
        original_record={"problem_statement": "The original issue.\n\nWith details."},
    )

    write_private_instance_artifacts(paths, instance)

    assert (paths.ground_truth / "issue.md").read_text() == (
        "# owner__repo-123\n\nThe original issue.\n\nWith details.\n"
    )
    assert (paths.ground_truth / "fix.patch").read_text() == instance.golden_patch
