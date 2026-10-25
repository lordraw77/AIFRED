# syntax=docker/dockerfile:1
# AIFRED image: two stages, hash-pinned dependencies, non-root, no build tools in the final image.
# Base image pinned by digest (python 3.13.17, Debian 12). To update: pull the tag, copy its digest here,
# regenerate requirements.lock with `make lock`, then run the tests.
ARG PYTHON_IMAGE=python:3.13-slim-bookworm@sha256:e32be0e55814a08977501ca863271d9bd02443710db8f2afaf8372e2c4508530

FROM ${PYTHON_IMAGE} AS build
ENV PIP_DISABLE_PIP_VERSION_CHECK=1 PIP_NO_CACHE_DIR=1
RUN python -m venv /opt/venv
COPY requirements.lock /tmp/requirements.lock
# --require-hashes refuses any package that is not in the lock file with a matching hash
RUN /opt/venv/bin/pip install --require-hashes --no-deps -r /tmp/requirements.lock

FROM ${PYTHON_IMAGE}
ARG VERSION=0.1.0.dev0
LABEL org.opencontainers.image.title="AIFRED" \
      org.opencontainers.image.description="A lean, self-hosted personal AI agent" \
      org.opencontainers.image.version="${VERSION}"

RUN groupadd --gid 10001 aifred \
 && useradd --uid 10001 --gid 10001 --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin aifred \
 && install -d -o 10001 -g 10001 -m 0700 /data

ENV AIFRED_DATA_DIR=/data \
    AIFRED_API_HOST=0.0.0.0 \
    PATH=/opt/venv/bin:$PATH \
    PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --from=build /opt/venv /opt/venv
# the code is owned by root and read-only for the service
COPY aifred /app/aifred

WORKDIR /app
USER 10001:10001
VOLUME /data
EXPOSE 8700

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8700/health', timeout=3).status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "aifred"]
CMD ["serve"]
