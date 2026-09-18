"""Authenticated free-text playback on the entry's fixed receiver."""

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import SupportsResponse

from .const import DOMAIN
from .text import failure, validate_query


def _query(value):
    try:
        return validate_query(value)
    except ValueError as error:
        raise vol.Invalid(str(error)) from None


def register_services(hass):
    """Keep the action discoverable even while its config entry is unloaded."""
    async def play_query(call):
        user_id = call.context.user_id
        if user_id is None:
            return failure("not_authorized")
        entries = [entry for entry in hass.config_entries.async_entries(DOMAIN)
                   if entry.state is ConfigEntryState.LOADED]
        if len(entries) != 1:
            return failure("integration_unavailable")
        user = await hass.auth.async_get_user(user_id)
        return await entries[0].runtime_data.play_query(user, call.data["query"])

    hass.services.async_register(
        DOMAIN, "play_query", play_query,
        schema=vol.Schema({vol.Required("query"): _query}),
        supports_response=SupportsResponse.OPTIONAL,
    )
