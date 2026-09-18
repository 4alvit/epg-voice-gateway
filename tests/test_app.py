"""HTTP control-to-media flows; all upstreams and Amazon verification are synthetic."""

import asyncio
import json
import re
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from urllib.parse import urlsplit

import httpx
import pytest
import yaml
from fastapi.testclient import TestClient

from epg_voice_gateway.app import create_app
from epg_voice_gateway.catalog import Catalog
from epg_voice_gateway.config import Settings
from epg_voice_gateway.errors import CatalogUnavailable
from epg_voice_gateway.models import ProgramMatch

API_TOKEN = "synthetic-control-token-for-api-tests-only"
SKILL_ID = "amzn1.ask.skill.synthetic-api-test"
AUTH = {"Authorization": f"Bearer {API_TOKEN}"}
AMAZON_HEADERS = {
    "Content-Type": "application/json",
    "SignatureCertChainUrl": "https://certificate.example.test/synthetic.pem",
    "Signature-256": "synthetic-signature",
}
PROXY = "http://proxy.example.test:8080"


def program(channel_id="a" * 24, channel_name="Kids Channel"):
    now = datetime.now(UTC)
    return ProgramMatch(
        "Kukutiki", channel_id, channel_name,
        now - timedelta(minutes=5), now + timedelta(minutes=25),
    )


class StubCatalog:
    ready = True

    def __init__(self, matches):
        self.resolve = AsyncMock(return_value=matches)
        self.current_on_channel = AsyncMock(side_effect=lambda channel_id: [
            match for match in matches if match.channel_id == channel_id
        ])
        self.stream_url = Mock(side_effect=lambda match: (
            f"{PROXY}/channel/{match.channel_id}/index.m3u8?secret=synthetic-upstream"
        ))


class MediaBytes(httpx.AsyncByteStream):
    def __init__(self, data):
        self.data = data

    async def __aiter__(self):
        yield self.data


@pytest.fixture
def api():
    @contextmanager
    def opened(*, matches=None, handler=None, verifier=None, **settings_overrides):
        values = {
            "api_token": API_TOKEN,
            "signing_key": "synthetic-media-signing-key-for-api-tests-only",
            "hls_proxy_url": PROXY,
            "media_base_url": "https://media.example.test",
            "xmltv_urls": ["https://guide.example.test/guide.xml"],
            "alexa_skill_id": SKILL_ID,
            "ha_url": "https://ha.example.test",
            "ha_token": "synthetic-ha-token",
            "ha_targets": {"kitchen": "media_player.kitchen_display"},
        }
        values.update(settings_overrides)
        guide = StubCatalog([program()] if matches is None else matches)
        requests = []

        def capture(request):
            requests.append(request)
            return handler(request) if handler else httpx.Response(404)

        upstream = httpx.AsyncClient(transport=httpx.MockTransport(capture))
        verify = verifier or Mock()
        app = create_app(
            Settings(**values), client=upstream, catalog=guide,
            verifier=verify, start_refresh=False,
        )
        try:
            with TestClient(app) as client:
                yield SimpleNamespace(
                    client=client, app=app, catalog=guide, requests=requests, verifier=verify,
                )
        finally:
            asyncio.run(upstream.aclose())

    return opened


def alexa_request(*, interfaces=None):
    return {
        "version": "1.0",
        "context": {
            "System": {
                "application": {"applicationId": SKILL_ID},
                "device": {
                    "supportedInterfaces": {"VideoApp": {}} if interfaces is None else interfaces,
                },
            },
        },
        "session": {"application": {"applicationId": SKILL_ID}},
        "request": {
            "type": "IntentRequest",
            "intent": {
                "name": "PlayProgramIntent",
                "slots": {"Query": {"value": "Kukutiki"}},
            },
        },
    }


@pytest.mark.parametrize("path", ["/v1/resolve", "/v1/play"])
@pytest.mark.parametrize("authorization", [None, "Bearer incorrect", "Basic synthetic"])
def test_control_authentication_precedes_catalog_and_session_creation(api, path, authorization):
    with api() as run:
        headers = {"Authorization": authorization} if authorization else {}
        with patch.object(run.app.state.sessions, "create") as create:
            response = run.client.post(path, headers=headers, json={"query": "Kukutiki"})
        assert response.status_code == 401
        assert response.headers["Cache-Control"] == "no-store"
        run.catalog.resolve.assert_not_awaited()
        run.catalog.current_on_channel.assert_not_awaited()
        create.assert_not_called()
        assert run.requests == []


def test_resolve_returns_only_public_program_fields_and_does_not_create_session(api):
    match = program()
    with api(matches=[match]) as run:
        with patch.object(run.app.state.sessions, "create") as create:
            response = run.client.post("/v1/resolve", headers=AUTH, json={"query": "Kukutiki"})
        assert response.status_code == 200
        assert response.json() == {"matches": [{
            "title": match.title,
            "channel_id": match.channel_id,
            "channel_name": match.channel_name,
            "starts_at": match.starts_at.isoformat(),
            "ends_at": match.ends_at.isoformat(),
        }]}
        run.catalog.resolve.assert_awaited_once_with("Kukutiki")
        run.catalog.stream_url.assert_not_called()
        create.assert_not_called()


@pytest.mark.parametrize("matches,status,error", [
    ([], 404, "no_current_programme"),
    ([program(), program("b" * 24, "Another Channel")], 409, "ambiguous_programme"),
])
def test_no_match_and_ambiguity_never_start_playback(api, matches, status, error):
    with api(matches=matches) as run:
        with patch.object(run.app.state.sessions, "create") as create:
            response = run.client.post("/v1/play", headers=AUTH, json={"query": "Kukutiki"})
        assert response.status_code == status
        assert response.json()["error"] == error
        assert "media_url" not in response.json()
        create.assert_not_called()
        run.catalog.stream_url.assert_not_called()
        assert run.requests == []


def test_selected_channel_gets_its_own_scoped_playback_session(api):
    first, selected = program(), program("b" * 24, "Second Channel")
    with api(matches=[first, selected]) as run:
        response = run.client.post("/v1/play", headers=AUTH, json={
            "query": "Kukutiki", "channel_id": selected.channel_id,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["programme"]["channel_id"] == selected.channel_id
        assert body["expires_at"] > datetime.now(UTC).timestamp()
        media = urlsplit(body["media_url"])
        assert media.scheme == "https" and media.hostname == "media.example.test"
        token, resource_name = media.path.split("/")[-2:]
        resource = run.app.state.sessions.resolve(token, resource_name)
        assert f"/channel/{selected.channel_id}/" in resource.upstream_url
        assert "synthetic-upstream" not in response.text
        run.catalog.stream_url.assert_called_once_with(selected)
        run.catalog.resolve.assert_awaited_once_with("Kukutiki", channel_id=selected.channel_id)
        run.catalog.current_on_channel.assert_not_awaited()
        assert run.requests == []


def test_unknown_selected_channel_does_not_fall_back_to_another_match(api):
    with api() as run:
        response = run.client.post("/v1/play", headers=AUTH, json={
            "query": "Kukutiki", "channel_id": "f" * 24,
        })
        assert response.status_code == 404
        run.catalog.stream_url.assert_not_called()
        run.catalog.current_on_channel.assert_not_awaited()


def test_title_filter_does_not_fall_back_to_a_saved_channel_airing_another_title(api):
    selected = program()
    with api(matches=[selected]) as run:
        run.catalog.resolve.return_value = []
        response = run.client.post("/v1/play", headers=AUTH, json={
            "query": "A Different Programme", "channel_id": selected.channel_id,
        })
        assert response.status_code == 404
        run.catalog.resolve.assert_awaited_once_with("A Different Programme", channel_id=selected.channel_id)
        run.catalog.current_on_channel.assert_not_awaited()
        run.catalog.stream_url.assert_not_called()


@pytest.mark.parametrize("optional_query", [{}, {"query": None}, {"query": ""}, {"query": " \t\n"}])
def test_saved_channel_plays_current_programme_without_a_title_query(api, optional_query):
    selected = program("b" * 24, "Favourite Channel")
    with api(matches=[program(), selected]) as run:
        response = run.client.post("/v1/play", headers=AUTH, json={
            "channel_id": selected.channel_id, **optional_query,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["programme"]["channel_id"] == selected.channel_id
        assert body["programme"]["title"] == selected.title
        assert "synthetic-upstream" not in response.text
        token, resource_name = urlsplit(body["media_url"]).path.split("/")[-2:]
        resource = run.app.state.sessions.resolve(token, resource_name)
        assert f"/channel/{selected.channel_id}/" in resource.upstream_url
        run.catalog.current_on_channel.assert_awaited_once_with(selected.channel_id)
        run.catalog.resolve.assert_not_awaited()
        run.catalog.stream_url.assert_called_once_with(selected)
        assert run.requests == []


@pytest.mark.parametrize("matches,status,error", [
    ([], 404, "no_current_programme"),
    ([program("b" * 24)], 404, "no_current_programme"),
    ([program(), program()], 409, "ambiguous_programme"),
])
def test_saved_channel_without_unique_current_programme_never_starts_playback(api, matches, status, error):
    with api(matches=matches) as run:
        with patch.object(run.app.state.sessions, "create") as create:
            response = run.client.post("/v1/play", headers=AUTH, json={"channel_id": "a" * 24})
        assert response.status_code == status
        assert response.json()["error"] == error
        assert "media_url" not in response.json()
        run.catalog.resolve.assert_not_awaited()
        run.catalog.stream_url.assert_not_called()
        create.assert_not_called()


@pytest.mark.parametrize("payload", [
    {}, {"target": "kitchen"}, {"query": None}, {"query": " \t\n"},
    {"channel_id": ""}, {"channel_id": "A" * 24}, {"channel_id": "../channel"},
    {"channel_id": "https://other.example.test/live.m3u8"},
    {"query": 123, "channel_id": "a" * 24},
    {"query": "x" * 201, "channel_id": "a" * 24},
    {"channel_id": "a" * 24, "url": "https://other.example.test/live.m3u8"},
])
def test_invalid_saved_channel_requests_are_rejected_before_catalog_access(api, payload):
    with api() as run:
        with patch.object(run.app.state.sessions, "create") as create:
            response = run.client.post("/v1/play", headers=AUTH, json=payload)
        assert response.status_code == 400
        run.catalog.resolve.assert_not_awaited()
        run.catalog.current_on_channel.assert_not_awaited()
        create.assert_not_called()


def test_saved_channel_stale_catalog_error_does_not_create_session(api):
    with api() as run:
        run.catalog.current_on_channel.side_effect = CatalogUnavailable()
        with patch.object(run.app.state.sessions, "create") as create:
            response = run.client.post("/v1/play", headers=AUTH, json={"channel_id": "a" * 24})
        assert response.status_code == 503
        assert response.json() == {"error": "catalog_unavailable"}
        run.catalog.stream_url.assert_not_called()
        create.assert_not_called()


def test_expired_catalogue_keeps_service_and_existing_media_available():
    now = datetime.now(UTC)
    start, end = now - timedelta(hours=1), now + timedelta(hours=1)
    clock = [1.0]
    feed_available = [True]
    playlist = (
        '#EXTM3U\n#EXTINF:-1 tvg-id="news",News Channel\n'
        f'{PROXY}/channel/news/index.m3u8\n'
    )
    xmltv = (
        '<tv><channel id="news"><display-name>News Channel</display-name></channel>'
        f'<programme channel="news" start="{start:%Y%m%d%H%M%S %z}" '
        f'stop="{end:%Y%m%d%H%M%S %z}"><title>Daily News</title></programme></tv>'
    )

    def handler(request):
        if request.url.path == "/playlist.m3u8":
            return httpx.Response(200, text=playlist)
        if request.url.path == "/guide.xml":
            return httpx.Response(200, text=xmltv) if feed_available[0] else httpx.Response(503)
        if request.url.path == "/channel/news/index.m3u8":
            return httpx.Response(200, text=(
                '#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\n/ts/news.ts\n'
            ))
        if request.url.path == "/ts/news.ts":
            return httpx.Response(200, stream=MediaBytes(b"synthetic-video"), headers={
                "Content-Type": "video/mp2t",
            })
        pytest.fail("Unexpected upstream resource")

    config = Settings(
        api_token=API_TOKEN,
        signing_key="synthetic-media-signing-key-for-api-tests-only",
        hls_proxy_url=PROXY,
        media_base_url="https://media.example.test",
        xmltv_urls=["https://guide.example.test/guide.xml"],
    )
    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    catalog = Catalog(config, upstream, clock=lambda: clock[0])
    manifest = Path(__file__).parents[1] / "deploy" / "k3s.yaml"
    deployment = next(item for item in yaml.safe_load_all(manifest.read_text())
                      if item and item.get("kind") == "Deployment")
    probe_path = deployment["spec"]["template"]["spec"]["containers"][0]["readinessProbe"]["httpGet"]["path"]
    try:
        asyncio.run(catalog.refresh())
        app = create_app(config, client=upstream, catalog=catalog, start_refresh=False)
        with TestClient(app) as client:
            assert client.get("/readyz").status_code == 200
            playback = client.post("/v1/play", headers=AUTH, json={"query": "Daily News"})
            assert playback.status_code == 200
            media_path = urlsplit(playback.json()["media_url"]).path
            channel_id = playback.json()["programme"]["channel_id"]

            feed_available[0] = False
            with pytest.raises(CatalogUnavailable):
                asyncio.run(catalog.refresh())
            clock[0] += config.max_stale_seconds + 1

            assert client.get("/readyz").status_code == 503
            assert client.get(probe_path).status_code == 200
            with patch.object(app.state.sessions, "create", wraps=app.state.sessions.create) as create:
                for path, payload in (
                    ("/v1/resolve", {"query": "Daily News"}),
                    ("/v1/play", {"query": "Daily News"}),
                    ("/v1/play", {"channel_id": channel_id}),
                    ("/v1/play", {"query": "Daily News", "channel_id": channel_id, "exact_title": True}),
                ):
                    response = client.post(path, headers=AUTH, json=payload)
                    assert response.status_code == 503
                    assert response.json() == {"error": "catalog_unavailable"}
                for path in ("/v1/catalogue", "/v1/channels"):
                    response = client.get(path, headers=AUTH)
                    assert response.status_code == 503
                    assert response.json() == {"error": "catalog_unavailable"}
                create.assert_not_called()

            media = client.get(media_path)
            assert media.status_code == 200
            segment_path = urlsplit(media.text.splitlines()[-1]).path
            segment = client.get(segment_path)
            assert segment.status_code == 200
            assert segment.content == b"synthetic-video"
    finally:
        catalog.close()
        asyncio.run(upstream.aclose())


@pytest.mark.parametrize("target,status", [
    ("bedroom", 503), ("media_player.arbitrary", 400), ("../service", 400),
])
def test_unapproved_targets_do_not_create_or_dispatch_playback(api, target, status):
    with api() as run:
        with patch.object(run.app.state.sessions, "create") as create:
            response = run.client.post("/v1/play", headers=AUTH, json={
                "query": "Kukutiki", "target": target,
            })
        assert response.status_code == status
        create.assert_not_called()
        assert run.requests == []


@pytest.mark.parametrize("selection", [{"query": "Kukutiki"}, {"channel_id": "a" * 24}])
def test_cast_success_dispatches_the_same_scoped_url_returned_to_caller(api, selection):
    def handler(request):
        assert str(request.url) == "https://ha.example.test/api/services/media_player/play_media"
        return httpx.Response(200, json=[])

    with api(handler=handler) as run:
        response = run.client.post("/v1/play", headers=AUTH, json={
            **selection, "target": "kitchen",
        })
        assert response.status_code == 200
        assert response.json()["status"] == "playing"
        assert len(run.requests) == 1
        sent = json.loads(run.requests[0].content)
        assert sent["entity_id"] == "media_player.kitchen_display"
        assert sent["media_content_id"] == response.json()["media_url"]
        assert "synthetic-upstream" not in str(sent)


def test_cast_failure_revokes_the_created_session_and_hides_ha_response(api):
    with api(handler=lambda request: httpx.Response(503, text="synthetic-private-HA-error")) as run:
        response = run.client.post("/v1/play", headers=AUTH, json={
            "query": "Kukutiki", "target": "kitchen",
        })
        assert response.status_code == 503
        assert response.json() == {"error": "playback_unavailable"}
        assert len(run.requests) == 1
        dispatched_url = json.loads(run.requests[0].content)["media_content_id"]
        revoked = run.client.get(urlsplit(dispatched_url).path)
        assert revoked.status_code == 404
        assert revoked.json() == {"error": "media_session_not_found"}
        assert len(run.requests) == 1


def test_catalog_failure_is_a_safe_control_api_error(api):
    with api() as run:
        run.catalog.resolve.side_effect = CatalogUnavailable("synthetic-provider-secret")
        response = run.client.post("/v1/play", headers=AUTH, json={"query": "Kukutiki"})
        assert response.status_code == 503
        assert response.json() == {"error": "catalog_unavailable"}
        assert run.requests == []


@pytest.mark.parametrize("field,value", [
    ("context", None), ("context", []), ("session", []),
    ("context", {"System": None}),
    ("context", {"System": {"application": "malformed"}}),
])
def test_malformed_alexa_identity_structure_is_rejected_before_verification(api, field, value):
    payload = alexa_request()
    payload[field] = value
    with api() as run:
        response = run.client.post("/alexa", headers=AMAZON_HEADERS, json=payload)
        assert response.status_code == 400
        run.verifier.assert_not_called()
        run.catalog.resolve.assert_not_awaited()


@pytest.mark.parametrize("context_id,session_id", [
    ("unapproved-skill", SKILL_ID),
    (SKILL_ID, "unapproved-skill"),
    (None, None),
])
def test_missing_or_conflicting_skill_ids_are_rejected_before_verification(api, context_id, session_id):
    payload = alexa_request()
    payload["context"]["System"]["application"]["applicationId"] = context_id
    payload["session"]["application"]["applicationId"] = session_id
    with api() as run:
        response = run.client.post("/alexa", headers=AMAZON_HEADERS, json=payload)
        assert response.status_code == 400
        run.verifier.assert_not_called()
        run.catalog.resolve.assert_not_awaited()


def test_alexa_verifies_exact_received_body_before_resolving_or_creating_session(api):
    body = json.dumps(alexa_request(), indent=2)
    events = []

    def verify(headers, received):
        events.append("verified")
        assert headers["signature-256"] == AMAZON_HEADERS["Signature-256"]
        assert received == body

    with api(verifier=verify) as run:
        match = program()

        async def resolve(query):
            events.append("resolved")
            assert query == "Kukutiki"
            return [match]

        run.catalog.resolve.side_effect = resolve
        response = run.client.post("/alexa", headers=AMAZON_HEADERS, content=body)
        assert response.status_code == 200
        assert events == ["verified", "resolved"]
        directive = response.json()["response"]["directives"][0]
        assert directive["type"] == "VideoApp.Launch"
        assert "shouldEndSession" not in response.json()["response"]
        token, resource = urlsplit(directive["videoItem"]["source"]).path.split("/")[-2:]
        assert run.app.state.sessions.resolve(token, resource).content_kind == "manifest"


def test_capability_diagnostics_are_opt_in_and_exclude_request_values(api, caplog):
    payload = alexa_request(interfaces={
        "VideoApp": {}, "private-interface-marker": {"secret": "private-value-marker"},
    })
    payload["request"] = {"type": "LaunchRequest", "requestId": "private-request-marker"}
    with api() as run:
        assert run.client.post("/alexa", headers=AMAZON_HEADERS, json=payload).status_code == 200
    assert "alexa_device_capabilities" not in caplog.text
    with api(alexa_capability_diagnostics=True) as run:
        assert run.client.post("/alexa", headers=AMAZON_HEADERS, json=payload).status_code == 200
    messages = [r.getMessage() for r in caplog.records if "alexa_device_capabilities" in r.message]
    assert messages == [
        "alexa_device_capabilities video_app=True apl=False apl_video=False "
        "apl_video_reason=missing_apl"
    ]
    assert "private-" not in caplog.text


def test_rejected_signatures_never_produce_capability_diagnostics(api, caplog):
    with api(alexa_capability_diagnostics=True, verifier=Mock(side_effect=ValueError)) as run:
        response = run.client.post("/alexa", headers=AMAZON_HEADERS, json=alexa_request())
        assert response.status_code == 400
        assert run.app.state.capability_diagnostics_remaining == 20
    assert "alexa_device_capabilities" not in caplog.text


@pytest.mark.parametrize("expired", [False, True])
def test_capability_diagnostics_stop_at_count_or_time_limit(api, caplog, expired):
    payload = alexa_request()
    payload["request"] = {"type": "LaunchRequest"}
    with api(alexa_capability_diagnostics=True) as run:
        run.app.state.capability_diagnostics_remaining = 1
        if expired:
            run.app.state.capability_diagnostics_until = 0
        for _ in range(2):
            assert run.client.post("/alexa", headers=AMAZON_HEADERS, json=payload).status_code == 200
    messages = [r for r in caplog.records if "alexa_device_capabilities" in r.message]
    assert len(messages) == (0 if expired else 1)


def test_failed_body_verification_never_reaches_resolution_or_session_creation(api):
    original = json.dumps(alexa_request())
    tampered = original.replace("Kukutiki", "Tampered title")

    def verify(headers, body):
        # Cryptographic acceptance is independently covered by test_signature.py.
        if body != original:
            raise ValueError("synthetic-private-verification-detail")

    with api(verifier=verify) as run:
        with patch.object(run.app.state.sessions, "create") as create:
            response = run.client.post("/alexa", headers=AMAZON_HEADERS, content=tampered)
        assert response.status_code == 400
        assert "synthetic-private" not in response.text
        run.catalog.resolve.assert_not_awaited()
        create.assert_not_called()


@pytest.mark.parametrize("stage", ["verification", "handler"])
def test_alexa_deadline_identifies_stage_without_logging_private_request(api, monkeypatch, caplog, stage):
    release = Event()
    real_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda delay: real_timeout(0.02 if delay == 6 else delay))

    def verify(headers, body):
        if stage == "verification":
            release.wait(2)

    async def handle(payload):
        await asyncio.sleep(2)

    with api(verifier=verify) as run:
        run.app.state.alexa.handle = AsyncMock(side_effect=handle)
        try:
            response = run.client.post("/alexa", headers=AMAZON_HEADERS, json=alexa_request())
            assert response.status_code == 400
            if stage == "verification":
                run.app.state.alexa.handle.assert_not_awaited()
                assert run.app.state.verification_slots._value == 3
            messages = [record.getMessage() for record in caplog.records
                        if record.name == "epg_voice_gateway.app"]
            assert messages == [f"alexa_request_deadline_exceeded stage={stage}"]
            assert SKILL_ID not in caplog.text
            assert AMAZON_HEADERS["SignatureCertChainUrl"] not in caplog.text
            assert AMAZON_HEADERS["Signature-256"] not in caplog.text
        finally:
            release.set()


def test_verified_apl_only_device_gets_explanation_without_media_session(api):
    with api() as run:
        payload = alexa_request(interfaces={"Alexa.Presentation.APL": {}})
        with patch.object(run.app.state.sessions, "create") as create:
            response = run.client.post("/alexa", headers=AMAZON_HEADERS, json=payload)
        assert response.status_code == 200
        run.verifier.assert_called_once()
        assert "does not support video" in response.json()["response"]["outputSpeech"]["text"]
        run.catalog.resolve.assert_not_awaited()
        create.assert_not_called()


def test_api_session_rewrites_recursive_hls_and_serves_key_map_and_ranged_segment(api):
    master = '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=800000\n/play/live/index.m3u8?secret=synthetic-upstream\n'
    variant = (
        '#EXTM3U\n#EXT-X-TARGETDURATION:10\n'
        '#EXT-X-KEY:METHOD=AES-128,URI="/key/live.key?secret=synthetic-upstream"\n'
        '#EXT-X-MAP:URI="/map/init.mp4?secret=synthetic-upstream"\n'
        '#EXTINF:10,\n'
        f'{PROXY}/ts/live.ts?secret=synthetic-upstream\n'
    )

    def handler(request):
        path = request.url.path
        if path.startswith("/channel/") or path == "/play/live/index.m3u8":
            assert "Range" not in request.headers
            text = master if path.startswith("/channel/") else variant
            return httpx.Response(200, text=text)
        if path == "/key/live.key":
            return httpx.Response(200, stream=MediaBytes(b"synthetic-key-123"))
        if path == "/map/init.mp4":
            return httpx.Response(200, stream=MediaBytes(b"synthetic-init"))
        if path == "/ts/live.ts":
            assert request.headers["Range"] == "bytes=0-2"
            return httpx.Response(206, stream=MediaBytes(b"vid"), headers={
                "Content-Type": "video/mp2t", "Content-Range": "bytes 0-2/10",
            })
        pytest.fail("The session fetched an unregistered upstream resource")

    with api(handler=handler) as run:
        playback = run.client.post("/v1/play", headers=AUTH, json={"query": "Kukutiki"})
        media_url = playback.json()["media_url"]
        first = run.client.get(urlsplit(media_url).path, headers={"Range": "bytes=0-0"})
        assert first.status_code == 200
        child_url = first.text.splitlines()[-1]
        second = run.client.get(urlsplit(child_url).path)
        assert second.status_code == 200
        for response in (first, second):
            assert "synthetic-upstream" not in response.text
            assert "proxy.example" not in response.text
            assert response.headers["Content-Type"].startswith("application/vnd.apple.mpegurl")
        key_url, map_url = re.findall(r'URI="([^"]+)"', second.text)
        segment_url = second.text.splitlines()[-1]
        for url in (child_url, key_url, map_url, segment_url):
            assert url.startswith("https://media.example.test/media/")
        assert run.client.get(urlsplit(key_url).path).content == b"synthetic-key-123"
        assert run.client.get(urlsplit(map_url).path).content == b"synthetic-init"
        segment = run.client.get(urlsplit(segment_url).path, headers={"Range": "bytes=0-2"})
        assert segment.status_code == 206 and segment.content == b"vid"
        assert segment.headers["Content-Range"] == "bytes 0-2/10"
        preflight = run.client.options(urlsplit(segment_url).path)
        assert preflight.status_code == 204
        assert preflight.headers["Access-Control-Allow-Headers"] == "Range"
        assert len(run.requests) == 5
