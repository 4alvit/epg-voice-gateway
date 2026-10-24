# Release and deployment acceptance

The project is pre-stable in the 0.1.0 beta series. A Git commit uniquely identifies each development build. Do not describe a successful CI run as an accepted stable household deployment.

For each proposed release the maintainer reviews compatibility and security notes, updates pyproject.toml and CHANGELOG.md, refreshes uv.lock when needed (the container exports its hash-checked runtime requirements directly from this lock), and runs all CONTRIBUTING.md checks. Build the wheel/sdist with `uv build` and the runtime container with `docker build -t epg-voice-gateway:reviewed .`. Keep source and license notices in artifacts. Do not embed private environment files, cache databases or credentials.

For RC or stable release acceptance, test the exact candidate on the intended private system: readiness and refresh, known/unknown/ambiguous programme queries, authorization failures, full media playback and expiry, optional voice routes being shipped, restart and rollback. Record candidate commit, environment, scenarios and result in the release PR. Only then tag a unique semantic version and publish its notes/artifacts through normal repository permissions. Signing and reproducible binary guarantees are not yet claimed.

Upgrades preserve private configuration outside the repository. Back up the database and deployment configuration, test an isolated candidate, and retain the previous reviewed artifact for rollback. A sole maintainer currently owns acceptance; there is no backup recovery commitment.

## Export a beta for acceptance

The existing Checks workflow can export the exact image that passed its installed
startup/authentication smoke test and security inventory. It also builds the
wheel and source distribution without network access using that image's pinned
build backends. This export is off by default and only permitted on `main`:

```sh
gh workflow run checks.yml --repo 4alvit/epg-voice-gateway --ref main -f export_candidate=true
```

Select the run you dispatched; require its exact `headSha`, `workflow_dispatch`,
`main`, and successful completion of **every** required suite, including the
Worker profile. Download `epg-beta-COMMIT_SHA` into an empty private directory.
Verify every entry in `SHA256SUMS`, the version and source in
`candidate-manifest.json`, and the image configuration digest after import.
The manifest's two acceptance flags deliberately remain false: exporting an
image is not a deployment or physical playback test.

The archive contains the runtime image, wheel, sdist, complete public source,
security inventory and SBOM. The combined payload is limited to 400 MiB and
retained for seven days. Publish only a unique beta tag with these verified
bytes and explicit limitations. Retain the exact manifest for later private
acceptance; never silently replace its files or promote it solely because CI
passed. RC and stable still require the private-system tests above.
