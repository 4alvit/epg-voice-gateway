"""Settings validators for reachable deployment misconfiguration."""

import pytest
from pydantic import ValidationError

from epg_voice_gateway.config import Settings

TOKEN = "synthetic-settings-boundary-api-token-32b"
SIGNING = "synthetic-settings-boundary-signing-key"


def settings(**overrides):
    values = dict(
        api_token=TOKEN,
        signing_key=SIGNING,
        hls_proxy_url="http://proxy.example.test:8080",
        media_base_url="https://media.example.test",
        xmltv_urls=["https://guide.example.test/guide.xml"],
    )
    values.update(overrides)
    return Settings(**values)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"xmltv_urls": ["https://user:secret@guide.example.test/guide.xml"]},
            "Do not embed HTTP basic-auth credentials",
        ),
        ({"xmltv_urls": ["ftp://guide.example.test/guide.xml"]}, "XMLTV sources must be"),
        ({"xmltv_urls": ["https://guide.example.test/guide.xml#fragment"]}, "XMLTV sources must be"),
        ({"playlist_path": "/other.m3u8"}, "Use an existing HLS proxy catalogue endpoint"),
        ({"database_path": "relative.sqlite3"}, "The database path must be absolute"),
        ({"database_path": "/tmp/catalog.sqlite3/"}, "The database path must be absolute"),
        ({"api_token": "too-short"}, "At least 32 bytes of secret material are required"),
        ({"signing_key": "short"}, "At least 32 bytes of secret material are required"),
        (
            {"refresh_seconds": 120, "max_stale_seconds": 60},
            "The stale-data window must cover the refresh interval",
        ),
        ({"ha_url": "http://ha.example.test", "ha_token": ""}, "Configure both Home Assistant"),
        ({"ha_url": "", "ha_token": "x" * 40}, "Configure both Home Assistant"),
    ],
)
def test_settings_reject_reachable_misconfiguration(overrides, message):
    with pytest.raises(ValidationError, match=message):
        settings(**overrides)


def test_settings_accept_memory_database_and_known_playlist_paths():
    for path in (":memory:", "/tmp/catalog.sqlite3"):
        assert settings(database_path=path).database_path == path
    for playlist in ("/playlist.m3u8", "/m3u8"):
        assert settings(playlist_path=playlist).playlist_path == playlist
