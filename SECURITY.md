# Security policy

This is an actively maintained, single-household development service. The current main branch is supported. There is no stable public release or multi-household security guarantee. Update by reviewing changes, backing up private configuration/database, rebuilding from the reviewed commit and testing a private deployment before replacing the running service. The latest source is https://github.com/4alvit/epg-voice-gateway.

Report suspected vulnerabilities privately using https://github.com/4alvit/epg-voice-gateway/security/advisories/new or email alvit.work@gmail.com. Include affected commit/version, a minimal synthetic reproduction, impact and a safe way to contact you. Never include real credentials or live media. Private vulnerability reporting was enabled and verified on 2026-10-16.

The maintainer aims to acknowledge within 7 days, investigate severity and affected versions, and agree a fix/disclosure timeline with the reporter. Critical issues are prioritized immediately; other significant known vulnerabilities are tracked and fixed promptly, normally within 60 days. Involve affected upstream maintainers privately where needed. Test the fix, review it, publish release notes with applicable CVE/GHSA identifiers and affected/fixed versions, and credit reporters unless they request anonymity. If no acknowledgement arrives, follow up using the other private channel. These are process targets, not a staffed service-level guarantee.

Keep API tokens and HMAC keys independent, randomly generated and at least 32 bytes; use a secret manager or mode-0600 private environment file. Do not log bearer session URLs. Limit service exposure and privileges. The direct HTTPS profile is documented in docs/tls-policy.md. Existing LAN HTTP, HA, Alexa and Worker deployments have distinct transport and identity boundaries; do not infer public-service readiness from the private profile.

Renovate updates Python and Actions dependencies; locked updates require review, tests and an advisory check. CI runs Ruff security rules over first-party Python and CodeQL over Python, JavaScript and workflows. Tests include malicious input, authorization and actual TLS boundaries. Native dependency internals are not claimed memory-safe solely because the application is Python.
The runtime image explicitly upgrades zlib to Debian's
`1:1.3.dfsg+really1.3.1-1+deb13u1` repair for
[DSA-6613-1](https://security-tracker.debian.org/tracker/DSA-6613-1), including
CVE-2026-76844 and CVE-2026-85091. The complete image inventory must contain that
exact version, release and epoch. Existing XZ protections and all language
HIGH/CRITICAL and fixed OS HIGH/CRITICAL gates remain in force; this is not a
zero-vulnerability claim.

## Coupled lock regeneration

Renovate updates the authoritative Python project and uv lock, then regenerates the coupled export in the same update. The runner permits only these literal uv commands with shell execution disabled. `--locked --offline --no-build` rejects a stale project/lock pair and prevents export-time build hooks. The compile step also forbids source builds. Use uv 0.13.4 and review all affected lock files together.

```sh
uv export --locked --offline --no-build --no-dev --no-emit-project --format requirements-txt --output-file .renovate-constraints.txt --no-hashes
uv pip compile .github/requirements-fuzz.in --constraint .renovate-constraints.txt --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --no-build --generate-hashes --no-header --no-annotate --output-file .github/requirements-fuzz.txt
```

`.renovate-constraints.txt` is temporary generator input; do not commit it. This keeps the Linux/Python 3.12 fuzz closure aligned with production dependencies.

## Static metadata for the pinned oscrypto source

The existing oscrypto dependency uses immutable archive `d5f3437ed24257895ae1edd9e503cfb352e635a8` (SHA-256 `f40d112bb8b5ca8cd424d64d80b3bd39a48bc1d769206f2c4d79fd9b0618ae74`). Its reviewed `setup.py` declares version 1.3.0 and only `asn1crypto>=1.5.1`, with no Python-version constraint or optional dependencies. The exact-version `tool.uv.dependency-metadata` entry records those declarations so dependency resolution can run with `--no-build` without executing the archive build backend. The source origin, selected version and distribution hash remain unchanged. Recheck the archive metadata and hash together before changing this source pin.
