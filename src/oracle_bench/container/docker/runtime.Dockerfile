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
ARG PROJECT_WORKDIR
ARG PYTEST_VERSION

# The source adapter infers both paths; stop here if the image disagrees.
RUN test -x "${PROJECT_PYTHON}" || { echo "No Python at ${PROJECT_PYTHON}" >&2; exit 1; }
RUN test -d "${PROJECT_WORKDIR}" || { echo "No repository at ${PROJECT_WORKDIR}" >&2; exit 1; }

COPY --from=harnesses /usr/local/bin/node /opt/oracle-node/node
COPY --from=harnesses /opt/oracle-agent /opt/oracle-agent
COPY container_helpers/ /opt/oracle-bench/container-helpers/

ENV PATH="${PROJECT_PYTHON_DIR}:/opt/oracle-node:/opt/oracle-agent/node_modules/.bin:${PATH}"
# SWE-rebench V2 images point these at a root-owned /workspace. Tools treat an
# empty value as unset, so they use the running user's home, as in SWE-bench images.
ENV XDG_CACHE_HOME= XDG_CONFIG_HOME= XDG_DATA_HOME= PIP_CACHE_DIR= PYTHONPYCACHEPREFIX=

RUN "${PROJECT_PYTHON}" -m pip install "pytest==${PYTEST_VERSION}"
RUN useradd --create-home --uid 10001 oracle
