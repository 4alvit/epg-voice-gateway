# Release and deployment acceptance

The project is pre-stable at source version 0.1.0. A Git commit uniquely identifies each development build. Do not describe a successful CI run as an accepted stable household deployment.

For each proposed release the maintainer reviews compatibility and security notes, updates pyproject.toml and CHANGELOG.md, refreshes uv.lock when needed (the container exports its hash-checked runtime requirements directly from this lock), and runs all CONTRIBUTING.md checks. Build the wheel/sdist with `uv build` and the runtime container with `docker build -t epg-voice-gateway:reviewed .`. Keep source and license notices in artifacts. Do not embed private environment files, cache databases or credentials.

For release acceptance, test the exact candidate on the intended private system: readiness and refresh, known/unknown/ambiguous programme queries, authorization failures, full media playback and expiry, optional voice routes being shipped, restart and rollback. Record candidate commit, environment, scenarios and result in the release PR. Only then tag a unique semantic version and publish its notes/artifacts through normal repository permissions. Signing and reproducible binary guarantees are not yet claimed.

Upgrades preserve private configuration outside the repository. Back up the database and deployment configuration, test an isolated candidate, and retain the previous reviewed artifact for rollback. A sole maintainer currently owns acceptance; there is no backup recovery commitment.
