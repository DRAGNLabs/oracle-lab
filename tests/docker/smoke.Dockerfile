# Exercise SDK multistage builds with an already-present runtime; no downloads.
ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS source
FROM ${BASE_IMAGE}
ARG SMOKE_ID
LABEL oracle-bench.smoke="${SMOKE_ID}"
COPY --from=source /bin/bash /tmp/oracle-smoke-bash
COPY container_helpers/ /opt/oracle-bench/container-helpers/
