# Free-text playback with Home Assistant Assist

The optional `epg_voice_channels` integration adds the action
`epg_voice_channels.play_query`. It resolves a channel or a currently airing
programme through the gateway and uses the display already selected in the
integration. The existing Google Channel device and OAuth configuration are
unchanged.

This action requires a real, active Home Assistant user context with control
permission for that display. The authenticated caller may be an owner, an admin,
or a regular user. It does not impersonate the dedicated Google user. A call
without user identity, including an unattended automation with no user context,
is rejected. The Google fulfillment route retains its separate dedicated-user
restriction.

## Install the optional sentences

1. Install the current gateway and custom integration following
   [the channel guide](../../docs/google-channels.md). Confirm that the gateway's
   authenticated `POST /v1/play-text` API is available.
2. Copy `assist-sentences.en.yaml` to
   `<config>/custom_sentences/en/epg_voice.yaml`. Optionally copy the Russian
   example to `<config>/custom_sentences/ru/epg_voice.yaml`.
3. Add the mapping in `assist-intent-script.yaml` under the existing
   `intent_script:` section, or include that file. Preserve existing intents;
   do not create two top-level `intent_script:` keys.
4. Validate the Home Assistant configuration. Reload intent scripts and the
   conversation integration's custom sentences if already loaded; when adding
   either integration for the first time, restart Home Assistant after validation.
5. Select a Home Assistant conversation agent in an Assist pipeline with the
   matching language. A third-party LLM conversation agent is not required and
   may not use these custom sentences.

These opt-in examples recognize media verbs only. They do not install a global
wildcard intent that consumes unrelated home-control requests. Existing custom
media sentences may overlap; keep one handler for the same phrases. In English,
the bare `play`, `watch`, and `show` forms intentionally route media requests to
this gateway. Remove those forms if another media integration should handle them,
and retain the explicit `on home channels` form. Pause, resume, volume, lighting,
and other home-control commands remain outside these examples.

## Use and verify

Start with typed text in Assist while signed in as a user who can control the
configured display. Use a channel that exists in your playlist, for example
`play Example News`, `watch Example News on home channels`, or a current
programme title. Russian examples include `играй Кукутики`, `включи канал CNN`,
`покажи передачу Коломбо`, or `включи CNN на экране`. The `включи` and `покажи`
forms require a media noun or the fixed suffix `на экране` / `на home channels`,
so `включи свет` remains a normal home-control command. Channel/programme nouns
are preserved as search constraints. Both languages remove fixed display phrases
from the query and do not accept arbitrary receiver names. Service responses are
English. Queries, including any channel/programme prefix, are limited to 200
characters.

The service can also be invoked from Developer Tools > Actions:

```yaml
action: epg_voice_channels.play_query
data:
  query: "play Example News"
```

An action sequence can capture the response using `response_variable`. The
response contains only `status`, a plain `message`, and a `matches` list of channel
names. It never includes a signed media URL or gateway credential. Statuses are:

- `accepted`: Home Assistant accepted one validated playback request. Confirm
  moving video, sound, and sustained progress on the receiver separately.
- `ambiguous`: several channels match; repeat a complete request with a more
  specific channel name. There is no implicit conversational choice state.
- `no_match`: no matching channel or current programme was found.
- `unavailable`: the catalogue, matched media, display, or dispatch is unavailable.
- `busy`: another Google or Assist channel change is still in progress.
- `not_authorized`: the caller lacks a usable user identity or display permission.
- `integration_unavailable`: the custom integration has no single loaded entry.

The service does not accept a target entity, URL, or channel ID from the sentence.
It rechecks the caller's permission after obtaining a grant and before dispatch.
Concurrent Google and Assist channel changes use the same guard. A timeout after
receiver dispatch may still leave an accepted command playing, so inspect the
screen before repeating requests. Normal Home Assistant traces can contain query
text and the internal media-player action; treat Home Assistant backups and
diagnostic exports as private.

After typed requests work, use Assist on a phone or a configured Assist satellite.
The Lenovo/Google display remains the video receiver. Installing these sentences
does not connect that display's Google microphone to Assist, and Google
Cloud-to-cloud does not forward unrestricted speech to this action.

To remove this entry point, remove the `EPGVoicePlayMedia` intent and its sentence
files, then reload the corresponding integrations. This does not remove the
Google Channel device.

References: [custom sentences](https://www.home-assistant.io/voice_control/custom_sentences/),
[intent script responses](https://www.home-assistant.io/integrations/intent_script/#using-the-action-response),
and [service response support](https://developers.home-assistant.io/docs/dev_101_services/).
