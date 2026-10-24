"""Pure catalogue parser contracts. No clients, databases or playback are started."""
import gzip
from datetime import UTC, datetime
from urllib.parse import urlsplit

from epg_voice_gateway.catalog import parse_playlist, parse_xmltv
from epg_voice_gateway.errors import InvalidSource

ORIGIN = "https://proxy.example.test"
NOW = datetime(2026, 10, 21, 12, tzinfo=UTC)


def fuzz_playlist(data: bytes) -> None:
    if len(data) > 16384:
        return
    text = data.decode("utf-8", errors="replace")
    try:
        channels = parse_playlist(text, ORIGIN)
    except InvalidSource:
        return
    assert channels
    assert len({channel.channel_id for channel in channels}) == len(channels)
    assert parse_playlist(text, ORIGIN) == channels
    for channel in channels:
        parsed = urlsplit(channel.stream_url)
        assert (parsed.scheme, parsed.netloc) == ("https", "proxy.example.test")
        assert parsed.username is None and not parsed.fragment
        assert parsed.path.startswith("/channel/")
        assert abs(channel.shift_seconds) <= 86400


def fuzz_xmltv(data: bytes) -> None:
    if len(data) > 16384:
        return
    try:
        result = parse_xmltv(data, NOW, max_bytes=65536)
    except InvalidSource:
        return
    names, programmes = result
    assert programmes
    assert all(isinstance(key, str) for key in names)
    assert all(programme.title and programme.starts_at < programme.ends_at for programme in programmes)
    assert parse_xmltv(data, NOW, max_bytes=65536) == result
    if not data.startswith(b"\x1f\x8b"):
        assert parse_xmltv(gzip.compress(data, mtime=0), NOW, max_bytes=65536) == result
