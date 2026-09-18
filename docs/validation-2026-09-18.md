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
Phone UI visibility, microphone recognition and playback initiated by Google
remain separate checks.

A transient error on HA's browser login page was also reported during these
attempts. Its cause was not established; successful linking does not identify
or prove a fix for that earlier browser error.

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
UI visibility, automatic catalogue resynchronization after an EPG change,
microphone recognition and playback initiated by Google remain separate open
checks. Human picture/audio confirmation for this version,
repeated same-choice device playback, HD compatibility and longer source
stability also remain open. Alexa physical playback was not established by
these Google/Cast checks. No public integration release or PR merge occurred.

Raw diagnostics and deployment-specific helpers remain private. Earlier native
observer checks could implicitly launch a Cast receiver and are inconclusive;
the final checks used HA observation and one guarded non-launching native status
snapshot at the end. Final channel tests were serialized to respect proxy limits.
