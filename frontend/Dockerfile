# Ubuntu rather than node:alpine so the whole compose stack shares one base image
# and builds in environments with no access to other registry namespaces.
# Ubuntu 26.04 packages Node 22, comfortably above Next 14's floor of 18.17.
FROM ubuntu:26.04 AS build

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend .
# NEXT_PUBLIC_* is inlined at build time, so the API base is a build arg, not a
# runtime variable. Change it and the image has to be rebuilt.
ARG NEXT_PUBLIC_API_BASE=http://localhost:8000
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
RUN npm run build

FROM ubuntu:26.04 AS runtime
ENV DEBIAN_FRONTEND=noninteractive NODE_ENV=production PORT=3000 HOSTNAME=0.0.0.0
RUN apt-get update && apt-get install -y --no-install-recommends nodejs ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /usr/sbin/nologin app

WORKDIR /app
# `output: "standalone"` emits a server bundle with its own minimal node_modules.
COPY --from=build --chown=app:app /app/.next/standalone ./
COPY --from=build --chown=app:app /app/.next/static ./.next/static
COPY --from=build --chown=app:app /app/public ./public
USER app
EXPOSE 3000
CMD ["node", "server.js"]
