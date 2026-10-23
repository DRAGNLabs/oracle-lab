# Internal image recipes

[`prepare_image`](../images.py) uses these Dockerfiles. Users do not build them
directly.

The configured project image must already contain the repository checkout, its
dependencies, and the configured Python interpreter. Image preparation then:

1. builds one toolchain image with the pinned Codex, Claude Code, and OpenCode
   CLIs;
2. extends the project image with those CLIs, pytest, and Oracle Lab's
   container helpers.

Every container a run starts uses the result: generation, reference, evaluation,
and the judge. Each run records the image inputs and resolved IDs. Tags are
derived from content, so an identical build reuses the local Docker cache.

## Trust boundary

Build contexts contain only these checked-in recipes and the public container
helpers. These never enter an image build: dataset records, repair patches,
private reference tests, generated tests, credentials, and the host run
directory.

Credentials and task patches go only to the disposable containers that need
them. Generation, evaluation, reference, and judge containers use Docker's
outbound bridge network, so tests that need the network can run, and so can the
judge's model call.

## Project image requirements

The project image must provide:

- Linux with a glibc compatible with the configured Node image;
- bash, Git, `useradd`, and `chown`;
- Python 3.9 or newer with pip;
- a free UID and GID `10001` for the non-root `oracle` user.

Oracle Lab does not build project environments. The project image must already
satisfy the repository's native and Python dependencies.

The container helpers, the judge's included, are installed at
`/opt/oracle-lab/container-helpers/`. The CLI versions come from the checked-in
`package.json` and its lockfile, and are recorded in the run's resolved
toolchain. Test-tool versions also come from the resolved configuration. Models
and credentials are inputs to each run, not image build arguments.
