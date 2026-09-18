"""Named channels are explicit, bounded, unambiguous, and independent of titles."""

import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import ValidationError

from epg_voice_gateway.catalog import Catalog
from epg_voice_gateway.channel_catalogue import ChannelCandidate, build_channel_catalogue
from epg_voice_gateway.config import Settings
from epg_voice_gateway.errors import CatalogUnavailable
from epg_voice_gateway.models import Channel, ProgramMatch
from epg_voice_gateway.storage import CatalogStorageError

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)


def channel_id(index):
    return f"{index:024x}"


def settings(**overrides):
    return Settings(
        api_token="synthetic-channel-catalogue-api-token-only",
        signing_key="synthetic-channel-signing-key-for-tests",
        hls_proxy_url="http://proxy.example.test:8080",
        media_base_url="https://media.example.test",
        xmltv_urls=["https://guide.example.test/synthetic.xml"], **overrides,
    )


def channel(index, name=None, epg_name=None):
    return Channel(
        channel_id(index), name or f"Channel {index}", f"private-provider-id-{index}",
        epg_name or name or f"Channel {index}",
        "http://proxy.example.test:8080/channel/private?token=synthetic-secret",
    )


def programme(index, title="Current", *, start=None, end=None):
    return ProgramMatch(
        title, channel_id(index), f"Channel {index}",
        start or NOW - timedelta(minutes=5), end or NOW + timedelta(minutes=5),
    )


@contextmanager
def opened(channels, programmes, **overrides):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    clock = [100.0]
    catalog = Catalog(settings(**overrides), client, clock=lambda: clock[0])
    catalog.store.replace(channels, programmes, NOW.timestamp())
    catalog.updated_at = clock[0]
    try:
        yield catalog, clock
    finally:
        catalog.close()
        asyncio.run(client.aclose())


def read(catalog, now=NOW):
    return asyncio.run(catalog.channel_catalogue(now=now))


def candidate(index, name, epg_name="", available=True):
    return ChannelCandidate(channel_id(index), name, epg_name, available)


def test_explicit_channel_allowlist_is_separate_and_empty_fails_closed():
    with opened([channel(1)], [programme(1)], voice_channel_ids=[channel_id(1)]) as (catalog, _):
        assert read(catalog)["channels"] == []
        assert asyncio.run(catalog.voice_catalogue(now=NOW))["options"]


def test_configured_order_filters_before_limit_and_never_publishes_private_fields():
    channels = [channel(index) for index in range(1, 41)]
    programmes = [programme(index) for index in range(1, 41)]
    with opened(
        channels, programmes, channel_voice_ids=[channel_id(40), channel_id(2)],
        channel_voice_limit=1,
    ) as (catalog, _):
        result = read(catalog)
        assert result["channels"] == [{
            "channel_id": channel_id(40), "name": "Channel 40", "synonyms": [], "available": True,
        }]
        serialized = json.dumps(result)
        assert all(value not in serialized for value in [
            "http", "private", "provider", "synthetic", "Current", "number", "stream_url",
        ])
        rows = catalog.store.channel_candidates(NOW, [channel_id(40)])
        assert set(vars(rows[0])) == {"channel_id", "name", "epg_name", "available"}


def test_current_boundaries_overlap_and_missing_schedule_preserve_channel_slots():
    channels = [channel(index) for index in range(1, 6)]
    programmes = [
        programme(1, start=NOW),
        programme(2, end=NOW),
        programme(3, start=NOW + timedelta(seconds=1)),
        programme(4), programme(4, start=NOW - timedelta(minutes=1)),
    ]
    with opened(
        channels, programmes, channel_voice_ids=[item.channel_id for item in channels],
    ) as (catalog, _):
        result = read(catalog)
        assert [item["available"] for item in result["channels"]] == [True, False, False, False, False]
        assert [item["channel_id"] for item in result["channels"]] == [
            item.channel_id for item in channels
        ]
        later = read(catalog, NOW + timedelta(seconds=1))
        assert later["channels"][2]["available"] is True
        assert later["revision"] != result["revision"]


def test_programme_turnover_and_source_order_do_not_change_lineup_revision():
    channels = [channel(1, "News", "News International"), channel(2, "Cinema")]
    with opened(
        channels, [programme(1), programme(2)],
        channel_voice_ids=[channel_id(2), channel_id(1)],
    ) as (catalog, _):
        first = read(catalog)
        catalog.store.replace(
            list(reversed(channels)), [programme(2, "Next Film"), programme(1, "Next News")],
            NOW.timestamp() + 60,
        )
        assert read(catalog) == first
        assert first["channels"][1]["synonyms"] == ["News International"]
        catalog.store.replace(channels, [programme(2)], NOW.timestamp() + 120)
        unavailable = read(catalog)
        assert unavailable["channels"][1]["channel_id"] == channel_id(1)
        assert unavailable["channels"][1]["available"] is False
        assert unavailable["revision"] != first["revision"]


def test_names_are_canonical_bounded_and_unicode_safe_without_truncation():
    result = build_channel_catalogue([
        candidate(1, "  Ｎｅｗｓ   HD ", "News HD"),
        candidate(2, "Кино", "Кино International"),
        candidate(3, "n" * 101), candidate(4, "A\nB"), candidate(5, "A\u200bB"),
        candidate(6, "Visit https://private.example.test/token"),
        candidate(7, "www.private.example.test"), candidate(8, "ftp://private.example.test"),
        candidate(9, "!@#$"), candidate(10, "Valid", "https://private.example.test/secret"),
        ChannelCandidate("invalid-id", "Invalid", "", True),
    ], 30)
    assert [item["name"] for item in result["channels"]] == ["News HD", "Кино", "Valid"]
    assert result["channels"][0]["synonyms"] == []
    assert result["channels"][1]["synonyms"] == ["Кино International"]
    assert result["channels"][2]["synonyms"] == []


def test_normalized_name_collisions_exclude_all_conflicting_channels():
    result = build_channel_catalogue([
        candidate(1, "Néws"), candidate(2, "NE\u0301WS!"), candidate(3, "Distinct"),
    ], 30)
    assert [item["name"] for item in result["channels"]] == ["Distinct"]


def test_cross_channel_synonyms_cannot_shadow_names_or_aliases_even_beyond_limit():
    candidates = [
        candidate(1, "First", "Second"), candidate(2, "Second", "Shared"),
        candidate(3, "Third", "SHARED!"), candidate(4, "Fourth", "Unique"),
    ]
    result = build_channel_catalogue(candidates, 30)
    assert [item["synonyms"] for item in result["channels"]] == [[], [], [], ["Unique"]]
    limited = build_channel_catalogue(candidates, 1)
    assert limited["channels"][0]["synonyms"] == []


def test_duplicate_ids_are_not_published_twice():
    item = candidate(1, "First", "Alternate")
    assert len(build_channel_catalogue([item, item], 30)["channels"]) == 1


def test_default_thirty_is_configurable_up_to_two_hundred_and_missing_ids_are_ignored():
    channels = [channel(index) for index in range(1, 71)]
    ids = [item.channel_id for item in channels]
    with opened(channels, [], channel_voice_ids=[channel_id(100), *ids]) as (catalog, _):
        assert len(read(catalog)["channels"]) == 30
        catalog.settings.channel_voice_limit = 70
        assert len(read(catalog)["channels"]) == 70
        assert not any(item["available"] for item in read(catalog)["channels"])


def test_unloaded_expired_and_closed_catalogues_fail_even_with_empty_allowlist():
    with opened([channel(1)], [programme(1)]) as (catalog, clock):
        catalog.updated_at = None
        with pytest.raises(CatalogUnavailable):
            read(catalog)
        catalog.updated_at = 100
        clock[0] = 100 + catalog.settings.max_stale_seconds
        assert read(catalog)["channels"] == []
        clock[0] += 0.01
        with pytest.raises(CatalogUnavailable):
            read(catalog)
        clock[0] = 100
        catalog.close()
        with pytest.raises(CatalogUnavailable) as error:
            read(catalog)
        assert error.value.code == "catalog_unavailable"


@pytest.mark.parametrize("overrides", [
    {"channel_voice_ids": ["not-an-id"]},
    {"channel_voice_ids": [channel_id(index) for index in range(201)]},
    {"channel_voice_limit": 0}, {"channel_voice_limit": 201},
])
def test_configuration_bounds(overrides):
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_configuration_preserves_deduplicated_priority_and_environment(monkeypatch):
    monkeypatch.setenv("EPG_CHANNEL_VOICE_IDS", json.dumps([channel_id(2), channel_id(1), channel_id(2)]))
    monkeypatch.setenv("EPG_CHANNEL_VOICE_LIMIT", "60")
    result = settings()
    assert result.channel_voice_ids == [channel_id(2), channel_id(1)]
    assert result.channel_voice_limit == 60
    assert result.voice_channel_ids == []


def test_storage_input_bounds_and_sql_values_cannot_change_the_catalogue():
    with opened([channel(1)], [programme(1)]) as (catalog, _):
        assert catalog.store.channel_candidates(NOW, ["' OR 1=1 --"]) == []
        assert len(catalog.store.channel_candidates(NOW, [channel_id(1)])) == 1
        for value in [None, "wrong", [""] * 201]:
            with pytest.raises(CatalogStorageError):
                catalog.store.channel_candidates(NOW, value)
        with pytest.raises(CatalogStorageError):
            catalog.store.channel_candidates(NOW.replace(tzinfo=None), [channel_id(1)])


@pytest.mark.parametrize("limit", [0, 201, True])
def test_builder_bounds(limit):
    with pytest.raises(ValueError):
        build_channel_catalogue([], limit)
