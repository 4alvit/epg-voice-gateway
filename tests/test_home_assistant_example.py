"""Exercise the shipped YAML and Jinja contracts, without a live HA installation.

The small harness supports only the actions used by this package. It verifies
payloads and fail-closed sequencing; HA configuration validation and real Cast
playback remain deployment checks.
"""

import ast
import copy
import hashlib
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
    "epg_voice_gateway_catalogue_url": "https://control.example.test/v1/catalogue",
    "epg_voice_gateway_authorization": "Bearer synthetic-api-token",
    "epg_voice_player_entity": "media_player.example_display",
    "epg_voice_news_channel_id": "a" * 24,
    "epg_voice_google_agent_user_id": "",
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


def catalogue(options=None):
    options = options if options is not None else [
        {"label": "Kukutiki", "query": "Кукутики", "channel_id": "a" * 24},
    ]
    return {"status": 200, "content": {
        "options": options,
        "revision": hashlib.sha256(json.dumps(options, sort_keys=True).encode()).hexdigest(),
    }}


class ScriptStopped(Exception):
    """An explicit, safe stop from the example's YAML sequence."""

    def __init__(self, message, error=True):
        super().__init__(message)
        self.error = error


class ExampleHarness:
    def __init__(self, response=None, catalogue_response=None):
        self.package = yaml.load((EXAMPLES / "package.yaml").read_text(), Loader=ExampleLoader)
        self.templates = environment(NativeEnvironment)
        self.text_templates = environment(Environment)
        self.response = response if response is not None else success()
        self.catalogue_response = catalogue_response if catalogue_response is not None else catalogue()
        self.requests = []
        self.casts = []
        self.catalogue_requests = 0
        self.syncs = []
        self.options = list(self.package["input_select"]["home_tv"]["options"])
        self.states = {
            "input_select.home_tv": "Ready",
            "input_boolean.epg_voice_auto_sync": "off",
            **{"input_text." + key: value.get("initial", "")
               for key, value in self.package["input_text"].items()},
        }
        self.templates.globals.update({
            "states": lambda entity: self.states.get(entity, "unknown"),
            "is_state": lambda entity, value: self.states.get(entity) == value,
            "state_attr": lambda entity, attribute: self.options if
            (entity, attribute) == ("input_select.home_tv", "options") else None,
        })

    def render(self, value, variables):
        if isinstance(value, str) and ("{{" in value or "{%" in value):
            rendered = self.templates.from_string(value).render(variables)
            if isinstance(rendered, str):
                # HA parses stripped template results into native values;
                # Jinja's NativeEnvironment alone retains block whitespace.
                try:
                    return ast.literal_eval(rendered.strip())
                except (SyntaxError, ValueError):
                    pass
            return rendered
        if isinstance(value, dict):
            return {key: self.render(item, variables) for key, item in value.items()}
        return value

    def run(self, name="epg_voice_play_programme", **variables):
        try:
            self.sequence(self.package["script"][name]["sequence"], variables)
        except ScriptStopped as stopped:
            if stopped.error:
                raise

    def event(self, *, domain="input_select", service="select_option", **data):
        automation = self.package["automation"][0]
        event_trigger = next(item for item in automation["triggers"] if item["id"] == "select")
        if event_trigger["event_data"] != {"domain": domain, "service": service}:
            return
        variables = {"trigger": {"id": "select", "event": {"data": {"service_data": data}}}}
        condition = automation["conditions"][0]["value_template"]
        allowed = self.render(condition, variables)
        assert isinstance(allowed, bool)
        if allowed:
            self.sequence(automation["actions"], variables)

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
                for key, value in step["variables"].items():
                    variables[key] = self.render(value, variables)
            elif "stop" in step:
                raise ScriptStopped(step["stop"], step.get("error", False))
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
                elif action == "rest_command.epg_voice_catalogue":
                    self.catalogue_requests += 1
                    if isinstance(self.catalogue_response, Exception):
                        assert step["continue_on_error"] is True
                        continue
                    response = self.catalogue_response() if callable(self.catalogue_response) else self.catalogue_response
                    variables[step["response_variable"]] = copy.deepcopy(response)
                elif action == "input_select.set_options":
                    assert step["target"]["entity_id"] == "input_select.home_tv"
                    self.options = data["options"]
                elif action == "input_select.select_option":
                    assert step["target"]["entity_id"] == "input_select.home_tv"
                    assert data["option"] in self.options
                    self.states["input_select.home_tv"] = data["option"]
                    # Real call_service events include internal neutral resets.
                    self.event(entity_id="input_select.home_tv", option=data["option"])
                elif action == "input_text.set_value":
                    self.states[step["target"]["entity_id"]] = data["value"]
                elif action == "google_assistant.request_sync":
                    self.syncs.append(data)
                    assert step["continue_on_error"] is True
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


def test_script_exposure_and_traces_keep_dispatcher_private():
    package = ExampleHarness().package
    assert "google_assistant" not in package
    google = yaml.safe_load((EXAMPLES / "google-assistant.example.yaml").read_text())
    exposure = google["google_assistant"]["entity_config"]
    assert exposure["script.epg_voice_play_programme"]["expose"] is False
    assert exposure["script.epg_voice_catalogue_controller"]["expose"] is False
    assert exposure["input_select.home_tv"]["expose"] is True
    assert exposure["script.play_kukutiki"]["expose"] is True
    assert exposure["script.play_news_channel"]["expose"] is True
    for name, script in package["script"].items():
        assert script["trace"]["stored_traces"] == 0
        assert script["mode"] == ("queued" if name == "epg_voice_catalogue_controller" else "single")
    command = package["rest_command"]["epg_voice_session"]
    assert command["headers"]["Authorization"] == SECRETS["epg_voice_gateway_authorization"]
    assert command["timeout"] == 60
    # Global logger entries must be merged once, not duplicated across packages.
    assert "logger" not in package


def test_field_selectors_survive_home_assistant_package_merging():
    fields = ExampleHarness().package["script"]["epg_voice_play_programme"]["fields"]
    assert fields.keys() == {"query", "channel_id", "exact_title"}
    for field in (fields["query"], fields["channel_id"]):
        selector = field["selector"]
        assert len(selector) == 1
        # HA package merging drops empty mappings before selector validation.
        # A bare `text: {}` disables the dispatcher after an otherwise clean reload.
        assert selector["text"] == {"multiline": False}
    assert fields["exact_title"]["default"] is False
    assert "selector" not in fields["exact_title"]


def test_all_secret_references_have_generic_documented_placeholders():
    examples = yaml.safe_load((EXAMPLES / "secrets.example.yaml").read_text())
    assert examples.keys() == SECRETS.keys()
    assert examples["epg_voice_player_entity"] == "media_player.example_display"
    assert examples["epg_voice_news_channel_id"].startswith("REPLACE_WITH_")


def test_catalogue_refresh_updates_choices_without_playback_or_automatic_sync():
    example = ExampleHarness()
    example.run("epg_voice_catalogue_controller")
    assert example.options == ["Ready", "Kukutiki"]
    assert example.states["input_select.home_tv"] == "Ready"
    assert example.catalogue_requests == 1
    assert example.requests == example.casts == example.syncs == []
    assert example.states["input_text.epg_voice_catalogue_revision"] == catalogue()["content"]["revision"]
    assert "Ask Google to sync" in example.states["input_text.epg_voice_google_sync_status"]
    command = example.package["rest_command"]["epg_voice_catalogue"]
    assert command == {
        "url": SECRETS["epg_voice_gateway_catalogue_url"], "method": "GET",
        "headers": {"Authorization": SECRETS["epg_voice_gateway_authorization"]}, "timeout": 15,
    }


def test_repeated_explicit_selections_fetch_fresh_mapping_and_do_not_recurse_on_reset():
    example = ExampleHarness()
    example.run("epg_voice_catalogue_controller")
    for _ in range(2):
        example.event(entity_id="input_select.home_tv", option="Kukutiki")
        assert example.states["input_select.home_tv"] == "Ready"
    assert example.catalogue_requests == 3
    assert example.requests == [{"query": "Кукутики", "channel_id": "a" * 24, "exact_title": True}] * 2
    assert len(example.casts) == 2
    assert all(cast["target"] == {"entity_id": SECRETS["epg_voice_player_entity"]} for cast in example.casts)


@pytest.mark.parametrize("event", [
    {"entity_id": "input_select.other", "option": "Kukutiki"},
    {"entity_id": ["input_select.home_tv", "input_select.other"], "option": "Kukutiki"},
    {"entity_id": "all", "option": "Kukutiki"},
    {"entity_id": "input_select.home_tv", "option": "Ready"},
    {"entity_id": "input_select.home_tv", "option": "Unavailable"},
    {"entity_id": "input_select.home_tv", "option": "Not offered"},
    {"entity_id": "input_select.home_tv", "option": None},
    {"service": "set_options", "entity_id": "input_select.home_tv", "option": "Kukutiki"},
])
def test_only_explicit_single_selector_calls_for_offered_playable_labels_dispatch(event):
    example = ExampleHarness()
    example.options = ["Ready", "Kukutiki"]
    example.event(**event)
    assert example.catalogue_requests == 0
    assert example.requests == example.casts == []


def test_selection_uses_fresh_server_mapping_instead_of_previous_title_or_channel():
    example = ExampleHarness()
    example.run("epg_voice_catalogue_controller")
    example.catalogue_response = catalogue([
        {"label": "Kukutiki", "query": 'Кукутики "New episode"', "channel_id": "b" * 24},
    ])
    example.event(entity_id=["input_select.home_tv"], option="Kukutiki")
    assert example.requests == [{"query": 'Кукутики "New episode"', "channel_id": "b" * 24, "exact_title": True}]


def test_selection_removed_by_refresh_cannot_launch_from_an_old_delivered_label():
    example = ExampleHarness()
    example.run("epg_voice_catalogue_controller")
    example.catalogue_response = catalogue([])
    example.event(entity_id="input_select.home_tv", option="Kukutiki")
    assert example.options == ["Ready", "Unavailable"]
    assert example.states["input_select.home_tv"] == "Ready"
    assert example.requests == example.casts == []


def test_queued_selection_no_longer_offered_fails_closed_even_if_fresh_map_reintroduces_it():
    example = ExampleHarness()
    example.run("epg_voice_catalogue_controller", option="Kukutiki")
    assert example.options == ["Ready", "Kukutiki"]
    assert example.requests == example.casts == []


def test_exact_title_rejection_resets_selector_before_dispatcher_error():
    example = ExampleHarness(response={"status": 404, "content": {}})
    example.options = ["Ready", "Kukutiki"]
    example.states["input_select.home_tv"] = "Kukutiki"
    with pytest.raises(ScriptStopped, match="No current programme"):
        example.event(entity_id="input_select.home_tv", option="Kukutiki")
    assert example.states["input_select.home_tv"] == "Ready"
    assert example.requests[0]["exact_title"] is True
    assert example.casts == []


@pytest.mark.parametrize("response", [
    TimeoutError("synthetic-private-transport-error"),
    {"status": 503, "content": "synthetic-private-error"},
    {"status": 200, "content": {}},
    {"status": 200, "content": {"options": "wrong", "revision": "f" * 64}},
    {"status": 200, "content": {"options": [], "revision": "invalid"}},
    catalogue([{"label": "Ready", "query": "News", "channel_id": "a" * 24}]),
    catalogue([{"label": " Bad label", "query": "News", "channel_id": "a" * 24}]),
    catalogue([{"label": "Bad\nlabel", "query": "News", "channel_id": "a" * 24}]),
    catalogue([{"label": "Long" * 26, "query": "News", "channel_id": "a" * 24}]),
    catalogue([{"label": "Bad", "query": " ", "channel_id": "a" * 24}]),
    catalogue([{"label": "Bad", "query": "News", "channel_id": "https://source.example.test"}]),
    catalogue([{"label": name, "query": "News", "channel_id": "a" * 24} for name in ("News", "NEWS")]),
])
def test_catalogue_failure_clears_stale_choices_without_launching(response):
    example = ExampleHarness(catalogue_response=response)
    example.options = ["Ready", "Kukutiki"]
    example.states["input_select.home_tv"] = "Kukutiki"
    example.event(entity_id="input_select.home_tv", option="Kukutiki")
    assert example.options == ["Ready", "Unavailable"]
    assert example.states["input_select.home_tv"] == "Ready"
    assert example.states["input_text.epg_voice_catalogue_revision"] == "unavailable"
    assert example.requests == example.casts == []


def test_optional_sync_targets_explicit_linked_user_only_when_choice_labels_change():
    example = ExampleHarness()
    example.states["input_boolean.epg_voice_auto_sync"] = "on"
    example.states["input_text.epg_voice_google_agent_user_id"] = "a" * 32
    example.run("epg_voice_catalogue_controller")
    example.run("epg_voice_catalogue_controller")
    assert example.syncs == [{"agent_user_id": "a" * 32}]
    status = example.states["input_text.epg_voice_google_sync_status"]
    assert "unverified" in status and "requested" in status
    example.catalogue_response = catalogue([{"label": "Kukutiki", "query": "Next Kukutiki", "channel_id": "a" * 24}])
    example.run("epg_voice_catalogue_controller")
    assert len(example.syncs) == 1  # Mapping changed, but Google-visible modes did not.
    example.catalogue_response = catalogue([])
    example.run("epg_voice_catalogue_controller")
    assert len(example.syncs) == 2


def test_unconfigured_auto_sync_retains_explicit_manual_sync_status():
    example = ExampleHarness()
    example.states["input_boolean.epg_voice_auto_sync"] = "on"
    example.run("epg_voice_catalogue_controller")
    assert example.syncs == []
    assert "Ask Google to sync" in example.states["input_text.epg_voice_google_sync_status"]


def test_refresh_and_selection_share_one_bounded_queue_and_never_watch_state_changes():
    package = ExampleHarness().package
    controller = package["script"]["epg_voice_catalogue_controller"]
    assert controller["mode"] == "queued" and controller["max"] == 10
    assert controller["trace"]["stored_traces"] == 0
    automation = package["automation"][0]
    assert automation["trace"]["stored_traces"] == 0
    assert {trigger["trigger"] for trigger in automation["triggers"]} == {"event", "time_pattern", "homeassistant"}
    assert next(trigger for trigger in automation["triggers"] if trigger["trigger"] == "time_pattern")["minutes"] == "/5"
    assert automation["actions"][0]["action"] == "script.epg_voice_catalogue_controller"
    assert package["input_boolean"]["epg_voice_auto_sync"]["initial"] is False
