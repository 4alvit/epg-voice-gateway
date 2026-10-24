"""Exercise the installed image without network access or operator credentials."""

import importlib.metadata
import importlib.util
import os
import shutil
import tomllib
from pathlib import Path

from cryptography.hazmat.backends.openssl.backend import backend
from fastapi.testclient import TestClient

from epg_voice_gateway.app import create_app
from epg_voice_gateway.catalog import parse_playlist
from epg_voice_gateway.config import Settings
from epg_voice_gateway.errors import InvalidSource

assert os.getuid() == 10001, "The image must run as its unprivileged service user"
assert importlib.util.find_spec("pip") is None, "The runtime must not ship pip's vendors"
assert importlib.util.find_spec("ensurepip") is None, "The bundled installer must be absent"
assert not any(shutil.which(name) for name in ("pip", "pip3", "pip3.14"))
assert importlib.metadata.version("cryptography") == "50.0.2"
assert backend.openssl_version_number() >= 0x40000030, (
    "The installed crypto wheel must include OpenSSL 4.0.3 or newer"
)
expected_version = tomllib.loads(Path("/app/pyproject.toml").read_text())["project"]["version"]
assert importlib.metadata.version("epg-voice-gateway") == expected_version
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
print("Installed container: unprivileged user, no bundled installer, startup and auth passed")

# Exercise the installed parser on the same mixed-source shape as production,
# using only synthetic URLs and without making any network request.
mixed = (
    '#EXTM3U\n#EXTINF:-1,Direct stream\nhttps://direct.example.test/ace/demo\n'
    '/channel/orphan/index.m3u8\n'
    '#EXTINF:-1 tvg-id="supported",Supported channel\n/channel/demo/index.m3u8\n'
)
channels = parse_playlist(mixed, "https://media.example.test")
assert len(channels) == 1 and channels[0].epg_id == "supported"
assert channels[0].stream_url == "https://media.example.test/channel/demo/index.m3u8"
try:
    parse_playlist(
        '#EXTM3U\n#EXTINF:-1,Direct stream\nhttps://direct.example.test/ace/demo\n',
        "https://media.example.test",
    )
except InvalidSource:
    pass
else:
    raise AssertionError("An entirely unsupported playlist must remain unavailable")
print("Installed mixed playlist: only supported HLS channels accepted; empty result rejected")
