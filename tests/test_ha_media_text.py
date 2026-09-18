"""Assist service contracts; no live household commands or speech claims."""

import asyncio
import copy
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import yaml
from test_ha_channel_integration import (
    CHANNEL,
    COMPONENT,
    MEDIA,
    SETTINGS,
    GatewayResponse,
    execution,
    grant,
    load,
    p,
)
from test_ha_channel_integration import (
    runtime as runtime,
)
from test_ha_channel_integration import (
    runtime_module as runtime_module,
)
from test_ha_channel_integration import (
    view_module as view_module,
)

text = load("text")


def text_grant():
    result = grant()
    result["programme"]["channel_name"] = "Example News"
    return result


def prepare(value):
    value.request_response = AsyncMock(return_value=(200, text_grant()))


async def test_text_playback_uses_fixed_receiver_real_grant_and_actual_user(runtime):
    value, user, entry = runtime
    prepare(value)
    user.id = "phone_user"
    user.is_admin = user.is_owner = True
    entry.state = "not_loaded"
    result = await value.play_query(user, "  play Example News  ")
    assert result == {
        "status": "accepted", "message": "Starting Example News.", "matches": ["Example News"],
    }
    value.request_response.assert_awaited_once_with(
        "/v1/play-text", {"query": "play Example News"}, accepted_statuses=(200, 404, 409),
    )
    call = value.hass.services.async_call.await_args
    assert call.args == ("media_player", "play_media", {
        "entity_id": SETTINGS["target_entity"], "media_content_id": MEDIA,
        "media_content_type": "application/vnd.apple.mpegurl",
    })
    assert call.kwargs["context"].user_id == "phone_user"
    assert call.kwargs["blocking"] is True
    assert "media_url" not in json.dumps(result)
    assert MEDIA not in json.dumps(result)
    assert CHANNEL not in json.dumps(result)
    value.request.assert_not_awaited()


@pytest.mark.parametrize("change", ["missing_user", "inactive", "denied", "unloaded", "busy"])
async def test_text_permission_or_busy_denial_never_requests_grant(runtime, change):
    value, user, _ = runtime
    prepare(value)
    if change == "missing_user":
        user = None
    elif change == "inactive":
        user.is_active = False
    elif change == "denied":
        user.permissions.check_entity = lambda *_args: False
    elif change == "unloaded":
        value.active = False
    else:
        value.playing = True
    result = await value.play_query(user, "Example News")
    assert result["status"] == ("busy" if change == "busy" else "not_authorized")
    value.request_response.assert_not_awaited()
    value.hass.services.async_call.assert_not_awaited()


@pytest.mark.parametrize("change", ["revoked", "unloaded", "target_offline"])
async def test_text_rechecks_permission_and_target_after_gateway_io(runtime, change):
    value, user, _ = runtime

    async def response(*_args, **_kwargs):
        if change == "revoked":
            revoked = copy.copy(user)
            revoked.is_active = False
            value.hass.auth.async_get_user.return_value = revoked
        elif change == "unloaded":
            value.active = False
        else:
            value.hass.states.get = lambda *_args: SimpleNamespace(state="unavailable")
        return 200, text_grant()

    value.request_response = AsyncMock(side_effect=response)
    result = await value.play_query(user, "Example News")
    assert result["status"] in ("not_authorized", "unavailable")
    value.hass.services.async_call.assert_not_awaited()
    assert value.playing is False


@pytest.mark.parametrize("status,data,expected", [
    (404, {"error": "no_media_match"}, "no_match"),
    (404, {"error": "media_unavailable"}, "unavailable"),
    (404, {"error": "no_current_programme"}, "unavailable"),
    (409, {"error": "ambiguous_media", "matches": [
        {"channel_name": "Example News", "channel_id": CHANNEL, "media_url": MEDIA},
        {"channel_name": "Example News HD", "reason": "private diagnostic"},
    ]}, "ambiguous"),
    (409, {"error": "ambiguous_media", "matches": [
        {"channel_name": "https://private.example.test/token"},
        {"channel_name": "Safe Name"},
    ]}, "unavailable"),
])
async def test_text_failures_never_play_or_return_raw_gateway_data(runtime, status, data, expected):
    value, user, _ = runtime
    value.request_response = AsyncMock(return_value=(status, data))
    result = await value.play_query(user, "requested title")
    assert result["status"] == expected
    value.hass.services.async_call.assert_not_awaited()
    serialized = json.dumps(result)
    for secret in (MEDIA, CHANNEL, "private", "requested title", "media_url"):
        assert secret not in serialized


@pytest.mark.parametrize("change", ["wrong_origin", "bad_id", "expired", "bad_name", "bad_status"])
async def test_text_invalid_grants_never_reach_player(runtime, change):
    value, user, _ = runtime
    result = text_grant()
    if change == "wrong_origin":
        result["media_url"] = "https://wrong.example.test/stream.m3u8"
    elif change == "bad_id":
        result["programme"]["channel_id"] = "caller supplied path"
    elif change == "expired":
        result["expires_at"] = 1
    elif change == "bad_name":
        result["programme"]["channel_name"] = "<speak>Injected</speak>"
    else:
        result["status"] = "matched"
    value.request_response = AsyncMock(return_value=(200, result))
    assert (await value.play_query(user, "Example News"))["status"] == "unavailable"
    value.hass.services.async_call.assert_not_awaited()


async def test_google_and_text_share_one_concurrency_guard(runtime):
    value, user, _ = runtime
    waiting, release = asyncio.Event(), asyncio.Event()

    async def response(*_args, **_kwargs):
        waiting.set()
        await release.wait()
        return 200, text_grant()

    value.request_response = AsyncMock(side_effect=response)
    first = asyncio.create_task(value.play_query(user, "Example News"))
    await waiting.wait()
    assert (await value.play_query(user, "Other News"))["status"] == "busy"
    with pytest.raises(p.ChannelError, match="deviceBusy"):
        await value.play(user, [execution()], "google-request")
    value.request.assert_not_awaited()
    release.set()
    assert (await first)["status"] == "accepted"
    value.hass.services.async_call.assert_awaited_once()


async def test_text_timeout_releases_guard_without_disclosing_exception(runtime, runtime_module, monkeypatch):
    value, user, _ = runtime
    monkeypatch.setattr(runtime_module, "OPERATION_TIMEOUT", 0.01)

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(10)

    value.request_response = AsyncMock(side_effect=slow)
    assert (await value.play_query(user, "Example News"))["status"] == "unavailable"
    assert value.playing is False
    value.hass.services.async_call.assert_not_awaited()


async def test_text_transport_keeps_typed_errors_bounded_and_credential_free(runtime, runtime_module):
    value, _, _ = runtime
    body = {"error": "ambiguous_media", "matches": [{"channel_name": "A"}, {"channel_name": "B"}]}
    send = Mock(return_value=GatewayResponse(json.dumps(body).encode(), status=409))
    value.hass.session = SimpleNamespace(request=send)
    result = await runtime_module.Runtime.request_response(
        value, "/v1/play-text", {"query": "A"}, accepted_statuses=(200, 404, 409),
    )
    assert result == (409, body)
    assert send.call_args.args == ("POST", SETTINGS["gateway_origin"] + "/v1/play-text")
    assert send.call_args.kwargs["allow_redirects"] is False
    assert send.call_args.kwargs["headers"] == {"Authorization": "Bearer " + SETTINGS["gateway_token"]}
    assert send.call_args.kwargs["timeout"].total == 2
    send.return_value = GatewayResponse(b'{"error":"a","error":"b"}', status=409)
    with pytest.raises(p.ChannelError):
        await runtime_module.Runtime.request_response(value, "/v1/play-text", {}, accepted_statuses=(409,))


@pytest.mark.parametrize("query", [None, 1, "", "  ", "x" * 201, "play\nnews", "play\u200bnews"])
def test_query_validation_has_no_implicit_coercion_or_control_characters(query):
    with pytest.raises(ValueError):
        text.validate_query(query)


def test_query_length_matches_gateway_limit():
    assert text.validate_query("x" * 200) == "x" * 200


@pytest.fixture
def service_module(monkeypatch, runtime_module):
    vol = ModuleType("voluptuous")
    vol.Required = lambda key: key
    vol.Schema = lambda fields: fields
    vol.Invalid = type("Invalid", (Exception,), {})
    monkeypatch.setitem(sys.modules, "voluptuous", vol)
    monkeypatch.setattr(sys.modules["homeassistant.core"], "SupportsResponse",
                        SimpleNamespace(OPTIONAL="optional"), raising=False)
    return load("services")


async def test_service_registration_resolves_user_and_live_entry_without_impersonation(runtime, service_module):
    value, user, _ = runtime
    prepare(value)
    adapter = SimpleNamespace(state="loaded", runtime_data=value)
    value.hass.config_entries.async_entries = lambda _domain: [adapter]
    value.hass.services.async_register = Mock()
    service_module.register_services(value.hass)
    registration = value.hass.services.async_register.call_args
    assert registration.args[:2] == ("epg_voice_channels", "play_query")
    assert registration.kwargs["supports_response"] == "optional"
    assert set(registration.kwargs["schema"]) == {"query"}
    handler = registration.args[2]
    call = SimpleNamespace(context=SimpleNamespace(user_id=user.id), data={"query": "Example News"})
    assert (await handler(call))["status"] == "accepted"
    value.hass.auth.async_get_user.assert_awaited_with(user.id)
    value.request_response.reset_mock()
    call.context.user_id = None
    assert (await handler(call))["status"] == "not_authorized"
    call.context.user_id = user.id
    adapter.state = "not_loaded"
    assert (await handler(call))["status"] == "integration_unavailable"
    value.request_response.assert_not_awaited()


def test_service_schema_reports_sanitized_validation_error(service_module):
    with pytest.raises(service_module.vol.Invalid, match="between 1 and 200"):
        service_module._query("\nprivate input")


async def test_integration_setup_registers_discoverable_action(runtime, service_module, view_module):
    value, _, _ = runtime
    value.hass.http = SimpleNamespace(register_view=Mock())
    value.hass.services.async_register = Mock()
    module, _ = view_module
    assert await module.async_setup(value.hass, {}) is True
    assert value.hass.http.register_view.call_count == 1
    assert value.hass.services.async_register.call_args.args[:2] == ("epg_voice_channels", "play_query")


def test_assist_examples_return_service_response_and_use_explicit_verbs():
    # Custom component layout is integrations/home_assistant/custom_components/domain.
    examples = COMPONENT.parents[3] / "examples/home-assistant"
    intent = yaml.safe_load((examples / "assist-intent-script.yaml").read_text())["EPGVoicePlayMedia"]
    assert intent["action"][0]["action"] == "epg_voice_channels.play_query"
    assert intent["action"][0]["data"] == {"query": "{{ (query_prefix | default('')) ~ query }}"}
    assert intent["action"][1]["response_variable"] == intent["action"][0]["response_variable"]
    assert intent["speech"]["text"] == "{{ action_response.message }}"
    for language in ("en", "ru"):
        data = yaml.safe_load((examples / f"assist-sentences.{language}.yaml").read_text())
        assert data["language"] == language
        assert data["lists"]["query"] == {"wildcard": True}
        assert all(sentence != "{query}" for item in data["intents"]["EPGVoicePlayMedia"]["data"]
                   for sentence in item["sentences"])


def test_optional_hassil_media_sentences_do_not_intercept_home_controls():
    """Run with the deployed HA's Hassil version for grammar acceptance."""
    hassil = pytest.importorskip("hassil")
    examples = COMPONENT.parents[3] / "examples/home-assistant"
    positive = {
        "en": {
            "play Example News": "Example News",
            "watch Example News on home channels": "Example News",
            "show programme Colombo on home channels": "programme Colombo",
        },
        "ru": {
            "играй Кукутики": "Кукутики",
            "включи канал CNN": "channel CNN",
            "покажи передачу Коломбо": "programme Коломбо",
            "включи CNN на экране": "CNN",
            "включи канал CNN на экране": "канал CNN",
            "покажи Коломбо на home channels": "Коломбо",
        },
    }
    negative = {
        "en": ["turn on the lights", "turn off the kitchen light", "pause the music",
               "resume playback", "next track", "set the volume to fifty percent"],
        "ru": ["включи свет", "покажи температуру", "выключи свет", "включи музыку",
               "пауза", "следующий трек", "сделай громче"],
    }
    for language in positive:
        intents = hassil.Intents.from_dict(yaml.safe_load(
            (examples / f"assist-sentences.{language}.yaml").read_text(),
        ))
        for sentence, expected in positive[language].items():
            match = hassil.recognize(sentence, intents, language=language)
            assert match is not None, sentence
            prefix = match.entities.get("query_prefix")
            actual = (prefix.value if prefix else "") + match.entities["query"].value
            assert actual == expected
        for sentence in negative[language]:
            assert hassil.recognize(sentence, intents, language=language) is None, sentence
