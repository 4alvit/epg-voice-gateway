"""Read configured M3U/XMLTV sources and resolve programmes airing now."""

import asyncio
import gzip
import hashlib
import io
import json
import logging
import re
import time
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import httpx
from defusedxml.ElementTree import iterparse

from .automatic_channels import (
    PreparedChannel,
    prepare_automatic_channels,
    render_automatic_channels,
)
from .channel_catalogue import ChannelCandidate, build_channel_catalogue
from .config import Settings
from .errors import CatalogUnavailable, InvalidSource
from .models import Channel, ProgramMatch, Programme
from .resolver import resolve_media
from .search import normalize, query_variants
from .source_cache import SourceCache
from .storage import CatalogStorageError, CatalogStore
from .voice_catalogue import build_voice_catalogue

LOGGER = logging.getLogger(__name__)
_DEFAULT_SOURCE_BYTES = 64 * 1024 * 1024
_MAX_SOURCE_TRANSFER_BYTES = 64 * 1024 * 1024


class _BoundedReader:
    """Feed a parser incrementally without allowing an unbounded gzip read."""

    def __init__(self, source, limit: int):
        self.source = source
        self.remaining = limit

    def read(self, size: int = -1) -> bytes:
        if size == 0:
            return b""
        # Read one byte past the boundary to distinguish exact-size EOF from an
        # oversized document. Gzip validates its trailer when EOF is consumed.
        amount = min(size if size > 0 else 65536, 65536, self.remaining + 1)
        data = self.source.read(amount)
        self.remaining -= len(data)
        if self.remaining < 0:
            raise InvalidSource()
        return data


def parse_playlist(text: str, proxy_origin: str) -> list[Channel]:
    if not text.lstrip("\ufeff").startswith("#EXTM3U"):
        raise InvalidSource()
    channels: dict[str, Channel] = {}
    attributes: dict[str, str] = {}
    title = ""
    header_shift = "0"
    skipped = 0
    expected = urlsplit(proxy_origin)
    for line in text.splitlines():
        line = line.strip().lstrip("\ufeff")
        if line.startswith("#EXTM3U"):
            header = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            header_shift = header.get("tvg-shift", "0")
        elif line.startswith("#EXTINF"):
            attributes = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
            # Attribute values may contain commas; only split after their quoted region.
            quoted = False
            for index, character in enumerate(line):
                if character == '"':
                    quoted = not quoted
                elif character == "," and not quoted:
                    title = line[index + 1 :].strip()
                    break
            else:
                title = ""
        elif line and not line.startswith("#") and title:
            try:
                shift = int(float(attributes.get("tvg-shift", header_shift)) * 3600)
                if abs(shift) > 86400:
                    raise ValueError()
                url = urljoin(proxy_origin + "/", line)
                parsed = urlsplit(url)
            except (ValueError, OverflowError):
                raise InvalidSource() from None
            if (
                (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc)
                or parsed.username is not None
                or parsed.fragment
                or not parsed.path.startswith("/channel/")
            ):
                # Mixed proxy playlists may also contain direct streams. They
                # are outside this gateway's playback boundary and must never
                # enter its catalogue or inherit metadata onto the next URL.
                skipped += 1
                attributes, title = {}, ""
                continue
            identifier = hashlib.sha256((parsed.path + "\0" + title).encode()).hexdigest()[:24]
            channels[identifier] = Channel(
                identifier, title, attributes.get("tvg-id", ""),
                attributes.get("tvg-name", title), url, shift,
            )
            attributes, title = {}, ""
    if skipped:
        LOGGER.warning("playlist_entries_skipped count=%d", skipped)
    if not channels:
        raise InvalidSource()
    return list(channels.values())


def xmltv_time(value: str) -> datetime:
    parts = value.split()
    if not parts or len(parts[0]) not in (12, 14) or len(parts) > 2:
        raise ValueError("Invalid XMLTV time")
    pattern = "%Y%m%d%H%M%S" if len(parts[0]) == 14 else "%Y%m%d%H%M"
    return datetime.strptime(parts[0] + " " + (parts[1] if len(parts) == 2 else "+0000"), pattern + " %z").astimezone(UTC)


def parse_xmltv(
    data: bytes,
    now: datetime,
    max_bytes: int = _DEFAULT_SOURCE_BYTES,
    *,
    channels: list[Channel] | None = None,
) -> tuple[dict[str, list[str]], list[Programme]]:
    """Parse a bounded plaintext or gzip guide without a decoded document copy.

    ``max_bytes`` limits decoded XML. Optional playlist channels limit retained
    programmes, while channel metadata remains available for cross-feed mapping.
    Filtered parsing requires XMLTV channel metadata to precede programmes; an
    out-of-order guide fails closed instead of silently losing name-only matches.
    """
    if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes <= 0:
        raise InvalidSource()
    names: dict[str, list[str]] = {}
    programmes: list[Programme] = []
    wanted_ids = {channel.epg_id for channel in channels or () if channel.epg_id}
    wanted_names = {
        normalized
        for channel in channels or ()
        for name in (channel.epg_name, channel.name)
        if (normalized := normalize(name))
    }
    matched_ids = set(wanted_ids)
    saw_programme = False
    # Retain a bounded useful window, including all supported channel time shifts.
    oldest, newest = now - timedelta(hours=30), now + timedelta(hours=72)
    try:
        with io.BytesIO(data) as compressed:
            decoded = gzip.GzipFile(fileobj=compressed, mode="rb") if data[:2] == b"\x1f\x8b" else compressed
            with decoded:
                events = iterparse(_BoundedReader(decoded, max_bytes), events=("start", "end"), forbid_entities=True, forbid_external=True)
                _, root = next(events)
                if root.tag != "tv":
                    raise InvalidSource()
                for event, element in events:
                    if event != "end":
                        continue
                    if element.tag == "channel":
                        if channels is not None and saw_programme:
                            raise InvalidSource()
                        identifier = element.attrib.get("id", "")
                        names[identifier] = [child.text or "" for child in element.findall("display-name")]
                        if wanted_names.intersection(normalize(name) for name in names[identifier]):
                            matched_ids.add(identifier)
                        element.clear()
                        root.clear()
                    elif element.tag == "programme":
                        saw_programme = True
                        try:
                            epg_id = element.attrib["channel"]
                            if channels is None or epg_id in matched_ids:
                                start = xmltv_time(element.attrib["start"])
                                end = xmltv_time(element.attrib["stop"])
                                title = (element.findtext("title") or "").strip()
                                if title and start < end and end >= oldest and start <= newest:
                                    programmes.append(Programme(epg_id, title, start, end))
                        except (KeyError, ValueError):
                            pass
                        element.clear()
                        root.clear()
    except InvalidSource:
        raise
    except Exception:
        raise InvalidSource() from None
    if not programmes and (channels is None or not saw_programme):
        raise InvalidSource()
    return names, programmes


def expand_gzip(data: bytes, limit: int) -> bytes:
    if data[:2] != b"\x1f\x8b":
        return data
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        result = decoder.decompress(data, limit + 1)
        if len(result) > limit or not decoder.eof or decoder.unused_data:
            raise InvalidSource()
        return result
    except zlib.error:
        raise InvalidSource() from None


class Catalog:
    def __init__(self, settings: Settings, client: httpx.AsyncClient, *, clock=time.monotonic):
        self.settings = settings
        self.client = client
        self.clock = clock
        self.store = CatalogStore(settings.database_path)
        self.source_cache = None if settings.database_path == ":memory:" else SourceCache(Path(settings.database_path).parent / "sources")
        self.source_fingerprint = hashlib.sha256(json.dumps([settings.hls_proxy_url, settings.playlist_path, settings.xmltv_urls], ensure_ascii=True).encode()).hexdigest()
        saved = self.store.refreshed_at if self.store.source_fingerprint == self.source_fingerprint else None
        self.updated_at = None if saved is None else self.clock() - max(0, time.time() - saved)
        self.last_refresh_failed = False
        self._lock = asyncio.Lock()
        self._publication_lock = asyncio.Lock()
        self._channel_generation = 0
        self._channel_names: tuple[int, tuple[PreparedChannel, ...]] | None = None
        self._channel_name_task: asyncio.Task | None = None
        self._imported_channel_names: list[ChannelCandidate] | None = None
        self._closed = False

    @property
    def ready(self) -> bool:
        return self.updated_at is not None and self.clock() - self.updated_at <= self.settings.max_stale_seconds

    async def _download(self, url: str, limit: int, *, keep_gzip: bool = False) -> bytes:
        """Bound transfer bytes independently from decoded XML size.

        XMLTV preserves gzip bytes, including HTTP gzip encoding, for the bounded
        streaming parser. XMLTV transfers are capped at 64 MiB even when the
        configured decoded-document limit is larger. Playlist behavior is kept.
        """
        if keep_gzip:
            limit = min(limit, _MAX_SOURCE_TRANSFER_BYTES)
            if self.source_cache is not None:
                return await self.source_cache.fetch(self.client, url, limit)
        async with self.client.stream("GET", url, follow_redirects=False, timeout=20, headers={"Accept-Encoding": "identity"}) as response:
            if response.status_code != 200:
                raise CatalogUnavailable()
            if keep_gzip and response.headers.get("content-encoding", "identity").lower() not in ("identity", "gzip"):
                raise CatalogUnavailable()
            body = bytearray()
            if keep_gzip and response.is_stream_consumed:
                # An injected/cached response may already have been read. Real
                # network responses use aiter_raw below to avoid HTTP decoding.
                body.extend(response.content)
            else:
                chunks = response.aiter_raw(65536) if keep_gzip else response.aiter_bytes(65536)
                async for chunk in chunks:
                    body.extend(chunk)
                    if len(body) > limit:
                        raise CatalogUnavailable()
            if len(body) > limit:
                raise CatalogUnavailable()
            return bytes(body) if keep_gzip else expand_gzip(bytes(body), limit)

    async def refresh(self) -> None:
        async with self._lock:
            try:
                playlist = await self._download(self.settings.hls_proxy_url + self.settings.playlist_path, 8 * 1024 * 1024)
                channels = parse_playlist(playlist.decode("utf-8-sig"), self.settings.hls_proxy_url)
                now = datetime.now(UTC)
                source_names: dict[str, list[str]] = {}
                raw_ids: dict[str, list[str]] = {}
                programmes = []
                for source_index, url in enumerate(self.settings.xmltv_urls):
                    raw = await self._download(url, min(self.settings.max_source_bytes, _MAX_SOURCE_TRANSFER_BYTES), keep_gzip=True)
                    try:
                        names, items = await asyncio.to_thread(parse_xmltv, raw, now, self.settings.max_source_bytes, channels=channels)
                    except InvalidSource:
                        if self.source_cache is not None:
                            await asyncio.to_thread(self.source_cache.invalidate, url)
                        raise
                    for identifier in dict.fromkeys(item.epg_id for item in items):
                        scoped = str(source_index) + "\x1f" + identifier
                        raw_ids.setdefault(identifier, []).append(scoped)
                        source_names[scoped] = names.get(identifier, [])
                    programmes.extend(Programme(str(source_index) + "\x1f" + item.epg_id, item.title, item.starts_at, item.ends_at) for item in items)
                by_name: dict[str, list[str]] = {}
                for identifier, names in source_names.items():
                    for name in names:
                        by_name.setdefault(normalize(name), []).append(identifier)
                mapping: dict[str, list[Channel]] = {}
                for channel in channels:
                    candidates = raw_ids.get(channel.epg_id, [])
                    identifier = candidates[0] if len(candidates) == 1 else ""
                    if len(candidates) > 1:
                        # IDs are local to a feed. Disambiguate collisions by channel
                        # names; the first configured matching feed has precedence.
                        wanted = {normalize(channel.epg_name), normalize(channel.name)}
                        candidates = [candidate for candidate in candidates if wanted.intersection(normalize(name) for name in source_names[candidate])]
                        identifier = candidates[0] if candidates else ""
                    if not identifier:
                        candidates = by_name.get(normalize(channel.epg_name), [])
                        if not candidates:
                            candidates = by_name.get(normalize(channel.name), [])
                        if candidates:
                            first_feed = candidates[0].split("\x1f", 1)[0]
                            same_feed = set(candidate for candidate in candidates if candidate.split("\x1f", 1)[0] == first_feed)
                            # Names reused by two IDs in one feed are ambiguous.
                            identifier = next(iter(same_feed)) if len(same_feed) == 1 else ""
                    if identifier:
                        mapping.setdefault(identifier, []).append(channel)
                matches = []
                for programme in programmes:
                    for channel in mapping.get(programme.epg_id, []):
                        delta = timedelta(seconds=channel.shift_seconds)
                        matches.append(ProgramMatch(programme.title, channel.channel_id, channel.name, programme.starts_at + delta, programme.ends_at + delta))
                if not matches:
                    raise CatalogUnavailable()
                # Publication reads must not observe a new database import with
                # names cached from the preceding generation. Downloading and
                # parsing remain outside this brief commit boundary.
                async with self._publication_lock:
                    commit = asyncio.create_task(asyncio.to_thread(
                        self.store.replace, channels, matches, time.time(),
                        source_fingerprint=self.source_fingerprint,
                    ))
                    cancelled = False
                    while True:
                        try:
                            await asyncio.shield(commit)
                            break
                        except asyncio.CancelledError:
                            # A worker thread cannot be cancelled once SQLite
                            # starts writing. Keep the publication lock until
                            # its result and generation are known, even during
                            # shutdown or repeated cancellation requests.
                            cancelled = True
                            if commit.cancelled():
                                # An event-loop shutdown may cancel every task,
                                # including the shielded worker wrapper. Never
                                # serve cached names with an unknown commit.
                                self.updated_at = None
                                self._channel_names = None
                                raise
                    self.updated_at = self.clock()
                    self.last_refresh_failed = False
                    self._channel_generation += 1
                    self._channel_names = None
                    self._imported_channel_names = [ChannelCandidate(
                        channel.channel_id, channel.name, channel.epg_name, False,
                    ) for channel in channels]
                    if cancelled:
                        raise asyncio.CancelledError
                self._schedule_channel_names()
            except Exception:
                self.last_refresh_failed = True
                LOGGER.warning("catalog_refresh_failed")
                raise CatalogUnavailable() from None

    async def run(self) -> None:
        # Warm a restored catalogue without blocking startup or a first request.
        # All callers share this task; an HTTP timeout cannot cancel its build.
        self._schedule_channel_names()
        if self.ready:
            remaining = self.settings.refresh_seconds - (self.clock() - self.updated_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
        while True:
            delay = self.settings.refresh_seconds
            try:
                await self.refresh()
            except CatalogUnavailable:
                delay = min(60, delay)
            await asyncio.sleep(delay)

    def close(self) -> None:
        self._closed = True
        if self._channel_name_task is not None:
            self._channel_name_task.cancel()
        self.store.close()

    def _schedule_channel_names(self) -> asyncio.Task | None:
        if self._closed or not self.ready or self.settings.channel_voice_mode != "automatic":
            return None
        if self._channel_names is not None and self._channel_names[0] == self._channel_generation:
            return None
        if self._channel_name_task is None or self._channel_name_task.done():
            self._channel_name_task = asyncio.create_task(self._prepare_channel_names())
            # Background warmup errors stay private and are retried on the next
            # request. Retrieving the exception avoids an unhandled-task log.
            self._channel_name_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception(),
            )
        return self._channel_name_task

    async def _prepare_channel_names(self) -> None:
        while not self._closed:
            async with self._publication_lock:
                if not self.ready or self._closed:
                    raise CatalogUnavailable()
                generation = self._channel_generation
                channels = self._imported_channel_names
                if channels is None:
                    # Restored databases need one complete snapshot. Subsequent
                    # imports reuse the labels already parsed during refresh.
                    try:
                        channels, _ = await asyncio.to_thread(
                            self.store.media_candidates, datetime.now(UTC),
                        )
                    except CatalogStorageError:
                        raise CatalogUnavailable() from None
            try:
                prepared = await asyncio.to_thread(
                    prepare_automatic_channels, channels,
                    preferred_channel_ids=self.settings.media_preferred_channel_ids,
                    aliases=self.settings.channel_aliases, limit=self.settings.channel_voice_limit,
                )
            except ValueError:
                raise CatalogUnavailable() from None
            async with self._publication_lock:
                if not self.ready or self._closed:
                    raise CatalogUnavailable()
                if generation == self._channel_generation:
                    self._channel_names = (generation, prepared)
                    return
                # A newer import committed while collision analysis was running.
                # Discard the old result and rebuild within the same shared task.
        raise CatalogUnavailable()

    async def resolve(
        self, query: str, *, now: datetime | None = None, channel_id: str | None = None,
        exact_title: bool = False,
    ) -> list[ProgramMatch]:
        if not self.ready:
            raise CatalogUnavailable()
        normalized = normalize(query)
        if not normalized or len(query) > 200:
            return []
        variants = {normalized} if exact_title else query_variants(query, self.settings.title_aliases)
        instant = now or datetime.now(UTC)
        try:
            return await asyncio.to_thread(
                self.store.resolve, variants, instant, channel_id=channel_id,
                exact_title=normalized if exact_title else None,
            )
        except CatalogStorageError:
            raise CatalogUnavailable() from None

    async def voice_catalogue(self, *, now: datetime | None = None) -> dict:
        """Return bounded current selections; unavailable data never becomes an empty list."""
        if not self.ready:
            raise CatalogUnavailable()
        try:
            candidates = await asyncio.to_thread(
                self.store.voice_candidates, now or datetime.now(UTC), self.settings.voice_channel_ids,
            )
        except CatalogStorageError:
            raise CatalogUnavailable() from None
        return build_voice_catalogue(
            candidates, self.settings.voice_preferred_queries, self.settings.title_aliases,
            self.settings.voice_catalogue_limit,
        )

    async def channel_catalogue(self, *, now: datetime | None = None) -> dict:
        """Return a bounded publication list, separate from full-catalogue search."""
        if not self.ready:
            raise CatalogUnavailable()
        if self.settings.channel_voice_mode == "automatic":
            while not self._closed:
                task = self._schedule_channel_names()
                if task is not None:
                    await asyncio.shield(task)
                async with self._publication_lock:
                    if not self.ready or self._closed:
                        raise CatalogUnavailable()
                    cached = self._channel_names
                    if cached is None or cached[0] != self._channel_generation:
                        continue
                    prepared = cached[1]
                    try:
                        current = await asyncio.to_thread(
                            self.store.channel_candidates, now or datetime.now(UTC),
                            [row.channel_id for row in prepared],
                        )
                    except CatalogStorageError:
                        raise CatalogUnavailable() from None
                    if not self.ready:
                        raise CatalogUnavailable()
                    return render_automatic_channels(prepared, current)
            raise CatalogUnavailable()
        try:
            candidates = await asyncio.to_thread(
                self.store.channel_candidates, now or datetime.now(UTC),
                self.settings.channel_voice_ids,
            )
        except CatalogStorageError:
            raise CatalogUnavailable() from None
        return build_channel_catalogue(candidates, self.settings.channel_voice_limit)

    async def media_candidates(self, *, now: datetime | None = None):
        if not self.ready:
            raise CatalogUnavailable()
        try:
            return await asyncio.to_thread(
                self.store.media_candidates, now or datetime.now(UTC),
            )
        except CatalogStorageError:
            raise CatalogUnavailable() from None

    async def search_media(self, query: str, *, now: datetime | None = None) -> dict:
        """Search all imported channels, independently of Google publication limits."""
        channels, programmes = await self.media_candidates(now=now)
        return await asyncio.to_thread(
            resolve_media, query, channels, programmes,
            aliases=self.settings.title_aliases,
            channel_aliases=self.settings.channel_aliases,
            preferred_channel_ids=self.settings.media_preferred_channel_ids,
        )

    async def media_selection(self, candidate: dict) -> list[ProgramMatch]:
        """Revalidate a selected candidate against the current guide before playback."""
        current = await self.current_on_channel(candidate["channel_id"])
        if len(current) != 1:
            return []
        if candidate["match_kind"] == "programme" and normalize(current[0].title) != normalize(candidate["title"]):
            return []
        return current

    async def current_on_channel(self, channel_id: str, *, now: datetime | None = None) -> list[ProgramMatch]:
        """Resolve a saved channel against the fresh catalogue's current schedule."""
        if not self.ready:
            raise CatalogUnavailable()
        try:
            return await asyncio.to_thread(self.store.current_on_channel, channel_id, now or datetime.now(UTC))
        except CatalogStorageError:
            raise CatalogUnavailable() from None

    def stream_url(self, match: ProgramMatch) -> str:
        if not self.ready:
            raise CatalogUnavailable()
        try:
            url = self.store.stream_url(match.channel_id)
        except CatalogStorageError:
            raise CatalogUnavailable() from None
        if url is None:
            raise CatalogUnavailable()
        return url
