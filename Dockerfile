FROM python:3.15-slim@sha256:8c13a3f37a6a5b71498228e902c4b9f51c1f7900d49fabf71f9a650c6120e0d0 AS build-base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
# Upgrade the pinned base for DSA-6549-1 (XZ), DSA-6607-1 (APT), and DSA-6613-1 (zlib).
# Disable download retries while the old APT client bootstraps its own fix;
# Debian signature verification stays enabled throughout.
RUN apt-get -o Acquire::Retries=0 update \
    && apt-get -o Acquire::Retries=0 install --yes --no-install-recommends --only-upgrade \
        apt=3.0.4 libapt-pkg7.0=3.0.4 liblzma5=5.8.1-1+deb13u2 \
        zlib1g=1:1.3.dfsg+really1.3.1-1+deb13u1 \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb

# Bootstrap only the official, hash-verified wheel before resolving/building anything.
COPY container/requirements-installer.txt /tmp/requirements-installer.txt
RUN python -m pip --isolated --disable-pip-version-check install --no-cache-dir \
    --only-binary=:all: --no-deps --require-hashes -r /tmp/requirements-installer.txt
COPY container/requirements-build.txt /tmp/requirements-build.txt
RUN python -m pip --isolated --disable-pip-version-check install --no-cache-dir \
    --only-binary=:all: --require-hashes -r /tmp/requirements-build.txt
# Export from the single reviewed lock without network resolution. Keep uv out
# of the runtime image; dependency PRs only need to update uv.lock.
FROM build-base AS lock-export
COPY container/requirements-export.txt /tmp/requirements-export.txt
RUN python -m pip --isolated --disable-pip-version-check install --no-cache-dir \
    --only-binary=:all: --no-deps --require-hashes -r /tmp/requirements-export.txt
COPY pyproject.toml uv.lock ./
RUN uv export --locked --offline --no-dev --no-emit-project -o /requirements.lock

FROM build-base AS runtime
COPY --from=lock-export /requirements.lock ./requirements.lock
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
# No runtime operation installs packages. Remove both copies of the installer,
# including ensurepip's bundled wheel and their otherwise vulnerable vendors.
RUN python -m pip --isolated --disable-pip-version-check install --no-cache-dir \
    --no-build-isolation --require-hashes -r requirements.lock \
    && python -m pip --isolated --disable-pip-version-check install --no-cache-dir \
        --no-build-isolation --no-deps . \
    && python -m pip --isolated --disable-pip-version-check check \
    && python -m pip --isolated --disable-pip-version-check uninstall --yes pip \
    && python -c 'import shutil, sysconfig; from pathlib import Path; shutil.rmtree(Path(sysconfig.get_path("stdlib")) / "ensurepip")' \
    && groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid 10001 --no-create-home gateway

USER 10001:10001
EXPOSE 8080

# Session URLs are bearer credentials: do not write request paths to access logs.
# One worker is required while playback sessions are stored in memory.
CMD ["uvicorn", "epg_voice_gateway.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
