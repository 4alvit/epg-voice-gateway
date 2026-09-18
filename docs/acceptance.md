# Acceptance and troubleshooting

Validate each layer independently. Unit tests and generated directives cannot
prove that a physical display can fetch, decode, and play a provider's video.
Keep deployment receipts private and redact session URLs, provider references,
skill/user IDs, domains, IP addresses, and tokens from shared evidence.

## Application checks

1. Verify `/healthz` and `/readyz` through a local port forward after deployment.
2. Resolve a currently airing program using an authenticated control request.
   Confirm the title, channel, and active interval against the configured EPG.
3. Confirm unknown titles and programs that ended already do not start an
   unrelated channel. Exercise a configured title alias.
4. Confirm unauthenticated control calls are rejected. Confirm unsigned Alexa
   requests and requests for a different skill are rejected.
5. Confirm `/media/` is unavailable through the Cloudflare control hostname and
   the media hostname does not expose control or administration paths.

Never use provider credentials or raw stream URLs as test fixture data. Use
synthetic channels in public tests. Perform live checks only against the
operator-configured service and preserve only bounded, sanitized outcomes.

## Media transport checks

Create a fresh session through an authenticated play request. With a client on
the same network as the intended device, fetch the returned HTTPS playlist and
one bounded video segment. Inspect the rewritten playlist privately:

- Every fetchable URI stays on the configured HTTPS media origin.
- Nested playlists, keys, initialization segments, and media segments receive
  session-authorized URLs.
- Responses preserve the resource content type and range behavior required by
  the selected stream.
- A tampered, expired, or unknown grant cannot retrieve a resource.
- Provider error pages, credentials, and upstream URLs are not exposed in errors.

Repeat from outside the home network before describing a route as WAN-ready.
Verify the trusted certificate and both advertised IP families. Successful
HTTPS transport does not establish that the device supports the video's codecs,
audio format, resolution, encryption, or live-stream behavior.

Run transport probes before the continuous playback test, not against a second
channel while the display is playing. A proxy or provider may allow only one
simultaneous stream per client, and gateway fetches can share that upstream
client identity. A diagnostic request for another channel can then invalidate
the display's active stream. Inspect the configured limit and proxy lifecycle
errors before attributing the resulting player error to a codec. Do not raise
the limit without confirming the provider's concurrency allowance.

## Alexa acceptance

Follow the [Alexa setup example](../examples/alexa/README.md) to import the
interaction model and configure the private skill.

Enable Video App in the custom skill's interface settings and rebuild its
interaction model. Use a device whose signed incoming request advertises
`VideoApp`. Test the skill's invocation and program slot with a compatible
SD/720p channel first. Verify visible moving video and audible sound, followed
by pause/resume/stop behavior. Do not infer Samsung Family Hub video support
from previously rendered Alexa energy cards.

Record the model, locale, capability result, channel codec/resolution, start
latency, playback duration, and observed result privately. A simulator-generated
`VideoApp.Launch` directive is response-contract proof only. Actual playback on
the target device is the final platform gate.

## Google acceptance

Follow the [Home Assistant example](../examples/home-assistant/README.md) to
configure the authenticated dispatcher, Home TV selector, and optional fixed
shortcuts.

First use Home Assistant to play the gateway-issued media URL on the intended
Cast display. Then test the complete Routine -> exposed Home Assistant script
-> gateway lookup -> Cast playback sequence. Check the EPG again when repeating
the command so a previous session or channel is not mistaken for fresh lookup.

For the dynamic catalogue, verify that Google SYNC advertises exactly the
intended devices and that Home TV's Modes options match the current HA list.
Verify QUERY returns Ready after a selection. A synthetic SetModes request
tests the fulfillment and HA event path; it does not test real Google account
linking or speech recognition. After linking the household account, sync devices
and speak a command naming a currently listed option. Repeat that same choice
to check that it starts a fresh session rather than being ignored.

The fixed Routines have predefined inputs; the dynamic selector advertises a
finite list. Neither route extracts an unrestricted programme-name variable
from speech. Check the actual display mapping and account language.

Observe an isolated stream for at least two minutes after it starts. Require
PLAYING at the end, an advancing timeline, and no terminal ERROR after startup;
an earlier PLAYING sample followed by a failure is not sustained acceptance.
A successful Home Assistant service call or native Cast status alone is not
physical picture/audio proof: verify moving video and sound on the device.

Run this check for every candidate channel on the intended receiver. One
successful SD stream does not validate HD sources. Keep later failures in the
receipt even when the initial observation passed; a two-minute result does not
prove uninterrupted long-term playback.

Ensure the status observer cannot launch, replace, or stop a receiver app.
Some Cast libraries automatically launch the default receiver when a media
status request has no active media namespace. Disable that fallback and guard
status requests against the existing namespace. If an attached observer might
affect playback, repeat using HA state reads and a single non-launching native
status check at the end. Preserve earlier receipts as inconclusive rather than
treating an observer-induced app transition as a production failure.

## Common failure boundaries

- **Not ready:** inspect catalog/EPG refresh success, configured IDs, clock, feed
  reachability, and staleness thresholds. Do not disable freshness checks to
  hide a feed problem.
- **Alexa HTTP 403:** separate edge rejection from application signature or
  skill-ID validation. A successful browser GET does not prove signed Amazon
  POST requests can reach the handler.
- **Media HTTP 401/403/404:** use a newly issued grant and check the media origin,
  path rules, expiration, and whether the pod restarted. Session storage is
  process-local.
- **Playlist loads but video fails:** check every nested resource, trusted TLS,
  device DNS, range requests, encryption, and codecs. Wrapping a manifest in
  HTTPS does not make its remaining HTTP references compatible.
- **Receiver stays idle after fetching a segment:** distinguish successful
  upstream downloads from actual receiver decoding. Normal proxy downloads
  followed by no further receiver requests do not establish the exact codec
  cause. Validate another source or an explicitly configured transcoding path;
  this gateway does not transcode.
- **Playing stream exhausts upstream retries:** correlate segment timeout and
  stream-stop timestamps before blaming EPG selection or account linking.
  A later successful TCP connection does not prove media requests recovered.
- **Stream stops after a rollout or expiry:** request playback again. The current
  implementation deliberately does not persist or indefinitely renew sessions.
- **Stream stops when another channel is probed:** check proxy/provider limits
  per client and stream eviction logs. Repeat with one isolated stream and no
  parallel channel probes before changing media or decoder configuration.
- **No matching program:** verify the provider's XMLTV channel IDs and times.
  Add explicit title aliases for recognizer spellings; do not silently choose an
  unrelated channel.

## Evidence levels

A useful release receipt distinguishes source/test success, installed image,
readiness and live EPG lookup, signed media transport, assistant response
contract, and physical device playback. List unperformed checks explicitly.
Neither a Ready pod nor an HTTPS playlist alone justifies claiming that the
Alexa or Google experience is complete.
