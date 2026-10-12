"""Blinded, interactive workspaces for human generated-test raters."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

from docker import DockerClient
from docker.errors import DockerException, NotFound
from docker.models.containers import Container

from oracle_bench.config import RunConfig, load_config
from oracle_bench.container.images import require_saved_image
from oracle_bench.container.sandbox import (
    ORACLE_USER,
    Profile,
    Sandbox,
    create_sandbox_container,
    docker_client,
)
from oracle_bench.generation import verify_bundle
from oracle_bench.io import digest, read_json, require_files, utc_stamp, write_json
from oracle_bench.judge.contracts import (
    RATER_ID,
    HumanWorkspaceMetadata,
    complete_human_judgment,
)
from oracle_bench.judge.workspace import JUDGE_ROOT, WorkspaceSpec, build_judge_workspace
from oracle_bench.paths import RunPaths
from oracle_bench.results import read_evaluation_result, read_instance_record

RUN_DIRECTORY_LABEL = "oracle-bench.run-directory"
RATER_LABEL = "oracle-bench.rater"
HUMAN_JUDGMENT_TEMPLATE = Path(__file__).resolve().parent / "templates/human-judgment.json"


def create_human_workspace(config: RunConfig, paths: RunPaths, rater: str) -> str:
    """Reconstruct one blinded workspace and leave its container running."""
    if not RATER_ID.fullmatch(rater):
        raise ValueError("Rater ID must use 1-64 ASCII letters, digits, underscores, or hyphens")
    config.require_judge()
    require_files(
        [paths.runtime, paths.instance, paths.results, paths.judge.workspace_spec],
        "Human workspace inputs",
    )

    expected_spec = WorkspaceSpec.model_validate(read_json(paths.judge.workspace_spec))
    image = read_json(paths.runtime)["image"]
    name = _container_name(paths.root, rater)

    with docker_client() as client:
        require_saved_image(client, image, "opening a rater workspace")
        container = create_sandbox_container(
            client,
            image,
            config,
            Profile.HUMAN_JUDGE,
            name=name,
            network_mode="none",
            container_user=ORACLE_USER,
            labels={RUN_DIRECTORY_LABEL: str(paths.root), RATER_LABEL: rater},
        )
        try:
            container.start()
            sandbox = Sandbox(container, config.require_runtime(), paths.judge.human.log)
            actual_spec = build_judge_workspace(
                sandbox,
                config,
                paths,
                image,
                read_instance_record(paths.instance),
                read_evaluation_result(paths.results),
                verify_bundle(paths),
                persist=False,
            )
            if actual_spec != expected_spec:
                raise ValueError("Reconstructed human workspace does not match workspace-spec.json")
            workspace_file = _prepare_human_files(sandbox, paths, name, rater)
        except BaseException as error:
            try:
                container.remove(force=True)
            except DockerException:
                error.add_note(
                    f"Human workspace cleanup also failed; remove container {name} manually."
                )
            raise

    return "\n".join(
        [
            f"Container: {name}",
            f"Shell: docker exec -it -u {ORACLE_USER} {name} /bin/bash",
            "VS Code: Dev Containers: Attach to Running Container, then open "
            f"{JUDGE_ROOT}/human-judge.code-workspace",
            f"Workspace file: {workspace_file}",
        ]
    )


def collect_human_judgment(container_name: str, *, archive_existing: bool = False) -> Path:
    """Validate and save a human rating without stopping its workspace."""
    with docker_client() as client:
        container, labels = _human_container(client, container_name)
        rater = labels[RATER_LABEL]
        paths = RunPaths.open(Path(labels[RUN_DIRECTORY_LABEL]))
        config = load_config(paths.config, resolved=True)

        # Read the rater's draft out of the container; nothing is written back.
        with TemporaryDirectory(prefix="oracle-human-judgment-") as directory:
            draft = Path(directory) / "judgment.json"
            sandbox = Sandbox(container, config.require_runtime(), Path(directory) / "download.log")
            try:
                sandbox.download(JUDGE_ROOT + "/output/judgment.json", draft)
            except NotFound:
                raise FileNotFoundError(
                    f"No draft exists at {JUDGE_ROOT}/output/judgment.json"
                ) from None
            raw = draft.read_text()

    try:
        judgment = complete_human_judgment(raw)
    except ValueError as error:
        raise ValueError(f"Human judgment is invalid: {error}") from error

    # The saved metadata names the rater, so a relabelled container cannot write
    # over somebody else's rating. This is also what checks the rater ID itself.
    metadata_path = paths.judge.human.workspaces / f"{container_name}.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Human workspace metadata is missing: {metadata_path}")
    metadata = HumanWorkspaceMetadata.model_validate(read_json(metadata_path))
    if metadata.rater != rater or metadata.container != container_name:
        raise ValueError("Human workspace metadata does not match the container")

    destination = paths.judge.human.judgments / rater
    if destination.exists():
        if not archive_existing:
            raise FileExistsError(
                f"A judgment already exists for {rater}; use --archive-existing to replace it"
            )
        # Not archive_files: a rater's directory is renamed to the stamp, not moved inside it.
        archive = paths.judge.human.history / rater / utc_stamp()
        archive.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(destination, archive)
    destination.mkdir(parents=True)
    (destination / "judgment.raw.json").write_text(raw)
    write_json(destination / "judgment.json", judgment.model_dump())
    write_json(destination / "provenance.json", metadata.model_dump())
    return destination / "judgment.json"


def remove_human_workspace(container_name: str) -> str:
    """Remove only a container carrying the human-workspace profile label."""
    with docker_client() as client:
        container, _ = _human_container(client, container_name)
        container.remove(force=True)
    return container_name


def _prepare_human_files(
    sandbox: Sandbox, paths: RunPaths, container_name: str, rater: str
) -> Path:
    workspace = {
        "folders": [{"name": "Generated-test judgment", "path": JUDGE_ROOT}],
        "settings": {"files.exclude": {"**/.git": True}},
    }
    host_directory = paths.judge.human.workspaces
    host_directory.mkdir(parents=True, exist_ok=True)
    workspace_file = host_directory / f"{container_name}.code-workspace"
    write_json(workspace_file, workspace)
    draft_file = host_directory / f"{container_name}.judgment-template.json"
    shutil.copyfile(HUMAN_JUDGMENT_TEMPLATE, draft_file)
    sandbox.upload(workspace_file, JUDGE_ROOT + "/human-judge.code-workspace")
    sandbox.run(["mkdir", "-p", JUDGE_ROOT + "/output"])
    sandbox.upload(draft_file, JUDGE_ROOT + "/output/judgment.json")
    # Worktrees share Git metadata with the image checkout. Freeze both so an
    # interactive editor cannot alter source files or the shared object store.
    sandbox.run(["chmod", "-R", "a-w", JUDGE_ROOT, sandbox.runtime.workdir])
    sandbox.run(["chown", "-R", ORACLE_USER, JUDGE_ROOT + "/output"])
    sandbox.run(["chmod", "700", JUDGE_ROOT + "/output"])
    sandbox.run(["chmod", "600", JUDGE_ROOT + "/output/judgment.json"])
    metadata = HumanWorkspaceMetadata(
        container=container_name,
        rater=rater,
        rubric_sha256=digest(paths.judge.rubric.read_bytes()),
        workspace_spec_sha256=digest(paths.judge.workspace_spec.read_bytes()),
    )
    write_json(host_directory / f"{container_name}.json", metadata.model_dump())
    return workspace_file


def _human_container(client: DockerClient, name: str) -> tuple[Container, dict[str, str]]:
    """Return the container and its labels, refusing anything that is not a rater box."""
    try:
        container = client.containers.get(name)
    except NotFound:
        raise FileNotFoundError(f"Human judge container does not exist: {name}") from None
    labels = container.attrs.get("Config", {}).get("Labels", {}) or {}
    if labels.get("oracle-bench.profile") != Profile.HUMAN_JUDGE.value:
        raise ValueError(f"Refusing to operate on non-human-judge container: {name}")
    if RUN_DIRECTORY_LABEL not in labels or RATER_LABEL not in labels:
        raise ValueError(f"Human judge container is missing provenance labels: {name}")
    return container, labels


def _container_name(run_dir: Path, rater: str) -> str:
    run_hash = hashlib.sha256(str(run_dir).encode()).hexdigest()[:10]
    rater_hash = hashlib.sha256(rater.encode()).hexdigest()[:8]
    fixed_length = len("oracle-judge---") + len(run_hash) + len(rater_hash)
    readable_rater = rater[: 63 - fixed_length]
    return f"oracle-judge-{run_hash}-{readable_rater}-{rater_hash}"
