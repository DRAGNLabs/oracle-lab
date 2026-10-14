ARG HARNESS_IMAGE
ARG PROJECT_IMAGE

FROM ${HARNESS_IMAGE} AS harnesses
FROM ${PROJECT_IMAGE}

USER root

# SWE-rebench V2 images ship their task record, golden patch included.
RUN rm -rf /swebench_matterhorn \
    && if find / -xdev -name swebench_instance.json | grep .; then exit 1; fi

ARG PROJECT_PYTHON
ARG PROJECT_PYTHON_DIR
ARG PYTEST_VERSION

COPY --from=harnesses /usr/local/bin/node /opt/oracle-node/node
COPY --from=harnesses /opt/oracle-agent /opt/oracle-agent
COPY container_helpers/ /opt/oracle-bench/container-helpers/

ENV PATH="${PROJECT_PYTHON_DIR}:/opt/oracle-node:/opt/oracle-agent/node_modules/.bin:${PATH}"

RUN "${PROJECT_PYTHON}" -m pip install "pytest==${PYTEST_VERSION}"
RUN useradd --create-home --uid 10001 oracle
