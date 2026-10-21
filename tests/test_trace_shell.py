import pytest

from oracle_lab.harnesses.steps import EditFile, ReadFile, Search, WriteFile
from oracle_lab.trace.shell import (
    PatchFile,
    RunCommand,
    RunPython,
    RunTests,
    classify,
    split,
)


def actions(command):
    return [action for part in split(command) for action in classify(part)]


def test_codex_wrapper_is_unwrapped_and_its_list_split():
    found = actions(
        "/bin/sh -lc \"sed -n '1,30p' pkg/mod.py; rg -n 'def f|class G' pkg/mod.py | head; "
        'git log --oneline -3"'
    )

    assert found == [
        ReadFile("pkg/mod.py", 1, 30),
        Search("def f|class G", "pkg/mod.py"),
        RunCommand("git log --oneline -3", git_history=True),
    ]


def test_heredoc_ending_a_list_belongs_to_its_last_command():
    found = actions("cd /tmp && python - <<'EOF' 2>&1 | grep -v W\nprint(1)\nEOF")

    assert found[0] == RunCommand("cd /tmp")
    assert isinstance(found[1], RunPython) and found[1].script == "print(1)\n"


def test_list_inside_a_pipeline_is_still_split():
    found = actions("sed -i '/^import os$/d' t.py && python -m pytest -q 2>&1 | tail -5")

    assert found == [EditFile("t.py", None, None), RunTests("python -m pytest -q")]


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("sed -n '10,20p;40p' a.py", [ReadFile("a.py", 10, 20), ReadFile("a.py", 40, 40)]),
        ("head -n 50 a.py", [ReadFile("a.py", 1, 50)]),
        ("awk 'NR>=5 && NR<=9' a.py", [ReadFile("a.py", 5, 9)]),
        ("cat a.py b.py", [ReadFile("a.py"), ReadFile("b.py")]),
        ("timeout 30 nl -ba a.py", [ReadFile("a.py")]),
        ("grep -rn -A 3 'x' src", [Search("x", "src")]),
        ("rg --files src", [RunCommand("rg --files src")]),
        ("git grep -n prepare_body", [Search("prepare_body")]),
    ],
)
def test_reads_and_searches(command, expected):
    assert actions(command) == expected


def test_writes_and_edits():
    assert actions("cat > t.py <<'EOF'\nx = 1\nEOF") == [WriteFile("t.py", "x = 1\n")]
    assert actions("echo '' > oracle_tests/__init__.py") == [
        WriteFile("oracle_tests/__init__.py", "\n")
    ]
    assert actions("echo x >> t.py") == [EditFile("t.py", None, None)]
    assert actions("sed -i 's/a/b/' t.py") == [EditFile("t.py", None, None)]


def test_apply_patch_gives_one_action_per_file():
    patch = (
        "apply_patch <<'PATCH'\n*** Begin Patch\n*** Add File: oracle_tests/test_a.py\n"
        "+import a\n+\n*** Update File: oracle_tests/test_b.py\n@@\n-old\n+new\n"
        "*** End Patch\nPATCH"
    )

    assert actions(patch) == [
        WriteFile("oracle_tests/test_a.py", "import a\n\n"),
        PatchFile("oracle_tests/test_b.py", "@@\n-old\n+new\n"),
    ]


def test_python_invocations():
    assert actions("python -m pytest -q oracle_tests") == [
        RunTests("python -m pytest -q oracle_tests")
    ]
    assert actions("pytest --version") == [RunCommand("pytest --version")]
    assert actions("python --version") == [RunCommand("python --version")]
    assert actions('python -c "import a; print(a.f())"') == [
        RunPython('python -c "import a; print(a.f())"', "import a; print(a.f())")
    ]
    assert actions("python /tmp/probe.py") == [RunPython("python /tmp/probe.py", "")]
