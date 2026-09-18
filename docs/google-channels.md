# Select TV channels with Google Home

The optional `epg_voice_channels` Home Assistant integration adds **Home
Channels**, a TV device with Google's Channel trait. It advertises a small,
explicit list of channel names. Selecting a name creates a fresh gateway
playback session and starts the configured Cast receiver through HA.

Existing Google devices and intents continue through HA's stock Google
Assistant integration. The existing HLS proxy supplies the video; this
component does not install another proxy, transcode streams, or publish
provider URLs. Home TV's programme selector and fixed voice shortcuts remain
separate features.

## Requirements

- A working gateway, fresh EPG, and existing HLS proxy. Verify at least one
  channel on the intended display before publishing a lineup.
- HA's manual Google Assistant integration and a working Google account link.
  For initial setup, follow the
  [HA installation and linking guide](../examples/home-assistant/README.md).
- A dedicated, active **non-admin, non-owner HA user** for that Google link,
  without the local-only restriction and with HA `CONTROL` permission on one
  fixed `media_player` entity. The wrapper checks this permission again when
  executing a command. It does not create narrower OAuth scopes or change the
  user's ordinary HA permissions.
- A trusted HTTPS media origin reachable by the receiver. Keep video on the
  existing direct media route described in [networking](networking.md).

## Configure the channel lineup

Use gateway channel IDs from your own authenticated catalogue or resolution
results. These are opaque gateway IDs, not provider IDs, playlist positions,
or channel numbers. Confirm the associated stream's codecs and resolution on
the chosen receiver.

Add the settings to the gateway's existing private environment or Secret:

```dotenv
EPG_CHANNEL_VOICE_IDS=["0123456789abcdef01234567"]
EPG_CHANNEL_VOICE_LIMIT=30
```

The ID above is synthetic; replace it with a verified ID. The default allowlist
is `[]`, which exposes **no channels**. It does not inherit
`EPG_VOICE_CHANNEL_IDS`, the separate programme-selection allowlist. The limit
defaults to 30 and accepts 1–200; begin with a few verified channels. Google
recommends a small advertised lineup for faster name resolution.
[Google Channel trait](https://developers.home.google.com/cloud-to-cloud/traits/channel).

After applying the gateway configuration through its existing deployment
method, inspect authenticated `GET /v1/channels` over the private control
route. It returns `channels` containing `channel_id`, `name`, `synonyms`, and
`available`, plus a deterministic `revision`. No stream URL is included.
Keep this endpoint private; the supplied public control allowlists do not
need another route.

Channel identities and names stay stable across programme changes. A missing
or ambiguous current EPG entry makes a channel unavailable; a stale catalogue
fails the request. Duplicate names and conflicting synonyms are excluded to
avoid selecting the wrong channel. `available: true` confirms current EPG
mapping, not receiver compatibility or upstream video health.

## Install the HA component

Back up the HA configuration and any existing copy of this integration to a
protected location outside `custom_components`. Include the existing Google
configuration and route settings in the rollback record. Preserve credentials
privately; do not copy `.storage`, `secrets.yaml`, tokens, or downloaded
diagnostics into this repository.

For a first installation, copy **only this component's directory**. From the
repository root, replacing the config path for your installation:

```sh
set -eu
HA_CONFIG_DIR=/path/to/home-assistant/config
test -d "$HA_CONFIG_DIR"
test ! -e "$HA_CONFIG_DIR/custom_components/epg_voice_channels"
mkdir -p "$HA_CONFIG_DIR/custom_components"
cp -R integrations/home_assistant/custom_components/epg_voice_channels \
  "$HA_CONFIG_DIR/custom_components/"
```

The existence check deliberately stops an accidental overwrite. For an
upgrade, back up the old `epg_voice_channels` directory and replace that
directory alone during a maintenance window. Never replace or remove the
whole `custom_components` directory.

Run the configuration check appropriate to your HA installation before
restarting. Home Assistant OS provides `ha core check`; in a Container
installation, run `python -m homeassistant --script check_config -c /config`
inside the HA container. See HA's
[configuration troubleshooting guide](https://www.home-assistant.io/docs/configuration/troubleshooting/).
Initial discovery of a new custom component may require an HA restart; a
successful configuration check does not prove it has loaded.

In **Settings > Devices & services > Add integration**, select **EPG Voice
Channels**. Only one adapter entry is supported. All six fields are required;
there are no household defaults:

- `gateway_origin`: the internal HTTP or HTTPS gateway origin, without an API
  path, query, or credentials; for example `http://gateway.example.internal:8080`.
- `gateway_token`: the existing gateway API token. This is not the linked
  user's HA password or an HA long-lived access token.
- `signed_media_origin`: the exact HTTPS origin from `EPG_MEDIA_BASE_URL`,
  for example `https://media.example.invalid`. Only grants from this origin
  are accepted.
- `google_entry_id`: select the existing, loaded Google Assistant integration
  from the dropdown. Its internal identifier is handled by the component.
- `agent_user_id`: select the dedicated non-admin HA login already used by the
  Google account link. Choose the account by its displayed name.
- `target_entity`: the one verified Cast entity, for example
  `media_player.example_display`.

Store the real values through HA's configuration flow and protect its config
storage and backups. Do not hand-edit HA's `.storage` files. A different linked
user does not receive the added Home Channels device. Existing stock devices
retain their existing HA authorization rules.

The integration and account dropdowns avoid collecting internal IDs. Setup
checks the user's permissions, the loaded Google integration, the
receiver's availability, and the gateway channel catalogue. Keep the gateway
fresh and the receiver available while configuring. To change settings later,
use the entry's **Reconfigure** flow and supply all six values; saved secrets
are not displayed. Reconfiguration reloads the adapter entry.

## Route Google fulfillment

The authenticated HA wrapper is:

```text
POST /api/epg_voice/google_assistant
```

It uses the original Google-to-HA bearer token. It does not replace the OAuth
browser login, token endpoint, client ID, or existing account link.

With the optional [Workers VPC relay](../deploy/google-home-worker-vpc/README.md),
enable its trusted deployment setting `CHANNELS_ENABLED="true"` (Terraform:
`channels_enabled = true`). The public fulfillment path remains
`/api/google_assistant`; the Worker maps that one path to the private wrapper.
The default `false` preserves stock HA fulfillment. Callers cannot select
the destination with a query parameter or request header. The Worker does
not receive a gateway API token or gain a media route.

With an already working direct HTTPS route, set the Google Console's cloud
fulfillment URL to `https://ha.example.invalid/api/epg_voice/google_assistant`.
If this uses the ordinary Cloudflare Tunnel fallback, first follow the
explicitly accepted [Plan B procedure](google-linking-troubleshooting.md#plan-b-ordinary-cloudflare-tunnel-with-bot-fight-mode-disabled).
Do not disable Bot Fight Mode as part of installing this component. Preserve
all existing OAuth URLs and credentials when only fulfillment routing changes.

Changing this path alone does not require unlinking and relinking the Google
account. Request a device sync for the **linked dedicated user** after the
component and route are ready:

```yaml
action: google_assistant.request_sync
data:
  agent_user_id: YOUR_LINKED_HA_USER_ID
```

Use the existing Google project and HomeGraph credentials from the linking
setup. RequestSync requires a working setup; the HA service call returning
success does not expose Google's response. Verify a subsequent fulfillment
SYNC and Google's stored inventory. The component polls every five minutes
and requests sync when advertised IDs or names change, only for the already
registered dedicated Google user. Failed syncs are retried on later polls;
this does not establish a missing account link. Request a manual sync when
verifying a lineup change immediately. Programme changes alone do not rename
the channels or require sync. During a temporary catalogue failure, SYNC may
retain previously fetched names; playback still requires a fresh catalogue.

## Use and verify

After sync, check that Google sees **Home Channels** as
`action.devices.types.TV` with `action.devices.traits.Channel`, with the
expected finite `availableChannels` list. The device is command-only and does
not report which channel the physical screen is playing.

In the existing Google Console integration, add **TV** to its device types,
preserving Scene, Sensor, or other types already in use. Keep the existing
account-linking fields and test account. Verify the saved selection and
HomeGraph inventory after RequestSync.

For a configured channel whose published name is `Example News`, try:

- “Hey Google, change the channel to Example News on Home Channels.”
- “Hey Google, switch to Example News on Home Channels.”

These are **candidate phrases until verified with the real microphone,
Google account, language, and receiver**. This implementation accepts named
`selectChannel` requests mapped by Google to an advertised channel key. It
does not invent channel numbers or implement next, previous, or return-channel
commands. It does not search arbitrary spoken programme titles.

For each accepted selection, the component fetches the current channel list,
checks the selected ID and current availability, and requests a fresh grant
from `/v1/play` using that channel ID. HA receives only the signed media URL
and HLS content type; the receiver infers the stream type. There is no reused
playback URL in a voice routine or channel name.

Verify separately that the command reached the wrapper, a fresh session was
created, HA accepted playback on the fixed entity, and the actual screen and
sound continue beyond startup. Google's spoken acknowledgment or EXECUTE
success means HA's playback service completed, not that playback continued.
A dispatch timeout can occur after the receiver accepted the command, so
check the screen before repeating it. If Google still says it is
“activating” an old news shortcut, distinguish that stock script from a
Channel request to Home Channels.

Overlapping channel changes return `deviceBusy` instead of allowing an older
request to overtake a newer one. Completed request IDs are deduplicated for
two minutes, up to 128 entries, so a transport retry does not restart playback.
Repeating the spoken phrase normally creates a new request and can start a new
session. Unlinking the registered Google agent prevents further custom
playback even if an HA access token has not yet expired.

Start with an SD or 720p H.264/AAC stream already tested on the display. HLS,
codec profile, resolution, audio, TLS, and network support depend on the
receiver; this project cannot make a 4K stream compatible by changing its
name. [Cast media support](https://developers.google.com/cast/docs/media).

## Failures and rollback

A missing Home Channels device can mean an empty lineup, the wrong linked
user or Google entry, missing control permission, an unloaded component, or
a route still targeting stock HA. `noAvailableChannel` can mean an unknown
channel, an EPG gap, ambiguous current programming, or conflicting selectors.
`deviceOffline` can indicate unavailable gateway data, user authorization,
Google integration, or receiver; `channelSwitchFailed` indicates dispatch
failure or timeout. Unsupported relative/return commands are rejected.
Diagnose the source and request stage before changing account credentials.
Keep bearer tokens, grants, raw source URLs, and request bodies out of
published logs.

To roll back:

1. Restore the prior fulfillment route: set `CHANNELS_ENABLED="false"` through
   the Worker's existing deployment owner, or restore the previous direct
   fulfillment URL. Preserve OAuth, the account link, and unrelated routes.
2. RequestSync for the same dedicated user and verify the custom device is
   removed while stock HA devices still work.
3. Disable or remove only the EPG Voice Channels integration entry. Restore
   the backed-up component if reverting an upgrade, or remove only its own
   directory if uninstalling. Validate configuration and restart HA when
   required to complete removal.
4. Restore the previous gateway channel allowlist if desired; `[]` disables
   this lineup. Preserve the programme selector, fixed shortcuts, gateway
   credentials, media ingress, and existing HLS proxy.

Keep the protected backups until stock fulfillment and any retained playback
route are verified. No Cloudflare billing, zone-security, or HLS-proxy change
is required merely to enable or remove this component.
