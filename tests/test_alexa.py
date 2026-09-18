import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from epg_voice_gateway.alexa import AlexaAdapter
from epg_voice_gateway.errors import CatalogUnavailable, PlaybackUnavailable
from epg_voice_gateway.models import ProgramMatch

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
MEDIA_URL = "https://media.example.com/session/opaque/index.m3u8"


def program(**changes):
    values = {
        "title": "Kukutiki",
        "channel_id": "kids",
        "channel_name": "Kids Channel",
        "starts_at": NOW - timedelta(minutes=10),
        "ends_at": NOW + timedelta(minutes=20),
    }
    values.update(changes)
    return ProgramMatch(**values)


def request(*, interfaces=None, intent="PlayProgramIntent", query="Kukutiki"):
    return {
        "context": {
            "System": {
                "device": {
                    "supportedInterfaces": {"VideoApp": {}} if interfaces is None else interfaces
                }
            }
        },
        "request": {
            "type": "IntentRequest",
            "intent": {"name": intent, "slots": {"Query": {"value": query}}},
        },
    }


def adapter(matches=None):
    resolver = AsyncMock(return_value=[program()] if matches is None else matches)
    session_url = AsyncMock(return_value=MEDIA_URL)
    return AlexaAdapter(resolver, session_url, now=lambda: NOW), resolver, session_url


def test_launches_current_match_with_valid_videoapp_contract():
    skill, resolver, session_url = adapter()
    result = asyncio.run(skill.handle(request(query="  Kukutiki  ")))
    resolver.assert_awaited_once_with("Kukutiki")
    session_url.assert_awaited_once_with(program())
    response = result["response"]
    assert "shouldEndSession" not in response
    assert response["directives"] == [
        {
            "type": "VideoApp.Launch",
            "videoItem": {
                "source": MEDIA_URL,
                "metadata": {"title": "Kukutiki", "subtitle": "Kids Channel"},
            },
        }
    ]


@pytest.mark.parametrize("interfaces", [{}, {"Alexa.Presentation.APL": {}}, [], None])
def test_requires_videoapp_even_when_apl_is_available(interfaces):
    skill, resolver, session_url = adapter()
    payload = request()
    payload["context"]["System"]["device"]["supportedInterfaces"] = interfaces
    result = asyncio.run(skill.handle(payload))
    assert "does not support video" in result["response"]["outputSpeech"]["text"]
    assert "directives" not in result["response"]
    resolver.assert_not_awaited()
    session_url.assert_not_awaited()


@pytest.mark.parametrize("query", [None, "", "  ", {}, "x" * 201])
def test_missing_or_oversized_query_does_not_call_catalog(query):
    skill, resolver, session_url = adapter()
    result = asyncio.run(skill.handle(request(query=query)))
    assert result["response"]["shouldEndSession"] is False
    resolver.assert_not_awaited()
    session_url.assert_not_awaited()


def test_no_match_does_not_create_playback_session():
    skill, _, session_url = adapter(matches=[])
    result = asyncio.run(skill.handle(request()))
    assert "airing right now" in result["response"]["outputSpeech"]["text"]
    session_url.assert_not_awaited()


def test_ambiguous_match_does_not_silently_choose_first_channel():
    skill, _, session_url = adapter([program(), program(channel_id="other")])
    result = asyncio.run(skill.handle(request()))
    assert "more than one" in result["response"]["outputSpeech"]["text"]
    assert "directives" not in result["response"]
    session_url.assert_not_awaited()


@pytest.mark.parametrize(
    "match",
    [
        program(ends_at=NOW),
        program(starts_at=NOW + timedelta(seconds=1)),
        program(starts_at=NOW.replace(tzinfo=None)),
    ],
)
def test_stale_future_or_naive_program_never_launches(match):
    skill, _, session_url = adapter([match])
    result = asyncio.run(skill.handle(request()))
    assert "directives" not in result["response"]
    session_url.assert_not_awaited()


@pytest.mark.parametrize("error", [CatalogUnavailable, PlaybackUnavailable])
def test_domain_errors_are_safe_and_do_not_leak_error_text(error):
    skill, resolver, session_url = adapter()
    resolver.side_effect = error("private-provider-url-and-token")
    result = asyncio.run(skill.handle(request()))
    assert "private-provider" not in json.dumps(result)
    assert "right now" in result["response"]["outputSpeech"]["text"]
    session_url.assert_not_awaited()


def test_session_error_does_not_leak_error_text():
    skill, _, session_url = adapter()
    session_url.side_effect = PlaybackUnavailable("private-token")
    result = asyncio.run(skill.handle(request()))
    assert "private-token" not in json.dumps(result)
    assert "directives" not in result["response"]


@pytest.mark.parametrize(
    "source",
    [
        "http://media.example.com/video.m3u8",
        "https://user:password@media.example.com/video.m3u8",
        "https://media.example.com/video.m3u8#fragment",
        "https://media.example.com/not-a-playlist",
        "https://media.example.com:invalid/video.m3u8",
        "https://media.example.com/video.m3u8\n",
    ],
)
def test_invalid_playback_url_never_reaches_alexa(source):
    skill, _, session_url = adapter()
    session_url.return_value = source
    result = asyncio.run(skill.handle(request()))
    assert "directives" not in result["response"]
    assert source not in json.dumps(result)


@pytest.mark.parametrize("intent", ["AMAZON.StopIntent", "AMAZON.CancelIntent"])
def test_stop_ends_skill_without_starting_playback(intent):
    skill, resolver, session_url = adapter()
    result = asyncio.run(skill.handle(request(intent=intent)))
    assert result["response"]["shouldEndSession"] is True
    resolver.assert_not_awaited()
    session_url.assert_not_awaited()


@pytest.mark.parametrize("intent", ["AMAZON.HelpIntent", "AMAZON.FallbackIntent", "Unknown"])
def test_help_and_unrecognized_intents_offer_program_prompt(intent):
    skill, resolver, _ = adapter()
    result = asyncio.run(skill.handle(request(intent=intent)))
    assert result["response"]["shouldEndSession"] is False
    assert "play Kukutiki" in result["response"]["outputSpeech"]["text"]
    resolver.assert_not_awaited()


def test_launch_prompts_and_session_end_is_empty():
    skill, resolver, _ = adapter()
    result = asyncio.run(skill.handle({"request": {"type": "LaunchRequest"}}))
    assert result["response"]["shouldEndSession"] is False
    ended = asyncio.run(skill.handle({"request": {"type": "SessionEndedRequest"}}))
    assert ended == {"version": "1.0", "response": {}}
    resolver.assert_not_awaited()


def test_interaction_model_has_searchquery_carrier_phrases():
    model_path = Path(__file__).parents[1] / "examples/alexa/en-US.json"
    model = json.loads(model_path.read_text())["interactionModel"]["languageModel"]
    assert model["invocationName"] == "home television"
    play = next(item for item in model["intents"] if item["name"] == "PlayProgramIntent")
    assert play["slots"] == [{"name": "Query", "type": "AMAZON.SearchQuery"}]
    assert all(sample.count("{Query}") == 1 and sample != "{Query}" for sample in play["samples"])
