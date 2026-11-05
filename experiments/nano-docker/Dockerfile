ARG BASE_IMAGE
FROM ${BASE_IMAGE} AS runtime
ENV PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD=1
RUN apt-get update && apt-get install -y --no-install-recommends python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && npm install --global npm@11.9.0 --ignore-scripts --no-audit --no-fund

FROM runtime AS experiment
ARG DEPENDENCY_NONCE
COPY prepared /opt/prepared
COPY tools /opt/tools
RUN test -n "$DEPENDENCY_NONCE" && python3 /opt/tools/nano_docker_worker.py prepare
CMD ["python3", "-c", "import time; time.sleep(1800)"]
