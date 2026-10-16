FROM python:3.14-slim@sha256:a2b82f3c48559aa0a8446d9af49826b6e2b2016f4cd2afabfe6013ec53729170

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# The pinned base predates DSA-6549-1. Keep Debian signature verification enabled
# and upgrade only the existing runtime library to the exact security revision.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade liblzma5=5.8.1-1+deb13u2 \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb

# Bootstrap only the official, hash-verified wheel before resolving/building anything.
COPY container/requirements-installer.txt /tmp/requirements-installer.txt
RUN python -m pip --isolated --disable-pip-version-check install --no-cache-dir \
    --only-binary=:all: --no-deps --require-hashes -r /tmp/requirements-installer.txt
COPY container/requirements-build.txt /tmp/requirements-build.txt
RUN python -m pip --isolated --disable-pip-version-check install --no-cache-dir \
    --only-binary=:all: --require-hashes -r /tmp/requirements-build.txt
COPY pyproject.toml README.md LICENSE requirements.lock ./
COPY src ./src
RUN python -m pip --isolated --disable-pip-version-check install --no-cache-dir \
    --no-build-isolation --require-hashes -r requirements.lock \
    && python -m pip --isolated --disable-pip-version-check install --no-cache-dir \
        --no-build-isolation --no-deps . \
    && python -m pip --isolated --disable-pip-version-check check \
    && groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid 10001 --no-create-home gateway

USER 10001:10001
EXPOSE 8080

# Session URLs are bearer credentials: do not write request paths to access logs.
# One worker is required while playback sessions are stored in memory.
CMD ["uvicorn", "epg_voice_gateway.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
