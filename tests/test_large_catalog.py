"""Small fixtures exercise bounded parsing used for large XMLTV sources."""

import gzip
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from epg_voice_gateway.catalog import Catalog, parse_xmltv
from epg_voice_gateway.config import Settings
from epg_voice_gateway.errors import CatalogUnavailable, InvalidSource
from epg_voice_gateway.models import Channel

NOW = datetime(2026, 9, 17, 12, tzinfo=UTC)


def programme(identifier="kids", title="Current show", start=None, stop=None):
    start = start or NOW - timedelta(minutes=10)
    stop = stop or NOW + timedelta(minutes=10)
    return f'<programme channel="{identifier}" start="{start:%Y%m%d%H%M%S %z}" stop="{stop:%Y%m%d%H%M%S %z}"><title>{title}</title></programme>'


def guide(programmes=None):
    programmes = programmes if programmes is not None else programme()
    return ('<tv><channel id="kids"><display-name>Kids</display-name></channel>'
            '<channel id="other"><display-name>Other</display-name></channel>'
            + programmes + '</tv>').encode()


def channel(identifier="kids", name="Kids"):
    return Channel("synthetic-channel-id", name, identifier, name, "http://proxy.test/channel/kids.m3u8", 0)


def settings(**values):
    return Settings(
        api_token="a" * 40, signing_key="s" * 40,
        hls_proxy_url="http://proxy.test", media_base_url="https://media.example.test",
        xmltv_urls=["http://guide.test/guide.xml.gz"], **values,
    )


@pytest.mark.parametrize("compressed", [False, True])
def test_exact_decoded_limit_succeeds_and_one_byte_smaller_fails(compressed):
    xml = guide()
    data = gzip.compress(xml) if compressed else xml
    assert parse_xmltv(data, NOW, max_bytes=len(xml))[1][0].title == "Current show"
    with pytest.raises(InvalidSource):
        parse_xmltv(data, NOW, max_bytes=len(xml) - 1)


def test_gzip_parser_reads_incrementally_without_full_expansion(monkeypatch):
    future = programme(title="Far future", start=NOW + timedelta(days=30), stop=NOW + timedelta(days=31))
    xml = guide(programme() + future * 500)
    data = gzip.compress(xml)
    reads = []
    original = gzip.GzipFile

    class ObservedGzip(original):
        def read(self, size=-1):
            reads.append(size)
            assert 0 < size <= 65536
            return super().read(size)

    monkeypatch.setattr(gzip, "GzipFile", ObservedGzip)
    names, programmes = parse_xmltv(data, NOW, max_bytes=len(xml))
    assert names["kids"] == ["Kids"]
    assert [item.title for item in programmes] == ["Current show"]
    assert len(reads) > 3


@pytest.mark.parametrize("change", ["truncated", "crc", "trailing"])
def test_gzip_integrity_is_checked_after_complete_xml(change):
    data = gzip.compress(guide())
    if change == "truncated":
        data = data[:-4]
    elif change == "crc":
        data = data[:-8] + bytes([data[-8] ^ 1]) + data[-7:]
    else:
        data += b"untrusted-trailing-content"
    with pytest.raises(InvalidSource):
        parse_xmltv(data, NOW)


def test_compressed_entities_remain_forbidden():
    xml = b'<!DOCTYPE tv [<!ENTITY leak SYSTEM "file:///etc/passwd">]>' + guide().replace(b"Current show", b"&leak;")
    with pytest.raises(InvalidSource):
        parse_xmltv(gzip.compress(xml), NOW)


def test_filtered_parser_discards_unrelated_programmes_but_keeps_channel_metadata():
    xml = guide(programme() + programme("other", "Unrelated show"))
    names, programmes = parse_xmltv(gzip.compress(xml), NOW, channels=[channel()])
    assert set(names) == {"kids", "other"}
    assert [item.title for item in programmes] == ["Current show"]


def test_filtered_parser_preserves_channel_name_fallback_without_matching_epg_id():
    xml = guide(programme() + programme("other", "Unrelated show"))
    _, programmes = parse_xmltv(xml, NOW, channels=[channel(identifier="provider-specific-id")])
    assert [item.title for item in programmes] == ["Current show"]


def test_unrelated_filtered_feed_can_be_combined_with_other_matching_feeds():
    names, programmes = parse_xmltv(guide(programme("other", "Unrelated show")), NOW, channels=[channel()])
    assert names["other"] == ["Other"]
    assert programmes == []


def test_filtered_parser_rejects_late_channel_metadata_instead_of_partial_matches():
    xml = ('<tv>' + programme() + '<channel id="kids"><display-name>Kids</display-name></channel></tv>').encode()
    assert parse_xmltv(xml, NOW)[1]
    with pytest.raises(InvalidSource):
        parse_xmltv(xml, NOW, channels=[channel(identifier="provider-specific-id")])


class RawChunks(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        for index in range(0, len(self.data), 31):
            yield self.data[index:index + 31]


@pytest.mark.parametrize("content_encoding", ["identity", "gzip"])
async def test_xmltv_download_preserves_compressed_bytes_for_bounded_parser(content_encoding):
    xml = guide(programme() * 30)
    data = gzip.compress(xml)
    assert len(data) < 1024 < len(xml)

    def handler(request):
        assert request.headers["Accept-Encoding"] == "identity"
        return httpx.Response(200, stream=RawChunks(data), headers={"Content-Encoding": content_encoding})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        catalogue = Catalog(settings(max_source_bytes=1024), client)
        raw = await catalogue._download("http://guide.test/guide.xml.gz", 1024, keep_gzip=True)
    assert raw == data
    with pytest.raises(InvalidSource):
        parse_xmltv(raw, NOW, max_bytes=1024)
    assert len(parse_xmltv(raw, NOW, max_bytes=len(xml))[1]) == 30


async def test_transfer_limit_is_independent_from_decoded_limit():
    data = gzip.compress(guide())

    def handler(request):
        return httpx.Response(200, stream=RawChunks(data))

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        catalogue = Catalog(settings(), client)
        with pytest.raises(CatalogUnavailable):
            await catalogue._download("http://guide.test/guide.xml.gz", len(data) - 1, keep_gzip=True)


def test_config_allows_explicit_512_mib_decoded_limit():
    assert settings(max_source_bytes=512 * 1024 * 1024).max_source_bytes == 512 * 1024 * 1024
