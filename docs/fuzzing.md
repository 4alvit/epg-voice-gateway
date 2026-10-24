# RC fuzz testing

[![RC fuzz assessment](https://github.com/4alvit/epg-voice-gateway/actions/workflows/fuzz-candidate.yml/badge.svg)](https://github.com/4alvit/epg-voice-gateway/actions/workflows/fuzz-candidate.yml)

![Public RC fuzz process](diagrams/rc-fuzzing.svg)

## Scope and purpose

**M3U playlists and bounded XMLTV / gzip guides.** The targets invoke `src/epg_voice_gateway/catalog.py`: `parse_playlist` and `parse_xmltv` directly. They test production code rather than a copied parser or a random JSON-only loop.

The playlist target checks same-origin channel URLs, absence of credentials/fragments, unique identities and bounded time shifts. The XMLTV target checks valid programme intervals and equivalent plain/gzip parsing, with a 64 KiB decoded limit. No HTTP client, database, certificate fetcher or playback worker is started.

The engine is **Python / Atheris**. Two targets each receive a 60-second mutation budget; input size is capped at 16384 bytes. Dependencies are pinned in the isolated fuzz lockfile. Fuzz tooling is not an application runtime dependency.

## When it runs

The shared `venus-os-ci-toolkit` workflow accepts only an explicit `channel=rc` dispatch from this public repository's default branch. It does not start timed mutation on pull requests, ordinary pushes, beta releases, schedules or private repositories. Ordinary tests replay fixed inputs without a timed campaign.

```sh
gh workflow run fuzz-candidate.yml --repo 4alvit/epg-voice-gateway --ref main -f channel=rc
```

Use the exact source commit and run ID when reviewing the result. A green historical badge is not proof for a later untested commit. The shared workflow and runner are pinned together to a reviewed full commit SHA.

This workflow produces an assessment artifact only. It neither tags nor publishes a beta/RC, and it does not satisfy the physical acceptance requirements for RC or stable releases in [RELEASING.md](../RELEASING.md).

## Local deterministic verification

Run the repository's documented dependency setup, then:

```sh
uv run --frozen pytest -q tests/test_fuzz_contracts.py tests/test_catalog.py
```

The committed corpus includes valid formats, malformed boundaries and explicit security rejections. To add a regression, minimize the failure, remove any sensitive data, save a synthetic case in `fuzz/corpus/`, and rerun the ordinary tests before another RC assessment.

## Results and limitations

The workflow retains a machine-readable report tied to source revision and workflow run, actual engine execution counts, diagnostics and generated crash inputs. Positive execution counts and successful invariant checks are required; an empty or skipped target is not a passing campaign. Download the report from the matching Actions run. Keep the run URL with any release review before artifact retention expires.

A failure is actionable: reproduce it with the same locked toolchain, correct the production behavior or an incorrect invariant, retain the minimized regression, and rerun the assessment. Do not weaken the assertions or relabel a crash to make the workflow green.

This is bounded repository CI fuzzing, not registration in Google's OSS-Fuzz service, a proof of absence of bugs, or a physical-device test. See the [shared workflow documentation](https://github.com/victron-venus/venus-os-ci-toolkit/blob/main/docs/rc-fuzzing.md) for isolation, budgets and evidence rules.

## Updating parser dependencies

The dedicated fuzz lock is constrained to the application's reviewed `uv.lock`; it includes the production parser import closure plus Atheris, without the voice/certificate runtime. Refresh both together when those parser dependencies change:

```sh
uv export --locked --no-dev --no-emit-project --no-hashes -o /tmp/epg-runtime-constraints.txt
uv pip compile .github/requirements-fuzz.in --constraint /tmp/epg-runtime-constraints.txt --python-version 3.12 --python-platform x86_64-unknown-linux-gnu --generate-hashes --no-header --no-annotate -o .github/requirements-fuzz.txt
```

Review dependency changes and run the deterministic parser tests and Linux candidate assessment before accepting the new lock.
