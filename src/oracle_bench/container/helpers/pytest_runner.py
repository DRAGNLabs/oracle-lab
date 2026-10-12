"""Standalone runner copied into evaluation containers (Python 3.6+).

Use pytest hooks instead of interpreting the process exit code as a test result.
Write progress on every report so a crash/timeout leaves useful partial evidence.
"""

import json
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any, Generator

import pytest


def write(path: Path, data: object) -> None:
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    temporary.replace(path)


class Recorder:
    def __init__(self, output: Path) -> None:
        self.output = output
        self.data = {
            "status": "running",
            "exit_code": None,
            "tests": {},
            "collection_errors": [],
            "collected": 0,
        }

    def save(self) -> None:
        write(self.output / "tests.json", self.data)

    def pytest_collection_modifyitems(self, items: list) -> None:
        self.data["collected"] = len(items)
        for item in items:
            if item.nodeid in self.data["tests"]:
                raise ValueError("Duplicate test ID: " + item.nodeid)
            self.data["tests"][item.nodeid] = {"outcome": "not_run", "phases": []}
        self.save()

    def pytest_collectreport(self, report) -> None:
        if report.failed:
            self.data["collection_errors"].append(
                {"nodeid": report.nodeid, "details": str(report.longrepr)}
            )
            self.save()

    @pytest.hookimpl(hookwrapper=True)
    # `Any` is the value pytest sends back in. We don't name pytest's type for it,
    # so this file keeps working whatever pytest version the repo pins.
    def pytest_runtest_makereport(self, item, call) -> Generator[None, Any, None]:
        """Attach structured exception evidence before the report is recorded."""
        outcome = yield
        report = outcome.get_result()
        if call.excinfo is None:
            return
        exception_type = call.excinfo.type
        value = call.excinfo.value
        assertion_types = (AssertionError, pytest.fail.Exception)
        report.oracle_failure = {
            "kind": "assertion" if isinstance(value, assertion_types) else "exception",
            "exception_type": exception_type.__module__ + "." + exception_type.__qualname__,
            "message": str(value),
        }

    def pytest_runtest_logreport(self, report) -> None:
        case = self.data["tests"].setdefault(report.nodeid, {"outcome": "not_run", "phases": []})
        phase = {
            "phase": report.when,
            "outcome": report.outcome,
            "duration_seconds": report.duration,
        }
        if report.longrepr:
            phase["details"] = str(report.longrepr)
        if hasattr(report, "oracle_failure"):
            phase["failure"] = report.oracle_failure
        if hasattr(report, "wasxfail"):
            phase["expected_failure"] = str(report.wasxfail)
        case["phases"].append(phase)
        if hasattr(report, "wasxfail"):
            case["outcome"] = "xpass" if report.passed else "xfail"
        elif report.failed:
            if report.when != "call":
                case["outcome"] = report.when + "_error"
            elif str(report.longrepr).startswith("[XPASS(strict)]"):
                case["outcome"] = "xpass"
            else:
                case["outcome"] = "fail"
                case["failure"] = phase.get("failure", {"kind": "unknown"})
        elif report.skipped:
            case["outcome"] = "skip"
        elif report.when == "call" and case["outcome"] == "not_run":
            case["outcome"] = "pass"
        self.save()


def run(settings: dict) -> int:
    output = Path(settings["output"])
    output.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(output)
    recorder.save()
    os.chdir(settings["workdir"])
    sys.path.insert(0, settings["workdir"])
    coverage_result = {"status": "unavailable", "reason": "Coverage did not finish"}
    cov = None
    source_files = []
    try:
        # Ensure editable installs/import paths reference the selected checkout.
        # Probe in a separate interpreter so imports by the tests remain measurable.
        probe = """import importlib, json, pathlib, sys
origins = {}
for name in sys.argv[1:]:
    location = pathlib.Path(importlib.import_module(name).__file__).resolve()
    if pathlib.Path.cwd().resolve() not in location.parents:
        raise RuntimeError('Imported %s from outside checkout: %s' % (name, location))
    origins[name] = str(location)
print(json.dumps(origins))
"""
        checked = subprocess.run(
            [sys.executable, "-c", probe, *settings.get("import_modules", [])],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            check=False,
        )
        if checked.returncode:
            raise RuntimeError(checked.stderr)
        origins = json.loads(checked.stdout)
        write(output / "imports.json", origins)
        try:
            import coverage

            roots = [Path(p).resolve() for p in settings["source_roots"]]
            if any(not p.exists() for p in roots):
                raise ValueError("A configured coverage source root does not exist")
            source_files = sorted(
                {
                    str(file)
                    for root in roots
                    for file in (root.rglob("*.py") if root.is_dir() else [root])
                }
            )
            cov = coverage.Coverage(
                data_file=str(output / ".coverage"),
                source=sorted({str(p if p.is_dir() else p.parent) for p in roots}),
                config_file=False,
            )
            cov.start()
        except Exception as exc:
            coverage_result = {"status": "unavailable", "reason": str(exc)}
            cov = None
        os.environ.pop("PYTEST_ADDOPTS", None)
        # Run generated targets only; don't inherit a repository's default targets/addopts.
        # One file that fails to collect must not stop the other files from running.
        arguments = ["-o", "addopts=", "-q", "--tb=short", "--continue-on-collection-errors"]
        for plugin in settings.get("pytest_plugins", []):
            arguments.extend(["-p", plugin])
        arguments.extend(settings["targets"])
        write(output / "command.json", [sys.executable, "-m", "pytest", *arguments])
        exit_code = int(pytest.main(arguments, plugins=[recorder]))
        recorder.data["exit_code"] = exit_code
        # Collection errors stay recorded; they only decide the status when no test ran.
        if exit_code in (0, 1) and recorder.data["collected"]:
            recorder.data["status"] = "completed"
        elif recorder.data["collection_errors"]:
            recorder.data["status"] = "collection_error"
        elif exit_code in (0, 1, 5):
            recorder.data["status"] = "no_tests"
        else:
            recorder.data["status"] = "runner_error"
    except BaseException:
        recorder.data["status"] = "runner_error"
        recorder.data["error"] = traceback.format_exc()
    finally:
        if cov is not None:
            try:
                cov.stop()
                cov.save()
                # Legacy repositories can contain platform-specific or vendored
                # Python files that the selected interpreter cannot parse. They
                # should not erase coverage for the valid production sources.
                cov.json_report(
                    morfs=source_files,
                    outfile=str(output / "coverage.raw.json"),
                    ignore_errors=True,
                )
                raw = json.loads((output / "coverage.raw.json").read_text())
                totals = raw["totals"]
                coverage_result = {
                    "status": "available",
                    "kind": "line",
                    "covered_lines": totals["covered_lines"],
                    "executable_lines": totals["num_statements"],
                    "percent": totals["percent_covered"],
                    "files": raw["files"],
                }
            except Exception as exc:
                coverage_result = {"status": "unavailable", "reason": str(exc)}
        write(output / "coverage.json", coverage_result)
        recorder.save()
    return 0 if recorder.data["status"] in {"completed", "no_tests", "collection_error"} else 2


if __name__ == "__main__":
    raise SystemExit(run(json.loads(Path(sys.argv[1]).read_text())))
