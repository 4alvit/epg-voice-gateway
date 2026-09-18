"""Add a user-scoped Channel device while retaining HA's Google OAuth handler."""

import asyncio
from datetime import timedelta

from aiohttp.web import Request, Response
from homeassistant.components.google_assistant.const import SOURCE_CLOUD
from homeassistant.components.google_assistant.smart_home import async_handle_message
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval

from .const import API_PATH, DOMAIN, MAX_REQUEST_BYTES
from .protocol import (
    DISCONNECT,
    SYNC,
    ChannelError,
    Settings,
    advertised_channels,
    handle_message,
    parse_json,
)
from .runtime import Runtime, allowed_user, google_config


async def async_setup(hass: HomeAssistant, _config: dict) -> bool:
    """Register once; the view resolves active config entries for every request."""
    from .services import register_services

    hass.http.register_view(ChannelView(hass))
    register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    settings = Settings.parse(dict(entry.data))
    runtime = Runtime(hass, entry.entry_id, settings)
    entry.runtime_data = runtime
    entry.async_on_unload(async_track_time_interval(hass, runtime.refresh, timedelta(minutes=5)))
    return True


async def async_unload_entry(_hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """The registered route remains inert until an entry is loaded again."""
    entry.runtime_data.close()
    entry.runtime_data.cached_channels.clear()
    entry.runtime_data.synced_channels = None
    return True


class ChannelView(HomeAssistantView):
    url = API_PATH
    name = "api:epg_voice:google_assistant"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def post(self, request: Request) -> Response:
        """Use exactly the identity authenticated by HA's HTTP middleware."""
        entries = [entry for entry in self.hass.config_entries.async_entries(DOMAIN)
                   if entry.state is ConfigEntryState.LOADED]
        if len(entries) != 1:
            return self.json({"error": "integration_unavailable"}, status_code=503)
        runtime = entries[0].runtime_data
        try:
            config = google_config(self.hass, runtime.settings)
        except ChannelError:
            return self.json({"error": "google_integration_unavailable"}, status_code=503)
        if request.content_length is not None and request.content_length > MAX_REQUEST_BYTES:
            return self.json({"error": "request_too_large"}, status_code=413)
        if request.content_type != "application/json":
            return self.json({"error": "invalid_content_type"}, status_code=415)
        try:
            async with asyncio.timeout(1):
                raw = bytearray()
                async for chunk in request.content.iter_chunked(8192):
                    raw.extend(chunk)
                    if len(raw) > MAX_REQUEST_BYTES:
                        return self.json({"error": "request_too_large"}, status_code=413)
                message = parse_json(raw)
        except TimeoutError:
            return self.json({"error": "request_timeout"}, status_code=408)
        except (ValueError, UnicodeError, RecursionError):
            return self.json({"error": "invalid_json"}, status_code=400)
        user = request["hass_user"]
        authorized = allowed_user(user, runtime.settings)

        async def delegate(stock_message):
            return await async_handle_message(
                self.hass, config, user.id, user.id, stock_message, SOURCE_CLOUD,
            )

        async def catalogue():
            is_sync = message["inputs"][0]["intent"] == SYNC
            if not is_sync and not runtime.target_available():
                raise ChannelError()
            channels = await runtime.catalogue(cached_fallback=is_sync)
            if is_sync:
                runtime.synced_channels = advertised_channels(channels)
            return channels

        async def play(executions):
            await runtime.play(user, executions, message["requestId"])

        result = await handle_message(
            message, runtime.device_id, authorized, delegate, catalogue, play,
        )
        if (
            authorized and isinstance(message, dict) and isinstance(message.get("inputs"), list)
            and len(message["inputs"]) == 1 and isinstance(message["inputs"][0], dict)
            and message["inputs"][0].get("intent") == DISCONNECT
        ):
            runtime.cached_channels.clear()
            runtime.synced_channels = None
            runtime.completed_requests.clear()
        return self.json(result)
