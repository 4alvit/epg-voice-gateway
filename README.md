# EPG Voice Gateway

[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/15583/badge)](https://www.bestpractices.dev/projects/15583)

Find a channel or a programme that is airing now and play it on a compatible Alexa
screen or Google Cast device. This service consumes an **existing HLS Proxy**
and XMLTV guide. It does not depend on OttPlay or install a media proxy.

An indexed SQLite database stores the last usable schedule on a persistent
volume. Full-text title search and channel/time indexes avoid scanning the full
guide for each request. Downloaded guides are cached with HTTP validators, so a
server that supports conditional requests need not resend an unchanged feed.
The first version uses existing k3s storage and requires no cloud database account.

SQLite provides the database engine; this project owns the XMLTV import and
search schema. Keeping the catalogue beside the gateway lets voice queries use
the last fresh snapshot without contacting an external database. A managed
database, including an Oracle Cloud deployment, is an optional future migration
if multiple gateway instances need to share writes or serve separate households.
It is not required for the current single-instance deployment.

The initial implementation serves one household. Operator configuration and
credentials belong in Kubernetes Secrets or a private environment file.
Multi-household account linking is not implemented.
Keep the Alexa skill in development/private use. Public distribution requires
account linking and per-household authorization before it can safely serve users.

## Request flow

1. A voice adapter submits a channel name or programme title.
2. The service searches fresh XMLTV data for `start <= now < stop` and matches
   the programme's channel to the HLS proxy's M3U catalogue.
3. A unique match produces an expiring playback session. Ambiguous matches need
   a more specific query; missing programmes are reported without selecting an
   unrelated channel.
4. The device fetches every manifest and media resource through that session.
   The existing HLS Proxy continues to fetch and cache the provider's streams.

The small control requests can use Cloudflare. Video uses a separate direct
HTTPS route. Do not put `/media/` through a Cloudflare Free public tunnel.

## Run locally

Python 3.12 or newer is required. Configuration keys are illustrated in
[`deploy/runtime.env.example`](deploy/runtime.env.example). Supply real secrets
outside Git, then run:

```sh
uv sync --frozen --extra test
uv run uvicorn epg_voice_gateway.app:app --host 127.0.0.1 --port 8080 --no-access-log
```

The catalogue loads in the background. `/healthz` checks the process, while
`/readyz` returns success only after a usable catalogue and guide have loaded.
Failed refreshes retain the previous snapshot only until its configured stale
deadline. XMLTV must contain programme start and stop times and channel IDs or
unambiguous channel display names that match the configured playlist.
Channel IDs are scoped to each XMLTV source. When multiple sources reuse an ID,
channel names disambiguate it; the first matching configured source takes precedence.
An ambiguous display name within that source is skipped. Updates replace the
catalogue in one database transaction. A fresh saved catalogue is immediately
usable after restart; interrupted downloads and updates preserve the last good
database. Playback sessions remain process-local and expire on restart.

Set `EPG_DATABASE_PATH` to an absolute persistent path, such as
`/data/catalog.sqlite3`. Without it, local development uses an in-memory SQLite
database. Gzip XMLTV is parsed incrementally, retaining only relevant channels
and a bounded time window. Compressed transfers are capped at 64 MiB; the decoded
XML budget is `EPG_MAX_SOURCE_BYTES` (64 MiB by default, up to 512 MiB). For large
daily guides, use an appropriate refresh interval, for example 21600 seconds,
and a stale deadline such as 43200 seconds instead of downloading every few minutes.

`EPG_TITLE_ALIASES` maps spoken titles to alternative guide spellings, for example:

```json
{"kukutiki": ["Кукутики"], "tom and jerry": ["Том и Джерри"]}
```

Aliases are data, not a language model. The service matches whole normalized
words and honours XMLTV timezone offsets and playlist `tvg-shift` values.

## HTTP interfaces

- `POST /v1/search`: authenticated full-catalogue channel/programme search, with
  aliases, transliteration, and conservative typo handling; it creates no session.
- `POST /v1/play-text`: authenticated free-text selection and fresh signed playback.
  See [matching rules, API errors, and configuration](docs/media-search.md).
- `POST /v1/resolve`: `{ "query": "Kukutiki" }`, with
  `Authorization: Bearer <EPG_API_TOKEN>`. Returns current matching programmes.
- `GET /v1/catalogue`: uses the same bearer authorization and returns
  `{ "options": [{ "label": "Columbo", "query": "Columbo", "channel_id": "<channel ID>" }], "revision": "<SHA-256>" }`.
  Options describe currently airing titles; no playback URLs are included.
  The revision changes only when the ordered options change.
- `GET /v1/channels`: uses the same bearer authorization and returns
  `{ "channels": [{ "channel_id": "<channel ID>", "name": "News Channel", "synonyms": [], "available": true }], "revision": "<SHA-256>" }`.
  It publishes the explicit or opt-in automatic channel voice lineup, with current EPG
  availability. It includes no stream URLs, programme titles or provider IDs.
  Keep this endpoint on the internal HA-to-gateway route.
- `POST /v1/play`: `{ "query": "Kukutiki", "target": "kitchen" }`.
  Creates a session and asks Home Assistant to cast it to a configured target.
  Omit `target` to obtain a playback URL without starting a device.
  Supply `{ "channel_id": "<verified catalogue channel ID>" }` to play the
  current programme on a saved channel. This still requires a fresh guide and
  exactly one current programme. With both `query` and `channel_id`, the channel
  filters the title search; it does not bypass a missing title match.
  For an option from `/v1/catalogue`, send its `query` and `channel_id` with
  `"exact_title": true`. This requires the complete normalized title to still
  be airing on that channel, without expanding aliases or matching a later
  programme that merely contains the old title. A missing title returns 404;
  ambiguity returns 409; an unavailable or stale guide returns 503.
- `POST /alexa`: verifies the Amazon certificate, request signature, timestamp,
  and exact configured skill ID before processing the voice request.
- `GET /media/{session}/{resource}`: validates both session and resource before
  retrieving a manifest, segment, key, or initialization resource.

Control credentials and playback URLs are bearer credentials. Do not log them
or publish them in issues. Access logs are disabled in the supplied runtime.
The session store has bounded capacity and expiry; restarting the process
invalidates sessions. Run exactly one replica and one worker in this release.

The finite voice catalogue defaults to 30 options; `EPG_VOICE_CATALOGUE_LIMIT`
accepts 1–50. `EPG_VOICE_CHANNEL_IDS` is an ordered allowlist of up to 200
verified channel IDs; an empty list permits every mapped channel.
`EPG_VOICE_PREFERRED_QUERIES` prioritizes up to 50 spoken labels, using
`EPG_TITLE_ALIASES` to find their currently airing EPG titles. A preference
does not create an option when its programme is off air.

Named channel selection has a separate explicit allowlist,
`EPG_CHANNEL_VOICE_IDS`. Its default empty list publishes no channels.
`EPG_CHANNEL_VOICE_LIMIT` defaults to 30 and accepts 1–200. Entries keep their
configured order and remain in the lineup during guide gaps with
`available: false`; they cannot start playback without one current programme.
Channel names and synonyms are bounded and ambiguous names are excluded.
The revision is independent of programme titles. The list contains no invented
channel numbers. See [Google channel selection](docs/google-channels.md).

Catalogue reads use the SQLite current-time index and consider at most 2,000
current rows, ordered by configured channel priority, then channel name and ID.
Preferences rank only this bounded set. Overlapping schedules are excluded.
Duplicate normalized titles use the first channel in that order, and each
option retains that channel ID for playback. Labels are unique, at most 100
characters, and exclude control characters and the reserved values `Ready`
and `Unavailable`. Full title queries are at most 200 characters; titles are
never truncated into broader searches. Long titles need a shorter preferred
label to appear. A fresh guide with no eligible programmes returns empty
options; stale or unavailable data returns 503 instead.

## Alexa installation and use

Create a custom skill with the interaction model and settings in
[`examples/alexa/README.md`](examples/alexa/README.md). Set its HTTPS endpoint to
the control host's `/alexa` path and configure the exact skill ID on the server.
The server refuses unsigned requests and requests for another skill.

Verified Amazon certificate chains are cached for up to five minutes, with at
most eight entries. Concurrent requests share a certificate download, while every
request still checks trust, certificate dates, the timestamp, and its signature.
The gateway rejects requests when verification exceeds its deadline and logs only
the fixed failure stage. Reliable DNS from the gateway pod is still required for
cold downloads and refreshes; a certificate cache does not repair cluster networking.

For Cloudflare deployments, use the dedicated
[Alexa HTTPS relay and parameterized Terraform](deploy/alexa-worker-vpc/README.md).
Plan A publishes only `POST /alexa` through Workers VPC to the private gateway.
Plan B uses an ordinary tunnel after an explicit decision to disable the zone's
Bot Fight Mode. The relay keeps media and authenticated catalogue APIs private;
it does not replace the separate HTTPS media route.

Example invocation: **"Alexa, ask home television to play Kukutiki."** The requested
channel or current programme is searched dynamically in the shared catalogue.
The adapter prefers `VideoApp`. An experimental APL Video fallback is available
when a device explicitly advertises APL 1.8 or newer and H.264 video capability.
Rendering APL cards alone is insufficient, and these capabilities do not prove
that a particular third-party display can play live HLS. Verify the actual
screen and its playback controls. Alexa requires an Internet-accessible, trusted
HTTPS media endpoint. LAN-only HTTPS and an API smoke test do not prove playback.

For names that an English recognizer spells differently, configure exact
channel aliases such as **REN TV**, **Wren TV**, and **Ren Television** against
the intended catalogue channel. An optional distinct alias such as
**Rainbow Television** can be easier to pronounce. Check for name collisions
and pin the intended regional/quality variant before publishing it to Google.
Aliases affect matching after recognition; test the actual spoken phrase too.

## Google installation and use

Use the Home Assistant package and instructions under
[`examples/home-assistant`](examples/home-assistant). Configure a fixed player
entity and gateway credential in HA, then test the supplied scripts locally.
The private dispatcher obtains a fresh playback session and HA starts Cast
playback. This route does not require an HA token in the gateway.

Connect HA through its Google Assistant integration and expose **Home TV** plus
the fixed **Play Kukutiki** and **Play News Channel** wrappers. Home TV refreshes
its finite programme list from the current EPG every five minutes. One handler
plays any offered title; no per-programme script is required. Configure a tested
channel allowlist and preferred title aliases on the gateway. Selection fetches
a fresh catalogue and rechecks the exact title and channel before playback.

When Columbo is listed, a candidate command is **"Hey Google, set Home TV option
to Columbo."** Verify the wording and recognition on your device. Google uses
the Modes trait and only knows the published choices; this is not an arbitrary
spoken-text slot. Configure HomeGraph automatic Request Sync or ask Google to
sync devices after option changes. The detailed setup and its acceptance limits
are in the linked example instructions.

The manual integration needs no Home Assistant Cloud subscription. The fixed
scene command **"Hey Google, activate Play News Channel"** remains available;
a Google Home Routine can give it a shorter phrase. Cast support alone does not
establish voice control. The retired Conversational Actions platform is not
used. A Nest Hub or compatible Cast screen is required to show video.

For channel names, the optional [Home Channels integration](docs/google-channels.md)
adds a separate virtual TV using Google's Channel trait. Its authenticated HA
fulfillment wrapper preserves the existing scenes and Home TV, advertises the
configured channel lineup only to the dedicated account, and revalidates the
selected channel before requesting a fresh signed stream. It requires installing
the supplied HA custom integration and selecting the extended fulfillment route;
the base package alone does not enable named channel commands.

## Free-text Assist and automatic channel names

The [dynamic media resolver](docs/media-search.md) searches all imported channels
and current programmes independently of Google's finite published list.
`EPG_CHANNEL_VOICE_MODE=automatic` builds that list with safe names and aliases;
the default remains `explicit`. No language model or cloud database is required.

For unrestricted query text, install the [HA Assist sentences and intent](examples/home-assistant/assist-README.md)
and use typed Assist, the HA phone app, or an Assist satellite. Alexa uses the
same resolver through its SearchQuery slot. The Google display remains a Cast
receiver; its microphone does not automatically become an Assist microphone.

## Deployment and verification

- [Ordered setup checklist, from deployment to voice playback](docs/setup-checklist.md)
- [Deployment](docs/deployment.md)
- [Control and media networking](docs/networking.md)
- [Acceptance checks and physical-device limits](docs/acceptance.md)
- [Google linking failures and the optional private relay](docs/google-linking-troubleshooting.md)
- [Named Google channel selection and installation](docs/google-channels.md)
- [Dynamic media search and automatic channel publication](docs/media-search.md)
- [Free-text Assist installation and use](examples/home-assistant/assist-README.md)
- [Recorded validation and remaining gates](docs/validation-2026-09-18.md)

```sh
uv run --extra test pytest
uv run --extra test ruff check .
```

`uv.lock` pins development dependencies. The container installs runtime packages
from the hash-checked `requirements.lock`. To update both deliberately, run
`uv lock` and `uv export --frozen --no-dev --no-emit-project -o requirements.lock`,
then rerun the tests and rebuild the image.

Transport security does not convert codecs or resolution. Validate the actual
channel against the receiving device, including HLS encryption when applicable.
Use a compatible channel variant or separately managed transcoding if needed.

## Project maintenance and security

See [Contributing](CONTRIBUTING.md), [Security](SECURITY.md), [Code of Conduct](CODE_OF_CONDUCT.md), [Governance](GOVERNANCE.md), [Roadmap](ROADMAP.md), [Release acceptance](RELEASING.md), [Changelog](CHANGELOG.md), [quality evidence](docs/QUALITY.md) and the [complete private HTTPS API](docs/tls-policy.md). OpenSSF answers describe verified capabilities and explicitly retain unmet Silver requirements, including backup access continuity.
