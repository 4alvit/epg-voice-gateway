"""Short-lived authorization for media served by an existing HLS proxy.

This module never contacts a provider or fetches media. The HTTP adapter must
fetch ``MediaResource.upstream_url`` without following redirects. Session state
is intentionally process-local: a restart revokes every playback URL, and this
prototype must run with one worker/replica or sticky routing to the same process.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import unquote, urljoin, urlsplit

ContentKind = Literal["manifest", "segment", "key", "map"]
_PREFIXES = ("/channel/", "/play/", "/ts/", "/key/", "/map/")
_TOKEN_RE = re.compile(r"v1\.([A-Za-z0-9_-]{32})\.([0-9]{1,12})\.([A-Za-z0-9_-]{43})\Z")
_RESOURCE_RE = re.compile(r"[A-Za-z0-9_-]{32}\.(?:m3u8|ts|m4s|mp4|aac|vtt|key|bin)\Z")
_URI_RE = re.compile(r'(?P<prefix>(?:^|,)URI=)"(?P<uri>[^"\r\n]*)"')
_URI_MARKER_RE = re.compile(r"(?:^|,)URI=")
_MANIFEST_URI_TAGS = {
    "#EXT-X-MEDIA",
    "#EXT-X-I-FRAME-STREAM-INF",
    "#EXT-X-RENDITION-REPORT",
    "#EXT-X-IMAGE-STREAM-INF",
}
_MAX_MANIFEST_BYTES = 1024 * 1024


class MediaError(ValueError):
    """A safe error: messages contain codes, never upstream URLs or tokens."""

    def __init__(self, code: str, status_code: int = 400) -> None:
        self.code = code
        self.status_code = status_code
        super().__init__(code)


@dataclass(frozen=True)
class PlaybackSession:
    token: str = field(repr=False)
    expires_at: int
    url: str = field(repr=False)


@dataclass(frozen=True)
class MediaResource:
    upstream_url: str = field(repr=False)
    content_kind: ContentKind


@dataclass
class _Session:
    expires_at: int
    resources: dict[str, MediaResource] = field(default_factory=dict)
    resource_names: dict[tuple[str, ContentKind], str] = field(default_factory=dict)


def _origin(url: str) -> tuple[str, str, int]:
    """Return a strict origin, rejecting URL parser ambiguities."""
    if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        raise MediaError("invalid_media_url")
    if "\\" in url:
        raise MediaError("invalid_media_url")
    try:
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise MediaError("invalid_media_url")
        if parts.username is not None or parts.password is not None:
            raise MediaError("invalid_media_url")
        if parts.fragment or "%" in parts.netloc:
            raise MediaError("invalid_media_url")
        hostname = parts.hostname.encode("idna").decode("ascii").lower()
        port = parts.port if parts.port is not None else (443 if parts.scheme == "https" else 80)
        if port == 0:
            raise MediaError("invalid_media_url")
        return parts.scheme, hostname, port
    except (UnicodeError, ValueError) as exc:
        if isinstance(exc, MediaError):
            raise
        raise MediaError("invalid_media_url") from None


def _reject_ambiguous_path(path: str) -> None:
    """Reject encoded traversal before urljoin or an upstream can normalize it."""
    candidate = path
    for _ in range(5):
        if "\\" in candidate or "//" in candidate:
            raise MediaError("invalid_media_path")
        if any(ord(char) <= 32 or ord(char) == 127 for char in candidate):
            raise MediaError("invalid_media_path")
        if any(part in (".", "..") for part in candidate.split("/")):
            raise MediaError("invalid_media_path")
        decoded = unquote(candidate, errors="strict")
        if decoded == candidate:
            return
        candidate = decoded
    raise MediaError("invalid_media_path")


class MediaSessionStore:
    """Authorize bounded live sessions against exactly one HLS proxy origin.

    Manifest refreshes do not extend the expiry. Repeated child URLs reuse the
    same opaque resource name. Expired sessions are pruned on access or creation;
    active sessions are never evicted to make room for a new request.
    """

    def __init__(
        self,
        proxy_origin: str,
        public_base_url: str,
        signing_key: bytes | str,
        *,
        lifetime_seconds: int = 7200,
        max_sessions: int = 64,
        max_resources_per_session: int = 4096,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._proxy_origin = _origin(proxy_origin)
        public_origin = _origin(public_base_url)
        for url in (proxy_origin, public_base_url):
            parts = urlsplit(url)
            if parts.path not in ("", "/") or parts.query:
                raise MediaError("origin_must_not_include_path_or_query")
        if public_origin[0] != "https":
            raise MediaError("public_media_origin_requires_https")
        if isinstance(signing_key, str):
            signing_key = signing_key.encode("utf-8")
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise MediaError("media_signing_key_too_short")
        for value in (lifetime_seconds, max_sessions, max_resources_per_session):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise MediaError("invalid_media_capacity_or_lifetime")
        self._public_base_url = public_base_url.rstrip("/")
        self._key = signing_key
        self._lifetime_seconds = lifetime_seconds
        self._max_sessions = max_sessions
        self._max_resources = max_resources_per_session
        self._clock = clock
        self._sessions: dict[str, _Session] = {}
        self._lock = threading.RLock()

    def _validate_upstream(self, url: str) -> str:
        if _origin(url) != self._proxy_origin:
            raise MediaError("media_origin_not_allowed", 403)
        path = urlsplit(url).path
        try:
            _reject_ambiguous_path(path)
        except UnicodeError:
            raise MediaError("invalid_media_path") from None
        if not path.startswith(_PREFIXES):
            raise MediaError("media_path_not_allowed", 403)
        return url

    def _resolve_reference(self, base_url: str, reference: str) -> str:
        if not reference or "{$" in reference or "\\" in reference:
            raise MediaError("invalid_manifest_reference")
        if any(ord(char) <= 32 or ord(char) == 127 for char in reference):
            raise MediaError("invalid_manifest_reference")
        try:
            _reject_ambiguous_path(urlsplit(reference).path)
            result = urljoin(base_url, reference)
        except (UnicodeError, ValueError):
            raise MediaError("invalid_manifest_reference") from None
        return self._validate_upstream(result)

    def _signature(self, payload: str) -> str:
        digest = hmac.new(self._key, payload.encode("ascii"), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")

    def _prune(self, now: float) -> None:
        for session_id in tuple(self._sessions):
            if self._sessions[session_id].expires_at <= now:
                del self._sessions[session_id]

    def _session(self, token: str) -> _Session:
        match = _TOKEN_RE.fullmatch(token) if isinstance(token, str) else None
        if match is None:
            raise MediaError("invalid_media_session", 403)
        session_id, expires, signature = match.groups()
        payload = f"v1.{session_id}.{expires}"
        if not hmac.compare_digest(signature, self._signature(payload)):
            raise MediaError("invalid_media_session", 403)
        now = self._clock()
        self._prune(now)
        if int(expires) <= now:
            raise MediaError("media_session_expired", 410)
        session = self._sessions.get(session_id)
        if session is None or session.expires_at != int(expires):
            raise MediaError("media_session_not_found", 404)
        return session

    def _resource_name(self, session: _Session, upstream_url: str, kind: ContentKind) -> str:
        key = (upstream_url, kind)
        existing = session.resource_names.get(key)
        if existing is not None:
            return existing
        if len(session.resources) >= self._max_resources:
            raise MediaError("media_resource_capacity_exceeded", 503)
        if kind == "manifest":
            extension = "m3u8"
        elif kind == "key":
            extension = "key"
        else:
            extension = urlsplit(upstream_url).path.rsplit(".", 1)[-1].lower()
            if extension not in {"ts", "m4s", "mp4", "aac", "vtt"}:
                extension = "bin"
        name = f"{secrets.token_urlsafe(24)}.{extension}"
        while name in session.resources:
            name = f"{secrets.token_urlsafe(24)}.{extension}"
        session.resources[name] = MediaResource(upstream_url, kind)
        session.resource_names[key] = name
        return name

    def _url(self, token: str, name: str) -> str:
        return f"{self._public_base_url}/media/{token}/{name}"

    def create(self, upstream_url: str) -> PlaybackSession:
        """Create a session whose initial resource is a master/media manifest."""
        self._validate_upstream(upstream_url)
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._sessions) >= self._max_sessions:
                raise MediaError("media_session_capacity_exceeded", 503)
            session_id = secrets.token_urlsafe(24)
            while session_id in self._sessions:
                session_id = secrets.token_urlsafe(24)
            expires_at = int(now) + self._lifetime_seconds
            payload = f"v1.{session_id}.{expires_at}"
            token = f"{payload}.{self._signature(payload)}"
            session = _Session(expires_at)
            self._sessions[session_id] = session
            name = self._resource_name(session, upstream_url, "manifest")
            return PlaybackSession(token, expires_at, self._url(token, name))

    def resolve(self, token: str, resource_name: str) -> MediaResource:
        """Resolve an authorized opaque path; never accept an upstream URL here."""
        with self._lock:
            session = self._session(token)
            if not isinstance(resource_name, str) or not _RESOURCE_RE.fullmatch(resource_name):
                raise MediaError("media_resource_not_found", 404)
            resource = session.resources.get(resource_name)
            if resource is None:
                raise MediaError("media_resource_not_found", 404)
            return resource

    def revoke(self, token: str) -> None:
        """Revoke this session and every child resource immediately."""
        with self._lock:
            self._session(token)
            del self._sessions[token.split(".")[1]]

    def rewrite_manifest(self, token: str, upstream_manifest_url: str, text: str) -> str:
        """Rewrite every supported HLS URI into a session-bound HTTPS URL.

        Unsupported URI syntax, variable substitution, content steering, unsafe
        origins, and excess resource counts fail the whole response. The source
        manifest must already belong to the same session.
        """
        if not isinstance(text, str) or len(text.encode("utf-8")) > _MAX_MANIFEST_BYTES:
            raise MediaError("invalid_manifest", 502)
        lines = text.splitlines()
        if not lines or lines[0].lstrip("\ufeff") != "#EXTM3U":
            raise MediaError("invalid_manifest", 502)
        with self._lock:
            session = self._session(token)
            if (upstream_manifest_url, "manifest") not in session.resource_names:
                raise MediaError("manifest_not_in_session", 403)
            replacements: list[tuple[int, int, int, str, ContentKind]] = []
            next_is_manifest = False
            for index, line in enumerate(lines):
                if not line:
                    continue
                if not line.startswith("#"):
                    kind: ContentKind = "manifest" if next_is_manifest else "segment"
                    upstream = self._resolve_reference(upstream_manifest_url, line)
                    replacements.append((index, 0, len(line), upstream, kind))
                    next_is_manifest = False
                    continue
                tag, separator, attributes = line.partition(":")
                if tag in {"#EXT-X-CONTENT-STEERING", "#EXT-X-DEFINE"}:
                    raise MediaError("unsupported_manifest_feature", 502)
                if tag == "#EXT-X-STREAM-INF":
                    next_is_manifest = True
                if not separator:
                    continue
                matches = list(_URI_RE.finditer(attributes))
                if len(matches) != len(_URI_MARKER_RE.findall(attributes)):
                    raise MediaError("invalid_manifest_uri", 502)
                if tag in _MANIFEST_URI_TAGS:
                    kind = "manifest"
                elif tag in {"#EXT-X-KEY", "#EXT-X-SESSION-KEY"}:
                    kind = "key"
                elif tag == "#EXT-X-MAP":
                    kind = "map"
                else:
                    kind = "segment"
                for match in matches:
                    upstream = self._resolve_reference(upstream_manifest_url, match.group("uri"))
                    offset = len(tag) + 1
                    replacements.append((index, offset + match.start("uri"), offset + match.end("uri"), upstream, kind))
            if next_is_manifest:
                raise MediaError("invalid_manifest", 502)
            new_resources = {(url, kind) for _, _, _, url, kind in replacements} - session.resource_names.keys()
            if len(session.resources) + len(new_resources) > self._max_resources:
                raise MediaError("media_resource_capacity_exceeded", 503)
            for index, start, end, upstream, kind in reversed(replacements):
                name = self._resource_name(session, upstream, kind)
                lines[index] = lines[index][:start] + self._url(token, name) + lines[index][end:]
            lines[0] = "#EXTM3U"
            return "\n".join(lines) + "\n"
