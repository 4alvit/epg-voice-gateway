"""Independent boundary regressions using synthetic credentials and media."""

import asyncio
import json
import threading
from urllib.parse import urlsplit

import anyio
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from epg_voice_gateway.app import create_app
from epg_voice_gateway.config import Settings


class StaticCatalog:
    ready = True

    async def resolve(self, query):
        return []


class TrackedStream(httpx.AsyncByteStream):
    def __init__(self, chunks=(), error=None):
        self.chunks = chunks
        self.error = error
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.error is not None:
            raise self.error

    async def aclose(self):
        self.closed = True


def settings():
    return Settings(
        api_token="synthetic-control-token-never-used-in-production",
        signing_key="synthetic-signing-key-never-used-in-production",
        hls_proxy_url="http://proxy.example.test:8080",
        media_base_url="https://media.example.test",
        xmltv_urls=["https://guide.example.test/guide.xml"],
        alexa_skill_id="amzn1.ask.skill.synthetic-test-skill",
    )


def media_path(app):
    manifest = "http://proxy.example.test:8080/channel/demo.m3u8?q=synthetic-upstream-secret"
    playback = app.state.sessions.create(manifest)
    rewritten = app.state.sessions.rewrite_manifest(
        playback.token, manifest,
        "#EXTM3U\n/ts/demo/one.ts?q=synthetic-upstream-secret\n",
    )
    return urlsplit(rewritten.splitlines()[-1]).path


def test_deep_untrusted_json_is_rejected_before_certificate_verification():
    def verifier(*args):
        pytest.fail("Malformed JSON reached certificate verification")

    app = create_app(settings(), catalog=StaticCatalog(), verifier=verifier, start_refresh=False)
    body = '{"nested":' + "[" * 16000 + "0" + "]" * 16000 + "}"
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/alexa", content=body, headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.headers["Cache-Control"] == "no-store"


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_media_redirects_are_closed_and_never_followed_or_reflected(status):
    requests = []
    body = TrackedStream()

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status, stream=body,
            headers={"Location": "http://127.0.0.1/private?synthetic-secret"},
        )

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    app = create_app(settings(), client=upstream, catalog=StaticCatalog(), start_refresh=False)
    with TestClient(app) as client:
        response = client.get(media_path(app))
    assert len(requests) == 1
    assert response.status_code == 502
    assert "Location" not in response.headers
    assert "synthetic" not in response.text
    assert body.closed


def test_range_is_forwarded_without_caller_credentials_and_safe_headers_are_preserved():
    body = TrackedStream([b"1234"])

    def handler(request):
        assert request.headers["Range"] == "bytes=2-5"
        assert request.headers["Accept-Encoding"] == "identity"
        assert "Authorization" not in request.headers
        assert "Cookie" not in request.headers
        assert "CF-Access-Client-Secret" not in request.headers
        return httpx.Response(206, stream=body, headers={
            "Content-Type": "video/mp2t",
            "Content-Length": "4",
            "Content-Range": "bytes 2-5/10",
            "Accept-Ranges": "bytes",
            "Set-Cookie": "credential=synthetic-private-value",
            "X-Upstream-Secret": "synthetic-private-value",
        })

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(settings(), client=upstream, catalog=StaticCatalog(), start_refresh=False)
    with TestClient(app) as client:
        response = client.get(media_path(app), headers={
            "Range": "bytes=2-5",
            "Authorization": "Bearer synthetic-client-secret",
            "Cookie": "session=synthetic-client-secret",
            "CF-Access-Client-Secret": "synthetic-client-secret",
        })
    assert response.status_code == 206
    assert response.content == b"1234"
    assert response.headers["Content-Range"] == "bytes 2-5/10"
    assert response.headers["Access-Control-Allow-Origin"] == "*"
    assert response.headers["Cache-Control"] == "no-store"
    assert "Set-Cookie" not in response.headers
    assert "X-Upstream-Secret" not in response.headers
    assert body.closed


def test_unsatisfiable_media_range_preserves_416_and_content_range():
    body = TrackedStream([b"synthetic-private-error-body"])

    def handler(request):
        return httpx.Response(416, stream=body, headers={
            "Content-Range": "bytes */10",
            "Content-Type": "text/html",
        })

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(settings(), client=upstream, catalog=StaticCatalog(), start_refresh=False)
    with TestClient(app) as client:
        response = client.get(media_path(app), headers={"Range": "bytes=100-200"})
    assert response.status_code == 416
    assert response.headers["Content-Range"] == "bytes */10"
    assert "synthetic" not in response.text
    assert body.closed


def test_stream_read_failure_closes_upstream_without_logging_credentials(caplog):
    body = TrackedStream([b"partial"], error=httpx.ReadError("synthetic-private-upstream-url"))

    def handler(request):
        return httpx.Response(200, stream=body, headers={"Content-Type": "video/mp2t"})

    upstream = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    app = create_app(settings(), client=upstream, catalog=StaticCatalog(), start_refresh=False)
    with TestClient(app, raise_server_exceptions=False) as client:
        client.get(media_path(app))
    assert body.closed
    assert "synthetic-private" not in caplog.text
    assert "synthetic-upstream" not in caplog.text


async def test_disconnect_before_first_media_chunk_closes_upstream():
    body = TrackedStream([b"never-read"])

    def handler(request):
        return httpx.Response(200, stream=body, headers={"Content-Type": "video/mp2t"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(settings(), client=upstream, catalog=StaticCatalog(), start_refresh=False)
        async with app.router.lifespan_context(app):
            path = media_path(app)
            token, resource_name = path.split("/")[-2:]
            request = Request({"type": "http", "app": app, "headers": []})
            route = next(route for route in app.routes if route.path == "/media/{token}/{resource_name}" and "GET" in route.methods)
            response = await route.endpoint(request, token, resource_name, None)

            async def disconnected_send(message):
                raise asyncio.CancelledError()

            async def receive():
                return {"type": "http.disconnect"}

            with pytest.raises(asyncio.CancelledError):
                await response(
                    {"type": "http", "asgi": {"spec_version": "2.4"}},
                    receive, disconnected_send,
                )
            assert body.closed


async def test_cancellation_during_manifest_read_shields_upstream_close():
    cancel_scope = None

    class CancelOnRead(httpx.AsyncByteStream):
        closed = False

        async def __aiter__(self):
            cancel_scope.cancel()
            await anyio.sleep(0)
            yield b"#EXTM3U\n"

        async def aclose(self):
            # A real transport close may contain a cancellation checkpoint.
            await anyio.sleep(0)
            self.closed = True

    body = CancelOnRead()

    def handler(request):
        return httpx.Response(200, stream=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as upstream:
        app = create_app(settings(), client=upstream, catalog=StaticCatalog(), start_refresh=False)
        async with app.router.lifespan_context(app):
            playback = app.state.sessions.create("http://proxy.example.test:8080/channel/demo.m3u8")
            token, resource_name = urlsplit(playback.url).path.split("/")[-2:]
            request = Request({"type": "http", "app": app, "headers": []})
            route = next(route for route in app.routes if route.path == "/media/{token}/{resource_name}" and "GET" in route.methods)
            with anyio.CancelScope() as cancel_scope:
                await route.endpoint(request, token, resource_name, None)
            assert body.closed


async def test_verification_saturation_stays_bounded_after_request_cancellation():
    loop = asyncio.get_running_loop()
    all_started = asyncio.Event()
    all_finished = asyncio.Event()
    release_threads = threading.Event()
    lock = threading.Lock()
    entered = 0
    finished = 0

    def verifier(*args):
        nonlocal entered, finished
        with lock:
            entered += 1
            if entered == 4:
                loop.call_soon_threadsafe(all_started.set)
        try:
            if not release_threads.wait(5):
                raise ValueError("Synthetic verifier was not released")
        finally:
            with lock:
                finished += 1
                if finished == 4:
                    loop.call_soon_threadsafe(all_finished.set)

    app = create_app(settings(), catalog=StaticCatalog(), verifier=verifier, start_refresh=False)
    route = next(route for route in app.routes if route.path == "/alexa")
    payload = json.dumps({
        "context": {"System": {"application": {"applicationId": settings().alexa_skill_id}}},
        "request": {"type": "LaunchRequest"},
    }).encode()

    def request():
        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        return Request({
            "type": "http", "app": app,
            "headers": [
                (b"content-type", b"application/json"),
                (b"signaturecertchainurl", b"https://synthetic.example.test/certificate"),
                (b"signature-256", b"synthetic-signature"),
            ],
        }, receive=receive)

    async with app.router.lifespan_context(app):
        tasks = [asyncio.create_task(route.endpoint(request())) for _ in range(4)]
        try:
            await asyncio.wait_for(all_started.wait(), timeout=2)
            with pytest.raises(HTTPException) as full:
                await route.endpoint(request())
            assert full.value.status_code == 429
            assert entered == 4
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            with pytest.raises(HTTPException) as still_full:
                await route.endpoint(request())
            assert still_full.value.status_code == 429
            assert entered == 4
            release_threads.set()
            await asyncio.wait_for(all_finished.wait(), timeout=2)
            response = await route.endpoint(request())
            assert response["version"] == "1.0"
            assert entered == 5
        finally:
            release_threads.set()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
