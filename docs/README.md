# Documentation

A gateway that turns TV channel and programme requests from voice assistants into
playable streams, plus the deployment and validation records behind it.

## Set up

- [Setup checklist](setup-checklist.md) — the sequence for a single-household installation.
- [Select TV channels with Google Home](google-channels.md) — the optional Home Assistant integration.
- [Dynamic channel and programme search](media-search.md) — how names and currently airing programmes are matched.
- [Google Home returns an error after HA login](google-linking-troubleshooting.md) — why a browser login is not proof.

## Deploy and operate

- [Deploy the gateway on Kubernetes](deployment.md) — the existing HLS proxy and EPG source it consumes.
- [Control and media networking](networking.md) — why voice commands and video take different routes.
- [Private HTTPS API profile](tls-policy.md) — the direct API path without Home Assistant.
- [Qualified Worker deployment profile](WORKER_SECURE_PROFILE.md) — what a new independent deployment must configure.
- [Acceptance and troubleshooting](acceptance.md) — validating each layer on its own.

## Evidence

- [Quality evidence and limits](QUALITY.md) — what the Checks workflow runs.
- [RC fuzz testing](fuzzing.md) — the fuzz candidate workflow.
- [Validation record: 2026-09-18](validation-2026-09-18.md) — one sanitized historical installation.
