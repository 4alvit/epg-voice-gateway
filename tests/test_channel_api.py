"""The channel API publishes only the configured lineup, without playback effects."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from epg_voice_gateway.app import create_app
from epg_voice_gateway.catalog import Catalog
from epg_voice_gateway.config import Settings
from epg_voice_gateway.models import Channel, ProgramMatch


def test_channel_api_authentication_filtered_privacy_and_no_playback():
    token = "synthetic-channel-api-authentication-secret"
    settings = Settings(
        api_token=token,
        signing_key="synthetic-channel-api-media-signing-secret",
        hls_proxy_url="http://proxy.example.test",
        media_base_url="https://media.example.test",
        xmltv_urls=["https://guide.example.test/synthetic.xml"],
        channel_voice_ids=["b" * 24, "a" * 24],
    )
    now = datetime.now(UTC)
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(500),
    ))
    catalog = Catalog(settings, upstream, clock=lambda: 100.0)
    channels = [
        Channel(letter * 24, name, "private-provider-id-" + letter, alias,
                f"http://proxy.example.test/channel/{letter}?key=private-provider-key")
        for letter, name, alias in (
            ("a", "News Channel", "World News"),
            ("b", "Kids Channel", "Kids Channel"),
            ("c", "Unpublished Channel", "Unpublished Channel"),
        )
    ]
    matches = [
        ProgramMatch("Private schedule title", c.channel_id, c.name,
                     now - timedelta(minutes=1), now + timedelta(minutes=10))
        for c in (channels[0], channels[2])
    ]
    catalog.store.replace(channels, matches, now.timestamp())
    catalog.updated_at = 100.0
    app = create_app(settings, client=upstream, catalog=catalog, start_refresh=False)
    try:
        with TestClient(app) as client, patch.object(app.state.sessions, "create") as create:
            with patch.object(catalog, "channel_catalogue", wraps=catalog.channel_catalogue) as read:
                for headers in ({}, {"Authorization": "Bearer wrong"},
                                {"Authorization": "Basic wrong"}):
                    response = client.get("/v1/channels", headers=headers)
                    assert response.status_code == 401
                    assert response.headers["Cache-Control"] == "no-store"
                read.assert_not_called()
            response = client.get("/v1/channels", headers={"Authorization": "Bearer " + token})
            assert response.status_code == 200
            assert response.headers["Cache-Control"] == "no-store"
            assert response.json()["channels"] == [
                {"channel_id": "b" * 24, "name": "Kids Channel", "synonyms": [],
                 "available": False},
                {"channel_id": "a" * 24, "name": "News Channel", "synonyms": ["World News"],
                 "available": True},
            ]
            assert len(response.json()["revision"]) == 64
            for private_value in ("http", "private-provider", "Private schedule title",
                                  "Unpublished Channel", "media_url"):
                assert private_value not in response.text
            create.assert_not_called()
    finally:
        catalog.close()
        asyncio.run(upstream.aclose())
