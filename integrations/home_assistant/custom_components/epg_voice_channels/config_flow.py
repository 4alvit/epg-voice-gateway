"""Configure the optional adapter without changing HA's Google integration."""

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import selector

from .const import DEVICE_NAME, DOMAIN
from .protocol import ChannelError, Settings
from .runtime import Runtime, allowed_user, google_config


async def schema(hass):
    google_entries = [
        {"value": entry.entry_id, "label": entry.title}
        for entry in hass.config_entries.async_entries("google_assistant")
        if entry.state is ConfigEntryState.LOADED
    ]
    users = [
        {"value": user.id, "label": user.name or "Unnamed dedicated user"}
        for user in await hass.auth.async_get_users()
        if user.is_active and not user.is_admin and not user.is_owner and not user.local_only
    ]
    return vol.Schema({
        vol.Required("gateway_origin"): str,
        vol.Required("gateway_token"): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        ),
        vol.Required("signed_media_origin"): str,
        vol.Required("google_entry_id"): selector.SelectSelector(
            selector.SelectSelectorConfig(options=google_entries, mode=selector.SelectSelectorMode.DROPDOWN)
        ),
        vol.Required("agent_user_id"): selector.SelectSelector(
            selector.SelectSelectorConfig(options=users, mode=selector.SelectSelectorMode.DROPDOWN)
        ),
        vol.Required("target_entity"): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="media_player")
        ),
    })


class ChannelConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, user_input):
        settings = Settings.parse(user_input)
        user = await self.hass.auth.async_get_user(settings.agent_user_id)
        if not allowed_user(user, settings):
            raise ValueError("Invalid dedicated user")
        google_config(self.hass, settings)
        runtime = Runtime(self.hass, "validation", settings)
        try:
            if not runtime.target_available():
                raise ValueError("Target unavailable")
            await runtime.catalogue()
        finally:
            runtime.close()

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            try:
                await self._validate(user_input)
            except ValueError:
                errors["base"] = "invalid_configuration"
            except ChannelError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(title=DEVICE_NAME, data=user_input)
        return self.async_show_form(step_id="user", data_schema=await schema(self.hass), errors=errors)

    async def async_step_reconfigure(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                await self._validate(user_input)
            except ValueError:
                errors["base"] = "invalid_configuration"
            except ChannelError:
                errors["base"] = "cannot_connect"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reconfigure_entry(), data=user_input,
                )
        return self.async_show_form(step_id="reconfigure", data_schema=await schema(self.hass), errors=errors)
