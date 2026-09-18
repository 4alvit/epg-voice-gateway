# Validation record: 2026-09-18

This sanitized historical record describes a single installation. It is not a
claim that all channels, displays, or assistant accounts work. No deployment
addresses, account IDs, credentials, provider URLs, or signed grants are included.

## Installed and checked

- Gateway runtime `6dda2a7e10301690257fa38bb6a5759f639a33df` was installed using a
  verified image and module hashes. Its persistent database and existing proxy
  were preserved. Later gateway changes through `bcd8de6` affected examples,
  tests and documentation.
- The HA package refreshes the finite Home TV catalogue, revalidates exact
  title/channel selections, requests fresh grants, and resets to Ready. The
  final minimal dispatcher update used one script reload without restarting HA
  or interrupting the active baseline stream. Initial installation had required
  a separate validated HA restart.
- Google draft setup, HomeGraph enablement and protected service-account key
  installation were completed without billing activation. No project IAM roles
  were granted to that service account. During initial preparation, its OAuth
  exchange succeeded and Request Sync returned 404 because the household had
  not yet linked. This was the initial state, before the linking result below.
- Gateway/example tests: 324 passed, one optional test skipped; Ruff passed.
  HA validation: 31 YAML files and 22 tests passed. CI passed for the recorded
  gateway source and HA revision `ddcc100`. The HA change remained in an open PR.

## Google account linking

Real household OAuth linking completed later on the same day. The user reported
that Google Home displayed the integration name and **Linked**. A read-only HA
check independently confirmed issued Google refresh tokens for the dedicated
non-admin user and that user's registration in the Google integration's agent
store. HA registers that user when handling the initial SYNC request; this is
evidence of an actual Google linking flow reaching HA, rather than the earlier
synthetic checks.

The accepted flow used the dedicated Workers VPC relay for Google's token and
fulfillment requests. Browser authorization remained on the existing HA origin
and HA continued to validate the real OAuth credentials.

A read-only HomeGraph `devices:sync` request using the existing project service
account returned HTTP 200, matched the dedicated agent and confirmed three
devices in Google's stored inventory:

- **Play News Channel**: SCENE with the Scene trait.
- **Play Kukutiki**: SCENE with the Scene trait.
- **Home TV**: SENSOR with the Modes trait.

HA's `google_assistant.request_sync` service was also invoked once and its HA
HTTP request returned 200. That service did not expose the underlying Google
response status, so its acknowledgement is not recorded as a separate Google
HTTP 200. The independent inventory read confirms the devices stored in
HomeGraph; it does not prove a later EPG change automatically resynchronizes.
Catalogue recovery was checked separately below. Phone UI visibility, dynamic
catalogue speech and successful playback initiated by Google remain separate
checks. A subsequent fixed-shortcut voice attempt is recorded below.

A transient error on HA's browser login page was also reported during these
attempts. Its cause was not established; successful linking does not identify
or prove a fix for that earlier browser error.

## Fixed news shortcut: real voice attempt

The user requested the news shortcut by voice and reported Google's
**"Activating News Channel"** acknowledgement. HA recorded the news wrapper and
playback dispatcher starting for that attempt. The dispatcher then encountered
a REST connection failure approximately 10 milliseconds later; no Cast request
was issued and the display did not begin playback.

This establishes recognition and delivery of one real fixed-shortcut Google
EXECUTE request through to the HA scripts. It does not establish dynamic
programme-title recognition or successful video playback. HA runs script-based
Scenes asynchronously, so Google's acknowledgement can precede a later
dispatcher failure.

The gateway process was healthy, but its catalogue had exceeded the configured
twelve-hour freshness window. The installed `/readyz` readiness probe returned
HTTP 503, removing the pod from its Kubernetes Service endpoints. This explained
why HA received a connection failure instead of the gateway's structured stale
catalogue error.

Source diagnostics identified a persistent HTTP redirect from the configured
XMLTV address. A successful DNS lookup and TCP connection still returned HTTP
301; the gateway deliberately accepts only 200 or a validated cached 304 and
does not follow redirects. The final canonical HTTPS source was verified
directly, including TLS, a bounded download and parsing with the deployed
implementation. It mapped current programmes for all configured voice channels.
No credentials were forwarded through the redirect chain. The source address
remains in private configuration.

A separate pod-network path to one CoreDNS endpoint timed out, while the other
endpoint worked. Host routes, neighbors and VXLAN forwarding entries were
consistent; the precise cause of that path failure remains unresolved. No
cluster firewall, routing or DNS settings were changed to mask it.

The readiness template fix in `ce889a0` passed CI. It uses `/healthz` for Service
readiness while retaining `/readyz` and all control API freshness checks. The
private source URL and readiness probe were updated during an idle window.
The replacement pod then fetched and committed a fresh catalogue. Its runtime
source matched the validated destination, both health endpoints returned 200,
and the Service had a Ready endpoint. Verification preserved the database and
its integrity, the existing HLS proxy and all other runtime secret values.
The protected provisioning files were updated to match the installed change.

The HA catalogue controller recovered its choices and requested synchronization.
A subsequent read-only HomeGraph inventory returned HTTP 200 and confirmed the
three expected devices, with all six Home TV settings matching HA exactly.
This verifies that particular recovery synchronization, rather than merely
acceptance of the HA service call.

A manual invocation of the installed news wrapper then returned HA HTTP 200,
created a fresh media grant and sustained playback through a 150-second check.
The final non-launching receiver snapshot was PLAYING with 147.578 seconds of
timeline advancement; HA did not lose the session after playback started. The
user separately confirmed moving video and sound on the physical display.
This was a manual HA recovery check, not a second Google voice request.

## Playback results

A minimal SD baseline passed a 150-second receiver check and remained playing
roughly fifteen minutes later. The full dynamic SD path passed another isolated
150-second check, including exact selection, fresh grant, minimal dispatch,
Ready reset and final PLAYING state with 150.473 seconds of timeline advancement.
It later stopped after upstream segment timeouts exhausted proxy retries.
That later failure remains part of the result.

Two HD candidates received correct selections and fresh grants but never reached
PLAYING during their isolated checks. Upstream proxy downloads continued without
an initiating timeout. The exact receiver/format cause was not established.
The gateway does not transcode; these channels are not verified playable.

A separate 125-second SD software-decode check passed. It does not establish
receiver hardware decoding or human picture/audio acceptance. An EPG-origin DNS
timeout was also observed; freshness enforcement remained enabled.

## Remaining acceptance

After accepted linking and the verified HomeGraph inventory, Google Home phone
UI visibility, continued scheduled resynchronization as programmes change,
dynamic catalogue speech recognition and successful playback initiated by
Google remain separate open checks. The fixed news phrase reached HA once, but
failed before Cast dispatch; the subsequent manual recovery check passed with
human picture/audio confirmation. Repeated same-choice device playback, HD
compatibility and longer source stability remain open. Alexa physical playback
was not established by these Google/Cast checks. No public integration release
or PR merge occurred.

Raw diagnostics and deployment-specific helpers remain private. Earlier native
observer checks could implicitly launch a Cast receiver and are inconclusive;
the final checks used HA observation and one guarded non-launching native status
snapshot at the end. Final channel tests were serialized to respect proxy limits.
