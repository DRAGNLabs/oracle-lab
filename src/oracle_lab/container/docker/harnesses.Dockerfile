ARG NODE_IMAGE
FROM ${NODE_IMAGE}

WORKDIR /opt/oracle-agent
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

ENV PATH="/opt/oracle-agent/node_modules/.bin:${PATH}"
