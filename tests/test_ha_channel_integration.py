"""Contract tests for the optional HA adapter, without running a household HA.

The protocol module is real; the runtime harness supplies only HA's auth,
configuration and service boundaries. These tests do not prove Google speech
recognition, installed HA lifecycle, Cast acceptance, or physical playback.
"""

import asyncio
import copy
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from epg_voice_gateway.media import MediaSessionStore

COMPONENT = Path(__file__).parents[1] / "integrations/home_assistant/custom_components/epg_voice_channels"
PACKAGE = "epg_channel_contract"
package = ModuleType(PACKAGE)
package.__path__ = [str(COMPONENT)]
sys.modules[PACKAGE] = package


def load(name):
    spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", COMPONENT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


p = load("protocol")
CHANNEL = "a" * 24
OTHER = "b" * 24
DEVICE = "epg_voice_channels:synthetic"
SESSION = MediaSessionStore(
    "http://proxy.example.test", "https://media.example.test", "synthetic-signing-key" * 2,
).create("http://proxy.example.test/channel/synthetic")
MEDIA = SESSION.url
SETTINGS = {
    "gateway_origin": "http://gateway.example.test:8080",
    "gateway_token": "synthetic-api-token",
    "signed_media_origin": "https://media.example.test",
    "google_entry_id": "synthetic_google_entry",
    "agent_user_id": "synthetic_agent_user",
    "target_entity": "media_player.example_display",
}


def catalogue():
    return {"channels": [
        {"channel_id": CHANNEL, "name": "Example News", "synonyms": ["News"], "available": True},
        {"channel_id": OTHER, "name": "Example Kids", "synonyms": [], "available": False},
    ], "revision": "c" * 64}


def request(intent, payload=None):
    return {"requestId": "request-42", "inputs": [{"intent": intent, "payload": payload or {}}]}


def execution(code=CHANNEL, **params):
    return {"command": p.SELECT, "params": {"channelCode": code, **params}}


def grant(url=MEDIA):
    return {"status": "ready", "media_url": url, "expires_at": SESSION.expires_at,
            "programme": {"channel_id": CHANNEL}}


def command(devices=None, executions=None):
    return {"devices": [{"id": d} for d in (devices or [DEVICE])],
            "execution": executions or [execution()]}


@pytest.mark.asyncio
async def test_sync_keeps_stock_devices_and_adds_only_allowed_user():
    stock = {"requestId": "request-42", "payload": {
        "agentUserId": "actual-user", "devices": [{"id": "script.news", "unchanged": True}],
    }}
    delegate = AsyncMock(return_value=stock)
    get = AsyncMock(return_value=p.parse_catalogue(catalogue()))
    play = AsyncMock()
    original = copy.deepcopy(stock)
    result = await p.handle_message(request(p.SYNC), DEVICE, True, delegate, get, play)
    assert result["payload"]["agentUserId"] == "actual-user"
    assert result["payload"]["devices"][0] == original["payload"]["devices"][0]
    added = result["payload"]["devices"][1]
    assert added["name"]["name"] == "Home Channels"
    assert added["traits"] == ["action.devices.traits.Channel"]
    assert added["attributes"]["availableChannels"][0] == {
        "key": CHANNEL, "names": ["Example News", "News"],
    }
    assert "available" not in json.dumps(added["attributes"]["availableChannels"])
    assert "gateway" not in json.dumps(added)
    assert stock == original
    denied = await p.handle_message(request(p.SYNC), DEVICE, False, delegate, get, play)
    assert denied == original
    assert get.await_count == 1
    play.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_gateway_failure_or_empty_allowlist_preserves_other_devices():
    stock = {"requestId": "request-42", "payload": {"devices": [{"id": "script.news"}]}}
    for get in (AsyncMock(side_effect=p.ChannelError()), AsyncMock(return_value=[])):
        result = await p.handle_message(request(p.SYNC), DEVICE, True, AsyncMock(return_value=stock), get, AsyncMock())
        assert result == stock


@pytest.mark.asyncio
async def test_mixed_execute_delegates_once_preserving_stock_commands_errors_and_input():
    message = request(p.EXECUTE, {"commands": [
        command([DEVICE, "script.news"]), command(["input_select.home_tv"], [{"command": "stock.mode"}]),
    ]})
    original = copy.deepcopy(message)
    stock_error = {"ids": ["script.news"], "status": "ERROR", "errorCode": "deviceOffline"}
    delegate = AsyncMock(return_value={"requestId": "request-42", "payload": {"commands": [stock_error]}})
    play = AsyncMock()
    result = await p.handle_message(message, DEVICE, True, delegate, AsyncMock(), play)
    sent = delegate.await_args.args[0]
    assert sent["requestId"] == message["requestId"]
    assert sent["inputs"][0]["payload"]["commands"][0]["devices"] == [{"id": "script.news"}]
    assert sent["inputs"][0]["payload"]["commands"][1] == original["inputs"][0]["payload"]["commands"][1]
    assert result["payload"]["commands"] == [stock_error, {
        "ids": [DEVICE], "status": "SUCCESS", "states": {"online": True},
    }]
    assert message == original
    delegate.assert_awaited_once()
    play.assert_awaited_once_with([execution()])


@pytest.mark.asyncio
async def test_top_level_stock_error_prevents_custom_dispatch():
    error = {"requestId": "request-42", "payload": {"errorCode": "protocolError"}}
    play = AsyncMock()
    result = await p.handle_message(
        request(p.EXECUTE, {"commands": [command([DEVICE, "script.news"])]}), DEVICE, True,
        AsyncMock(return_value=error), AsyncMock(), play,
    )
    assert result == error
    play.assert_not_awaited()


@pytest.mark.asyncio
async def test_wrong_user_cannot_execute_virtual_device_but_stock_is_delegated():
    delegate = AsyncMock(return_value={"requestId": "request-42", "payload": {"commands": []}})
    play = AsyncMock()
    result = await p.handle_message(request(p.EXECUTE, {"commands": [command([DEVICE, "script.news"])]}),
                                    DEVICE, False, delegate, AsyncMock(), play)
    assert result["payload"]["commands"][-1]["errorCode"] == "deviceOffline"
    play.assert_not_awaited()
    delegate.assert_awaited_once()


@pytest.mark.asyncio
async def test_query_partitions_and_disconnect_keeps_stock_lifecycle():
    delegate = AsyncMock(return_value={"requestId": "request-42", "payload": {"devices": {
        "sensor.home_tv": {"online": True, "unchanged": "value"},
    }}})
    result = await p.handle_message(request(p.QUERY, {"devices": [
        {"id": DEVICE}, {"id": "sensor.home_tv"},
    ]}), DEVICE, False, delegate, AsyncMock(), AsyncMock())
    assert result["payload"]["devices"][DEVICE] == {"online": False}
    assert result["payload"]["devices"]["sensor.home_tv"]["unchanged"] == "value"
    assert delegate.await_args.args[0]["inputs"][0]["payload"]["devices"] == [{"id": "sensor.home_tv"}]
    disconnect = request(p.DISCONNECT)
    delegate = AsyncMock(return_value=None)
    assert await p.handle_message(disconnect, DEVICE, True, delegate, AsyncMock(), AsyncMock()) is None
    delegate.assert_awaited_once_with(disconnect)


@pytest.mark.parametrize("executions,code", [
    ([execution("f" * 24)], "noAvailableChannel"),
    ([execution(OTHER)], "noAvailableChannel"),
    ([execution(channelName="Different Channel")], "noAvailableChannel"),
    ([execution(channelNumber="2")], "noAvailableChannel"),
    ([{"command": p.SELECT, "params": {"channelName": "News"}}], "noAvailableChannel"),
    ([{"command": "action.devices.commands.relativeChannel", "params": {"relativeChannelChange": 1}}], "functionNotSupported"),
    ([{"command": "action.devices.commands.returnChannel", "params": {}}], "functionNotSupported"),
    ([execution(), execution()], "functionNotSupported"),
])
def test_selection_requires_one_known_current_channel(executions, code):
    with pytest.raises(p.ChannelError, match=code):
        p.select_channel(executions, p.parse_catalogue(catalogue()))


def test_google_optional_name_can_differ_in_case():
    assert p.select_channel([execution(channelName="example news")], p.parse_catalogue(catalogue())) == CHANNEL


@pytest.mark.parametrize("value", [None, [], {}, {"requestId": "x", "inputs": []},
    request(p.EXECUTE, {"commands": [{"devices": [{"id": DEVICE}], "execution": [None]}]}),
    request(p.QUERY, {"devices": "bad"}),
])
async def test_bad_envelopes_never_reach_handlers(value):
    delegate, play = AsyncMock(), AsyncMock()
    result = await p.handle_message(value, DEVICE, True, delegate, AsyncMock(), play)
    assert result["payload"]["errorCode"] == "protocolError"
    delegate.assert_not_awaited()
    play.assert_not_awaited()


@pytest.mark.parametrize("key,value", [
    ("gateway_origin", "http://user:secret@example.test"),
    ("gateway_origin", "http://example.test/v1/play"),
    ("gateway_origin", "http://example.test?target=private"),
    ("gateway_origin", "http://example.test/#fragment"),
    ("signed_media_origin", "http://media.example.test"),
    ("gateway_token", "synthetic-token\r\nInjected: value"),
    ("target_entity", "script.arbitrary"),
])
def test_settings_reject_endpoint_or_header_injection(key, value):
    with pytest.raises(ValueError):
        p.Settings.parse({**SETTINGS, key: value})


@pytest.mark.parametrize("url", [
    "http://media.example.test/media/x/manifest.m3u8",
    "https://attacker.example.test/media/x/manifest.m3u8",
    "https://media.example.test@attacker.example.test/media/x/manifest.m3u8",
    "https://media.example.test/media/x/manifest.m3u8?url=http://private",
    "https://media.example.test/media/x/manifest.m3u8#fragment",
    "https://media.example.test/other/manifest.m3u8",
])
def test_grant_must_use_fixed_https_media_origin(url):
    with pytest.raises(p.ChannelError):
        p.media_url(grant(url), SETTINGS["signed_media_origin"], CHANNEL)
    assert p.media_url(grant(), SETTINGS["signed_media_origin"], CHANNEL) == MEDIA


@pytest.mark.parametrize("override", [
    {"programme": {"channel_id": OTHER}}, {"programme": {}}, {"expires_at": 0},
    {"expires_at": True}, {"expires_at": "9999999999"}, {"status": "playing"},
])
def test_grant_requires_matching_channel_and_valid_future_expiry(override):
    with pytest.raises(p.ChannelError):
        p.media_url({**grant(), **override}, SETTINGS["signed_media_origin"], CHANNEL)


@pytest.mark.parametrize("raw", [
    '{"requestId":"first","requestId":"second"}',
    '{"outer":{"id":"first","id":"second"}}', '{"value":NaN}', '{"value":Infinity}',
])
def test_json_rejects_duplicate_keys_and_non_json_numbers(raw):
    with pytest.raises(ValueError):
        p.parse_json(raw)


@pytest.fixture
def runtime_module(monkeypatch):
    """Supply only the small installed HA API surface used by the runtime."""
    modules = {
        "aiohttp": {"ClientError": type("ClientError", (Exception,), {}),
                    "ClientTimeout": lambda **kw: SimpleNamespace(**kw),
                    "DummyCookieJar": lambda: "no-cookies"},
        "homeassistant.auth.permissions.const": {"POLICY_CONTROL": "control"},
        "homeassistant.config_entries": {"ConfigEntryState": SimpleNamespace(LOADED="loaded")},
        "homeassistant.core": {"Context": lambda **kw: SimpleNamespace(**kw)},
        "homeassistant.exceptions": {"HomeAssistantError": type("HomeAssistantError", (Exception,), {})},
        "homeassistant.helpers.aiohttp_client": {"async_create_clientsession": lambda hass, **_kw: hass.session},
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    return load("runtime")


@pytest.fixture
def runtime(runtime_module):
    permission = SimpleNamespace(check_entity=lambda entity, policy: entity == SETTINGS["target_entity"] and policy == "control")
    user = SimpleNamespace(id=SETTINGS["agent_user_id"], is_active=True, is_admin=False,
                           is_owner=False, local_only=False, permissions=permission)
    config = SimpleNamespace(async_get_agent_users=lambda: [user.id], async_sync_entities=AsyncMock(return_value=200))
    entry = SimpleNamespace(domain="google_assistant", state="loaded", runtime_data=config)
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_get_entry=lambda _: entry),
        states=SimpleNamespace(get=lambda _: SimpleNamespace(state="idle")),
        auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user)),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    value = runtime_module.Runtime(hass, "synthetic", p.Settings.parse(SETTINGS))
    value.request = AsyncMock(side_effect=[catalogue(), grant()])
    return value, user, entry


async def test_runtime_fixed_channel_dispatch_preserves_actual_user_context(runtime):
    value, user, _ = runtime
    await value.play(user, [execution()])
    assert value.request.await_args_list[0].args == ("/v1/channels",)
    assert value.request.await_args_list[1].args == ("/v1/play", {"channel_id": CHANNEL})
    call = value.hass.services.async_call.await_args
    assert call.args == ("media_player", "play_media", {
        "entity_id": SETTINGS["target_entity"], "media_content_id": MEDIA,
        "media_content_type": "application/vnd.apple.mpegurl",
    })
    assert call.kwargs["blocking"] is True
    assert call.kwargs["context"].user_id == user.id


@pytest.mark.parametrize("change", ["admin", "wrong_user", "permission", "unloaded", "inactive"])
async def test_permission_or_google_lifecycle_failure_never_creates_grant(runtime, change):
    value, user, entry = runtime
    if change == "admin":
        user.is_admin = True
    elif change == "wrong_user":
        user.id = "other_user"
    elif change == "permission":
        user.permissions.check_entity = lambda *_: False
    elif change == "unloaded":
        entry.state = "not_loaded"
    else:
        value.active = False
    with pytest.raises(p.ChannelError):
        await value.play(user, [execution()])
    value.request.assert_not_awaited()
    value.hass.services.async_call.assert_not_awaited()


async def test_stale_or_removed_channel_does_not_use_sync_cache_for_playback(runtime):
    value, user, _ = runtime
    value.cached_channels = p.parse_catalogue(catalogue())
    for answer in (p.ChannelError(), {"channels": [], "revision": "d" * 64}):
        value.request = AsyncMock(side_effect=answer) if isinstance(answer, Exception) else AsyncMock(return_value=answer)
        with pytest.raises(p.ChannelError):
            await value.play(user, [execution()])
        value.request.assert_awaited_once_with("/v1/channels")
    value.hass.services.async_call.assert_not_awaited()


async def test_permissions_revoked_during_gateway_io_prevent_dispatch(runtime):
    value, user, _ = runtime
    revoked = copy.copy(user)
    revoked.is_active = False
    value.hass.auth.async_get_user.return_value = revoked
    with pytest.raises(p.ChannelError):
        await value.play(user, [execution()])
    value.hass.services.async_call.assert_not_awaited()


async def test_bad_grant_or_service_error_is_not_success(runtime, runtime_module):
    value, user, _ = runtime
    value.request.side_effect = [catalogue(), grant("https://wrong.example.test/stream")]
    with pytest.raises(p.ChannelError):
        await value.play(user, [execution()])
    value.hass.services.async_call.assert_not_awaited()
    value.request.side_effect = [catalogue(), grant()]
    value.hass.services.async_call.side_effect = runtime_module.HomeAssistantError("private service detail")
    with pytest.raises(p.ChannelError, match="channelSwitchFailed") as error:
        await value.play(user, [execution()])
    assert "private" not in str(error.value)


async def test_refresh_syncs_only_changed_advertisement_for_registered_user(runtime):
    value, _, entry = runtime
    value.request = AsyncMock(return_value=catalogue())
    await value.refresh()
    await value.refresh()
    entry.runtime_data.async_sync_entities.assert_awaited_once_with(SETTINGS["agent_user_id"])
    changed = catalogue()
    changed["channels"][0]["available"] = False
    value.request.return_value = changed
    await value.refresh()
    assert entry.runtime_data.async_sync_entities.await_count == 1
    changed["channels"][0]["name"] = "Renamed News"
    await value.refresh()
    assert entry.runtime_data.async_sync_entities.await_count == 2
    entry.runtime_data.async_get_agent_users = lambda: []
    value.request.reset_mock()
    await value.refresh()
    value.request.assert_not_awaited()


async def test_sync_cache_survives_outage_but_fresh_empty_allowlist_clears_it(runtime):
    value, _, _ = runtime
    value.cached_channels = p.parse_catalogue(catalogue())
    value.request = AsyncMock(side_effect=p.ChannelError())
    assert await value.catalogue(cached_fallback=True) == value.cached_channels
    with pytest.raises(p.ChannelError):
        await value.catalogue()
    value.request = AsyncMock(return_value={"channels": [], "revision": "e" * 64})
    assert await value.catalogue(cached_fallback=True) == []
    assert value.cached_channels == []


class Body:
    def __init__(self, raw):
        self.raw = raw

    async def iter_chunked(self, size):
        for start in range(0, len(self.raw), size):
            yield self.raw[start:start + size]


class GatewayResponse:
    def __init__(self, raw, *, status=200, content_type="application/json"):
        self.content = Body(raw)
        self.status = status
        self.content_type = content_type

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        pass


async def test_gateway_transport_uses_fixed_origin_auth_no_redirect_and_bounded_json(runtime, runtime_module):
    value, _, _ = runtime
    calls = []

    def send(*args, **kwargs):
        calls.append((args, kwargs))
        return GatewayResponse(json.dumps(catalogue()).encode())

    value.hass.session = SimpleNamespace(request=send)
    result = await runtime_module.Runtime.request(value, "/v1/channels")
    assert result == catalogue()
    args, kwargs = calls[0]
    assert args == ("GET", SETTINGS["gateway_origin"] + "/v1/channels")
    assert kwargs["headers"] == {"Authorization": "Bearer " + SETTINGS["gateway_token"]}
    assert kwargs["allow_redirects"] is False
    assert kwargs["timeout"].total == 2
    with pytest.raises(p.ChannelError):
        await runtime_module.Runtime.request(value, "http://caller.example.test")
    assert len(calls) == 1


@pytest.mark.parametrize("response", [
    GatewayResponse(b'{}', status=302), GatewayResponse(b'private upstream error', status=503),
    GatewayResponse(b'{}', content_type="text/html"),
    GatewayResponse(b'{"channels":[],"channels":[]}'),
    GatewayResponse(b' ' * (256 * 1024 + 1)),
])
async def test_gateway_transport_fails_closed_without_disclosing_response(runtime, runtime_module, response):
    value, _, _ = runtime
    value.hass.session = SimpleNamespace(request=lambda *_args, **_kw: response)
    with pytest.raises(p.ChannelError) as error:
        await runtime_module.Runtime.request(value, "/v1/channels")
    assert str(error.value) == "deviceOffline"


async def test_slow_ha_dispatch_is_bounded_and_never_acknowledged_success(runtime, runtime_module, monkeypatch):
    value, user, _ = runtime
    monkeypatch.setattr(runtime_module, "OPERATION_TIMEOUT", 0.01)

    async def slow(*_args, **_kwargs):
        await asyncio.sleep(10)

    value.hass.services.async_call.side_effect = slow
    with pytest.raises(p.ChannelError, match="channelSwitchFailed"):
        await value.play(user, [execution()])
    value.hass.services.async_call.assert_awaited_once()


async def test_overlapping_channel_changes_fail_fast_without_reordering(runtime):
    value, user, _ = runtime
    waiting = asyncio.Event()
    release = asyncio.Event()

    async def gateway(path, _data=None):
        if path == "/v1/channels":
            return catalogue()
        waiting.set()
        await release.wait()
        return grant()

    value.request.side_effect = gateway
    first = asyncio.create_task(value.play(user, [execution()], "first"))
    await waiting.wait()
    with pytest.raises(p.ChannelError, match="deviceBusy"):
        await value.play(user, [execution(OTHER)], "second")
    assert value.request.await_count == 2
    release.set()
    await first
    value.hass.services.async_call.assert_awaited_once()


async def test_completed_google_retry_is_idempotent_but_conflicting_id_is_rejected(runtime):
    value, user, _ = runtime
    await value.play(user, [execution()], "same-request")
    await value.play(user, [execution()], "same-request")
    value.hass.services.async_call.assert_awaited_once()
    assert value.request.await_count == 2
    with pytest.raises(p.ChannelError, match="protocolError"):
        await value.play(user, [execution(OTHER)], "same-request")
    assert all(MEDIA not in str(item) for item in value.completed_requests.values())


async def test_disconnected_agent_cannot_execute_even_with_valid_ha_auth(runtime):
    value, user, entry = runtime
    entry.runtime_data.async_get_agent_users = lambda: []
    with pytest.raises(p.ChannelError):
        await value.play(user, [execution()])
    value.request.assert_not_awaited()
    value.hass.services.async_call.assert_not_awaited()


async def test_disconnect_during_grant_request_prevents_cast(runtime):
    value, user, entry = runtime

    async def gateway(path, _data=None):
        if path == "/v1/channels":
            return catalogue()
        entry.runtime_data.async_get_agent_users = lambda: []
        return grant()

    value.request.side_effect = gateway
    with pytest.raises(p.ChannelError):
        await value.play(user, [execution()])
    value.hass.services.async_call.assert_not_awaited()


@pytest.fixture
def view_module(monkeypatch, runtime_module):
    class View:
        def json(self, body, status_code=200):
            return SimpleNamespace(body=body, status=status_code)

    delegate = AsyncMock(return_value={"requestId": "request-42", "payload": {"devices": []}})
    modules = {
        "aiohttp.web": {"Request": dict, "Response": SimpleNamespace},
        "homeassistant.components.google_assistant.const": {"SOURCE_CLOUD": "cloud"},
        "homeassistant.components.google_assistant.smart_home": {"async_handle_message": delegate},
        "homeassistant.components.http": {"HomeAssistantView": View},
        "homeassistant.helpers.event": {"async_track_time_interval": lambda *_args: lambda: None},
    }
    for name, values in modules.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.setattr(sys.modules["homeassistant.config_entries"], "ConfigEntry", SimpleNamespace, raising=False)
    monkeypatch.setattr(sys.modules["homeassistant.core"], "HomeAssistant", SimpleNamespace, raising=False)
    return load("__init__"), delegate


class Incoming(dict):
    def __init__(self, user, raw, *, content_type="application/json", content_length=None):
        super().__init__(hass_user=user)
        self.content = Body(raw)
        self.content_type = content_type
        self.content_length = len(raw) if content_length is None else content_length


async def test_real_view_delegates_authenticated_identity_and_registers_only_new_path(runtime, view_module):
    value, user, entry = runtime
    module, delegate = view_module
    adapter = SimpleNamespace(state="loaded", runtime_data=value)
    value.hass.config_entries.async_entries = lambda _: [adapter]
    view = module.ChannelView(value.hass)
    assert view.requires_auth is True
    assert view.url == "/api/epg_voice/google_assistant"
    response = await view.post(Incoming(user, json.dumps(request(p.SYNC)).encode()))
    assert response.status == 200
    assert len(response.body["payload"]["devices"]) == 1
    args = delegate.await_args.args
    assert args[1] is entry.runtime_data
    assert args[2:4] == (user.id, user.id)
    assert args[-1] == "cloud"
    # Resolve the live Google entry on each request, including after unloading.
    entry.state = "not_loaded"
    response = await view.post(Incoming(user, json.dumps(request(p.SYNC)).encode()))
    assert response.status == 503
    assert delegate.await_count == 1


@pytest.mark.parametrize("raw,content_type,status", [
    (b'{}', "text/plain", 415), (b'{"x":1,"x":2}', "application/json", 400),
    (b' ' * (32 * 1024 + 1), "application/json", 413),
])
async def test_view_rejects_ambiguous_or_unbounded_payload_before_delegation(runtime, view_module, raw, content_type, status):
    value, user, _ = runtime
    module, delegate = view_module
    value.hass.config_entries.async_entries = lambda _: [SimpleNamespace(state="loaded", runtime_data=value)]
    response = await module.ChannelView(value.hass).post(Incoming(user, raw, content_type=content_type))
    assert response.status == status
    delegate.assert_not_awaited()


async def test_view_disconnect_delegates_and_clears_user_cache(runtime, view_module):
    value, user, _ = runtime
    module, delegate = view_module
    value.hass.config_entries.async_entries = lambda _: [SimpleNamespace(state="loaded", runtime_data=value)]
    value.cached_channels = p.parse_catalogue(catalogue())
    delegate.return_value = None
    response = await module.ChannelView(value.hass).post(Incoming(user, json.dumps(request(p.DISCONNECT)).encode()))
    assert response.body is None
    assert value.cached_channels == []
    assert value.synced_channels is None
    delegate.assert_awaited_once()


async def test_config_flow_uses_labelled_eligible_entries_and_users(runtime, runtime_module, monkeypatch):
    value, user, entry = runtime
    user.name = "Dedicated media user"
    admin = copy.copy(user)
    admin.id = "excluded_admin"
    admin.is_admin = True
    entry.entry_id = SETTINGS["google_entry_id"]
    entry.title = "Existing Google home project"
    unloaded = copy.copy(entry)
    unloaded.entry_id = "excluded_entry"
    unloaded.state = "not_loaded"
    value.hass.auth.async_get_users = AsyncMock(return_value=[user, admin])
    value.hass.config_entries.async_entries = lambda _: [entry, unloaded]

    class Flow:
        def __init_subclass__(cls, **_kwargs):
            pass

    entries = sys.modules["homeassistant.config_entries"]
    monkeypatch.setattr(entries, "ConfigFlow", Flow, raising=False)
    ha = ModuleType("homeassistant")
    ha.config_entries = entries
    monkeypatch.setitem(sys.modules, "homeassistant", ha)
    selectors = SimpleNamespace(
        TextSelector=lambda config: config, TextSelectorConfig=lambda **kw: kw,
        TextSelectorType=SimpleNamespace(PASSWORD="password"),
        SelectSelector=lambda config: config, SelectSelectorConfig=lambda **kw: kw,
        SelectSelectorMode=SimpleNamespace(DROPDOWN="dropdown"),
        EntitySelector=lambda config: config, EntitySelectorConfig=lambda **kw: kw,
    )
    helpers = ModuleType("homeassistant.helpers")
    helpers.selector = selectors
    monkeypatch.setitem(sys.modules, "homeassistant.helpers", helpers)
    vol = ModuleType("voluptuous")
    vol.Required = lambda key: key
    vol.Schema = lambda fields: fields
    monkeypatch.setitem(sys.modules, "voluptuous", vol)
    flow = load("config_flow")
    fields = await flow.schema(value.hass)
    assert fields["google_entry_id"]["options"] == [{
        "value": entry.entry_id, "label": entry.title,
    }]
    assert fields["agent_user_id"]["options"] == [{"value": user.id, "label": user.name}]
    assert fields["gateway_token"]["type"] == "password"
    assert fields["target_entity"]["domain"] == "media_player"
