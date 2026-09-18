"""Private HTTP validators and raw XMLTV bodies for configured guide sources.

This cache does not validate XML or extend catalog freshness on network errors.
The caller must invalidate a source after a parsing failure. Filenames are URL
hashes; metadata and errors never include source URLs or authentication details.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import tempfile
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import httpx

from .errors import CatalogUnavailable

MAX_TRANSFER_BYTES = 64 * 1024 * 1024
_MAX_METADATA_BYTES = 8192
_MAX_VALIDATOR_BYTES = 1024


def _safe_validator(value: str | None) -> str | None:
    if not value or len(value) > _MAX_VALIDATOR_BYTES:
        return None
    if any(ord(character) < 32 or ord(character) >= 127 for character in value):
        return None
    return value


def _validators(headers) -> tuple[str | None, str | None]:
    etag = _safe_validator(headers.get("etag"))
    if etag is not None and not re.fullmatch(r'(?:W/)?"[^"\x00-\x20\x7f]*"', etag):
        etag = None
    modified = _safe_validator(headers.get("last-modified"))
    if modified is not None:
        try:
            if parsedate_to_datetime(modified).tzinfo is None:
                modified = None
        except (TypeError, ValueError, OverflowError):
            modified = None
    return etag, modified


def _key(url: str) -> str:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or parsed.port == 0
            or "\\" in url
            or any(ord(character) <= 32 or ord(character) == 127 for character in url)
        ):
            raise ValueError()
        return hashlib.sha256(url.encode("utf-8")).hexdigest()
    except (TypeError, ValueError, UnicodeError):
        raise CatalogUnavailable() from None


class SourceCache:
    """Cache raw guide bodies atomically in a dedicated private directory.

    Use one instance per application process. Per-source locks prevent duplicate
    downloads within that process. A 304 response is accepted only after a
    size/digest-verified cached body justified sending conditional headers.
    """

    def __init__(self, directory: Path | str):
        self.directory = Path(directory)
        if self.directory.is_symlink():
            raise CatalogUnavailable()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        self._locks: dict[str, asyncio.Lock] = {}

    def _paths(self, key: str) -> tuple[Path, Path]:
        return self.directory / f"{key}.body", self.directory / f"{key}.json"

    def _entry(self, key: str, limit: int) -> dict | None:
        body_path, metadata_path = self._paths(key)
        try:
            if body_path.is_symlink() or metadata_path.is_symlink():
                return None
            if metadata_path.stat().st_size > _MAX_METADATA_BYTES:
                return None
            metadata = json.loads(metadata_path.read_bytes())
            if not isinstance(metadata, dict) or metadata.get("version") != 1:
                return None
            length, digest = metadata.get("length"), metadata.get("sha256")
            if (
                not isinstance(length, int) or isinstance(length, bool)
                or not 0 <= length <= limit or body_path.stat().st_size != length
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
            ):
                return None
            actual = hashlib.sha256()
            with body_path.open("rb") as stream:
                remaining = limit + 1
                while chunk := stream.read(min(65536, remaining)):
                    remaining -= len(chunk)
                    if remaining <= 0:
                        return None
                    actual.update(chunk)
            if actual.hexdigest() != digest:
                return None
            etag, modified = _validators({
                "etag": metadata.get("etag"), "last-modified": metadata.get("last_modified"),
            })
            return {"version": 1, "length": length, "sha256": digest, "etag": etag, "last_modified": modified}
        except (OSError, ValueError, TypeError, RecursionError):
            return None

    def _read(self, key: str, metadata: dict, limit: int) -> bytes:
        body_path, _ = self._paths(key)
        if body_path.is_symlink():
            raise CatalogUnavailable()
        try:
            with body_path.open("rb") as stream:
                body = stream.read(limit + 1)
            if len(body) > limit or len(body) != metadata["length"] or hashlib.sha256(body).hexdigest() != metadata["sha256"]:
                raise CatalogUnavailable()
            return body
        except OSError:
            raise CatalogUnavailable() from None

    def _atomic_write(self, path: Path, data: bytes) -> None:
        descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=self.directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _store(self, key: str, data: bytes, headers) -> None:
        body_path, metadata_path = self._paths(key)
        etag, modified = _validators(headers)
        metadata = {
            "version": 1, "length": len(data), "sha256": hashlib.sha256(data).hexdigest(),
            "etag": etag, "last_modified": modified,
        }
        try:
            # If interrupted between replacements, the old metadata fails its
            # digest check. The next fetch then makes an unconditional request.
            self._atomic_write(body_path, data)
            self._atomic_write(metadata_path, json.dumps(metadata).encode("utf-8"))
        except OSError:
            raise CatalogUnavailable() from None

    def invalidate(self, url: str) -> None:
        """Discard a parsed-invalid source so it cannot stay cached via 304."""
        try:
            for path in self._paths(_key(url)):
                path.unlink(missing_ok=True)
        except OSError:
            raise CatalogUnavailable() from None

    async def fetch(self, client: httpx.AsyncClient, url: str, limit: int) -> bytes:
        """Fetch at most 64 MiB of raw bytes, preserving gzip for the parser."""
        if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
            raise CatalogUnavailable()
        limit = min(limit, MAX_TRANSFER_BYTES)
        key = _key(url)
        async with self._locks.setdefault(key, asyncio.Lock()):
            metadata = await asyncio.to_thread(self._entry, key, limit)
            headers = {"Accept-Encoding": "identity"}
            if metadata is not None:
                if metadata["etag"]:
                    headers["If-None-Match"] = metadata["etag"]
                if metadata["last_modified"]:
                    headers["If-Modified-Since"] = metadata["last_modified"]
            try:
                response = await client.send(
                    client.build_request("GET", url, headers=headers, timeout=20),
                    stream=True, follow_redirects=False,
                )
            except httpx.HTTPError:
                raise CatalogUnavailable() from None
            try:
                if response.status_code == 304:
                    if metadata is None or len(headers) == 1:
                        raise CatalogUnavailable()
                    return await asyncio.to_thread(self._read, key, metadata, limit)
                if response.status_code != 200:
                    raise CatalogUnavailable()
                if response.headers.get("content-encoding", "identity").lower() not in ("identity", "gzip"):
                    raise CatalogUnavailable()
                length = response.headers.get("content-length", "")
                if length.isdecimal() and int(length) > limit:
                    raise CatalogUnavailable()
                data = bytearray()
                if response.is_stream_consumed:
                    data.extend(response.content)
                else:
                    async for chunk in response.aiter_raw(65536):
                        data.extend(chunk)
                        if len(data) > limit:
                            raise CatalogUnavailable()
                if len(data) > limit:
                    raise CatalogUnavailable()
                body = bytes(data)
                await asyncio.to_thread(self._store, key, body, response.headers)
                return body
            except httpx.HTTPError:
                raise CatalogUnavailable() from None
            finally:
                with anyio.CancelScope(shield=True):
                    await response.aclose()
