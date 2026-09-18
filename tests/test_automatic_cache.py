"""Automatic-name caching must not cache guide availability or cross imports."""

import asyncio
import threading
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest

from epg_voice_gateway import catalog as catalog_module
from epg_voice_gateway.automatic_channels import build_automatic_channels
from epg_voice_gateway.catalog import Catalog
from epg_voice_gateway.config import Settings
from epg_voice_gateway.errors import CatalogUnavailable
from epg_voice_gateway.models import Channel, ProgramMatch

NOW = datetime.now(UTC).replace(microsecond=0)


def settings(**extra):
    return Settings(
        api_token="a" * 40, signing_key="s" * 40,
        hls_proxy_url="http://proxy.example.test", media_base_url="https://media.example.test",
        xmltv_urls=["http://guide.example.test/guide.xml"], channel_voice_mode="automatic",
        **extra,
    )


def imported(name="News SD", channel_id="a" * 24):
    channel = Channel(channel_id, name, "provider-id", name,
                      "http://proxy.example.test/channel/synthetic/index.m3u8")
    programme = ProgramMatch("Current programme", channel.channel_id, channel.name,
                             NOW - timedelta(minutes=1), NOW + timedelta(minutes=1))
    return channel, programme


@pytest.fixture
async def cached_catalogue():
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(500)))
    clock = [100.0]
    value = Catalog(settings(), client, clock=lambda: clock[0])
    channel, programme = imported()
    value.store.replace([channel], [programme], NOW.timestamp())
    value.updated_at = clock[0]
    yield value, clock
    value.close()
    if value._channel_name_task is not None:
        await asyncio.gather(value._channel_name_task, return_exceptions=True)
    await client.aclose()


async def test_repeated_reads_only_query_selected_current_counts(cached_catalogue):
    value, _ = cached_catalogue
    original = catalog_module.prepare_automatic_channels
    with (
        patch.object(catalog_module, "prepare_automatic_channels", wraps=original) as build,
        patch.object(value.store, "media_candidates", wraps=value.store.media_candidates) as full,
        patch.object(value.store, "channel_candidates", wraps=value.store.channel_candidates) as selected,
    ):
        first = await value.channel_catalogue(now=NOW)
        second = await value.channel_catalogue(now=NOW + timedelta(minutes=2))
        assert first["channels"][0]["available"] is True
        assert second["channels"][0]["available"] is False
        assert first["revision"] != second["revision"]
        assert build.call_count == full.call_count == 1
        assert selected.call_count == 2
        assert all(call.args[1] == ["a" * 24] for call in selected.call_args_list)
        channels, _ = full(NOW + timedelta(minutes=2))
        assert second == build_automatic_channels(channels)


async def test_response_mutation_cannot_change_cached_metadata(cached_catalogue):
    value, _ = cached_catalogue
    first = await value.channel_catalogue(now=NOW)
    expected = await value.channel_catalogue(now=NOW)
    first["channels"][0]["name"] = "Caller changed this"
    first["channels"][0]["synonyms"].append("Caller inserted this")
    assert await value.channel_catalogue(now=NOW) == expected


async def test_concurrent_cold_reads_share_one_off_loop_build(cached_catalogue):
    value, _ = cached_catalogue
    entered, release = threading.Event(), threading.Event()
    original = catalog_module.prepare_automatic_channels
    loop_thread = threading.get_ident()
    calls = []

    def blocked(*args, **kwargs):
        calls.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    with patch.object(catalog_module, "prepare_automatic_channels", side_effect=blocked):
        tasks = [asyncio.create_task(value.channel_catalogue(now=NOW)) for _ in range(8)]
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await asyncio.sleep(0)
            assert len(calls) == 1 and calls[0] != loop_thread
        finally:
            release.set()
        results = await asyncio.gather(*tasks)
    assert all(result == results[0] for result in results)
    assert len(calls) == 1


async def test_caller_timeout_does_not_cancel_shared_warmup(cached_catalogue):
    value, _ = cached_catalogue
    entered, release = threading.Event(), threading.Event()
    original = catalog_module.prepare_automatic_channels

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    with patch.object(catalog_module, "prepare_automatic_channels", side_effect=blocked) as build:
        first = asyncio.create_task(value.channel_catalogue(now=NOW))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(first, 0.01)
            assert not value._channel_name_task.done()
        finally:
            release.set()
        result = await value.channel_catalogue(now=NOW)
    assert result["channels"][0]["available"] is True
    assert build.call_count == 1


def refresh_sources(name):
    playlist = (
        '#EXTM3U\n#EXTINF:-1 tvg-id="test",' + name
        + '\nhttp://proxy.example.test/channel/fresh/index.m3u8\n'
    ).encode()
    guide = (
        '<tv><channel id="test"><display-name>' + name + '</display-name></channel>'
        '<programme channel="test" start="' + (NOW - timedelta(minutes=1)).strftime("%Y%m%d%H%M%S %z")
        + '" stop="' + (NOW + timedelta(minutes=1)).strftime("%Y%m%d%H%M%S %z")
        + '"><title>Current programme</title></programme></tv>'
    ).encode()
    return playlist, guide


async def test_refresh_during_build_never_publishes_previous_generation(cached_catalogue):
    value, _ = cached_catalogue
    entered, release = threading.Event(), threading.Event()
    original = catalog_module.prepare_automatic_channels
    builds = []

    def blocked(candidates, **kwargs):
        builds.append([candidate.name for candidate in candidates])
        if len(builds) == 1:
            entered.set()
            assert release.wait(3)
        return original(candidates, **kwargs)

    playlist, guide = refresh_sources("Replacement SD")

    async def download(url, *_args, **_kwargs):
        return playlist if url.endswith(".m3u8") else guide

    value._download = download
    with patch.object(catalog_module, "prepare_automatic_channels", side_effect=blocked):
        request = asyncio.create_task(value.channel_catalogue(now=NOW))
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            await value.refresh()
            assert value._channel_names is None
        finally:
            release.set()
        result = await request
    assert builds == [["News SD"], ["Replacement SD"]]
    assert [row["name"] for row in result["channels"]] == ["Replacement"]
    assert value._channel_names[0] == value._channel_generation == 1


async def test_refresh_reuses_imported_labels_without_full_snapshot(cached_catalogue):
    value, _ = cached_catalogue
    await value.channel_catalogue(now=NOW)
    playlist, guide = refresh_sources("Replacement SD")

    async def download(url, *_args, **_kwargs):
        return playlist if url.endswith(".m3u8") else guide

    value._download = download
    with patch.object(value.store, "media_candidates", side_effect=AssertionError("Unexpected full scan")):
        await value.refresh()
        assert value._channel_name_task is not None
        result = await value.channel_catalogue(now=NOW)
    assert result["channels"][0]["name"] == "Replacement"


async def test_failed_refresh_keeps_last_good_names(cached_catalogue):
    value, _ = cached_catalogue
    expected = await value.channel_catalogue(now=NOW)
    cached = value._channel_names
    with pytest.raises(CatalogUnavailable):
        await value.refresh()
    assert value._channel_names is cached
    assert await value.channel_catalogue(now=NOW) == expected


async def test_cancelled_refresh_finishes_commit_and_invalidates_before_unlock(cached_catalogue):
    value, _ = cached_catalogue
    await value.channel_catalogue(now=NOW)
    entered, release = threading.Event(), threading.Event()
    original = value.store.replace
    playlist, guide = refresh_sources("Replacement SD")

    async def download(url, *_args, **_kwargs):
        return playlist if url.endswith(".m3u8") else guide

    def replace(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    value._download = download
    with patch.object(value.store, "replace", side_effect=replace):
        refresh = asyncio.create_task(value.refresh())
        try:
            assert await asyncio.to_thread(entered.wait, 1)
            refresh.cancel()
            await asyncio.sleep(0)
            request = asyncio.create_task(value.channel_catalogue(now=NOW))
            await asyncio.sleep(0)
            assert not request.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await refresh
        result = await request
    assert result["channels"][0]["name"] == "Replacement"
    assert value._channel_names[0] == value._channel_generation == 1


async def test_run_prewarms_restored_fresh_catalogue_before_refresh_sleep(cached_catalogue):
    value, _ = cached_catalogue
    run = asyncio.create_task(value.run())
    try:
        await asyncio.sleep(0)
        assert value._channel_name_task is not None
        await asyncio.wait_for(asyncio.shield(value._channel_name_task), 1)
        assert value._channel_names is not None
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


async def test_expiry_during_selected_count_read_never_serves_cached_lineup(cached_catalogue):
    value, clock = cached_catalogue
    await value.channel_catalogue(now=NOW)
    original = value.store.channel_candidates

    def expired(*args, **kwargs):
        clock[0] += value.settings.max_stale_seconds + 1
        return original(*args, **kwargs)

    with patch.object(value.store, "channel_candidates", side_effect=expired):
        with pytest.raises(CatalogUnavailable):
            await value.channel_catalogue(now=NOW)


async def test_close_cancels_background_task_and_disallows_cache_use(cached_catalogue):
    value, _ = cached_catalogue
    await value.channel_catalogue(now=NOW)
    value.close()
    with pytest.raises(CatalogUnavailable):
        await value.channel_catalogue(now=NOW)
