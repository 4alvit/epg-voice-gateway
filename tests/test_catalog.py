import gzip
from contextlib import aclosing
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from epg_voice_gateway.catalog import Catalog, expand_gzip, parse_playlist, parse_xmltv, xmltv_time
from epg_voice_gateway.config import Settings
from epg_voice_gateway.errors import CatalogUnavailable, InvalidSource

NOW = datetime.now(UTC).replace(microsecond=0)


def settings(**values):
    return Settings(api_token="a" * 40, signing_key="s" * 40,
                    hls_proxy_url="http://proxy.test:8080", media_base_url="https://media.example.test",
                    xmltv_urls=["http://guide.test/a.xml"], title_aliases={"kukutiki": ["Кукутики"]}, **values)


def guide(name="Kids", title="Кукутики", identifier="1", start=None, stop=None):
    start = start or NOW - timedelta(minutes=10)
    stop = stop or NOW + timedelta(minutes=10)
    return f'<tv><channel id="{identifier}"><display-name>{name}</display-name></channel><programme channel="{identifier}" start="{start:%Y%m%d%H%M%S %z}" stop="{stop:%Y%m%d%H%M%S %z}"><title>{title}</title></programme></tv>'.encode()


def playlist(name="Kids", identifier="1", shift="0"):
    return f'#EXTM3U\n#EXTINF:-1 tvg-id="{identifier}" tvg-shift="{shift}",{name}\nhttp://proxy.test:8080/channel/test/index.m3u8?q=private\n'.encode()


async def catalog_with(sources, config=None, clock=lambda: 1.0):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, content=sources[str(request.url)])))
    catalog = Catalog(config or settings(), client, clock=clock)
    await catalog.refresh()
    return catalog, client


async def test_current_window_alias_and_no_credentials_in_results():
    catalog, client = await catalog_with({"http://proxy.test:8080/playlist.m3u8": playlist(), "http://guide.test/a.xml": guide()})
    async with aclosing(client):
        result = await catalog.resolve("Kukutiki", now=NOW)
        assert len(result) == 1 and result[0].title == "Кукутики"
        assert "private" not in repr(result)
        assert await catalog.resolve("Кукутики", now=NOW + timedelta(minutes=10)) == []
        assert await catalog.resolve("кукут", now=NOW) == []
        assert await catalog.current_on_channel(result[0].channel_id, now=NOW) == result
        assert await catalog.current_on_channel(result[0].channel_id, now=NOW - timedelta(minutes=10)) == result
        assert await catalog.current_on_channel(result[0].channel_id, now=NOW + timedelta(minutes=10)) == []
        assert await catalog.current_on_channel("f" * 24, now=NOW) == []


async def test_saved_channel_requires_loaded_catalog_and_hides_storage_errors():
    async with httpx.AsyncClient() as client:
        catalog = Catalog(settings(), client)
        try:
            with pytest.raises(CatalogUnavailable):
                await catalog.current_on_channel("a" * 24, now=NOW)
            catalog.updated_at = catalog.clock()
            catalog.close()
            with pytest.raises(CatalogUnavailable) as error:
                await catalog.current_on_channel("a" * 24, now=NOW)
            assert error.value.code == "catalog_unavailable"
        finally:
            catalog.close()


async def test_cross_feed_id_collision_does_not_launch_wrong_channel():
    config = settings()
    config.xmltv_urls = ["http://guide.test/a.xml", "http://guide.test/b.xml"]
    catalog, client = await catalog_with({"http://proxy.test:8080/playlist.m3u8": playlist(), "http://guide.test/a.xml": guide(), "http://guide.test/b.xml": guide(name="News", title="Daily News")}, config)
    async with aclosing(client):
        assert await catalog.resolve("Daily News", now=NOW) == []
        assert len(await catalog.resolve("Kukutiki", now=NOW)) == 1


async def test_colliding_id_selects_feed_with_matching_channel_name():
    config = settings()
    config.xmltv_urls = ["http://guide.test/a.xml", "http://guide.test/b.xml"]
    catalog, client = await catalog_with({"http://proxy.test:8080/playlist.m3u8": playlist(name="News"), "http://guide.test/a.xml": guide(), "http://guide.test/b.xml": guide(name="News", title="Daily News")}, config)
    async with aclosing(client):
        assert await catalog.resolve("Kukutiki", now=NOW) == []
        result = await catalog.resolve("Daily News", now=NOW)
        assert len(result) == 1 and result[0].channel_name == "News"


async def test_failed_refresh_retains_last_good_then_expires():
    clock = [1.0]
    catalog, client = await catalog_with({"http://proxy.test:8080/playlist.m3u8": playlist(), "http://guide.test/a.xml": guide()}, clock=lambda: clock[0])
    async with aclosing(client):
        async def failed(*args):
            raise RuntimeError("secret upstream error")
        catalog._download = failed
        with pytest.raises(CatalogUnavailable):
            await catalog.refresh()
        assert len(await catalog.resolve("Kukutiki", now=NOW)) == 1
        current = await catalog.resolve("Kukutiki", now=NOW)
        assert await catalog.current_on_channel(current[0].channel_id, now=NOW) == current
        clock[0] = 10000
        with pytest.raises(CatalogUnavailable):
            await catalog.resolve("Kukutiki", now=NOW)
        with pytest.raises(CatalogUnavailable):
            await catalog.current_on_channel(current[0].channel_id, now=NOW)


async def test_positive_playlist_shift_moves_programme_forward():
    catalog, client = await catalog_with({"http://proxy.test:8080/playlist.m3u8": playlist(shift="2"), "http://guide.test/a.xml": guide(start=NOW - timedelta(hours=2, minutes=10), stop=NOW - timedelta(hours=1, minutes=50))})
    async with aclosing(client):
        assert len(await catalog.resolve("Kukutiki", now=NOW)) == 1


def test_xmltv_offsets_and_midnight_are_absolute_instants():
    assert xmltv_time("20260918003000 +0300") == datetime(2026, 9, 17, 21, 30, tzinfo=UTC)
    assert xmltv_time("202609172130") == datetime(2026, 9, 17, 21, 30, tzinfo=UTC)


def test_document_type_is_supported_but_entities_are_rejected():
    data = b'<!DOCTYPE tv SYSTEM "xmltv.dtd">' + guide()
    assert parse_xmltv(data, NOW)[1]
    malicious = b'<!DOCTYPE tv [<!ENTITY leak SYSTEM "file:///etc/passwd">]>' + guide().replace("Кукутики".encode(), b"&leak;")
    with pytest.raises(InvalidSource):
        parse_xmltv(malicious, NOW)


def test_gzip_limit_and_truncation():
    payload = b"hello" * 1000
    assert expand_gzip(gzip.compress(payload), 6000) == payload
    with pytest.raises(InvalidSource):
        expand_gzip(gzip.compress(payload), 100)
    with pytest.raises(InvalidSource):
        expand_gzip(gzip.compress(payload)[:-4], 6000)


def test_playlist_rejects_external_stream_and_honours_bom_shift():
    with pytest.raises(InvalidSource):
        parse_playlist(playlist().decode().replace("http://proxy.test:8080/channel", "http://elsewhere.test/channel"), "http://proxy.test:8080")
    text = '\ufeff#EXTM3U tvg-shift="2"\n#EXTINF:-1,Kids\n/channel/test/index.m3u8\n'
    assert parse_playlist(text, "http://proxy.test:8080")[0].shift_seconds == 7200


async def test_persisted_catalog_survives_restart_without_network(tmp_path):
    config = settings(database_path=str(tmp_path / "catalog.sqlite3"))
    catalog, client = await catalog_with({
        "http://proxy.test:8080/playlist.m3u8": playlist(),
        "http://guide.test/a.xml": guide(),
    }, config)
    original = await catalog.resolve("Kukutiki", now=NOW)
    catalog.close()
    await client.aclose()

    def unavailable(request):
        pytest.fail("A fresh persisted catalogue should not need the network")

    async with httpx.AsyncClient(transport=httpx.MockTransport(unavailable)) as offline:
        restored = Catalog(config, offline)
        try:
            assert restored.ready
            assert await restored.resolve("Kukutiki", now=NOW) == original
            assert await restored.current_on_channel(original[0].channel_id, now=NOW) == original
            assert restored.stream_url(original[0]).endswith("q=private")
        finally:
            restored.close()


async def test_duplicate_display_name_in_one_feed_is_not_silently_selected():
    first = guide(title="First Programme", identifier="id-a")
    second = guide(title="Second Programme", identifier="id-b")
    # XMLTV channel definitions must precede programme definitions.
    import xml.etree.ElementTree as ET
    roots = [ET.fromstring(first), ET.fromstring(second)]
    root = ET.Element("tv")
    for tag in ("channel", "programme"):
        for tree in roots:
            root.extend(tree.findall(tag))
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, content=playlist(identifier="") if request.url.path.endswith(".m3u8") else ET.tostring(root),
    )))
    catalog = Catalog(settings(), client)
    try:
        with pytest.raises(CatalogUnavailable):
            await catalog.refresh()
        assert not catalog.ready
    finally:
        catalog.close()
        await client.aclose()
