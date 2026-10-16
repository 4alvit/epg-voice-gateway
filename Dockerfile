FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md LICENSE requirements.lock ./
COPY src ./src
RUN pip install --no-cache-dir --require-hashes -r requirements.lock \
    && pip install --no-cache-dir --no-deps . \
    && groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid 10001 --no-create-home gateway

USER 10001:10001
EXPOSE 8080

# Session URLs are bearer credentials: do not write request paths to access logs.
# One worker is required while playback sessions are stored in memory.
CMD ["uvicorn", "epg_voice_gateway.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1", "--no-access-log"]
