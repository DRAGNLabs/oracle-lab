from oracle_bench.paths import RunPaths


def test_creating_a_run_makes_the_directories_its_stages_write_into(tmp_path):
    paths = RunPaths.create(tmp_path / "run")

    for directory in (
        paths.inputs,
        paths.build,
        paths.reference,
        paths.generation,
        paths.submission,
        paths.evaluation,
        paths.ground_truth,
        paths.judge.root,
    ):
        assert directory.is_dir(), directory
    # Every artifact path stays inside the run.
    assert paths.results.is_relative_to(paths.root)
    assert paths.judge.human.judgments.is_relative_to(paths.root)


def test_opening_a_saved_run_creates_nothing(tmp_path):
    RunPaths.open(tmp_path / "run")

    assert not (tmp_path / "run").exists()
