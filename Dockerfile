FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
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
