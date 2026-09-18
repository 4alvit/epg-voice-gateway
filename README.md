# EPG Voice Gateway

Find a programme that is airing now and play its channel on a compatible Alexa
screen or Google Cast device. This service consumes an **existing HLS Proxy**
and XMLTV guide. It does not depend on OttPlay or install a media proxy.

An indexed SQLite database stores the last usable schedule on a persistent
volume. Full-text title search and channel/time indexes avoid scanning the full
guide for each request. Downloaded guides are cached with HTTP validators, so a
server that supports conditional requests need not resend an unchanged feed.
The first version uses existing k3s storage and requires no cloud database account.

The initial implementation serves one household. Operator configuration and
credentials belong in Kubernetes Secrets or a private environment file.
Multi-household account linking is not implemented.
Keep the Alexa skill in development/private use. Public distribution requires
account linking and per-household authorization before it can safely serve users.

## Request flow

1. A voice adapter submits a programme title.
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

- `POST /v1/resolve`: `{ "query": "Kukutiki" }`, with
  `Authorization: Bearer <EPG_API_TOKEN>`. Returns current matching programmes.
- `POST /v1/play`: `{ "query": "Kukutiki", "target": "kitchen" }`.
  Creates a session and asks Home Assistant to cast it to a configured target.
  Omit `target` to obtain a playback URL without starting a device.
- `POST /alexa`: verifies the Amazon certificate, request signature, timestamp,
  and exact configured skill ID before processing the voice request.
- `GET /media/{session}/{resource}`: validates both session and resource before
  retrieving a manifest, segment, key, or initialization resource.

Control credentials and playback URLs are bearer credentials. Do not log them
or publish them in issues. Access logs are disabled in the supplied runtime.
The session store has bounded capacity and expiry; restarting the process
invalidates sessions. Run exactly one replica and one worker in this release.

## Alexa installation and use

Create a custom skill with the interaction model and settings in
[`examples/alexa/README.md`](examples/alexa/README.md). Set its HTTPS endpoint to
the control host's `/alexa` path and configure the exact skill ID on the server.
The server refuses unsigned requests and requests for another skill.

Example invocation: **"Alexa, ask home television to play Kukutiki."** The requested
title is searched dynamically in EPG. A device must advertise `VideoApp` support;
rendering APL cards alone does not establish video playback capability. Alexa
requires an Internet-accessible, trusted HTTPS media endpoint. LAN-only HTTPS
and an API smoke test do not prove Alexa playback.

## Google installation and use

Use the Home Assistant package and instructions under
[`examples/home-assistant`](examples/home-assistant). Configure a fixed target
alias, expose the script to Google, and attach a Routine with the desired phrase.
The script submits the programme query and Home Assistant starts Cast playback.

This provides a configured phrase such as **"Hey Google, play Kukutiki."** It
does not register a universal Google command with an arbitrary title slot.
The retired Conversational Actions platform is not used. A Nest Hub or a
compatible Cast screen is required to show video.

## Deployment and verification

- [Deployment](docs/deployment.md)
- [Control and media networking](docs/networking.md)
- [Acceptance checks and physical-device limits](docs/acceptance.md)

```sh
uv run pytest
uv run ruff check .
```

`uv.lock` pins development dependencies. The container installs runtime packages
from the hash-checked `requirements.lock`. To update both deliberately, run
`uv lock` and `uv export --frozen --no-dev --no-emit-project -o requirements.lock`,
then rerun the tests and rebuild the image.

Transport security does not convert codecs or resolution. Validate the actual
channel against the receiving device, including HLS encryption when applicable.
Use a compatible channel variant or separately managed transcoding if needed.
