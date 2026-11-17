import json
import subprocess
import sys
from pathlib import Path

import pytest

import oracle_lab.container.helpers.pytest_runner as runner

XFAIL_TEST = """import pytest

@pytest.mark.xfail(strict=True)
def test_known_bug():
    assert 1 + 1 == 3
"""


def run(tmp_path: Path, files: dict[str, str], **settings) -> dict:
    """Run the runner on generated `files` under `oracle_tests/`; return its tests.json."""
    work = tmp_path / "work"
    (work / "oracle_tests").mkdir(parents=True)
    for name, text in files.items():
        (work / "oracle_tests" / name).write_text(text)
    settings = {
        "workdir": str(work),
        "output": str(tmp_path / "out"),
        "targets": ["oracle_tests"],
        "import_modules": [],
        **settings,
    }
    (tmp_path / "settings.json").write_text(json.dumps(settings))
    subprocess.run(
        [sys.executable, runner.__file__, str(tmp_path / "settings.json")],
        check=False,
        cwd=tmp_path,
    )
    return json.loads((tmp_path / "out" / "tests.json").read_text())


@pytest.mark.parametrize("ignore, outcome", [(False, "xfail"), (True, "fail")])
def test_ignoring_xfail_markers_lets_the_assertion_decide(tmp_path: Path, ignore, outcome):
    result = run(tmp_path, {"test_bug.py": XFAIL_TEST}, ignore_xfail_markers=ignore)

    assert result["status"] == "completed"
    assert result["tests"]["oracle_tests/test_bug.py::test_known_bug"]["outcome"] == outcome


@pytest.mark.parametrize("name", ["pytester", "_pytest.pytester"])
def test_a_conftest_may_load_a_required_plugin_under_either_name(tmp_path: Path, name):
    # The runtime requires pytester; the generated conftest also loads it, by either name.
    files = {
        "conftest.py": f"pytest_plugins = [{name!r}]\n",
        "test_uses.py": "def test_uses(pytester):\n    assert pytester\n",
    }
    result = run(tmp_path, files, pytest_plugins=["pytester"])

    assert result["status"] == "completed", result.get("error")
    assert result["tests"]["oracle_tests/test_uses.py::test_uses"]["outcome"] == "pass"
