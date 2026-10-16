"""Exercise the installed image without network access or operator credentials."""

import importlib.metadata
import os

from cryptography.hazmat.backends.openssl.backend import backend
from fastapi.testclient import TestClient

from epg_voice_gateway.app import create_app
from epg_voice_gateway.config import Settings

assert os.getuid() == 10001, "The image must run as its unprivileged service user"
assert importlib.metadata.version("pip") == "26.2.1"
assert importlib.metadata.version("cryptography") == "50.0.2"
assert backend.openssl_version_number() >= 0x40000030, (
    "The installed crypto wheel must include OpenSSL 4.0.3 or newer"
)
assert importlib.metadata.version("epg-voice-gateway") == "0.1.0"
print(f"Installed cryptography native runtime: {backend.openssl_version_text()}")

settings = Settings(
    api_token="synthetic-container-test-token-123456789",
    signing_key="synthetic-container-signing-key-123456789",
    hls_proxy_url="https://media.example.test",
    media_base_url="https://gateway.example.test",
    xmltv_urls=["https://guide.example.test/guide.xml"],
    require_https=True,
)
with TestClient(create_app(settings, start_refresh=False)) as client:
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").status_code == 503
    assert client.post("/v1/resolve", json={"query": "News"}).status_code == 401
print("Installed container: private user, pinned installer, startup and auth passed")
