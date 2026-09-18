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
  were granted to that service account. Its OAuth exchange succeeded; Request
  Sync returned 404 for a household account that had not yet linked.
- Gateway/example tests: 324 passed, one optional test skipped; Ruff passed.
  HA validation: 31 YAML files and 22 tests passed. CI passed for the recorded
  gateway source and HA revision `ddcc100`. The HA change remained in an open PR.

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

At the end of this run, actual Google mobile linking, successful device sync
after linking, microphone recognition, human picture/audio confirmation for
this version, repeated same-choice device playback, HD compatibility and longer
source stability remained open. Alexa physical playback was not established by
these Google/Cast checks. No public integration release or PR merge occurred.

Raw diagnostics and deployment-specific helpers remain private. Earlier native
observer checks could implicitly launch a Cast receiver and are inconclusive;
the final checks used HA observation and one guarded non-launching native status
snapshot at the end. Final channel tests were serialized to respect proxy limits.
