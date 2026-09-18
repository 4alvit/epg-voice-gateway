"""Bounded control API and authenticated streaming adapter for an external HLS proxy."""

import asyncio
import hmac
import json
import logging
import re
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from time import monotonic
from typing import Annotated

import anyio
import httpx
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .alexa import AlexaAdapter
from .apl_video import capability_reason, capability_summary
from .catalog import Catalog
from .config import Settings
from .errors import GatewayError, PlaybackUnavailable
from .google import HomeAssistantCaster
from .media import MediaError, MediaSessionStore
from .signature import verify_amazon_request

LOGGER = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


class Query(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)


class TextPlayQuery(Query):
    target: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")


class PlayQuery(Query):
    query: str | None = Field(default=None, max_length=200)
    target: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    channel_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{24}$")
    exact_title: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def require_query_or_channel(self):
        if self.query is not None and not self.query.strip():
            self.query = None
        if self.query is None and self.channel_id is None:
            raise ValueError("A programme query or channel ID is required")
        if self.exact_title and (self.query is None or self.channel_id is None):
            raise ValueError("Exact title selection requires a query and channel ID")
        return self


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


async def _body(request: Request, limit: int = 32768) -> tuple[str, dict]:
    if request.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
        raise HTTPException(415, "application/json required")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > limit:
            raise HTTPException(413, "Request too large")
    try:
        text = data.decode("utf-8")
        result = json.loads(text, object_pairs_hook=_unique_object)
        if not isinstance(result, dict):
            raise ValueError()
        return text, result
    except (ValueError, UnicodeError, RecursionError):
        raise HTTPException(400, "Invalid JSON request") from None


class MediaStreamingResponse(StreamingResponse):
    """Close the upstream even when the client disconnects before the first chunk."""

    def __init__(self, content, *, upstream, **kwargs):
        super().__init__(content, **kwargs)
        self.upstream = upstream

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            await _close_upstream(self.upstream)


async def _close_upstream(upstream):
    with anyio.CancelScope(shield=True):
        await upstream.aclose()


def _programme(match):
    result = asdict(match)
    result["starts_at"] = match.starts_at.isoformat()
    result["ends_at"] = match.ends_at.isoformat()
    return result


def create_app(
    settings: Settings | None = None,
    *,
    client: httpx.AsyncClient | None = None,
    catalog=None,
    verifier=verify_amazon_request,
    start_refresh: bool = True,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        config = settings or Settings()
        http = client or httpx.AsyncClient(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(20, connect=3),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
        guide = catalog or Catalog(config, http)
        sessions = MediaSessionStore(
            config.hls_proxy_url, config.media_base_url,
            config.signing_key.get_secret_value().encode(),
            lifetime_seconds=config.session_ttl_seconds,
        )
        caster = HomeAssistantCaster(
            http, config.ha_url, config.ha_token.get_secret_value(), config.ha_targets
        ) if config.ha_url else None

        async def session_url(match):
            try:
                return sessions.create(guide.stream_url(match)).url
            except MediaError:
                raise PlaybackUnavailable() from None

        app.state.config, app.state.client = config, http
        app.state.catalog, app.state.sessions, app.state.caster = guide, sessions, caster
        app.state.alexa = AlexaAdapter(
            guide.resolve, session_url,
            media_resolver=getattr(guide, "search_media", None),
            media_selection=getattr(guide, "media_selection", None),
        )
        app.state.verification_slots = asyncio.Semaphore(4)
        # An explicit operator diagnostic window contains no request identities.
        app.state.capability_diagnostics_until = monotonic() + 600
        app.state.capability_diagnostics_remaining = (
            20 if config.alexa_capability_diagnostics else 0
        )
        task = asyncio.create_task(guide.run()) if start_refresh else None
        try:
            yield
        finally:
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            if isinstance(guide, Catalog):
                guide.close()
            if client is None:
                await http.aclose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def private_responses(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(GatewayError)
    async def gateway_error(request, error):
        return JSONResponse({"error": error.code}, status_code=error.status_code)

    @app.exception_handler(MediaError)
    async def media_error(request, error):
        return JSONResponse({"error": error.code}, status_code=error.status_code)

    def authorize(request):
        supplied = request.headers.get("authorization", "")
        expected = "Bearer " + request.app.state.config.api_token.get_secret_value()
        if not hmac.compare_digest(supplied.encode(), expected.encode()):
            raise HTTPException(401, "Authentication required")

    @app.get("/healthz")
    async def health():
        return {"status": "ok"}

    @app.get("/readyz")
    async def ready(request: Request):
        if not request.app.state.catalog.ready:
            return JSONResponse({"status": "not_ready"}, status_code=503)
        return {"status": "ready"}

    @app.post("/v1/resolve")
    async def resolve(request: Request):
        authorize(request)
        _, data = await _body(request)
        try:
            query = Query.model_validate(data)
        except ValidationError:
            raise HTTPException(400, "Invalid search request") from None
        matches = await request.app.state.catalog.resolve(query.query)
        return {"matches": [_programme(match) for match in matches]}

    @app.get("/v1/catalogue")
    async def catalogue(request: Request):
        authorize(request)
        return await request.app.state.catalog.voice_catalogue()

    @app.post("/v1/search")
    async def search(request: Request):
        authorize(request)
        _, data = await _body(request)
        try:
            query = Query.model_validate(data)
            return await request.app.state.catalog.search_media(query.query)
        except (ValidationError, ValueError):
            raise HTTPException(400, "Invalid media search request") from None

    @app.post("/v1/play-text")
    async def play_text(request: Request):
        authorize(request)
        _, data = await _body(request)
        state = request.app.state
        try:
            query = TextPlayQuery.model_validate(data)
            result = await state.catalog.search_media(query.query)
        except (ValidationError, ValueError):
            raise HTTPException(400, "Invalid media playback request") from None
        if result["status"] == "ambiguous":
            return JSONResponse(
                {"error": "ambiguous_media", "matches": result["matches"]}, status_code=409,
            )
        if result["status"] == "unavailable":
            return JSONResponse({"error": "media_unavailable"}, status_code=404)
        if result["status"] != "matched" or len(result["matches"]) != 1:
            return JSONResponse({"error": "no_media_match"}, status_code=404)
        # Never create a grant from a stale search result, guessed URL, or model output.
        matches = await state.catalog.media_selection(result["matches"][0])
        if len(matches) != 1:
            return JSONResponse({"error": "no_current_programme"}, status_code=404)
        if query.target and (state.caster is None or query.target not in state.config.ha_targets):
            raise PlaybackUnavailable()
        match = matches[0]
        session = state.sessions.create(state.catalog.stream_url(match))
        if query.target:
            try:
                await state.caster.play(query.target, session.url)
            except GatewayError:
                state.sessions.revoke(session.token)
                raise
        return {
            "status": "playing" if query.target else "ready",
            "programme": _programme(match),
            "media_url": session.url,
            "expires_at": session.expires_at,
        }

    @app.get("/v1/channels")
    async def channels(request: Request):
        authorize(request)
        return await request.app.state.catalog.channel_catalogue()

    @app.post("/v1/play")
    async def play(request: Request):
        authorize(request)
        _, data = await _body(request)
        try:
            query = PlayQuery.model_validate(data)
        except ValidationError:
            raise HTTPException(400, "Invalid playback request") from None
        state = request.app.state
        if query.query is None:
            matches = await state.catalog.current_on_channel(query.channel_id)
        else:
            if query.channel_id:
                if query.exact_title:
                    matches = await state.catalog.resolve(
                        query.query, channel_id=query.channel_id, exact_title=True,
                    )
                else:
                    matches = await state.catalog.resolve(query.query, channel_id=query.channel_id)
                matches = [match for match in matches if match.channel_id == query.channel_id]
            else:
                matches = await state.catalog.resolve(query.query)
        if not matches:
            return JSONResponse({"error": "no_current_programme"}, status_code=404)
        if len(matches) != 1:
            return JSONResponse({"error": "ambiguous_programme", "matches": [_programme(m) for m in matches]}, status_code=409)
        if query.target and (state.caster is None or query.target not in state.config.ha_targets):
            raise PlaybackUnavailable()
        match = matches[0]
        session = state.sessions.create(state.catalog.stream_url(match))
        if query.target:
            try:
                await state.caster.play(query.target, session.url)
            except GatewayError:
                state.sessions.revoke(session.token)
                raise
        return {
            "status": "playing" if query.target else "ready",
            "programme": _programme(match),
            "media_url": session.url,
            "expires_at": session.expires_at,
        }

    @app.post("/alexa")
    async def alexa(request: Request):
        state = request.app.state
        if not state.config.alexa_skill_id:
            return JSONResponse({"error": "skill_not_configured"}, status_code=503)
        text, data = await _body(request)
        try:
            identifiers = [
                data.get("context", {}).get("System", {}).get("application", {}).get("applicationId"),
                data.get("session", {}).get("application", {}).get("applicationId"),
            ]
        except (AttributeError, TypeError):
            raise HTTPException(400, "Invalid Alexa request") from None
        actual = [identifier for identifier in identifiers if identifier is not None]
        headers = {name: request.headers.get(name, "") for name in ("signaturecertchainurl", "signature-256")}
        if (
            not actual or any(identifier != state.config.alexa_skill_id for identifier in actual)
            or any(not value or len(value) > 8192 for value in headers.values())
        ):
            raise HTTPException(400, "Invalid Alexa request")
        try:
            async with asyncio.timeout(0.1):
                await state.verification_slots.acquire()
        except TimeoutError:
            raise HTTPException(429, "Try again shortly") from None
        verification = asyncio.create_task(asyncio.to_thread(verifier, headers, text))

        def completed(task):
            state.verification_slots.release()
            # A timed-out HTTP request must not leave an unobserved task exception.
            if not task.cancelled():
                task.exception()

        verification.add_done_callback(completed)
        stage = "verification"
        deadline = asyncio.timeout(6)
        try:
            async with deadline:
                # Keep its slot occupied until the thread exits, even on timeout.
                await asyncio.shield(verification)
                stage = "handler"
                if (
                    state.capability_diagnostics_remaining > 0
                    and monotonic() < state.capability_diagnostics_until
                ):
                    capabilities = capability_summary(data)
                    state.capability_diagnostics_remaining -= 1
                    LOGGER.warning(
                        "alexa_device_capabilities video_app=%s apl=%s apl_video=%s "
                        "apl_video_reason=%s",
                        capabilities["video_app"], capabilities["apl"],
                        capabilities["apl_video"], capability_reason(data),
                    )
                return await state.alexa.handle(data)
        except GatewayError:
            raise
        except Exception:
            if deadline.expired():
                # Fixed categories only: never log signed bodies, URLs or identities.
                LOGGER.warning("alexa_request_deadline_exceeded stage=%s", stage)
            # Certificate and SDK failures fail closed without logging request data.
            raise HTTPException(400, "Invalid Alexa request") from None

    @app.get("/media/{token}/{resource_name}")
    async def media(request: Request, token: str, resource_name: str, range_header: Annotated[str | None, Header(alias="Range")] = None):
        state = request.app.state
        resource = state.sessions.resolve(token, resource_name)
        headers = {"Accept-Encoding": "identity"}
        if range_header is not None:
            if len(range_header) > 100 or not re.fullmatch(r"bytes=(?:[0-9]+-[0-9]*|-[0-9]+)", range_header):
                raise HTTPException(400, "Unsupported byte range")
            if resource.content_kind != "manifest":
                headers["Range"] = range_header
        try:
            upstream = await state.client.send(
                state.client.build_request("GET", resource.upstream_url, headers=headers),
                stream=True, follow_redirects=False,
            )
        except httpx.HTTPError:
            raise HTTPException(502, "Media temporarily unavailable") from None
        response_headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "Content-Range,Accept-Ranges"}
        if upstream.status_code == 416:
            content_range = upstream.headers.get("content-range", "")
            if re.fullmatch(r"bytes \*/[0-9]+", content_range):
                response_headers["Content-Range"] = content_range
            await _close_upstream(upstream)
            return Response(status_code=416, headers=response_headers)
        if upstream.status_code not in (200, 206):
            await _close_upstream(upstream)
            raise HTTPException(502, "Media temporarily unavailable")
        if resource.content_kind == "manifest":
            try:
                data = bytearray()
                async for chunk in upstream.aiter_bytes(65536):
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        raise HTTPException(502, "Media manifest too large")
                rewritten = state.sessions.rewrite_manifest(token, resource.upstream_url, data.decode("utf-8"))
                return Response(rewritten, media_type="application/vnd.apple.mpegurl", headers=response_headers)
            except (UnicodeError, httpx.HTTPError):
                raise HTTPException(502, "Invalid media manifest") from None
            finally:
                await _close_upstream(upstream)

        for name in ("content-type", "content-length", "content-range", "accept-ranges", "content-encoding"):
            if name in upstream.headers:
                response_headers[name] = upstream.headers[name]

        async def chunks():
            try:
                async for chunk in upstream.aiter_raw(65536):
                    yield chunk
            except httpx.HTTPError:
                LOGGER.warning("media_stream_interrupted")
            finally:
                await _close_upstream(upstream)

        return MediaStreamingResponse(chunks(), upstream=upstream, status_code=upstream.status_code, headers=response_headers)

    @app.options("/media/{token}/{resource_name}")
    async def media_options(request: Request, token: str, resource_name: str):
        request.app.state.sessions.resolve(token, resource_name)
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, OPTIONS",
            "Access-Control-Allow-Headers": "Range",
        })

    return app


app = create_app()
