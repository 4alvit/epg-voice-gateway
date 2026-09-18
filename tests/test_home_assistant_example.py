"""Exercise the shipped YAML and Jinja contracts, without a live HA installation.

The small harness supports only the actions used by this package. It verifies
payloads and fail-closed sequencing; HA configuration validation and real Cast
playback remain deployment checks.
"""

import copy
import json
import re
from pathlib import Path

import pytest
import yaml
from jinja2 import Environment, StrictUndefined
from jinja2.nativetypes import NativeEnvironment

EXAMPLES = Path(__file__).parents[1] / "examples" / "home-assistant"
SECRETS = {
    "epg_voice_gateway_play_url": "https://control.example.test/v1/play",
    "epg_voice_gateway_authorization": "Bearer synthetic-api-token",
    "epg_voice_player_entity": "media_player.example_display",
    "epg_voice_news_channel_id": "a" * 24,
}
MEDIA_URL = "https://media.example.test/media/v1.synthetic/stream.m3u8"


class ExampleLoader(yaml.SafeLoader):
    pass


ExampleLoader.add_constructor(
    "!secret", lambda loader, node: SECRETS[loader.construct_scalar(node)],
)


def environment(kind):
    result = kind(undefined=StrictUndefined)
    result.filters["to_json"] = json.dumps
    result.filters["regex_match"] = lambda value, pattern: bool(re.match(pattern, value))
    return result


def success(media_url=MEDIA_URL):
    return {
        "status": 200,
        "content": {
            "status": "ready",
            "media_url": media_url,
            "programme": {"title": "Synthetic programme", "channel_name": "Example News"},
        },
    }


class ScriptStopped(Exception):
    """An explicit, safe stop from the example's YAML sequence."""


class ExampleHarness:
    def __init__(self, response=None):
        self.package = yaml.load((EXAMPLES / "package.yaml").read_text(), Loader=ExampleLoader)
        self.templates = environment(NativeEnvironment)
        self.text_templates = environment(Environment)
        self.response = response if response is not None else success()
        self.requests = []
        self.casts = []

    def render(self, value, variables):
        if isinstance(value, str) and ("{{" in value or "{%" in value):
            return self.templates.from_string(value).render(variables)
        if isinstance(value, dict):
            return {key: self.render(item, variables) for key, item in value.items()}
        return value

    def run(self, name="epg_voice_play_programme", **variables):
        self.sequence(self.package["script"][name]["sequence"], variables)

    def sequence(self, steps, variables):
        for step in steps:
            if "if" in step:
                condition = self.render(step["if"], variables)
                assert isinstance(condition, bool)
                if condition:
                    self.sequence(step["then"], variables)
            elif "choose" in step:
                for choice in step["choose"]:
                    if self.render(choice["conditions"], variables):
                        self.sequence(choice["sequence"], variables)
                        break
            elif "variables" in step:
                variables.update(self.render(step["variables"], variables))
            elif "stop" in step:
                assert step["error"] is True
                raise ScriptStopped(step["stop"])
            elif "action" in step:
                data = self.render(step.get("data", {}), variables)
                action = step["action"]
                if action == "rest_command.epg_voice_session":
                    template = self.package["rest_command"]["epg_voice_session"]["payload"]
                    self.requests.append(json.loads(self.text_templates.from_string(template).render(data)))
                    if isinstance(self.response, Exception):
                        assert step["continue_on_error"] is True
                        continue
                    response = self.response() if callable(self.response) else self.response
                    variables[step["response_variable"]] = copy.deepcopy(response)
                elif action == "media_player.play_media":
                    assert not step.get("continue_on_error", False)
                    self.casts.append({"target": self.render(step["target"], variables), "data": data})
                elif action.startswith("script."):
                    self.run(action.removeprefix("script."), **data)
                else:
                    pytest.fail(f"Unexpected example action: {action}")
            else:
                pytest.fail("Unsupported action in example contract harness")


def test_programme_wrapper_requests_session_then_casts_locally_to_fixed_player():
    example = ExampleHarness()
    example.run("play_kukutiki", target="arbitrary", query="caller override")
    assert example.requests == [{"query": "Kukutiki"}]
    assert example.casts == [{
        "target": {"entity_id": "media_player.example_display"},
        "data": {
            "media_content_id": MEDIA_URL,
            "media_content_type": "application/vnd.apple.mpegurl",
            "extra": {
                "stream_type": "LIVE",
                "metadata": {
                    "metadataType": 0,
                    "title": "Synthetic programme",
                    "subtitle": "Example News",
                },
            },
        },
    }]


def test_news_wrapper_uses_private_channel_id_without_an_epg_title():
    example = ExampleHarness()
    example.run("play_news_channel")
    assert example.requests == [{"channel_id": "a" * 24}]
    assert len(example.casts) == 1


def test_query_escaping_optional_channel_and_untrusted_target_cannot_change_payload():
    example = ExampleHarness()
    query = 'Кукутики "special", "target": "other"\nEpisode'
    example.run(query=query, channel_id=f" {'b' * 24} ", target="other")
    assert example.requests == [{"query": query, "channel_id": "b" * 24}]
    assert example.casts[0]["target"] == {"entity_id": SECRETS["epg_voice_player_entity"]}


def test_every_invocation_requests_a_fresh_session():
    responses = iter([success(MEDIA_URL), success(MEDIA_URL.replace("stream", "next"))])
    example = ExampleHarness(lambda: next(responses))
    example.run("play_kukutiki")
    example.run("play_kukutiki")
    assert len(example.requests) == 2
    assert example.casts[0]["data"]["media_content_id"] != example.casts[1]["data"]["media_content_id"]


@pytest.mark.parametrize("variables", [{}, {"query": "  "}, {"query": None, "channel_id": ""}])
def test_missing_inputs_stop_without_calling_gateway_or_cast(variables):
    example = ExampleHarness()
    with pytest.raises(ScriptStopped, match="Supply a programme title"):
        example.run(**variables)
    assert example.requests == []
    assert example.casts == []


@pytest.mark.parametrize("status,message", [
    (404, "No current programme"),
    (409, "Several programmes match"),
    (401, "rejected"),
    (503, "rejected"),
])
def test_http_errors_stop_before_cast_without_echoing_body(status, message):
    example = ExampleHarness({"status": status, "content": "private-response-data"})
    with pytest.raises(ScriptStopped, match=message) as error:
        example.run(query="News")
    assert "private-response-data" not in str(error.value)
    assert example.casts == []


def test_transport_failure_stops_safely_without_response_variable():
    example = ExampleHarness(TimeoutError("private-response-data"))
    with pytest.raises(ScriptStopped, match="did not respond") as error:
        example.run(query="News")
    assert "private-response-data" not in str(error.value)
    assert example.casts == []


@pytest.mark.parametrize("content", [
    "<html>Unexpected proxy page</html>",
    None,
    {},
    {"status": "ready", "programme": {}},
    {"status": "ready", "programme": {}, "media_url": None},
    {"status": "ready", "programme": {}, "media_url": "http://media.example.test/media/a/a.m3u8"},
    {"status": "ready", "programme": {}, "media_url": "https://user:pass@media.example.test/media/a/a.m3u8"},
    {"status": "ready", "programme": {}, "media_url": "https://media.example.test/login"},
    {"status": "ready", "programme": {}, "media_url": MEDIA_URL + "\n"},
    {"status": "ready", "programme": {}, "media_url": MEDIA_URL + "#fragment"},
    {"status": "playing", "programme": {}, "media_url": MEDIA_URL},
])
def test_malformed_success_responses_never_reach_cast(content):
    example = ExampleHarness({"status": 200, "content": content})
    with pytest.raises(ScriptStopped, match="invalid playback session"):
        example.run(query="News")
    assert example.casts == []


def test_script_exposure_traces_and_rest_logging_keep_dispatcher_private():
    package = ExampleHarness().package
    assert "google_assistant" not in package
    google = yaml.safe_load((EXAMPLES / "google-assistant.example.yaml").read_text())
    exposure = google["google_assistant"]["entity_config"]
    assert exposure["script.epg_voice_play_programme"]["expose"] is False
    assert exposure["script.play_kukutiki"]["expose"] is True
    assert exposure["script.play_news_channel"]["expose"] is True
    for script in package["script"].values():
        assert script["trace"]["stored_traces"] == 0
        assert script["mode"] == "single"
    command = package["rest_command"]["epg_voice_session"]
    assert command["headers"]["Authorization"] == SECRETS["epg_voice_gateway_authorization"]
    assert command["timeout"] == 60
    assert package["logger"]["logs"]["homeassistant.components.rest_command"] == "warning"


def test_all_secret_references_have_generic_documented_placeholders():
    examples = yaml.safe_load((EXAMPLES / "secrets.example.yaml").read_text())
    assert examples.keys() == SECRETS.keys()
    assert examples["epg_voice_player_entity"] == "media_player.example_display"
    assert examples["epg_voice_news_channel_id"].startswith("REPLACE_WITH_")
