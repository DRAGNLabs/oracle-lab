"""Build checked-in image recipes and retain their exact inputs with the run."""

import json
import shutil
from collections.abc import Iterable
from pathlib import Path
from typing import TextIO

from docker import DockerClient
from docker.errors import ImageNotFound
from docker.models.images import Image

from oracle_lab.config import HARNESS_VERSIONS, ClassificationConfig, RunConfig
from oracle_lab.container.lifecycle import setup_deadline
from oracle_lab.io import digest, write_json


def require_saved_image(client: DockerClient, image: str, action: str) -> None:
    """Every later stage reuses the run's own image, so refuse rather than build another."""
    try:
        client.images.get(image)
    except ImageNotFound:
        raise RuntimeError(f"Saved runtime image is missing. Restore it before {action}.") from None


def image_id(image: Image) -> str:
    """The SDK types an image ID as optional; one we just resolved always has it."""
    if image.id is None:
        raise RuntimeError("Docker returned an image without an ID")
    return image.id


def inspect_harness_versions(client: DockerClient, image: Image) -> dict[str, str]:
    """Read each installed CLI's own version string from the final runtime."""
    observed = {}
    # Each CLI's binary is named after its harness. If an npm package ever renames
    # its binary, this probe fails at build time.
    for harness in HARNESS_VERSIONS:
        output = client.containers.run(
            image_id(image),
            [harness, "--version"],
            user="10001:10001",
            environment={"HOME": "/home/oracle"},
            network_disabled=True,
            remove=True,
        )
        observed[harness] = output.decode(errors="replace").strip()
    return observed


def resolve_image(client: DockerClient, reference: str, platform: str, log: TextIO) -> Image:
    """Reuse a local parent image, pulling only when it is absent."""
    try:
        image = client.images.get(reference)
    except ImageNotFound:
        events = client.api.pull(reference, platform=platform, stream=True, decode=True)
        record_events(events, log, operation="pull")
        image = client.images.get(reference)
    return image


def image_tag(
    context: Path, recipe: str, arguments: dict[str, str], platform: str
) -> tuple[str, dict]:
    """Name an image by hashing everything that could change it.

    Also returns what went into the hash, which the caller saves for provenance.
    """
    # Hash every file in the context, not just the Dockerfile. Editing a helper
    # script has to produce a new tag too.
    inputs = {
        "platform": platform,
        "arguments": arguments,
        "recipe": recipe,
        "files": {
            path.relative_to(context).as_posix(): digest(path.read_bytes())
            for path in sorted(context.rglob("*"))
            if path.is_file()
        },
    }
    identity = digest(json.dumps(inputs, sort_keys=True).encode())[:24]
    return "oracle-lab/" + recipe.split(".")[0] + ":" + identity, inputs


def record_events(events: Iterable[dict], log: TextIO, *, operation: str) -> None:
    """Persist SDK evidence before interpreting success or failure."""
    for event in events:
        log.write(json.dumps(event) + "\n")
        log.flush()
        if "error" in event:
            raise RuntimeError(f"Image {operation} failed; see {log.name}")


def prepare_contexts(build: Path) -> tuple[Path, Path]:
    """Set up the two directories we hand to `docker build`, one per image.

    Docker uploads a build directory whole and any file in it can be copied into the
    image, so each gets only its own files and nothing else from the run.
    """
    assets = Path(__file__).parent
    harness_context = build / "harnesses"
    runtime_context = build / "runtime"
    harness_context.mkdir(parents=True)
    runtime_context.mkdir()

    # First image: node plus the three agent CLIs, at the versions in the lockfile.
    for name in ("harnesses.Dockerfile", "package.json", "package-lock.json"):
        shutil.copyfile(assets / "docker" / name, harness_context / name)

    # Second image: the repo's own SWE-bench image, with those CLIs and the scripts
    # that run inside containers added on top.
    shutil.copyfile(assets / "docker/runtime.Dockerfile", runtime_context / "runtime.Dockerfile")
    helpers = runtime_context / "container_helpers"
    helpers.mkdir()
    for helper in sorted((assets / "helpers").glob("*.py")):
        shutil.copyfile(helper, helpers / helper.name)
    return harness_context, runtime_context


def runtime_arguments(
    config: RunConfig | ClassificationConfig, source: Image, harnesses: Image
) -> dict[str, str]:
    runtime = config.require_runtime()
    return {
        "HARNESS_IMAGE": image_id(harnesses),
        "PROJECT_IMAGE": image_id(source),
        "PROJECT_PYTHON": runtime.python,
        "PROJECT_PYTHON_DIR": str(Path(runtime.python).parent),
        "PROJECT_WORKDIR": runtime.workdir,
        "PYTEST_VERSION": runtime.pytest_version or config.toolchain.pytest_version,
    }


def prepare_image(
    client: DockerClient,
    config: RunConfig | ClassificationConfig,
    build: Path,
) -> str:
    """Build the one image every stage of the run uses, and return its ID.

    Four images are involved but only the last is returned: two pulled, then two
    built on top of them.
    """
    runtime = config.require_runtime()
    harness_context, runtime_context = prepare_contexts(build)
    installed_harnesses = config.toolchain.harnesses
    lock_sha256 = digest((harness_context / "package-lock.json").read_bytes())
    provenance = {
        "installed_harnesses": installed_harnesses,
        "lockfile_sha256": lock_sha256,
    }

    def record(name: str, image: Image, reference: str) -> None:
        provenance[name] = {
            "reference": reference,
            "id": image.id,
            "digests": image.attrs.get("RepoDigests") or [],
        }
        write_json(build / "images.json", provenance)

    # Save unresolved inputs too, so a failed first pull still has provenance.
    write_json(
        build / "requested.json",
        {
            "source": runtime.image,
            "node": config.toolchain.node_image,
            "platform": runtime.platform,
            "installed_harnesses": installed_harnesses,
            "lockfile_sha256": lock_sha256,
        },
    )
    # Each step gets its own deadline because a pull or build can stall silently.
    with (build / "output.log").open("a") as log:
        # 1. Pull the repo's SWE-bench image: the project already installed at this
        #    instance's commit. This is what the tests will run against.
        with setup_deadline(config.limits.setup_seconds):
            source = resolve_image(client, runtime.image, runtime.platform, log)
            record("source", source, runtime.image)

        # 2. Pull a plain node image. Only needed to install the agent CLIs.
        with setup_deadline(config.limits.setup_seconds):
            node = resolve_image(client, config.toolchain.node_image, runtime.platform, log)
            record("node", node, config.toolchain.node_image)

        # 3. Build node + the three agent CLIs at the lockfile's versions. Kept
        #    separate from the project image so it is reused across instances.
        with setup_deadline(config.limits.setup_seconds):
            harnesses = build_image(
                client,
                harness_context,
                "harnesses.Dockerfile",
                {"NODE_IMAGE": image_id(node)},
                runtime.platform,
                log,
            )
            record("harnesses", harnesses, image_id(harnesses))

        # 4. Build the image the run actually uses: the project image from step 1,
        #    plus the CLIs from step 3, the in-container scripts, and pinned pytest.
        with setup_deadline(config.limits.setup_seconds):
            prepared = build_image(
                client,
                runtime_context,
                "runtime.Dockerfile",
                runtime_arguments(config, source, harnesses),
                runtime.platform,
                log,
            )
            record("runtime", prepared, image_id(prepared))

        # Ask each CLI its own version, rather than trusting the pins we asked for.
        provenance["observed_harness_versions"] = inspect_harness_versions(client, prepared)
        write_json(build / "images.json", provenance)
    return image_id(prepared)


def build_image(
    client: DockerClient,
    context: Path,
    recipe: str,
    arguments: dict[str, str],
    platform: str,
    log: TextIO,
) -> Image:
    """Save build inputs, reuse a matching image, or build the checked-in recipe."""
    tag, inputs = image_tag(context, recipe, arguments, platform)
    write_json(context.parent / recipe.replace(".Dockerfile", ".inputs.json"), inputs)
    try:
        return client.images.get(tag)
    except ImageNotFound:
        pass
    # The low-level SDK build method streams evidence even when a build fails.
    events = client.api.build(
        path=str(context),
        dockerfile=recipe,
        tag=tag,
        buildargs=arguments,
        platform=platform,
        rm=True,
        forcerm=True,
        decode=True,
    )
    record_events(events, log, operation="build")
    return client.images.get(tag)
