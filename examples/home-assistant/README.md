# Google voice through Home Assistant

The fixed-shortcut route is:

**Google voice shortcut -> exposed fixed HA script -> private HA dispatcher ->
gateway `/v1/play` -> HA Cast -> configured display**

Home Assistant requests a fresh signed stream URL, then calls its own
`media_player.play_media` action. The gateway does not need a Home Assistant
token for this setup. Leave `EPG_HA_URL`, `EPG_HA_TOKEN`, and `EPG_HA_TARGETS`
unset unless another client uses the gateway's optional Cast adapter.

The examples provide **Play Kukutiki**, which searches the current EPG, and
**Play News Channel**, which selects a verified catalogue channel. A channel
request still requires a fresh current programme in the guide. It does not
depend on the changing title of the news broadcast.

## Install

1. Add the display through Home Assistant's Google Cast integration and confirm
   ordinary video playback, including audio, on the physical device.
2. Merge [`secrets.example.yaml`](secrets.example.yaml) into private HA
   `secrets.yaml`. Set the fixed `media_player` entity, the gateway API token,
   and a gateway URL that HA can reach. A direct LAN route is suitable; this
   local call does not require a public Cloudflare endpoint.
3. Set `epg_voice_news_channel_id` to a verified channel ID from the gateway's
   catalogue. An authenticated `/v1/resolve` search for a programme currently on
   that channel returns its `channel_id`. Confirm its channel name and playback
   before using it. Remove `play_news_channel` and its Google exposure entry if
   you do not want this optional shortcut.
4. Configure `EPG_TITLE_ALIASES` in the gateway when the spoken title differs
   from the guide spelling; for example, `{"kukutiki":["Кукутики"]}`.
5. Load [`package.yaml`](package.yaml) through HA packages, or merge its sections
   into your configuration. It does not enable a Google integration. Validate
   the complete HA configuration, then reload REST commands and scripts, or
   restart HA when required. Confirm the dispatcher is available after reload:
   an installed validation check caught HA package merging dropping empty
   `text: {}` selectors even though the configuration check reported no errors.
   Keep the supplied `multiline: false` selector options.
6. Run `script.play_kukutiki` or `script.play_news_channel` manually. Check the
   physical display. A successful script call alone does not establish that
   the device has decoded the video.
7. Keep `script.epg_voice_play_programme` and `script.epg_voice_catalogue_controller`
   unexposed to Google and Assist. Expose the fixed wrappers and, if wanted, the
   **Home TV** selector described below. For an existing manual Google integration, merge
   [`google-assistant.example.yaml`](google-assistant.example.yaml) alongside
   its project configuration. For a new integration, follow
   [First-time manual Google linking](#first-time-manual-google-linking) below.
   Apply the explicit dispatcher exclusion before
   syncing, particularly if `expose_by_default` is enabled. Assign wrappers to
   an HA area so other household members can use them, then sync Google devices.
8. First try **"Hey Google, activate Play Kukutiki"**. For a shorter phrase,
   create a Google Home Routine with starter **"Play Kukutiki"** and an action
   that activates the **Play Kukutiki** scene. Repeat for **Play News Channel**.
   Exact recognition and Routine wording depend on account language and device;
   verify both phrases on the actual Google speaker or display.

If you already use Home Assistant Cloud instead of manual Google integration,
omit the separate Google configuration fragment and set the same exposure choices
in the Cloud UI. This example does not require or enable a subscription.

Google exposes HA scripts as scenes. It does **not** pass arbitrary spoken
programme names into script fields. The dispatcher can accept a variable query
from another authorized HA automation, but the Google wrappers deliberately
have fixed inputs and a fixed playback device. Additional fixed shortcuts need
a wrapper and Routine; the dynamic selector below uses one shared handler.
This setup does not use retired Google
Conversational Actions or require Gemini.

## Dynamic current-programme selector

The package also provides **Home TV**, an `input_select` whose options come from
the authenticated gateway `GET /v1/catalogue` endpoint. This offers currently
airing programmes without adding a script for each title. The fixed shortcuts
remain available.

1. Configure the gateway's `EPG_VOICE_CHANNEL_IDS` with an ordered JSON list of
   verified channel IDs. The default empty list considers all mapped channels,
   subject to the catalogue limit. Use an explicit allowlist of channels whose
   codecs and resolution work on the intended display.
2. Optionally set `EPG_VOICE_PREFERRED_QUERIES`, for example
   `["Kukutiki", "Columbo"]`, and the required `EPG_TITLE_ALIASES`. These labels
   appear only while a matching programme is currently airing on an allowed
   channel. Other current titles fill the remaining places. The default
   catalogue limit is 30, configurable through `EPG_VOICE_CATALOGUE_LIMIT`.
3. Set `epg_voice_gateway_catalogue_url` in HA secrets to the reachable gateway
   `/v1/catalogue` URL. Keep `epg_voice_google_agent_user_id` empty unless enabling
   the optional automatic Google sync described below. Validate and restart HA
   when adding these helpers and automations for the first time.
4. Expose `input_select.home_tv` using the supplied Google exposure fragment.
   Include **Sensor** among the device types in the Google integration draft:
   stock HA maps `input_select` to a sensor with the Google **Modes** trait.
   It advertises an enumerated option list, rather than a television's search
   interface. Keep both dispatchers private.
5. Wait for the startup refresh, or run `script.epg_voice_catalogue_controller`
   with no fields to refresh manually. Inspect the **Home TV** options, sync
   Google devices, then select a current label from the HA UI first. Confirm
   picture and audio on the physical display.
6. Try **"Hey Google, set Home TV to Kukutiki"**, or the explicit form
   **"Hey Google, set Home TV option to Kukutiki"**. Substitute an option
   currently advertised to Google. These are phrase candidates; validate
   recognition on the household account and device. Google does not send
   arbitrary spoken programme names through this selector.

The automation refreshes every five minutes. **Ready** is the neutral state;
**Unavailable** is an additional nonplaying option when the catalogue is empty
or cannot be fetched or validated. Having at least two options keeps the
selector suitable for Google's Modes trait. A failed poll removes old choices
and does not start playback.

Only an explicit `input_select.select_option` service event addressed solely to
`input_select.home_tv`, with a listed nonneutral option, requests playback.
State changes and `set_options` updates do not. Each delivered selection fetches
a fresh server mapping, then sends its full title and pinned channel with
`exact_title: true`. If that exact programme has ended, playback fails rather
than matching a different title containing the same words. The selector resets
to **Ready** before dispatch, including when the request later fails. Repeated
selection calls can therefore request new sessions even if the same title was
previously selected; whether Google delivers a particular repeated voice
command remains a device acceptance check.

Refreshes and selections share one queued controller with a maximum of ten
runs, so option replacement cannot overlap its playback decision. Each queued
selection is checked again against the latest offered options and a fresh
catalogue. The helper stores only labels and a revision hash; programme mapping
is transient, and signed media URLs are never written to these helpers. Saved
traces are disabled for the controller and its calling automation.

### Synchronize changed options with Google

Google caches the advertised Modes settings. A changed HA list alone does not
establish that Google knows the new labels. Without HomeGraph configuration,
leave `input_boolean.epg_voice_auto_sync` disabled and ask Google to sync devices
after changes. `input_text.epg_voice_google_sync_status` explicitly indicates
when manual synchronization and phone verification are needed.

For automatic requests, configure HomeGraph and the private service-account
file according to the official HA setup instructions below. Set
`epg_voice_google_agent_user_id` to the HA user ID actually used to link Google,
not its username or an administrator's unrelated ID. Only after checking that
configuration, change the private package's `epg_voice_auto_sync.initial` to
`true`; a UI toggle alone is reset by the example's explicit `initial: false`
on restart. No service-account key belongs in this repository.

The controller requests `google_assistant.request_sync` only when visible
choice labels change, and supplies that explicit `agent_user_id`. Requests are
serialized with the controller. HA's service does not return HomeGraph's result
to the automation, so the status says **requested / acceptance unverified**;
it never claims a successful Google update. Check private HA logs and the Google
Home app. An account that has not linked the integration cannot be synchronized
successfully merely by creating its HA user or enabling the API.

[HA Modes implementation](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/google_assistant/trait.py),
[HA Google device-type mapping](https://github.com/home-assistant/core/blob/2026.9.1/homeassistant/components/google_assistant/const.py).

## First-time manual Google linking

This household setup uses Home Assistant's manual Google Assistant integration.
It does not require a paid Home Assistant Cloud subscription. Google links to
HA; the private gateway API and provider credentials are not entered into the
Google console.

### Public HTTPS and the Google draft

Use a public HA hostname with a trusted TLS certificate, such as
`https://ha.example.com`. Google must reach it from outside your home network.
Keep the HA login page, its frontend assets, and authentication-flow endpoints
reachable. The machine endpoints must accept Google's requests without an
extra Cloudflare Access login, service-token header, or browser challenge.
A successful check from a home-IP allowlist does not establish this.

Create a project in the [Google Home Developer Console](https://console.home.google.com/)
and record its **project ID**, which is different from its display name. Add a
Cloud-to-cloud integration and select the device types you will expose. Prepare
a 144 × 144 PNG integration icon. Google requires a round mark with transparent
space outside it; this is separate from a company-profile logo.
[Google integration setup](https://developers.home.google.com/cloud-to-cloud/integration/create)

You can use the supplied generic [Home Media icon](home-media-icon.png), with
its editable [SVG source](home-media-icon.svg). Rename a local copy of the PNG
to `YOUR_PROJECT_ID.png` before uploading it, as the console requires. Keep the
deployment-specific copy outside Git.

Configure these fields, replacing only the example hostname and project ID:

- OAuth client ID: `https://oauth-redirect.googleusercontent.com/r/YOUR_PROJECT_ID`
- Client secret: a nonempty alphanumeric value; HA does not use this field.
- Authorization URL: `https://ha.example.com/auth/authorize`
- Token URL: `https://ha.example.com/auth/token`
- Cloud fulfillment URL: `https://ha.example.com/api/google_assistant`
- Scopes: add `email` and `name` as two separate entries.
- Leave HTTP Basic authentication for the client ID and secret disabled.
- Leave App Flip and Local fulfillment disabled for the initial setup.

Save the integration as a draft. These settings follow the
[HA manual setup guide](https://www.home-assistant.io/integrations/google_assistant/#manual-setup-if-you-dont-have-home-assistant-cloud).

### Minimal HA configuration

Merge one `google_assistant` section into your private HA configuration:

```yaml
google_assistant:
  project_id: YOUR_PROJECT_ID
  report_state: false
  expose_by_default: false
  entity_config:
    script.epg_voice_play_programme:
      expose: false
    script.epg_voice_catalogue_controller:
      expose: false
    input_select.home_tv:
      name: Home TV
      expose: true
    script.play_kukutiki:
      name: Play Kukutiki
      expose: true
    script.play_news_channel:
      name: Play News Channel
      expose: true
```

The exposure entries match `google-assistant.example.yaml`; merge them instead
of loading two competing Google integration sections. Omit the news entry if
you removed that optional script. Assign the wrappers to the intended HA area.

No service-account JSON key is needed for this minimal configuration. HA
2026.9.2's [configuration schema](https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/components/google_assistant/__init__.py)
permits omitting it when `report_state` is false. This differs from the full
setup shown in HA's user guide. Validate against your installed HA version and
restart HA after first enabling the integration.

Without a service account, HA-initiated `google_assistant.request_sync` and
proactive state reporting are unavailable; incoming Google linking, discovery,
and commands use HA's OAuth and fulfillment endpoints. Ask Google to sync
devices after exposure changes. The credential requirement for HA-initiated
sync is explicit in [HA's implementation](https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/components/google_assistant/http.py).

For the standard reporting setup, follow HA's service-account and HomeGraph API
instructions, store the JSON file privately, add
`service_account: !include SERVICE_ACCOUNT.json`, and enable `report_state` if
needed. Keep that file out of Git and public logs. It is separate from the
gateway API token and the console's client-secret field.

### Link and verify on the household account

After the restart, an unauthenticated browser GET to
`https://ha.example.com/api/google_assistant` should return **405 Method Not
Allowed**. A 404 means the integration or route is missing; an Access login or
Cloudflare challenge means the request did not reach the required endpoint.

In the Google Home app, use **Add device → Works with Google Home**, choose
**[test] Your integration name**, and sign in to HA. Use the Google account that
owns the developer project. Google's scene acknowledgment is not playback
proof: run **“Hey Google, activate Play Kukutiki”** and verify picture and audio
on the display. Then create the shorter fixed Routine described above.

Account linking grants Google renewable HA access as the user who signs in.
The `email` and `name` entries are not entity-level HA token scopes, and exposing
only two scenes does not restrict the underlying OAuth token to those scenes.
Use a dedicated non-administrator HA user to avoid granting administrative
privileges. HA's standard non-administrator role still permits reading and
controlling all entities. Treat exposure as a discovery setting, not an
authorization boundary; a deployment requiring token-level access to only two
commands needs a separately scoped authorization service.
[HA token issuance](https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/components/auth/__init__.py),
[standard user policy](https://github.com/home-assistant/core/blob/2026.9.2/homeassistant/auth/permissions/system_policies.py).

For additional testers, grant the appropriate project access and have each
tester accept the developer terms. If the draft remains hidden, Google's
[tester activation instructions](https://developers.home.google.com/cloud-to-cloud/test#add-testers-to-your-integration)
require opening the project's Test or Retest flow before looking again in the
app. Household linking is separate from submitting certification test results.

### Draft branding and public release

The documented draft setup requires the integration icon. Company website,
privacy URL, company logo, and contacts belong to the separate
[company profile](https://developers.home.google.com/cloud-to-cloud/project/profile).
Google requires that profile before certification test plans; the draft
linking flow above does not submit it for certification. A support-site URL is
not listed as a field in the integration-creation instructions.

This distinction is not a privacy-policy exemption: Google's
[developer policies](https://developers.home.google.com/policies) require an
applicable privacy-policy link for integrations. Follow the current console
validation and use information that describes your own deployment before
sharing or publishing it. This example does not publish a Google integration.

## Dispatcher inputs and failures

`script.epg_voice_play_programme` accepts `query`, `channel_id`, or both. With
both supplied, the channel filters the title search. It sends no `target` to
the gateway, which returns a session with status `ready`. HA then starts Cast
playback locally with the signed media URL and HLS content type. HA and the
receiver infer the stream type. This minimal payload sustained playback in a
receiver compatibility check where adding an explicit `LIVE` stream type and
metadata did not; the check did not isolate which added field caused the failure.
The player entity is fixed in `secrets.yaml`; a voice caller cannot choose
another entity or service.
The optional `exact_title` field defaults to false; the dynamic selector sets
it to true together with the full current title and channel ID.

- Missing inputs stop before a network request.
- HTTP 404 means no current programme was found.
- HTTP 409 means the title is ambiguous; configure a more specific title or a
  verified `channel_id` in the fixed wrapper.
- Other HTTP failures, connection errors, and malformed success responses stop
  the script before Cast playback. Response bodies are not copied into errors.
- A Cast service error fails the script. Google's scene acknowledgment can occur
  before the background script finishes, so it is not a playback confirmation.

The REST command waits up to 60 seconds to obtain the session. Receiver startup
happens afterward inside HA, so it does not compete with the gateway adapter's
HTTP timeout. The alternative gateway-managed Cast adapter allows a 40-second
response read budget with five-second connection, write, and pool limits; keep
any caller's timeout longer when using that separate route.

## Playback and private data

- The only gateway credential in HA is `EPG_API_TOKEN`, supplied as a Bearer
  authorization header. Keep the token and deployment details out of Git.
- All supplied scripts disable saved traces because the response contains a
  signed media URL. Disable traces in any calling automation that stores the
  response too. Do not return the session URL from the dispatcher.
- Keep `homeassistant.components.rest_command` at `warning` or higher; debug
  logs can include the Authorization header. Merge this once into the existing
  global `logger.logs` configuration, rather than repeating it in packages:

  ```yaml
  logger:
    logs:
      homeassistant.components.rest_command: warning
  ```

  Cast and HTTP diagnostics can contain media URLs; keep HA logs private and
  redact URLs before sharing them. Disabling script traces is not log redaction.
  The Cast entity can also contain its current media URL; treat HA state,
  recorder history, and backups as private.
- The receiver must reach every HTTPS media resource itself. Use trusted TLS
  and a hostname resolvable by the receiver. Cast devices may use Google DNS,
  so `.local` or LAN-only DNS overrides may fail even when HA resolves them.
- Cloudflare Access browser login and service-token headers are unsuitable for
  receiver media requests. Playback uses the gateway's bounded signed URL.
- The gateway does not transcode. Verify codecs and resolution on the display.
  Sessions expire and are invalidated by gateway restarts; request playback
  again when a session ends.

## References

- [HA Google Assistant integration and script exposure](https://www.home-assistant.io/integrations/google_assistant/)
- [HA Cast playback and metadata](https://www.home-assistant.io/integrations/cast/)
- [HA REST command responses](https://www.home-assistant.io/integrations/rest_command/)
- [HA script syntax and errors](https://www.home-assistant.io/docs/scripts/)
- [Google Conversational Actions sunset](https://developers.google.com/assistant/ca-sunset)
