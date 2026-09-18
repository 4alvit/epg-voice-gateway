"""APL capability, document, and verified control-boundary contracts."""

import asyncio
import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from epg_voice_gateway.alexa import AlexaAdapter
from epg_voice_gateway.apl_video import (
    APL_INTERFACE,
    EVENT_NAMESPACE,
    VIDEO_ID,
    capability_reason,
    capability_summary,
    compatible_version,
)
from epg_voice_gateway.models import ProgramMatch

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
MEDIA_URL = "https://media.example.test/media/opaque/index.m3u8"


def request(*, version="1.8", codecs=None, intent="PlayProgramIntent"):
    return {
        "context": {
            "System": {"device": {"supportedInterfaces": {
                APL_INTERFACE: {"runtime": {"maxVersion": version}},
            }}},
            "Viewport": {"video": {"codecs": ["H_264_41"] if codecs is None else codecs}},
        },
        "request": {"type": "IntentRequest", "intent": {
            "name": intent, "slots": {"Query": {"value": "Example News"}},
        }},
    }


def adapter(**changes):
    values = dict(title="Example News", channel_id="news", channel_name="Example Channel",
                  starts_at=NOW - timedelta(minutes=1), ends_at=NOW + timedelta(minutes=10))
    values.update(changes)
    resolver = AsyncMock(return_value=[ProgramMatch(**values)])
    session = AsyncMock(return_value=MEDIA_URL)
    return AlexaAdapter(resolver, session, now=lambda: NOW), resolver, session


def launch(skill):
    return asyncio.run(skill.handle(request()))["response"]["directives"][0]


def controls(token, *, intent="AMAZON.PauseIntent"):
    payload = request(intent=intent)
    payload["context"][APL_INTERFACE] = {"token": token}
    return payload


def event(token, action="pause"):
    payload = controls(token)
    payload["request"] = {
        "type": APL_INTERFACE + ".UserEvent", "token": token,
        "arguments": [EVENT_NAMESPACE, action],
        "source": {"type": "TouchWrapper", "handler": "Press",
                   "id": "epgVideo" + action.title()},
    }
    return payload


def components(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from components(child)
    elif isinstance(value, list):
        for child in value:
            yield from components(child)


@pytest.mark.parametrize("version", ["1.8", "1.9", "2022.1", "2024.3", "2026.1"])
def test_selects_supported_baseline_without_reflecting_runtime_version(version):
    payload = request(version=version)
    assert compatible_version(payload) == "1.8"
    assert capability_summary(payload) == {"video_app": False, "apl": True, "apl_video": True}


@pytest.mark.parametrize("version", [None, {}, 1.8, True, "", "1.0", "1.3", "1.7", "v1.8",
                                     "1.8extra", "1.8.0", "01.8", "1.08x", " 1.8", "1.8\n",
                                     "1.08", "2024.03", "∞.8", "١.٨", "1." + "8" * 1000])
def test_unsupported_or_malformed_runtime_never_creates_a_media_grant(version):
    skill, resolver, session = adapter()
    payload = request(version=version)
    result = asyncio.run(skill.handle(payload))
    assert compatible_version(payload) is None
    assert capability_summary(payload)["apl_video"] is False
    assert "directives" not in result["response"]
    resolver.assert_not_awaited()
    session.assert_not_awaited()


@pytest.mark.parametrize("codecs", [[], "H_264_41", {}, ["H264_41"], ["H_265_42"],
                                   ["h_264_41"], ["H_264_41 ", None, {}]])
def test_requires_explicit_supported_h264_codec_before_lookup(codecs):
    skill, resolver, session = adapter()
    payload = request(codecs=codecs)
    result = asyncio.run(skill.handle(payload))
    assert "does not support video" in result["response"]["outputSpeech"]["text"]
    resolver.assert_not_awaited()
    session.assert_not_awaited()


@pytest.mark.parametrize("codec", ["H_264_41", "H_264_42"])
def test_apl_launch_has_controls_and_does_not_open_microphone(codec):
    skill, resolver, session = adapter()
    result = asyncio.run(skill.handle(request(codecs=[codec])))
    response = result["response"]
    assert "shouldEndSession" not in response
    assert "outputSpeech" not in response
    assert len(response["directives"]) == 1
    directive = response["directives"][0]
    assert directive["type"] == APL_INTERFACE + ".RenderDocument"
    assert directive["document"]["version"] == "1.8"
    video = next(item for item in components(directive["document"]) if item.get("type") == "Video")
    assert video["autoplay"] is True
    assert video["audioTrack"] == "background"
    assert video["source"] == "${payload.media.source}"
    assert directive["datasources"]["media"]["source"] == MEDIA_URL
    assert {item["accessibilityLabel"] for item in components(directive["document"])
            if item.get("type") == "TouchWrapper"} == {"Pause", "Resume", "Stop"}
    resolver.assert_awaited_once_with("Example News")
    session.assert_awaited_once()


def test_videoapp_still_wins_when_both_interfaces_are_supported():
    skill, _, _ = adapter()
    payload = request()
    payload["context"]["System"]["device"]["supportedInterfaces"]["VideoApp"] = {}
    response = asyncio.run(skill.handle(payload))["response"]
    assert [item["type"] for item in response["directives"]] == ["VideoApp.Launch"]
    assert "shouldEndSession" not in response


def test_catalogue_text_and_media_source_remain_datasource_literals():
    title = "<b>${dangerous.expression}</b> & News"
    skill, _, session = adapter(title=title, channel_name="<i>Channel</i>")
    session.return_value = MEDIA_URL + "?label=${other.expression}&another=1"
    directive = launch(skill)
    data = directive["datasources"]["media"]
    document = json.dumps(directive["document"])
    assert data["title"] == "&lt;b&gt;${dangerous.expression}&lt;/b&gt; &amp; News"
    assert data["channel"] == "&lt;i&gt;Channel&lt;/i&gt;"
    assert data["source"] == session.return_value
    assert "dangerous.expression" not in document
    assert "other.expression" not in document
    assert MEDIA_URL not in document


@pytest.mark.parametrize("source", ["http://media.example.test/index.m3u8",
                                    "https://user:secret@media.example.test/index.m3u8",
                                    "https://media.example.test/index.m3u8#fragment"])
def test_apl_uses_existing_safe_media_url_validation(source):
    skill, _, session = adapter()
    session.return_value = source
    result = asyncio.run(skill.handle(request()))
    assert "directives" not in result["response"]
    assert source not in json.dumps(result)


@pytest.mark.parametrize("intent,command", [("AMAZON.PauseIntent", "pause"),
                                           ("AMAZON.ResumeIntent", "play"),
                                           ("AMAZON.StopIntent", "pause"),
                                           ("AMAZON.CancelIntent", "pause")])
def test_voice_controls_target_owned_current_document_without_new_grant(intent, command):
    skill, resolver, session = adapter()
    token = launch(skill)["token"]
    resolver.reset_mock()
    session.reset_mock()
    response = asyncio.run(skill.handle(controls(token, intent=intent)))["response"]
    directive = response["directives"][0]
    assert directive["token"] == token
    assert directive["type"] == APL_INTERFACE + ".ExecuteCommands"
    assert directive["commands"][0] == {
        "type": "ControlMedia", "componentId": VIDEO_ID, "command": command,
    }
    if intent in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
        assert directive["commands"][-1] == {"type": "Finish"}
        assert response["shouldEndSession"] is True
    else:
        assert "shouldEndSession" not in response
        assert "outputSpeech" not in response
    resolver.assert_not_awaited()
    session.assert_not_awaited()


@pytest.mark.parametrize("action", ["pause", "resume", "stop"])
def test_touch_controls_are_local_and_have_strict_verified_event_handlers(action):
    skill, resolver, session = adapter()
    document = launch(skill)
    button = next(item for item in components(document) if item.get("id") == "epgVideo" + action.title())
    assert button["onPress"][0]["type"] == "ControlMedia"
    assert button["onPress"][1] == {"type": "SendEvent", "arguments": [EVENT_NAMESPACE, action]}
    if action == "stop":
        assert button["onPress"][-1] == {"type": "Finish"}
    resolver.reset_mock()
    session.reset_mock()
    response = asyncio.run(skill.handle(event(document["token"], action)))["response"]
    assert response == ({"shouldEndSession": True} if action == "stop" else {})
    resolver.assert_not_awaited()
    session.assert_not_awaited()


@pytest.mark.parametrize("mutation", [
    lambda p: p["request"].update(token="other-document"),
    lambda p: p["context"][APL_INTERFACE].update(token="other-document"),
    lambda p: p["request"].update(presentationUri="widget://other"),
    lambda p: p["request"].update(arguments=[EVENT_NAMESPACE, "play-url", MEDIA_URL]),
    lambda p: p["request"].update(arguments=[EVENT_NAMESPACE, "pause", MEDIA_URL]),
    lambda p: p["request"].update(arguments=[EVENT_NAMESPACE, {}]),
    lambda p: p["request"].update(arguments={"action": "pause"}),
    lambda p: p["request"].update(arguments=["other-namespace", "pause"]),
    lambda p: p["request"]["source"].update(id="differentButton"),
    lambda p: p["request"]["source"].update(type="Video"),
    lambda p: p["request"]["source"].update(handler="Other"),
    lambda p: p["context"].pop(APL_INTERFACE),
    lambda p: p["context"].pop("Viewport"),
])
def test_invalid_or_unrelated_user_event_has_no_effect(mutation):
    skill, resolver, session = adapter()
    token = launch(skill)["token"]
    resolver.reset_mock()
    session.reset_mock()
    payload = event(token)
    mutation(payload)
    assert asyncio.run(skill.handle(payload)) == {"version": "1.0", "response": {}}
    resolver.assert_not_awaited()
    session.assert_not_awaited()


def test_document_tokens_are_unique_and_cannot_be_reused_by_another_adapter():
    skill, _, _ = adapter()
    first, second = launch(skill)["token"], launch(skill)["token"]
    assert first != second
    other, resolver, session = adapter()
    result = asyncio.run(other.handle(controls(first)))
    assert "directives" not in result["response"]
    resolver.assert_not_awaited()
    session.assert_not_awaited()
    forged = first[:-1] + ("0" if first[-1] != "0" else "1")
    assert "directives" not in asyncio.run(skill.handle(controls(forged)))["response"]


def test_old_document_event_cannot_control_a_new_owned_document():
    skill, resolver, session = adapter()
    first, second = launch(skill)["token"], launch(skill)["token"]
    payload = event(first)
    payload["context"][APL_INTERFACE]["token"] = second
    resolver.reset_mock()
    session.reset_mock()
    assert asyncio.run(skill.handle(payload)) == {"version": "1.0", "response": {}}
    resolver.assert_not_awaited()
    session.assert_not_awaited()


def test_extra_client_source_and_component_values_cannot_supply_commands_or_media():
    skill, resolver, session = adapter()
    token = launch(skill)["token"]
    payload = event(token)
    payload["request"]["source"]["url"] = "https://untrusted.example.test/private.m3u8"
    payload["request"]["components"] = {"source": "client-media", "command": "client-command"}
    resolver.reset_mock()
    session.reset_mock()
    result = asyncio.run(skill.handle(payload))
    assert "untrusted.example" not in json.dumps(result)
    assert "client-" not in json.dumps(result)
    assert result["response"] == {}
    resolver.assert_not_awaited()
    session.assert_not_awaited()


def test_end_event_returns_voice_prompt_only_for_current_owned_video():
    skill, _, session = adapter()
    token = launch(skill)["token"]
    payload = event(token, "ended")
    payload["request"]["source"] = {"type": "Video", "handler": "End", "id": VIDEO_ID}
    response = asyncio.run(skill.handle(payload))["response"]
    assert response["shouldEndSession"] is False
    assert response["reprompt"]["outputSpeech"]["type"] == "PlainText"
    assert response["directives"][0]["commands"][-1] == {"type": "Finish"}
    assert session.await_count == 1


def test_fast_mode_video_failure_has_local_error_text_without_unsupported_send_event():
    skill, _, _ = adapter()
    video = next(item for item in components(launch(skill)) if item.get("type") == "Video")
    assert [command["type"] for command in video["onTrackFail"]] == ["SetValue"]
    assert "could not be played" in video["onTrackFail"][0]["value"]


def test_runtime_error_notification_does_not_speak_or_create_a_session():
    skill, resolver, session = adapter()
    payload = request()
    payload["request"] = {
        "type": APL_INTERFACE + ".RuntimeError", "token": "untrusted-document-token",
        "errors": [{"type": "INTERNAL_ERROR", "message": "private-source-and-token"}],
    }
    assert asyncio.run(skill.handle(payload)) == {"version": "1.0", "response": {}}
    resolver.assert_not_awaited()
    session.assert_not_awaited()


@pytest.mark.parametrize("payload", [None, True, [], "private-value", {}, {"context": []},
                                     {"context": {"System": {"device": []}}}])
def test_diagnostics_are_fixed_booleans_for_malformed_payloads(payload):
    assert capability_summary(payload) == {"video_app": False, "apl": False, "apl_video": False}


def test_diagnostics_do_not_include_any_identity_source_or_raw_capability_values():
    payload = request()
    payload["context"]["System"]["device"]["deviceId"] = "private-device"
    payload["context"]["System"]["apiAccessToken"] = "private-token"
    before = deepcopy(payload)
    summary = capability_summary(payload)
    assert set(summary) == {"video_app", "apl", "apl_video"}
    assert all(type(value) is bool for value in summary.values())
    assert payload == before


@pytest.mark.parametrize("mutation,reason", [
    (lambda p: None, "eligible"),
    (lambda p: p["context"]["System"]["device"]["supportedInterfaces"].clear(), "missing_apl"),
    (lambda p: p["context"]["System"]["device"]["supportedInterfaces"][APL_INTERFACE]
     ["runtime"].update(maxVersion="1.7"), "unsupported_runtime"),
    (lambda p: p["context"].pop("Viewport"), "missing_video"),
    (lambda p: p["context"]["Viewport"].update(video={}), "missing_video"),
    (lambda p: p["context"]["Viewport"].update(video=None), "missing_video"),
    (lambda p: p["context"]["Viewport"]["video"].update(codecs=[]), "unsupported_codec"),
    (lambda p: p["context"]["Viewport"]["video"].update(codecs=["H_265_42"]),
     "unsupported_codec"),
])
def test_capability_reason_identifies_exact_gate_without_changing_eligibility(mutation, reason):
    payload = request()
    mutation(payload)
    before = deepcopy(payload)
    assert capability_reason(payload) == reason
    assert capability_summary(payload)["apl_video"] is (reason == "eligible")
    assert payload == before


@pytest.mark.parametrize("payload,reason", [
    (None, "missing_apl"),
    ("private-device-and-token", "missing_apl"),
    ({"context": {"Viewport": "private-video-value"}}, "missing_apl"),
    (request(version="private-runtime-and-token"), "unsupported_runtime"),
    (request(version={"private-runtime": "private-token"}), "unsupported_runtime"),
    (request(codecs=["private-codec-and-url", {}, None]), "unsupported_codec"),
    (request(codecs="private-codec-and-token"), "unsupported_codec"),
])
def test_capability_reason_never_echoes_malformed_or_private_values(payload, reason):
    result = capability_reason(payload)
    assert result == reason
    assert result in {"eligible", "missing_apl", "unsupported_runtime", "missing_video",
                      "unsupported_codec"}


@pytest.mark.parametrize("action", ["pause", "resume", "stop", "ended"])
def test_control_envelope_is_compatible_with_installed_ask_verifier_models(action):
    from ask_sdk_core.serialize import DefaultSerializer
    from ask_sdk_model import RequestEnvelope

    skill, _, _ = adapter()
    token = launch(skill)["token"]
    payload = event(token, action)
    if action == "ended":
        payload["request"]["source"] = {"type": "Video", "handler": "End", "id": VIDEO_ID}
    payload["version"] = "1.0"
    payload["request"]["timestamp"] = NOW.isoformat()
    payload["request"]["requestId"] = "synthetic-apl-control"
    envelope = DefaultSerializer().deserialize(json.dumps(payload), RequestEnvelope)
    assert envelope.request.object_type == APL_INTERFACE + ".UserEvent"
    assert envelope.request.token == token
    assert envelope.context.alexa_presentation_apl.token == token
