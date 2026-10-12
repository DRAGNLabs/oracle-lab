"""Managed SDK containers with the benchmark's lifecycle and output policy."""

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import docker
from docker import DockerClient
from docker.errors import DockerException, NotFound
from docker.models.containers import Container
from requests.exceptions import RequestException

from oracle_bench.config import ClassificationConfig, RunConfig, RuntimeConfig
from oracle_bench.container import files
from oracle_bench.container.files import RedactedOutput
from oracle_bench.container.lifecycle import execution_watchdog, preserve_failure
from oracle_bench.io import read_json

HELPER_DIR = "/opt/oracle-bench/container-helpers"
ORACLE_USER = "10001:10001"


class Profile(Enum):
    GENERATION = "generation"
    EVALUATION = "evaluation"
    REFERENCE = "reference"
    JUDGE = "judge"
    CLASSIFICATION = "classification"
    HUMAN_JUDGE = "human-judge"


@dataclass
class CommandResult:
    exit_code: int
    duration_seconds: float
    timed_out: bool = False


class Sandbox:
    """A running container. Use open_sandbox to own its lifetime."""

    def __init__(self, container: Container, runtime: RuntimeConfig, log: Path) -> None:
        self.container = container
        self.runtime = runtime
        self.log = log
        self.helpers = HELPER_DIR

    def upload(self, source: Path, destination: str, *, contents: bool = False) -> None:
        files.upload(self.container, source, destination, contents=contents)

    def download(
        self,
        source: str,
        destination: Path,
        *,
        contents: bool = False,
        required: bool = True,
        secrets: tuple[str, ...] = (),
    ) -> bool:
        try:
            files.download(self.container, source, destination, contents=contents, secrets=secrets)
        except NotFound:
            if required:
                raise
            # Only a missing path is optional. API and archive failures propagate.
            self.log.parent.mkdir(parents=True, exist_ok=True)
            with self.log.open("a") as stream:
                stream.write(f"Optional artifact missing: {source}\n")
            return False
        return True

    def run(
        self,
        argv: list[str],
        *,
        # float, not int: callers pass timeouts straight from the config.
        timeout: float = 60,
        user: str = "root",
        environment: dict[str, str] | None = None,
        stdin: Path | None = None,
        stdout: Path | None = None,
        stderr: Path | None = None,
        log: Path | None = None,
        check: bool = True,
        secrets: tuple[str, ...] = (),
        workdir: str | None = None,
    ) -> CommandResult:
        """Execute argv, stream redacted output, and retain explicit process status.

        Credentials travel only in the exec environment. Do not log SDK request
        objects or raw SDK exceptions, which may contain that environment.
        """
        log = log or self.log
        token = uuid4().hex
        input_path = "/dev/null"
        status_path = f"/tmp/oracle-exec-{token}.json"
        if stdin is not None:
            input_path = f"/tmp/oracle-input-{token}"
            self.upload(stdin, input_path)
        command = [
            self.runtime.python,
            self.helpers + "/execute.py",
            str(timeout),
            input_path,
            status_path,
            *argv,
        ]
        try:
            with execution_watchdog(self.container, timeout) as watchdog_fired:
                supervisor_code = self._stream_command(
                    command,
                    user=user,
                    environment=environment,
                    stdout=stdout,
                    stderr=stderr,
                    log=log,
                    secrets=secrets,
                    workdir=workdir,
                )
                if watchdog_fired.is_set() or supervisor_code != 0:
                    raise RuntimeError(f"Container supervisor did not finish; see {log}")
                result = self._read_command_result(status_path)
        except (DockerException, RequestException):
            raise RuntimeError(f"Container execution transport failed; see {log}") from None
        if check and result.exit_code:
            raise RuntimeError(f"Container command failed (exit {result.exit_code}); see {log}")
        return result

    def _stream_command(
        self,
        command: list[str],
        *,
        user: str,
        environment: dict[str, str] | None,
        stdout: Path | None,
        stderr: Path | None,
        log: Path,
        secrets: tuple[str, ...],
        workdir: str | None,
    ) -> int:
        """Keep SDK stream handling separate from the command's lifecycle."""
        client = self.container.client
        if client is None:
            raise RuntimeError("Container is detached from its Docker client")
        api = client.api
        execution = api.exec_create(
            self.container.id,
            command,
            user=user,
            workdir=workdir or self.runtime.workdir,
            environment=environment or {},
        )
        with (
            RedactedOutput(stdout or log, secrets, append=stdout is None) as out,
            RedactedOutput(stderr or log, secrets, append=stderr is None) as err,
        ):
            for output, error in api.exec_start(execution["Id"], stream=True, demux=True):
                if output:
                    out.write(output)
                if error:
                    err.write(error)
        return api.exec_inspect(execution["Id"])["ExitCode"]

    def _read_command_result(self, status_path: str) -> CommandResult:
        # Supervisor completion and command completion are different outcomes.
        # Read the helper's status to distinguish a deadline from an ordinary exit.
        with TemporaryDirectory(prefix="oracle-exec-") as directory:
            status = Path(directory) / "status.json"
            self.download(status_path, status)
            return CommandResult(**read_json(status))

    def stop_background_processes(self) -> None:
        """Restart before capture to terminate even detached agent descendants."""
        self.container.stop(timeout=1)
        self.container.start()


@contextmanager
def open_sandbox(
    client: DockerClient,
    image: str,
    config: RunConfig | ClassificationConfig,
    profile: Profile,
    log: Path,
) -> Iterator[Sandbox]:
    """Create explicitly before starting, so failed starts are cleaned up too."""
    container = create_sandbox_container(client, image, config, profile)
    try:
        container.start()
        yield Sandbox(container, config.require_runtime(), log)
    finally:
        note = "Container cleanup also failed; inspect Docker for leftover containers."
        with preserve_failure(note):
            try:
                container.remove(force=True)
            except DockerException as exc:
                raise RuntimeError("Could not remove benchmark container") from exc


def create_sandbox_container(
    client: DockerClient,
    image: str,
    config: RunConfig | ClassificationConfig,
    profile: Profile,
    *,
    name: str | None = None,
    network_mode: str = "bridge",
    labels: dict[str, str] | None = None,
    container_user: str = "root",
) -> Container:
    """Create a stopped container with the benchmark's standard isolation limits."""
    runtime = config.require_runtime()
    limits = config.limits
    return client.containers.create(
        image,
        command=["-c", "sleep infinity"],
        entrypoint="/bin/bash",
        name=name or "oracle-bench-" + uuid4().hex[:16],
        detach=True,
        init=True,
        platform=runtime.platform,
        user=container_user,
        mem_limit=limits.memory,
        nano_cpus=int(limits.cpus * 1_000_000_000),
        security_opt=["no-new-privileges"],
        network_mode=network_mode,
        labels={"oracle-bench.profile": profile.value, **(labels or {})},
        # Every profile imports the same code, so the agent tests what evaluation runs.
        environment={"PYTHONPATH": runtime.pythonpath} if runtime.pythonpath else None,
        use_config_proxy=False,
    )


@contextmanager
def docker_client() -> Iterator[DockerClient]:
    """Own the SDK connection at the host workflow boundary."""
    client = None
    try:
        client = docker.from_env(timeout=60)
        if client.info()["OSType"] != "linux":
            raise RuntimeError("A Linux Docker daemon is required")
        yield client
    except DockerException:
        raise RuntimeError("Docker operation failed; check the daemon and stage log") from None
    finally:
        if client is not None:
            client.close()
