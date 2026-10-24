# Quality evidence and limits

The public Checks workflow runs Python 3.12 with frozen uv dependencies, Ruff style/import/name rules, Ruff security rules for src and integrations, the full pytest suite and two Node worker suites. CodeQL analyses Python, JavaScript and Actions on PRs/pushes and weekly. The runtime lock export must match uv.lock. Tests must not contact live devices or use operator credentials.

The 2026-10-16 local review passed 784 Python tests (2 platform/media skips), including 40 actual TLS boundary combinations and 5 new HTTPS configuration/end-to-end tests. The complete end-to-end test uses real temporary TLS servers: M3U/XMLTV ingestion, rejected unauthenticated request, authenticated resolution, expiring playback URL, manifest rewriting and segment transfer. All upstream/media bytes and credentials are synthetic. The final Linux Python 3.12 CI run passed 786 tests and 40 subtests with one optional media skip; this includes six HTTPS workflow/configuration cases. A physical-device acceptance or new fuzzing campaign is not inferred from this run.

The fresh Linux CI run reported 68 upstream warnings. In addition to the certificate warnings below, ASK SDK model docstrings/serialization regexes use unrecognized escape sequences; Python 3.12 preserves their intended literal backslashes but warns at first compilation. The locked Starlette test client warns about its httpx adapter and deprecated anyio BlockingPortal alias; these are test-harness compatibility notices, not application transport bypasses. Existing model/serialization and client tests pass. Keep these explicit exceptions under review when updating the upstream SDK/test stack; do not suppress all warnings.

Known warning disposition: ASK SDK 1.3.3 uses deprecated naïve certificate validity properties and datetime.utcnow during signature validation. These warnings originate in the pinned upstream verifier; existing tests verify expiry, trust, SAN and signature behavior. They do not disable validation. Track an upstream-compatible update; do not blanket-suppress warnings or fork security validation merely to silence them. Source syntax/style/security checks are clean. Three Ruff S608 exceptions are confined to reviewed SQL statements whose interpolated fragments are fixed clauses, placeholder counts and generated integer ranks; all external values remain parameter-bound and storage tests cover invalid inputs.

CodeQL can flag intentional weak-key fixtures; retain an explicit used-in-tests disposition and the test's strong-key calibration. No production scan path is excluded. Coverage, maximally strict diagnostics, dynamic fuzzing of this exact version and all optional deployment transports remain unproven for Silver. Native Python/OpenSSL dependencies require their own updates and review.

The installed-environment pip-audit review on 2026-10-16 found no published vulnerability in 45 dependencies (the local project is not a PyPI package and is separately source-reviewed). The oscrypto dependency is a specific upstream commit, not the older PyPI archive; URL-lock parsing cannot audit it directly, so its installed metadata was included in the environment audit. GitHub Dependabot, secret and advisory inventories were empty. This is dated evidence, not a permanent absence-of-vulnerabilities guarantee.

### Dependency-audit boundary

The 45-dependency result covers the selected installed Python environment. It does not establish a complete build or deployment dependency result: isolated build requirements (`hatchling`), operating-system packages in the pinned `python:3.12-slim` image, and the Cloudflare provider 5.24.0 pinned by both optional Worker Terraform deployments require separate inventories and advisory assessment. The Docker build now uses separate hash-checked installer/build locks and disables isolated backend resolution. This makes those inputs explicit; their audit and the image OS inventory remain separate from the original 45-dependency runtime result. Do not interpret the direct HTTPS profile or an empty GitHub alert list as clearing these other supported paths.

Before treating the whole project as meeting vulnerability-remediation criteria, record the exact resolved build packages and container platform/digest, scan their dependencies, and review the provider executable and its Go dependencies for applicable advisories. Upgrade or provide a fully tested compatible replacement for affected components. Keep unverified criteria marked unmet until this work is complete; a transport test alone is not a dependency audit.

### Runtime installer vendors and full-image gate — 2026-10-24

The unpublished 0.1.0b2 image passed the earlier narrow XZ check, but its complete
inventory exposed real vulnerable libraries vendored in pip 26.2.1 and in
ensurepip's bundled wheel: certifi (GHSA-6q48-6c7w-9gw2), msgpack
(GHSA-6v7p-g79w-8964), setuptools (CVE-2025-47273), and urllib3
(CVE-2026-97687 / CVE-2026-97689). The patched application distributions did not
replace those separate copies. This candidate was rejected before publication.

The build now bootstraps the official hash-verified pip 26.2.2 wheel, whose
certifi trust store removes the compromised roots described in the
[certifi advisory](https://github.com/advisories/GHSA-6q48-6c7w-9gw2).
After installation and `pip check`, the final runtime removes pip and ensurepip,
including their vendored code. The build/export stages still have their pinned
tools; deleting runtime files does not erase earlier image layers. No supported
runtime operation installs packages or creates a virtual environment. Upgrade
by rebuilding the image. Source installations retain their normal host tools.

The retained hatchling backend builds wheels and source archives offline without
pip. Every public container check exercises that export, startup, authentication,
and the absence of both installer modules. The actual-image security gate now
rejects every HIGH/CRITICAL language finding, whether discovered from package
metadata or an embedded SBOM, and every fixable HIGH/CRITICAL OS finding. It also
retains the exact XZ package requirement. Missing inventories fail closed.
Unfixed OS findings remain listed in the summary and full Trivy report; passing
this gate does not mean the image has zero vulnerabilities. Release and deployed
image acceptance remain separate from source checks.

### Container installer remediation — 2026-10-16

The pinned Linux/amd64 base image contains pip 25.0.1. A fresh Trivy inventory
identified six pip advisories, including five medium findings older than 60 days.
CVE-2025-8869 concerns a pre-PEP 706 extraction fallback and does not apply to that
Python 3.12 path; this is not a dismissal of the other findings. The Dockerfile
now installs only the hash-pinned official pip 26.2.1 wheel before other packages,
then the locked build backend closure. Runtime and local-project installation
cannot silently resolve a second isolated backend environment. The upstream
[release notes](https://pip.pypa.io/en/stable/news/) document the patched versions,
including index filename decoding and entry-point path handling fixes.

The fresh macOS Python 3.12.14 installed-environment audit after the locked build
checked 44 published dependencies and found no known vulnerabilities; the local
project was correctly excluded from PyPI matching. Both the pinned oscrypto
source archive and the application wheel built with isolation disabled, and
`pip check` passed. Hosted CI separately verifies the actual Linux container;
this local package result does not stand in for that result.

The base-image scan also contains OS findings requiring separate applicability
review. This installer change does not claim an advisory-free image, coverage of
other architectures, or remediation of the optional Cloudflare provider.

### Container XZ security update — 2026-10-16

The pinned base also contains `liblzma5` version `5.8.1-1+deb13u1`, affected by
[DSA-6549-1](https://security-tracker.debian.org/tracker/DSA-6549-1) and upstream
[GHSA-5qpq-xqfv-j9pg](https://github.com/tukaani-project/xz/security/advisories/GHSA-5qpq-xqfv-j9pg).
The Dockerfile upgrades only that existing library to Debian's exact fixed
revision `5.8.1-1+deb13u2` using the authenticated Debian repositories, then
removes the downloaded package cache and package lists. It retains the base
digest and all installer, build and runtime dependency locks.

The required public container job builds the actual image, runs the existing
network-disabled startup/authentication smoke test as UID 10001, and scans that
image with pinned Trivy. Its gate requires the exact fixed library revision and
rejects all three known XZ advisory identifiers. The complete finding report and
CycloneDX SBOM are retained as `container-security-evidence`; remaining findings
are not filtered out or treated as fixed. This narrow repair does not establish
whole-image vulnerability clearance or qualify the optional Cloudflare paths.

### Native cryptography wheel update — 2026-10-16

The runtime floor and both dependency locks now select `cryptography 50.0.2`.
Its official Linux wheel embeds OpenSSL 4.0.3, replacing the OpenSSL 4.0.2
embedded in the previous 50.0.1 wheel. The
[upstream changelog](https://cryptography.io/en/latest/changelog/#v50-0-2)
records the native-library update, including the OpenSSL fix for
[CVE-2026-84783](https://openssl-library.org/news/vulnerabilities/#CVE-2026-84783).
The required installed-image smoke check reads the running cryptography
backend's native version and rejects versions below 4.0.3. Existing certificate,
Alexa signature and complete HTTPS workflow tests exercise compatibility.
This check concerns the wheel's embedded OpenSSL; CPython's system TLS library,
remaining image findings and optional Cloudflare deployment are separate audit
boundaries. It does not claim a new release or complete vulnerability clearance.


## Worker provider qualification

The optional Google/Alexa Worker modules now select a shared, pinned security
profile through `scripts/worker_profile.py`. Its separate required public CI job
reconciles the original 397-package qualification, rebuilds the same production
binary and demands the original independently recorded SHA-256. It then runs
both modules' real schemas and mocked policy plans plus real TLS handshakes.
The [deployment guide](WORKER_SECURE_PROFILE.md) preserves state ownership and
distinguishes those tests from cloud/device acceptance. This does not retroactively
upgrade existing registry installations or clear every operator dependency.
