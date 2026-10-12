"""The localizer's rules, patch handling, and the cross-check, on small sources."""

import difflib

import pytest
from crosscheck import attribute_lines
from localize import compare, localize
from patches import apply_patch, parse_patch

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


def diff(before: str, after: str) -> str:
    lines = difflib.unified_diff(
        before.splitlines(), after.splitlines(), "a/m.py", "b/m.py", lineterm=""
    )
    return "diff --git a/m.py b/m.py\n" + "\n".join(lines) + "\n"


def target(after: str, before: str = BASE):
    result = localize([compare("m.py", before, after)])
    return result.level, [name.removeprefix("m.py::") for name in result.names]


# The method: compare syntax trees.


def test_a_body_change_names_the_qualified_method():
    assert target(change("self.records = []", "self.records = None")) == (
        "function",
        ["Handler.reset"],
    )


def test_a_nested_function_change_names_the_outer_function():
    assert target(change("        return value\n", "        return value + 1\n")) == (
        "function",
        ["helper"],
    )


def test_a_decorator_change_names_the_decorated_function():
    after = change("@name.setter", "@name.deleter")
    assert target(after) == ("function", ["Handler.name"])


def test_a_new_function_counts_only_through_its_call_site():
    after = change(
        "def check(value):\n    return value\n",
        "def check(value):\n    return _clean(value)\n\n\ndef _clean(\n    value,\n) -> int:\n"
        "    return value\n",
    )
    assert target(after) == ("function", ["check"])


def test_comment_and_formatting_changes_name_nothing():
    after = change("def check(value):\n", "def check( value ):  # identity\n")
    assert target(after) == ("file", ["m.py"])


def test_a_new_method_alone_names_its_class():
    after = change("    @property\n", "    def clear(self):\n        pass\n\n    @property\n")
    assert target(after) == ("class", ["Handler"])


def test_a_class_attribute_change_names_its_class():
    assert target(change("level = 1", "level = 2")) == ("class", ["Handler"])


def test_a_module_level_change_alone_names_the_file():
    result = localize([compare("m.py", BASE, change("import os", "import re"))])
    assert (result.level, result.names) == ("file", ["m.py"])
    assert result.lines == len(BASE.splitlines())


def test_functions_in_one_file_outrank_a_class_or_file_change_in_another():
    result = localize(
        [
            compare("a.py", BASE, change("level = 1", "level = 2")),
            compare("b.py", BASE, change("self.records = []", "self.records = None")),
            compare("c.py", BASE, change("import os", "import re")),
        ]
    )
    assert (result.level, result.names) == ("function", ["b.py::Handler.reset"])


def test_a_file_with_only_comment_changes_is_left_out_of_a_file_target():
    result = localize(
        [
            compare("a.py", BASE, change("import os", "import os  # stdlib")),
            compare("b.py", BASE, change("import os", "import re")),
        ]
    )
    assert (result.level, result.names) == ("file", ["b.py"])


def test_a_property_setter_is_told_apart_from_its_getter():
    after = change(
        "    def name(self, value):\n        pass", "    def name(self, value):\n        1"
    )
    assert target(after) == ("function", ["Handler.name"])
    assert localize([compare("m.py", BASE, after)]).lines == 3  # setter decorator, def, body


def test_a_deleted_file_names_every_function():
    assert target("") == ("function", ["Handler.reset", "Handler.name", "helper", "check"])


# Patch handling.


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


# The cross-check: attribute diff lines to function spans.


def attributed(after: str, before: str = BASE):
    (file,) = parse_patch(diff(before, after))
    return attribute_lines(before, file).units


def test_crosscheck_names_the_method_holding_a_changed_line():
    assert attributed(change("self.records = []", "self.records = None")) == ["Handler.reset"]


def test_crosscheck_credits_a_statement_after_a_blank_line_to_the_function_above():
    after = change(
        "        self.records = []\n\n", "        self.records = []\n\n        self.n = 0\n\n"
    )
    assert attributed(after) == ["Handler.reset"]


def test_crosscheck_ignores_the_body_of_a_new_function():
    after = change("def check(value):\n    return value\n", "def check(value):\n    return 1\n")
    assert attributed(after) == ["check"]
