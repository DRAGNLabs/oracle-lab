"""The repository checkout inside a sandbox: reset, rebuild, patch, baseline.

Shared by generation and evaluation. Kept separate from Docker transport and
image builds, and distinct from the judge's privileged evidence workspace.
"""

from pathlib import Path

from oracle_bench.config import RunConfig
from oracle_bench.container.sandbox import Sandbox
from oracle_bench.io import write_json


class Repository:
    def __init__(self, sandbox: Sandbox, config: RunConfig) -> None:
        self.sandbox = sandbox
        self.config = config
        self.runtime = config.require_runtime()
        self.setup_timeout = config.limits.setup_seconds

    def prepare(
        self,
        base_commit: str,
        directory: Path,
        *,
        reference: bool = False,
        sanitize_history: bool = False,
    ) -> None:
        """Reset tracked code to the base commit, keeping the image's untracked files.

        Those are compiled modules and install metadata, which ``git clean`` would delete.
        """
        self.sandbox.run(["git", "reset", "--hard", base_commit], timeout=self.setup_timeout)
        self.rebuild()
        settings = directory / "workspace.json"
        write_json(
            settings,
            {
                "workdir": self.runtime.workdir,
                "generated_dir": self.config.task.generated_dir,
                "hide": self.config.task.hides_existing_tests and not reference,
                "sanitize_history": sanitize_history,
                "existing_test_globs": self.runtime.existing_test_globs,
            },
        )
        self.sandbox.upload(settings, "/tmp/oracle-workspace.json")
        self.sandbox.run(
            [
                self.runtime.python,
                self.sandbox.helpers + "/prepare.py",
                "/tmp/oracle-workspace.json",
            ],
            timeout=self.setup_timeout,
        )

    def rebuild(self) -> None:
        # A source adapter's installation recipe is intentionally a shell script.
        self.sandbox.run(
            ["/bin/bash", "-c", "set -euo pipefail\n" + self.runtime.rebuild],
            timeout=self.setup_timeout,
        )

    def apply_patch(self, patch: str, path: Path) -> None:
        path.write_text(patch)
        self.sandbox.upload(path, "/tmp/oracle.patch")
        self.sandbox.run(
            ["git", "apply", "--check", "/tmp/oracle.patch"], timeout=self.setup_timeout
        )
        self.sandbox.run(["git", "apply", "/tmp/oracle.patch"], timeout=self.setup_timeout)
        self.sandbox.run(["rm", "/tmp/oracle.patch"])

    def baseline(self, path: Path) -> str:
        self.sandbox.run(["git", "rev-parse", "HEAD"], stdout=path)
        return path.read_text().strip()
