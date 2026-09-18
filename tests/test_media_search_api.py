"""Full-catalogue search and authenticated, revalidated text playback."""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from epg_voice_gateway.app import create_app
from epg_voice_gateway.catalog import Catalog
from epg_voice_gateway.config import Settings
from epg_voice_gateway.models import Channel, ProgramMatch

TOKEN = "synthetic-media-text-control-api-secret"
AUTH = {"Authorization": "Bearer " + TOKEN}


@contextmanager
def media_api(*, preferred=False, stale=False, overlap=False, **changes):
    settings = Settings(
        api_token=TOKEN, signing_key="synthetic-media-text-signing-secret",
        hls_proxy_url="http://proxy.example.test",
        media_base_url="https://media.example.test",
        xmltv_urls=["https://guide.example.test/guide.xml"],
        channel_voice_ids=["a" * 24],
        channel_aliases={"Example International": ["Example News"]},
        media_preferred_channel_ids=["a" * 24] if preferred else [],
        **changes,
    )
    now = datetime.now(UTC)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    catalogue = Catalog(settings, upstream, clock=lambda: 100.0)
    channels = [Channel(
        letter * 24, name, "private-provider-" + letter, name,
        f"http://proxy.example.test/channel/{letter}.m3u8?token=synthetic-provider-secret",
    ) for letter, name in (
        ("a", "Example International"), ("b", "Example Regional"),
        ("c", "Children Channel"), ("d", "Schedule Gap"),
    )]
    programmes = [ProgramMatch(
        title, c.channel_id, c.name, now - timedelta(minutes=5), now + timedelta(minutes=30),
    ) for c, title in zip(channels, ("News Bulletin", "News Bulletin", "Кукутики"), strict=False)]
    if overlap:
        programmes.append(ProgramMatch(
            "Overlapping Schedule", "a" * 24, channels[0].name,
            now - timedelta(minutes=1), now + timedelta(minutes=4),
        ))
    catalogue.store.replace(channels, programmes, now.timestamp())
    catalogue.updated_at = None if stale else 100.0
    app = create_app(settings, client=upstream, catalog=catalogue, start_refresh=False)
    try:
        with TestClient(app) as client:
            yield client, app, catalogue
    finally:
        catalogue.close()
        asyncio.run(upstream.aclose())


@pytest.mark.parametrize("route", ["/v1/search", "/v1/play-text"])
def test_authentication_before_search_or_grants(route):
    with media_api() as (client, app, catalogue):
        with patch.object(catalogue, "search_media") as search, patch.object(app.state.sessions, "create") as create:
            for headers in ({}, {"Authorization": "Bearer wrong"}):
                assert client.post(route, json={"query": "Example News"}, headers=headers).status_code == 401
        search.assert_not_called()
        create.assert_not_called()


def test_search_finds_channel_outside_google_allowlist_without_media_side_effects():
    with media_api() as (client, app, _), patch.object(app.state.sessions, "create") as create:
        result = client.post("/v1/search", json={"query": "Children Channel"}, headers=AUTH)
        assert result.status_code == 200
        assert result.json()["status"] == "matched"
        assert result.json()["matches"][0]["channel_id"] == "c" * 24
        assert result.json()["matches"][0]["match_kind"] == "channel"
        assert result.headers["Cache-Control"] == "no-store"
        for forbidden in ("http", "provider", "media_url", "stream_url"):
            assert forbidden not in result.text
        create.assert_not_called()


@pytest.mark.parametrize("query,identifier", [("Example News", "a"), ("Кукутики", "c")])
def test_text_playback_resolves_alias_or_programme_and_returns_signed_grant(query, identifier):
    with media_api() as (client, app, _):
        response = client.post("/v1/play-text", json={"query": query}, headers=AUTH)
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["programme"]["channel_id"] == identifier * 24
        assert body["media_url"].startswith("https://media.example.test/media/v1.")
        assert body["expires_at"] > datetime.now(UTC).timestamp()
        assert "synthetic-provider-secret" not in response.text
        assert len(app.state.sessions._sessions) == 1


@pytest.mark.parametrize("query,error", [
    ("Unfindable Quasar", "no_media_match"),
    ("Schedule Gap", "media_unavailable"),
])
def test_no_match_or_epg_gap_never_creates_a_grant(query, error):
    with media_api() as (client, app, _), patch.object(app.state.sessions, "create") as create:
        response = client.post("/v1/play-text", json={"query": query}, headers=AUTH)
        assert response.status_code == 404
        assert response.json()["error"] == error
        create.assert_not_called()


def test_ambiguous_programme_returns_choices_without_choosing_first():
    with media_api() as (client, app, _), patch.object(app.state.sessions, "create") as create:
        response = client.post("/v1/play-text", json={"query": "News Bulletin"}, headers=AUTH)
        assert response.status_code == 409
        assert response.json()["error"] == "ambiguous_media"
        assert {m["channel_id"] for m in response.json()["matches"]} == {"a" * 24, "b" * 24}
        create.assert_not_called()


def test_explicit_operator_preference_resolves_equivalent_programme_matches():
    with media_api(preferred=True) as (client, _, _):
        response = client.post("/v1/play-text", json={"query": "News Bulletin"}, headers=AUTH)
        assert response.status_code == 200
        assert response.json()["programme"]["channel_id"] == "a" * 24


@pytest.mark.parametrize("route", ["/v1/search", "/v1/play-text"])
def test_stale_catalogue_fails_closed(route):
    with media_api(stale=True) as (client, app, _), patch.object(app.state.sessions, "create") as create:
        assert client.post(route, json={"query": "Example News"}, headers=AUTH).status_code == 503
        create.assert_not_called()


def test_search_selection_is_revalidated_before_a_grant():
    with media_api() as (client, app, catalogue), patch.object(app.state.sessions, "create") as create:
        with patch.object(catalogue, "media_selection", AsyncMock(return_value=[])) as revalidate:
            response = client.post("/v1/play-text", json={"query": "Example News"}, headers=AUTH)
        assert response.status_code == 404
        assert response.json()["error"] == "no_current_programme"
        revalidate.assert_awaited_once()
        create.assert_not_called()


def test_overlapping_epg_is_unavailable_even_for_exact_channel_alias():
    with media_api(overlap=True) as (client, app, _), patch.object(app.state.sessions, "create") as create:
        result = client.post("/v1/play-text", json={"query": "Example News"}, headers=AUTH)
        assert result.status_code == 404
        create.assert_not_called()


@pytest.mark.parametrize("body", [
    {"query": "https://provider.example.test/private"},
    {"query": "Example News", "channel_id": "a" * 24},
    {"query": "Example News", "url": "http://private.example.test"},
    {"query": "x" * 201},
    {"query": ""},
])
def test_invalid_input_never_creates_a_grant(body):
    with media_api() as (client, app, _), patch.object(app.state.sessions, "create") as create:
        assert client.post("/v1/play-text", json=body, headers=AUTH).status_code == 400
        create.assert_not_called()


def test_unknown_cast_target_cannot_create_or_dispatch_a_grant():
    with media_api() as (client, app, _), patch.object(app.state.sessions, "create") as create:
        response = client.post("/v1/play-text", json={"query": "Example News", "target": "other"}, headers=AUTH)
        assert response.status_code == 503
        create.assert_not_called()


def test_automatic_publication_does_not_change_full_catalogue_search():
    with media_api(channel_voice_mode="automatic", channel_voice_limit=1, preferred=True) as (client, _, _):
        published = client.get("/v1/channels", headers=AUTH).json()
        assert len(published["channels"]) == 1
        assert published["channels"][0]["channel_id"] == "a" * 24
        search = client.post("/v1/search", json={"query": "Children Channel"}, headers=AUTH).json()
        assert search["status"] == "matched" and search["matches"][0]["channel_id"] == "c" * 24


def test_programme_selection_rejects_overlap_introduced_after_search():
    with media_api() as (_, _, catalogue):
        result = asyncio.run(catalogue.search_media("Кукутики"))
        candidate = result["matches"][0]
        now = datetime.now(UTC)
        c = Channel("c" * 24, "Children Channel", "private-c", "Children Channel", "http://proxy.example.test/channel/c.m3u8")
        records = [
            ProgramMatch("Кукутики", c.channel_id, c.name, now - timedelta(minutes=5), now + timedelta(minutes=10)),
            ProgramMatch("Other programme", c.channel_id, c.name, now - timedelta(minutes=1), now + timedelta(minutes=3)),
        ]
        catalogue.store.replace([c], records, now.timestamp())
        assert asyncio.run(catalogue.media_selection(candidate)) == []
