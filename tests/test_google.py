import asyncio
import json

import httpx
import pytest

from epg_voice_gateway.errors import PlaybackUnavailable
from epg_voice_gateway.google import HomeAssistantCaster

MEDIA_URL = "https://media.example.com/session/opaque/index.m3u8"


def run_cast(handler, *, target="kitchen", media_url=MEDIA_URL):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            caster = HomeAssistantCaster(
                client,
                "https://ha.example.com",
                "test-token",
                {"kitchen": "media_player.kitchen_display"},
            )
            await caster.play(target, media_url)

    return asyncio.run(run())


def test_calls_only_approved_entity_and_fixed_playback_service():
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert str(request.url) == "https://ha.example.com/api/services/media_player/play_media"
        assert request.headers["Authorization"] == "Bearer test-token"
        assert json.loads(request.content) == {
            "entity_id": "media_player.kitchen_display",
            "media_content_id": MEDIA_URL,
            "media_content_type": "application/vnd.apple.mpegurl",
        }
        return httpx.Response(200, json=[])

    run_cast(handler)
    assert len(requests) == 1


@pytest.mark.parametrize("target", ["unknown", "media_player.arbitrary", "../services/script/turn_on"])
def test_unknown_target_is_rejected_before_network_request(target):
    def handler(request):
        pytest.fail("An unapproved target reached Home Assistant")

    with pytest.raises(PlaybackUnavailable, match="not configured"):
        run_cast(handler, target=target)


@pytest.mark.parametrize(
    "media_url",
    [
        "http://media.example.com/video.m3u8",
        "https://user:password@media.example.com/video.m3u8",
        "https://media.example.com/video.m3u8#fragment",
        "https://media.example.com/not-a-playlist",
        "https://media.example.com:invalid/video.m3u8",
        "https://media.example.com/video.m3u8\n",
    ],
)
def test_unsafe_playback_url_is_rejected_without_network(media_url):
    def handler(request):
        pytest.fail("An invalid playback URL reached Home Assistant")

    with pytest.raises(PlaybackUnavailable):
        run_cast(handler, media_url=media_url)


@pytest.mark.parametrize("status", [301, 302, 307, 401, 403, 500])
def test_errors_and_redirects_are_not_followed_or_exposed(status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://unapproved.example.com"},
            text="private diagnostic with test-token",
        )

    with pytest.raises(PlaybackUnavailable) as error:
        run_cast(handler)
    assert len(requests) == 1
    assert "test-token" not in str(error.value)
    assert "private diagnostic" not in str(error.value)
    assert "unapproved" not in str(error.value)


def test_network_error_is_not_retried_and_omits_request_details():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ConnectError("private request details", request=request)

    with pytest.raises(PlaybackUnavailable) as error:
        run_cast(handler)
    assert len(requests) == 1
    assert "private request" not in str(error.value)


@pytest.mark.parametrize(
    "base_url",
    [
        "file:///etc/passwd",
        "https://user:password@ha.example.com",
        "https://ha.example.com?token=secret",
        "https://ha.example.com#fragment",
        "https://ha.example.com\n",
        "https://ha.example.com:invalid",
    ],
)
def test_invalid_ha_configuration_rejected(base_url):
    async def run():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError):
                HomeAssistantCaster(client, base_url, "test-token", {"kitchen": "media_player.kitchen"})

    asyncio.run(run())


@pytest.mark.parametrize(
    "targets",
    [{"kitchen": "script.anything"}, {"kitchen": "media_player.kitchen,media_player.office"}, {"bad alias": "media_player.kitchen"}],
)
def test_configuration_cannot_target_arbitrary_home_assistant_services(targets):
    async def run():
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError):
                HomeAssistantCaster(client, "https://ha.example.com", "test-token", targets)

    asyncio.run(run())
