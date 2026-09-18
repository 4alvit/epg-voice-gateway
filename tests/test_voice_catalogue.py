"""Finite voice selections use fresh schedules and never contain playback URLs."""

import asyncio
import json
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from epg_voice_gateway import storage
from epg_voice_gateway.app import create_app
from epg_voice_gateway.catalog import Catalog
from epg_voice_gateway.config import Settings
from epg_voice_gateway.errors import CatalogUnavailable
from epg_voice_gateway.models import Channel, ProgramMatch

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
TOKEN = "synthetic-voice-catalogue-api-token-only"
AUTH = {"Authorization": "Bearer " + TOKEN}


def settings(**overrides):
    return Settings(
        api_token=TOKEN, signing_key="synthetic-voice-signing-key-for-tests",
        hls_proxy_url="http://proxy.example.test:8080",
        media_base_url="https://media.example.test",
        xmltv_urls=["https://guide.example.test/synthetic.xml"], **overrides,
    )


def programme(title="Columbo", channel=1, name=None, *, start=None, end=None):
    return ProgramMatch(
        title, f"{channel:024x}", name or f"Channel {channel:04}",
        start or NOW - timedelta(minutes=5), end or NOW + timedelta(minutes=25),
    )


def replace(catalog, matches):
    channels = {
        match.channel_id: Channel(
            match.channel_id, match.channel_name, match.channel_id, match.channel_name,
            "http://proxy.example.test:8080/channel/hidden.m3u8?token=synthetic-secret",
        ) for match in matches
    }
    catalog.store.replace(list(channels.values()), matches, NOW.timestamp())


@contextmanager
def opened(matches, **overrides):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(500)))
    clock = [100.0]
    catalog = Catalog(settings(**overrides), client, clock=lambda: clock[0])
    replace(catalog, matches)
    catalog.updated_at = clock[0]
    try:
        yield catalog, client, clock
    finally:
        catalog.close()
        asyncio.run(client.aclose())


def read(catalog, now=NOW):
    return asyncio.run(catalog.voice_catalogue(now=now))


def test_http_catalogue_authentication_privacy_and_no_playback_side_effects():
    now = datetime.now(UTC)
    match = programme(start=now - timedelta(minutes=1), end=now + timedelta(minutes=10))
    with opened([match]) as (catalog, http, _):
        app = create_app(catalog.settings, catalog=catalog, client=http, start_refresh=False)
        with TestClient(app) as client, patch.object(app.state.sessions, "create") as create:
            for auth in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic wrong"}):
                assert client.get("/v1/catalogue", headers=auth).status_code == 401
            response = client.get("/v1/catalogue", headers=AUTH)
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert set(response.json()) == {"options", "revision"}
            assert response.json()["options"] == [{
                "label": "Columbo", "query": "Columbo", "channel_id": match.channel_id,
            }]
            assert len(response.json()["revision"]) == 64
            assert "http" not in response.text and "synthetic" not in response.text
            create.assert_not_called()


def test_start_is_inclusive_end_is_exclusive_and_future_titles_are_absent():
    matches = [
        programme("Starting", 1, start=NOW, end=NOW + timedelta(minutes=5)),
        programme("Ended", 2, start=NOW - timedelta(minutes=5), end=NOW),
        programme("Future", 3, start=NOW + timedelta(seconds=1)),
    ]
    with opened(matches) as (catalog, _, _):
        assert [option["label"] for option in read(catalog)["options"]] == ["Starting"]
        assert read(catalog, now=NOW + timedelta(minutes=30))["options"] == []


def test_normalized_duplicates_use_ordered_channel_allowlist_and_exclude_overlap():
    matches = [
        programme("Columbo", 1, name="Alpha"),
        programme("COLUMBO!", 2, name="Zulu"),
        programme("Outside", 3),
        programme("First overlap", 4),
        programme("Second overlap", 4, start=NOW - timedelta(minutes=2)),
    ]
    with opened(matches, voice_channel_ids=[f"{index:024x}" for index in (4, 2, 1)]) as (catalog, _, _):
        assert read(catalog)["options"] == [{
            "label": "COLUMBO!", "query": "COLUMBO!", "channel_id": f"{2:024x}",
        }]


def test_preferences_expand_aliases_but_return_exact_current_title_and_bounded_labels():
    long_title = "Кукутики " + "episode " * 15
    matches = [
        programme("Ordinary", 1), programme("Коломбо: Сезон 2", 2),
        programme(long_title, 3), programme("x" * 201, 4),
        programme("Ready", 5), programme("Unavailable", 6),
        programme("A\nB", 7), programme("A\u200bB", 8),
        programme("Visit https://private.example.test/secret", 9),
        programme("z" * 101, 10),
    ]
    with opened(
        matches, voice_preferred_queries=["Kukutiki", "Columbo", "Not airing"],
        title_aliases={"Kukutiki": ["Кукутики"], "Columbo": ["Коломбо"]},
    ) as (catalog, _, _):
        options = read(catalog)["options"]
        assert [option["label"] for option in options] == ["Kukutiki", "Columbo", "Ordinary"]
        assert options[0]["query"] == long_title.strip()
        assert options[1]["query"] == "Коломбо: Сезон 2"
        assert all(len(option["label"]) <= 100 and len(option["query"]) <= 200 for option in options)


def test_option_limit_and_revision_stability_depend_only_on_selected_options():
    matches = [programme(f"Programme {index}", index) for index in range(1, 61)]
    with opened(matches, voice_catalogue_limit=30) as (catalog, _, _):
        first = read(catalog)
        assert len(first["options"]) == 30
        replace(catalog, list(reversed(matches)))
        assert read(catalog) == first
        replace(catalog, matches + [programme("Future", 100, start=NOW + timedelta(minutes=1))])
        assert read(catalog) == first
        replace(catalog, [programme("Changed", 1), *matches[1:]])
        assert read(catalog)["revision"] != first["revision"]
        assert TOKEN not in json.dumps(first)


def test_candidate_cap_is_before_preferences_and_overlap_detection_is_not_truncated(monkeypatch):
    monkeypatch.setattr(storage, "VOICE_CANDIDATE_LIMIT", 2)
    matches = [programme("First", 1), programme("Second", 2), programme("Preferred", 3)]
    with opened(matches, voice_preferred_queries=["Preferred"]) as (catalog, _, _):
        assert [option["label"] for option in read(catalog)["options"]] == ["First", "Second"]
        replace(catalog, [*matches, programme("Overlap", 1, start=NOW - timedelta(minutes=1))])
        assert [option["label"] for option in read(catalog)["options"]] == ["Preferred", "Second"]


def test_unloaded_expired_and_closed_catalogues_fail_instead_of_publishing_empty_options():
    with opened([programme()]) as (catalog, _, clock):
        catalog.updated_at = None
        with pytest.raises(CatalogUnavailable):
            read(catalog)
        catalog.updated_at = 100
        clock[0] = 100 + catalog.settings.max_stale_seconds
        assert read(catalog)["options"]
        clock[0] += 0.01
        with pytest.raises(CatalogUnavailable):
            read(catalog)
        clock[0] = 100
        catalog.close()
        with pytest.raises(CatalogUnavailable) as error:
            read(catalog)
        assert error.value.code == "catalog_unavailable"


def test_selected_channel_query_is_filtered_before_global_twenty_match_limit():
    matches = [programme("Columbo", index) for index in range(1, 41)]
    with opened(matches) as (catalog, _, _):
        assert len(asyncio.run(catalog.resolve("Columbo", now=NOW))) == 20
        assert asyncio.run(catalog.resolve("Columbo", now=NOW, channel_id=f"{40:024x}")) == [matches[-1]]
        assert asyncio.run(catalog.resolve("Different", now=NOW, channel_id=f"{40:024x}")) == []


def test_exact_title_refuses_a_different_programme_containing_the_old_title():
    now = datetime.now(UTC)
    match = programme("World News", start=now - timedelta(minutes=1), end=now + timedelta(minutes=10))
    with opened([match], title_aliases={"News": ["World News"]}) as (catalog, http, _):
        app = create_app(catalog.settings, catalog=catalog, client=http, start_refresh=False)
        with TestClient(app) as client, patch.object(app.state.sessions, "create") as create:
            response = client.post("/v1/play", headers=AUTH, json={
                "query": "News", "channel_id": match.channel_id, "exact_title": True,
            })
            assert response.status_code == 404
            assert response.json() == {"error": "no_current_programme"}
            create.assert_not_called()


def test_exact_title_can_launch_a_catalogue_choice_beyond_the_global_search_limit():
    now = datetime.now(UTC)
    matches = [programme(
        "Columbo", index, start=now - timedelta(minutes=1), end=now + timedelta(minutes=10),
    ) for index in range(1, 41)]
    with opened(matches, voice_channel_ids=[f"{40:024x}"]) as (catalog, http, _):
        app = create_app(catalog.settings, catalog=catalog, client=http, start_refresh=False)
        with TestClient(app) as client:
            option = client.get("/v1/catalogue", headers=AUTH).json()["options"][0]
            response = client.post("/v1/play", headers=AUTH, json={
                "query": option["query"], "channel_id": option["channel_id"], "exact_title": True,
            })
            assert response.status_code == 200
            assert response.json()["programme"]["channel_id"] == f"{40:024x}"


@pytest.mark.parametrize("payload", [
    {"query": "News", "exact_title": True},
    {"channel_id": "a" * 24, "exact_title": True},
    {"query": "News", "channel_id": "a" * 24, "exact_title": "true"},
])
def test_exact_title_http_contract_requires_boolean_and_both_selection_fields(payload):
    with opened([programme()]) as (catalog, http, _):
        app = create_app(catalog.settings, catalog=catalog, client=http, start_refresh=False)
        with TestClient(app) as client:
            assert client.post("/v1/play", headers=AUTH, json=payload).status_code == 400


def test_http_catalogue_expiry_returns_unavailable_not_an_empty_success():
    with opened([programme()]) as (catalog, http, clock):
        clock[0] += catalog.settings.max_stale_seconds + 1
        app = create_app(catalog.settings, catalog=catalog, client=http, start_refresh=False)
        with TestClient(app) as client:
            response = client.get("/v1/catalogue", headers=AUTH)
            assert response.status_code == 503
            assert response.json() == {"error": "catalog_unavailable"}


@pytest.mark.parametrize("overrides", [
    {"voice_catalogue_limit": 0}, {"voice_catalogue_limit": 51},
    {"voice_channel_ids": ["../channel"]}, {"voice_channel_ids": ["A" * 24]},
    {"voice_preferred_queries": ["Ready"]}, {"voice_preferred_queries": ["Unavailable!"]},
    {"voice_preferred_queries": ["a\nb"]}, {"voice_preferred_queries": ["x" * 101]},
    {"voice_preferred_queries": ["!!!"]},
])
def test_invalid_voice_configuration_is_rejected(overrides):
    with pytest.raises(ValidationError):
        settings(**overrides)


def test_voice_configuration_deduplicates_without_losing_order():
    config = settings(
        voice_channel_ids=["b" * 24, "a" * 24, "b" * 24],
        voice_preferred_queries=["Columbo", "columbo!", "Kukutiki"],
    )
    assert config.voice_channel_ids == ["b" * 24, "a" * 24]
    assert config.voice_preferred_queries == ["Columbo", "Kukutiki"]
