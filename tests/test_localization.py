import difflib
import random
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path

import pytest
from fixtures import RUN_CONFIG, instance_record, passed_preflight, run_config

import oracle_lab.localization as localization
from oracle_lab.contracts.base import write_result
from oracle_lab.contracts.run import LocalizedTarget, TargetFile, read_localized_target
from oracle_lab.io import write_json
from oracle_lab.localization import load_target, production_diffs, read_source
from oracle_lab.localization.patches import apply_patch, parse_patch
from oracle_lab.localization.target import choose_target, compare, fix_scope
from oracle_lab.paths import OutputPaths, RunPaths

BASE = """\
import os


class Handler:
    level = 1

    def reset(self):
        self.records = []

    @property
    def name(self):
        return "h"

    @name.setter
    def name(self, value):
        pass


def helper(value):
    def inner():
        return value
    return inner()


def check(value):
    return value
"""


def change(old: str, new: str, source: str = BASE) -> str:
    assert old in source
    return source.replace(old, new)


def diff(before: str, after: str, path: str = "m.py", context: int = 3) -> str:
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), f"a/{path}", f"b/{path}", n=context, lineterm=""
    )
    return f"diff --git a/{path} b/{path}\n" + "\n".join(lines) + "\n"


def target(after: str | None, before: str = BASE) -> tuple[list[str], list[str]]:
    """The functions and classes named in m.py; both empty for a whole-file target."""
    result = choose_target([compare("m.py", before, after)])
    assert [entry.path for entry in result.files] == ["m.py"]
    return result.files[0].functions, result.files[0].classes


def test_a_body_change_names_the_qualified_method():
    after = change("self.records = []", "self.records = None")
    assert target(after) == (["Handler.reset"], [])


def test_a_nested_function_change_names_the_outer_function():
    after = change("        return value\n", "        return value + 1\n")
    assert target(after) == (["helper"], [])


def test_a_decorator_change_names_the_decorated_function():
    assert target(change("@name.setter", "@name.deleter")) == (["Handler.name"], [])


def test_a_new_function_counts_only_through_its_call_site():
    after = change(
        "def check(value):\n    return value\n",
        "def check(value):\n    return _clean(value)\n\n\ndef _clean(\n    value,\n) -> int:\n"
        "    return value\n",
    )
    assert target(after) == (["check"], [])


def test_comment_and_formatting_changes_name_nothing():
    after = change("def check(value):\n", "def check( value ):  # identity\n")
    assert target(after) == ([], [])


def test_a_new_method_alone_names_its_class():
    after = change("    @property\n", "    def clear(self):\n        pass\n\n    @property\n")
    assert target(after) == ([], ["Handler"])


def test_a_class_attribute_change_names_its_class():
    assert target(change("level = 1", "level = 2")) == ([], ["Handler"])


def test_a_module_level_change_alone_names_the_whole_file():
    result = choose_target([compare("m.py", BASE, change("import os", "import re"))])
    lines = len(BASE.splitlines())
    assert result.files == [TargetFile(path="m.py", functions=[], classes=[], lines=lines)]


def test_a_property_setter_is_told_apart_from_its_getter():
    after = change(
        "    def name(self, value):\n        pass", "    def name(self, value):\n        1"
    )
    result = choose_target([compare("m.py", BASE, after)])
    # The setter's decorator, `def` line and body.
    assert result.files == [
        TargetFile(path="m.py", functions=["Handler.name"], classes=[], lines=3)
    ]


def test_a_deleted_file_names_every_function_and_class():
    assert target(None) == (["Handler.reset", "Handler.name", "helper", "check"], ["Handler"])


def test_an_emptied_file_is_not_mistaken_for_a_deleted_one():
    result = choose_target([compare("m.py", "VALUE = 1\n", "")])
    assert result.text == "- `m.py` (whole file)"


def test_functions_and_class_code_are_named_across_files_but_module_code_is_not():
    result = choose_target(
        [
            compare("a.py", BASE, change("level = 1", "level = 2")),
            compare("b.py", BASE, change("self.records = []", "self.records = None")),
            compare("c.py", BASE, change("import os", "import re")),
        ]
    )
    named = [(entry.path, entry.functions, entry.classes) for entry in result.files]
    assert named == [("a.py", [], ["Handler"]), ("b.py", ["Handler.reset"], [])]


def test_a_function_and_its_class_code_are_named_together():
    after = change("level = 1", "level = 2", change("self.records = []", "self.records = None"))
    assert target(after) == (["Handler.reset"], ["Handler"])
    assert choose_target([compare("m.py", BASE, after)]).text == (
        "- `m.py`: `Handler.reset`, class `Handler`"
    )


def test_new_methods_name_their_class_only_when_nothing_else_changed():
    new_method = ("    @property\n", "    def clear(self):\n        pass\n\n    @property\n")
    assert target(change(*new_method)) == ([], ["Handler"])
    after = change(*new_method, change("self.records = []", "self.records = None"))
    assert target(after) == (["Handler.reset"], [])


def test_a_file_target_leaves_out_files_with_only_comment_changes():
    result = choose_target(
        [
            compare("a.py", BASE, change("import os", "import os  # stdlib")),
            compare("b.py", BASE, change("import os", "import re")),
        ]
    )
    assert [entry.path for entry in result.files] == ["b.py"]
    assert result.text == "- `b.py` (whole file)"


def test_an_async_function_is_named_like_any_function():
    before = "async def fetch():\n    return 1\n"
    assert target(before.replace("1", "2"), before) == (["fetch"], [])


CONDITIONAL = """\
import sys

if sys.version_info >= (3,):
    def pick():
        return 1
else:
    def pick():
        return 2

try:
    from fast import load
except ImportError:
    def load():
        return 3
"""


def test_functions_under_module_level_if_and_try_are_named():
    after = change("return 2", "return 20", CONDITIONAL)
    result = choose_target([compare("m.py", CONDITIONAL, after)])
    # Only the second `pick`, the one the repair changed, is counted.
    assert result.files == [TargetFile(path="m.py", functions=["pick"], classes=[], lines=2)]
    assert target(change("return 3", "return 30", CONDITIONAL), CONDITIONAL) == (["load"], [])


def test_a_class_inside_a_function_belongs_to_the_function():
    before = "def make():\n    class Local:\n        x = 1\n    return Local\n"
    assert target(change("x = 1", "x = 2", before), before) == (["make"], [])


def test_a_class_decorator_change_names_the_class():
    before = "from dataclasses import dataclass\n\n\n@dataclass\nclass Point:\n    x: int\n"
    after = change("@dataclass\n", "@dataclass(frozen=True)\n", before)
    assert target(after, before) == ([], ["Point"])


def test_docstring_changes_name_nothing():
    before = '"""Module."""\n\n\nclass C:\n    """Class."""\n\n    def m(self):\n        """Method."""\n        return 1\n'
    after = before.replace("Module.", "Mod.").replace("Class.", "Cls.").replace("Method.", "Meth.")
    assert target(after, before) == ([], [])


def test_a_function_whose_docstring_and_code_change_is_named():
    before = 'def f():\n    """Old."""\n    return 1\n'
    assert target('def f():\n    """New."""\n    return 2\n', before) == (["f"], [])


def test_a_function_left_with_only_its_docstring_is_named():
    before = 'def f():\n    """Doc."""\n    return 1\n'
    assert target('def f():\n    """Doc."""\n', before) == (["f"], [])


def test_an_added_overload_does_not_name_the_unchanged_function():
    before = "from typing import overload\n\n\ndef f(x):\n    return x\n"
    after = change("def f(x):", "@overload\ndef f(x: int) -> int: ...\n\n\ndef f(x):", before)
    assert target(after, before) == ([], [])


def scope(after: str | None, before: str = BASE) -> str:
    return fix_scope([compare("m.py", before, after)])


BODY_CHANGE = ("self.records = []", "self.records = None")
MODULE_CHANGE = ("import os\n", "import os\n\nLIMIT = 3\n")


def test_changes_inside_functions_are_function_scope():
    assert scope(change(*BODY_CHANGE)) == "function"


def test_a_class_attribute_change_is_class_scope():
    assert scope(change("level = 1", "level = 2", change(*BODY_CHANGE))) == "class"


def test_a_class_decorator_change_is_class_scope():
    assert scope(change("class Handler:", "@final\nclass Handler:")) == "class"


def test_module_level_code_is_module_scope_even_beside_other_changes():
    after = change(*MODULE_CHANGE, change("level = 1", "level = 2", change(*BODY_CHANGE)))
    assert scope(after) == "module"
    assert scope(change(*MODULE_CHANGE)) == "module"


def test_imports_and_new_definitions_beside_a_function_change_do_not_count():
    after = change(
        "import os\n",
        "import os\nimport re\n\ntry:\n    import fast\nexcept ImportError:\n    import slow\n"
        "\nif TYPE_CHECKING:\n    from typing import Any\n\n\ndef new_helper():\n    pass\n",
        change(*BODY_CHANGE),
    )
    after = change(
        "    @property\n", "    def clear(self):\n        pass\n\n    @property\n", after
    )
    assert scope(after) == "function"


def test_an_import_fallback_that_assigns_is_module_scope():
    after = change(
        "import os\n", "import os\n\ntry:\n    import fast\nexcept ImportError:\n    fast = None\n"
    )
    assert scope(after) == "module"


def test_new_methods_alone_are_class_scope():
    assert (
        scope(change("    @property\n", "    def clear(self):\n        pass\n\n    @property\n"))
        == "class"
    )


def test_a_new_function_alone_is_module_scope():
    assert scope(BASE + "\n\ndef added():\n    pass\n") == "module"


def test_removing_a_method_is_function_scope_and_removing_a_class_is_class_scope():
    assert scope(change("    def reset(self):\n        self.records = []\n\n", "")) == "function"
    before = "class Gone:\n    def m(self):\n        pass\n\n\nclass Kept:\n    pass\n"
    assert scope("class Kept:\n    pass\n", before) == "class"


def test_an_imports_only_repair_is_module_scope_with_a_whole_file_target():
    after = change("import os", "import re")
    assert scope(after) == "module"
    assert target(after) == ([], [])


def test_a_nested_class_attribute_names_the_nested_class():
    before = "class Outer:\n    class Inner:\n        size = 1\n"
    after = before.replace("size = 1", "size = 2")
    assert target(after, before) == ([], ["Outer.Inner"])
    assert scope(after, before) == "class"


def test_the_broadest_scope_across_files_wins():
    changes = [
        compare("a.py", BASE, change(*BODY_CHANGE)),
        compare("b.py", BASE, change("level = 1", "level = 2")),
    ]
    assert fix_scope(changes) == "class"


def test_a_buggy_file_that_does_not_parse_is_an_error():
    with pytest.raises(SyntaxError):
        compare("m.py", "def broken(:\n", "def broken():\n    pass\n")


def test_a_repair_with_no_existing_production_file_has_no_target():
    with pytest.raises(ValueError, match="no existing production Python file"):
        choose_target([])


def test_a_patch_rebuilds_the_fixed_file():
    after = change("self.records = []", "self.records = None")
    (file,) = parse_patch(diff(BASE, after))
    assert apply_patch(BASE, file) == after


def test_a_hunk_with_a_stale_header_applies_at_the_nearest_offset():
    after = change("self.records = []", "self.records = None")
    (file,) = parse_patch(diff(BASE, after))
    assert apply_patch("# one extra line\n" + BASE, file) == "# one extra line\n" + after


def test_a_patch_that_does_not_match_the_file_is_rejected():
    (file,) = parse_patch(diff(BASE, change("level = 1", "level = 2")))
    with pytest.raises(ValueError, match="matches nowhere"):
        apply_patch(change("level = 1", "level = 3"), file)


def test_a_patch_with_several_hunks_rebuilds_the_fixed_file():
    after = change("import os", "import re")
    after = change("level = 1", "level = 2", after)
    after = change("def check(value):\n    return value", "def check(value):\n    return 0", after)
    (file,) = parse_patch(diff(BASE, after, context=1))
    assert len(file.hunks) == 3
    assert apply_patch(BASE, file) == after


def test_a_patch_without_context_rebuilds_the_fixed_file():
    after = change("level = 1\n", "level = 1\n    width = 2\n")
    after = change("import os\n", "", after)
    (file,) = parse_patch(diff(BASE, after, context=0))
    assert apply_patch(BASE, file) == after


def test_a_patch_applies_to_a_file_with_windows_line_endings():
    after = change("self.records = []", "self.records = None")
    (file,) = parse_patch(diff(BASE, after))
    assert apply_patch(BASE.replace("\n", "\r\n"), file) == after


def test_a_renamed_file_is_compared_at_its_buggy_path():
    after = change("level = 1", "level = 2")
    patch = (
        diff(BASE, after, "package/old.py")
        .replace(
            "diff --git a/package/old.py b/package/old.py\n",
            "diff --git a/package/old.py b/package/new.py\n"
            "similarity index 95%\nrename from package/old.py\nrename to package/new.py\n",
        )
        .replace("+++ b/package/old.py", "+++ b/package/new.py")
    )
    ((path, file),) = production_diffs(patch, ["package"])
    assert (path, file.new_path) == ("package/old.py", "package/new.py")
    assert apply_patch(BASE, file) == after


def test_a_pure_rename_changes_no_code():
    patch = (
        "diff --git a/package/old.py b/package/new.py\n"
        "similarity index 100%\nrename from package/old.py\nrename to package/new.py\n"
    )
    assert production_diffs(patch, ["package"]) == []


def test_a_buggy_file_is_decoded_by_its_coding_line():
    assert read_source("# -*- coding: latin-1 -*-\nNAME = 'é'\n".encode("latin-1")).endswith(
        "NAME = 'é'\n"
    )
    assert read_source(b"\xef\xbb\xbfX = 1\n") == "X = 1\n"


def test_a_patch_names_created_and_deleted_files():
    patch = (
        "diff --git a/new.py b/new.py\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1 @@\n+X = 1\n"
        "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-X = 1\n"
    )
    created, deleted = parse_patch(patch)
    assert (created.old_path, created.new_path) == (None, "new.py")
    assert (deleted.old_path, deleted.new_path) == ("old.py", None)


def test_the_prompt_text_lists_each_file_at_its_level():
    functions = choose_target(
        [
            compare("a.py", BASE, change("self.records = []", "self.records = None")),
            compare("b.py", BASE, change("        return value\n", "        return 1\n")),
        ]
    )
    assert functions.text == "- `a.py`: `Handler.reset`\n- `b.py`: `helper`"
    one_class = choose_target([compare("m.py", BASE, change("level = 1", "level = 2"))])
    assert one_class.text == "- `m.py`: class `Handler`"
    whole_file = choose_target([compare("m.py", BASE, change("import os", "import re"))])
    assert whole_file.text == "- `m.py` (whole file)"


def test_the_target_artifact_round_trips_and_rejects_an_empty_target(tmp_path):
    target = choose_target([compare("m.py", BASE, change("level = 1", "level = 2"))])
    write_result(tmp_path / "target.json", target)
    assert read_localized_target(tmp_path / "target.json") == target
    with pytest.raises(ValueError):
        LocalizedTarget(files=[], text="", scope="function")


# The stage: read buggy files from the image, then compare.

SAMPLE = RUN_CONFIG


class FileSandbox:
    """Serves the buggy checkout from a dict and records every command."""

    def __init__(self, files: dict[str, str]):
        self.files = files
        self.commands = []
        self.downloads = []

    def run(self, argv, **kwargs):
        self.commands.append(argv)

    def download(self, source, destination):
        self.downloads.append(source)
        if source not in self.files:
            raise FileNotFoundError(source)
        destination.write_text(self.files[source])


def run_stage(monkeypatch, tmp_path, patch: str, files: dict[str, str]):
    config = run_config("package", image="example/runtime")
    sandbox = FileSandbox({f"/testbed/{path}": text for path, text in files.items()})
    opened = []

    @contextmanager
    def fake_open_sandbox(client, image, config, profile, log):
        opened.append((image, profile))
        yield sandbox

    monkeypatch.setattr(localization, "open_sandbox", fake_open_sandbox)
    paths = RunPaths.create(tmp_path / "run")
    instance = instance_record(golden_patch=patch)
    target = localization.localize(object(), config, "sha256:image", instance, paths)
    return target, sandbox, opened, paths


def test_the_stage_reads_the_reset_checkout_and_saves_the_target(monkeypatch, tmp_path):
    after = change("self.records = []", "self.records = None")
    target, sandbox, opened, paths = run_stage(
        monkeypatch, tmp_path, diff(BASE, after, "package/core.py"), {"package/core.py": BASE}
    )

    assert [profile.value for _, profile in opened] == ["localization"]
    assert sandbox.commands == [["git", "reset", "--hard", "a" * 40]]
    assert sandbox.downloads == ["/testbed/package/core.py"]
    assert target.files == [
        TargetFile(path="package/core.py", functions=["Handler.reset"], classes=[], lines=2)
    ]
    assert read_localized_target(paths.target) == target


def test_the_stage_skips_created_and_test_files_and_compares_a_deleted_file(monkeypatch, tmp_path):
    patch = (
        diff("", "X = 1\n", "package/new.py").replace("--- a/package/new.py", "--- /dev/null")
        + diff(BASE, change("level = 1", "level = 2"), "package/tests/test_core.py")
        + diff(BASE, "", "package/old.py").replace("+++ b/package/old.py", "+++ /dev/null")
    )
    target, sandbox, _, _ = run_stage(monkeypatch, tmp_path, patch, {"package/old.py": BASE})

    assert sandbox.downloads == ["/testbed/package/old.py"]
    assert target.files[0].functions == ["Handler.reset", "Handler.name", "helper", "check"]


def test_a_patch_that_does_not_match_the_image_stops_the_stage(monkeypatch, tmp_path):
    patch = diff(BASE, change("level = 1", "level = 2"), "package/core.py")
    with pytest.raises(ValueError, match="matches nowhere"):
        run_stage(
            monkeypatch, tmp_path, patch, {"package/core.py": change("level = 1", "level = 3")}
        )
    assert not (tmp_path / "run/inputs/target.json").exists()


def test_production_files_are_python_files_under_a_source_root_outside_tests():
    patch = "".join(
        diff("A = 1\n", "A = 2\n", path)
        for path in [
            "package/core.py",
            "package/tests/helpers.py",
            "docs/conf.py",
            "package/data.txt",
        ]
    )
    assert [path for path, _ in production_diffs(patch, ["package"])] == ["package/core.py"]


# The patch reader against Git: random edits, diffed by Git, rebuilt by `apply_patch`.

GIT = shutil.which("git")


def git_diff(before: str, after: str, directory: Path, context: int) -> str:
    (directory / "a.py").write_text(before)
    (directory / "b.py").write_text(after)
    result = subprocess.run(
        [GIT, "diff", "--no-index", f"-U{context}", "a.py", "b.py"],
        cwd=directory,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stderr  # 1 means the files differ
    return result.stdout


def random_edit(source: str, rng: random.Random) -> str:
    """One to four line insertions, deletions, or replacements at random places."""
    lines = source.splitlines()
    for _ in range(rng.randint(1, 4)):
        at = rng.randrange(len(lines) + 1)
        kind = rng.choice(["insert", "delete", "replace"]) if lines else "insert"
        new = [f"edited_{rng.randrange(1000)} = {rng.randrange(1000)}"] * rng.randint(1, 3)
        if kind == "insert":
            lines[at:at] = new
        elif at < len(lines):
            lines[at : at + rng.randint(1, 3)] = new if kind == "replace" else []
    return "\n".join(lines) + "\n"


@pytest.mark.skipif(GIT is None, reason="Needs the git binary")
@pytest.mark.parametrize("context", [0, 1, 3])
def test_apply_patch_rebuilds_what_git_diffed(tmp_path, context):
    # Repeated lines make a hunk match in more than one place.
    sources = [BASE, CONDITIONAL, "x = 1\n" * 12 + "y = 2\n" + "x = 1\n" * 12]
    rng = random.Random(context)
    for case in range(60):
        before = sources[case % len(sources)]
        after = random_edit(before, rng)
        if after == before:
            continue
        (file,) = parse_patch(git_diff(before, after, tmp_path, context))
        assert apply_patch(before, file) == after, (case, before, after)


# Loading the target preflight found.


def test_a_run_copies_the_newest_passed_preflight_of_its_problem(tmp_path):
    instance = instance_record()
    older = passed_preflight(tmp_path, instance, scope="function")
    # IDs order by start time; one created in the same second would tie by chance.
    older.root.rename(older.root.with_name("20200101T000000Z-00000000"))
    newest = passed_preflight(tmp_path, instance, scope="class")
    paths = RunPaths.create(tmp_path / "run")

    target = load_target(OutputPaths(tmp_path), instance, paths)

    assert target.scope == "class"
    assert paths.target.read_bytes() == newest.target.read_bytes()
    assert paths.target_log.read_text() == "localize log\n"
    assert paths.target_source.read_text() == f"preflight/{newest.root.name}\n"


def test_only_a_passed_preflight_of_the_same_record_is_used(tmp_path):
    instance = instance_record()
    failed = passed_preflight(tmp_path, instance)
    write_json(failed.status, {"stage": "reference", "state": "failed", "updated_at": "now"})
    passed_preflight(tmp_path, instance_record(instance_id="other__project-2"))
    passed_preflight(tmp_path, instance_record(base_commit="b" * 40))
    no_target = passed_preflight(tmp_path, instance)
    no_target.target.unlink()

    with pytest.raises(ValueError, match="No passed preflight has a target for owner__project-1"):
        load_target(OutputPaths(tmp_path), instance, RunPaths.create(tmp_path / "run"))
