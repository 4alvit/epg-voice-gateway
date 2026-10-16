# Changelog

## 0.1.0 development source (not a stable release)

The initial source version provides single-household XMLTV/M3U catalogue import, current-programme and channel search, bounded SQLite storage, authenticated controls, expiring HMAC media sessions, HLS proxying and private voice integrations. There are currently no published stable release assets. Reproducible source revisions are identified by their full Git commit; pyproject.toml records the planned 0.1.0 semantic version.

### Unreleased security and quality changes — 2026-10-16

- Replace the container's pip 25.0.1 with the hash-pinned official 26.2.1 wheel before package installation, addressing the old installer findings including CVE-2026-13346 and CVE-2026-8643. Pin the build backend closure and prevent silent isolated build dependency downloads. OS and optional-provider findings remain under separate review.
- Include the project license in the Docker build context and require a real installed-image startup/authentication smoke check in public CI.

- Validate exact public-key sizes on every certificate in owned outbound TLS chains before sending HTTP data; reject unsupported chains and preserve normal trust/hostname validation.
- Add an explicit loopback HTTPS launcher with verified configured key sizes and an HTTPS-only upstream configuration. Existing private LAN configurations remain supported with their documented transport limits.
- Add real certificate-boundary and complete guide-to-media HTTPS integration tests.
- Add CodeQL and enforced Ruff security checks, private vulnerability intake, contribution/governance/release documentation and a dependency update policy.

These changes harden the supported configuration; no project CVE/GHSA has been assigned. Future vulnerability fixes must name applicable identifiers here rather than implying that all dependency advisories are resolved.
