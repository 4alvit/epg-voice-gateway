"""Bounded gateway calls and permission-preserving HA dispatch."""

import asyncio
import hashlib
import json
import time

from aiohttp import ClientError, ClientTimeout, DummyCookieJar
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_create_clientsession

from .const import MAX_RESPONSE_BYTES, OPERATION_TIMEOUT
from .protocol import (
    ChannelError,
    advertised_channels,
    media_url,
    parse_catalogue,
    parse_json,
    select_channel,
)

TEXT_REQUEST_TIMEOUT = 4
TEXT_OPERATION_TIMEOUT = 8


def google_config(hass, settings):
    """Read the current runtime object; never retain one across a reload."""
    entry = hass.config_entries.async_get_entry(settings.google_entry_id)
    if entry is None or entry.domain != "google_assistant" or entry.state is not ConfigEntryState.LOADED:
        raise ChannelError()
    config = getattr(entry, "runtime_data", None)
    if config is None:
        raise ChannelError()
    return config


def allowed_user(user, settings):
    """Authentication does not grant access to the household's channel target."""
    return bool(
        user and user.id == settings.agent_user_id and user.is_active
        and not user.is_admin and not user.is_owner and not user.local_only
        and user.permissions.check_entity(settings.target_entity, POLICY_CONTROL)
    )


def allowed_text_user(user, settings):
    """Assist keeps its authenticated caller's existing receiver permission."""
    return bool(
        user and user.is_active
        and user.permissions.check_entity(settings.target_entity, POLICY_CONTROL)
    )


class Runtime:
    """Entry-scoped cache holds channel names only, never grants or OAuth tokens."""

    def __init__(self, hass, entry_id, settings):
        self.hass = hass
        self.settings = settings
        self.device_id = f"epg_voice_channels:{entry_id}"
        self.cached_channels = []
        self.synced_channels = None
        self.refreshing = False
        self.active = True
        self.session = None
        self.playing = False
        self.completed_requests = {}

    def close(self):
        """Detach our cookie-free session without closing HA's shared connector."""
        self.active = False
        self.completed_requests.clear()
        if self.session is not None:
            self.session.detach()
            self.session = None

    async def request(self, path, data=None):
        _status, result = await self.request_response(path, data)
        return result

    async def request_response(self, path, data=None, *, accepted_statuses=(200,), timeout=2):
        """No redirects, cookies, caller paths, or provider URLs are accepted."""
        if path not in ("/v1/channels", "/v1/play", "/v1/play-text"):
            raise ChannelError()
        try:
            if self.session is None:
                self.session = async_create_clientsession(
                    self.hass, auto_cleanup=False, cookie_jar=DummyCookieJar(),
                )
            async with self.session.request(
                "GET" if data is None else "POST", self.settings.gateway_origin + path,
                headers={"Authorization": f"Bearer {self.settings.gateway_token}"},
                json=data, allow_redirects=False, timeout=ClientTimeout(total=timeout),
            ) as response:
                if response.status not in accepted_statuses:
                    raise ChannelError("noAvailableChannel" if response.status in (404, 409) else "deviceOffline")
                if response.content_type != "application/json":
                    raise ChannelError()
                raw = bytearray()
                async for chunk in response.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > MAX_RESPONSE_BYTES:
                        raise ChannelError()
                return response.status, parse_json(raw)
        except (ClientError, TimeoutError, ValueError, UnicodeError, RecursionError):
            raise ChannelError() from None

    async def play_query(self, user, query):
        """Resolve free text and dispatch only a validated, unambiguous grant."""
        from .text import failure, grant_channel, selection_response, validate_query

        query = validate_query(query)
        if not self.active or not allowed_text_user(user, self.settings):
            return failure("not_authorized")
        if self.playing:
            return failure("busy")
        self.playing = True
        try:
            async with asyncio.timeout(TEXT_OPERATION_TIMEOUT):
                if not self.target_available():
                    return failure("unavailable")
                status, response = await self.request_response(
                    "/v1/play-text", {"query": query}, accepted_statuses=(200, 404, 409),
                    timeout=TEXT_REQUEST_TIMEOUT,
                )
                if status != 200:
                    return selection_response(status, response)
                channel_id, channel_name = grant_channel(response)
                url = media_url(response, self.settings.signed_media_origin, channel_id)
                current_user = await self.hass.auth.async_get_user(user.id)
                if not self.active or not allowed_text_user(current_user, self.settings):
                    return failure("not_authorized")
                if not self.target_available():
                    return failure("unavailable")
                await self.hass.services.async_call(
                    "media_player", "play_media",
                    {
                        "entity_id": self.settings.target_entity,
                        "media_content_id": url,
                        "media_content_type": "application/vnd.apple.mpegurl",
                    },
                    blocking=True, context=Context(user_id=current_user.id),
                )
                return {
                    "status": "accepted", "message": f"Starting {channel_name}.",
                    "matches": [channel_name],
                }
        except (ChannelError, TimeoutError, HomeAssistantError):
            return failure("unavailable")
        finally:
            self.playing = False

    async def catalogue(self, *, cached_fallback=False):
        try:
            channels = parse_catalogue(await self.request("/v1/channels"))
        except ChannelError:
            if cached_fallback:
                return self.cached_channels
            raise
        self.cached_channels = channels
        return channels

    def target_available(self):
        state = self.hass.states.get(self.settings.target_entity)
        return state is not None and state.state not in ("unavailable", "unknown")

    def registered_config(self, user):
        config = google_config(self.hass, self.settings)
        if user.id not in config.async_get_agent_users():
            raise ChannelError()
        return config

    async def play(self, user, executions, request_id=None):
        """Reject concurrent target changes and deduplicate completed retries."""
        if not self.active or not allowed_user(user, self.settings):
            raise ChannelError()
        self.registered_config(user)
        now = time.monotonic()
        self.completed_requests = {
            key: value for key, value in self.completed_requests.items() if value[0] > now
        }
        key = (user.id, request_id) if request_id is not None else None
        fingerprint = hashlib.sha256(json.dumps(executions, sort_keys=True).encode()).digest()
        if key is not None and key in self.completed_requests:
            _, previous, error = self.completed_requests[key]
            if previous != fingerprint:
                raise ChannelError("protocolError")
            if error is not None:
                raise ChannelError(error)
            return
        if self.playing:
            raise ChannelError("deviceBusy")
        self.playing = True
        error = "channelSwitchFailed"
        try:
            await self._play_once(user, executions)
            error = None
        except ChannelError as err:
            error = err.code
            raise
        except asyncio.CancelledError:
            error = "channelSwitchFailed"
            raise
        finally:
            self.playing = False
            if key is not None and self.active:
                if len(self.completed_requests) >= 128:
                    self.completed_requests.pop(next(iter(self.completed_requests)))
                self.completed_requests[key] = (time.monotonic() + 120, fingerprint, error)

    async def _play_once(self, user, executions):
        """Validate everything before one fixed-target service dispatch.

        SUCCESS means the HA service accepted the request. Device picture, sound
        and sustained playback require separate observation. A timeout during
        dispatch cannot undo an already accepted receiver command.
        """
        try:
            async with asyncio.timeout(OPERATION_TIMEOUT):
                if not self.active or not allowed_user(user, self.settings) or not self.target_available():
                    raise ChannelError()
                self.registered_config(user)
                channels = await self.catalogue()
                channel_id = select_channel(executions, channels)
                response = await self.request("/v1/play", {"channel_id": channel_id})
                url = media_url(response, self.settings.signed_media_origin, channel_id)
                # Permissions and entry availability can change during I/O.
                current_user = await self.hass.auth.async_get_user(user.id)
                if not self.active or not allowed_user(current_user, self.settings):
                    raise ChannelError()
                self.registered_config(current_user)
                await self.hass.services.async_call(
                    "media_player", "play_media",
                    {
                        "entity_id": self.settings.target_entity,
                        "media_content_id": url,
                        "media_content_type": "application/vnd.apple.mpegurl",
                    },
                    blocking=True, context=Context(user_id=current_user.id),
                )
        except (TimeoutError, HomeAssistantError):
            raise ChannelError("channelSwitchFailed") from None

    async def refresh(self, _now=None):
        """Request SYNC for changed names only, at most once per poll interval."""
        if self.refreshing or not self.active:
            return
        self.refreshing = True
        try:
            user = await self.hass.auth.async_get_user(self.settings.agent_user_id)
            if not allowed_user(user, self.settings):
                return
            config = google_config(self.hass, self.settings)
            if user.id not in config.async_get_agent_users():
                return
            channels = await self.catalogue()
            advertised = advertised_channels(channels)
            if advertised != self.synced_channels:
                # The stock method handles a disconnected agent's 404. Never
                # create registrations or request sync for a different user.
                async with asyncio.timeout(5):
                    status = await config.async_sync_entities(user.id)
                if status == 200:
                    self.synced_channels = advertised
        except (ChannelError, ClientError, TimeoutError, HomeAssistantError):
            # Private credentials and grant-bearing payloads must never reach logs.
            pass
        finally:
            self.refreshing = False
