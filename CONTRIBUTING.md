# Contributing

Use GitHub issues for reproducible bugs and feature proposals, and pull requests for changes. Discussion is public and retained at https://github.com/4alvit/epg-voice-gateway/issues. Use the private SECURITY.md route for vulnerabilities and CODE_OF_CONDUCT.md for confidential conduct reports. Do not post household media URLs, tokens, private keys, logs with session URLs or personal schedules.

Clone the public repository, install Python 3.12+ and uv, then run:

```sh
uv sync --frozen --extra test
uv run --frozen ruff check .
uv run --frozen ruff check src integrations --select S
uv run --frozen pytest -q
node --test deploy/google-home-worker-vpc/relay.test.mjs
node --test deploy/alexa-worker-vpc/relay.test.mjs
uv build
```

Use readable Python with type annotations for public boundaries, small functions and the checked-in Ruff rules. Use standard JavaScript with explicit validation for worker changes. All contributors, including maintainers, must add automated tests for significant functionality and regression tests for bug fixes where practical. Include invalid input, authorization, timeout and failure cases. Explain any test that cannot be automated. Never use production services, tokens, media or devices in tests. Existing TLS tests deliberately generate weak disposable keys only to verify rejection.

Describe the problem, resulting behavior, tests and compatibility in the PR. A maintainer reviews changes and waits for the required CI checks before normal merge. Keep security fixes reviewable and preserve third-party license notices. Contributions are under this repository's MIT license; do not submit material you cannot license accordingly. The maintainer aims to acknowledge reports promptly; unanswered reports can be followed up on their original issue.
