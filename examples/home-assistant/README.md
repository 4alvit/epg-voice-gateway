# Google voice through Home Assistant

The supported route is:

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
7. Keep `script.epg_voice_play_programme` unexposed to Google and Assist. Expose
   only the fixed wrappers. For an existing manual Google integration, merge
   [`google-assistant.example.yaml`](google-assistant.example.yaml) alongside
   its project and credentials. Apply the explicit dispatcher exclusion before
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
have fixed inputs and a fixed playback device. Add a wrapper and a Routine for
each additional favourite programme. This setup does not use retired Google
Conversational Actions or require Gemini.

## Dispatcher inputs and failures

`script.epg_voice_play_programme` accepts `query`, `channel_id`, or both. With
both supplied, the channel filters the title search. It sends no `target` to
the gateway, which returns a session with status `ready`. HA then starts Cast
playback locally with HLS content type, `LIVE` stream type, programme title and
channel metadata. The player entity is fixed in `secrets.yaml`; a voice caller
cannot choose another entity or service.

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
