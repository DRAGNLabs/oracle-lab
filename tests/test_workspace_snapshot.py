from pathlib import Path

from oracle_lab.container.helpers.workspace import snapshot


def test_snapshot_skips_tool_caches_from_agent_test_runs(tmp_path: Path) -> None:
    for name in (
        "pkg/module.py",
        "oracle_tests/test_new.py",
        ".hypothesis/examples/abc/def",
        ".pytest_cache/v/cache/lastfailed",
        "v/cache/lastfailed",
        "v/other.txt",
        ".coverage",
        ".coverage.host.123.456",
        ".coveragerc",
    ):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")

    assert sorted(snapshot(tmp_path)) == [
        ".coveragerc",
        "oracle_tests/test_new.py",
        "pkg/module.py",
        "v/other.txt",
    ]
