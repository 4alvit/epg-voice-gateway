"""Atomic SQLite snapshots and indexed, current-programme searches.

The database contains private proxy URLs. New directories are owner-only and
database files are mode 0600; callers must keep the database out of published
artifacts and use local storage that supports SQLite locking and WAL files.
"""

import math
import os
import sqlite3
import stat
import threading
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from .channel_catalogue import MAX_CHANNEL_VOICE_IDS, ChannelCandidate
from .errors import CatalogUnavailable
from .models import Channel, ProgramMatch
from .search import normalize

_SCHEMA_VERSION = 1
_MAX_RESULTS = 20
VOICE_CANDIDATE_LIMIT = 2000


class CatalogStorageError(CatalogUnavailable):
    """A safe storage error that never includes paths, records, or SQL values."""

    code = "catalog_storage_unavailable"


def _private_database_file(path: str) -> None:
    parent = Path(path).parent
    missing = []
    candidate = parent
    while not candidate.exists():
        missing.append(candidate)
        candidate = candidate.parent
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise OSError()
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError()
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


def _timestamp(value: datetime) -> float:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise CatalogStorageError("EPG timestamps must include a time zone")
    result = value.timestamp()
    if not math.isfinite(result):
        raise CatalogStorageError("Invalid EPG timestamp")
    return result


def _channel_row(channel: Channel) -> tuple:
    if not isinstance(channel, Channel) or not all(isinstance(value, str) for value in (
        channel.channel_id, channel.name, channel.epg_id, channel.epg_name, channel.stream_url,
    )):
        raise CatalogStorageError("Invalid EPG channel record")
    if not channel.channel_id or not isinstance(channel.shift_seconds, int):
        raise CatalogStorageError("Invalid EPG channel record")
    return (
        channel.channel_id, channel.name, channel.epg_id, channel.epg_name,
        channel.stream_url, channel.shift_seconds,
    )


def _programme_row(programme: ProgramMatch) -> tuple:
    if not isinstance(programme, ProgramMatch) or not all(isinstance(value, str) for value in (
        programme.title, programme.channel_id, programme.channel_name,
    )):
        raise CatalogStorageError("Invalid EPG programme record")
    return (
        programme.channel_id, programme.title, normalize(programme.title),
        programme.channel_name, programme.channel_name.casefold(),
        _timestamp(programme.starts_at), _timestamp(programme.ends_at),
    )


class CatalogStore:
    """One serialized SQLite connection with durable, atomic catalogue refreshes."""

    def __init__(self, path: str) -> None:
        self._lock = threading.RLock()
        self._connection: sqlite3.Connection | None = None
        try:
            if not isinstance(path, str) or not path or "\x00" in path:
                raise CatalogStorageError("A valid EPG database path is required")
            if path != ":memory:":
                _private_database_file(path)
            connection = sqlite3.connect(
                path, timeout=5, check_same_thread=False, isolation_level=None,
            )
            self._connection = connection
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 5000")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA synchronous = FULL")
            if path != ":memory:":
                mode = connection.execute("PRAGMA journal_mode = WAL").fetchone()[0]
                if mode.lower() != "wal":
                    raise CatalogStorageError("The EPG database requires SQLite WAL support")
            if connection.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                raise CatalogStorageError("The EPG database is damaged")
            self._require_fts5(connection)
            self._initialize_schema(connection)
            # Validate persisted freshness metadata before accepting a restart.
            _ = self.refreshed_at
            _ = self.source_fingerprint
        except CatalogStorageError:
            self.close()
            raise
        except (sqlite3.Error, OSError, ValueError):
            self.close()
            raise CatalogStorageError("The EPG database could not be opened or initialized") from None

    def __repr__(self) -> str:
        return "<CatalogStore>"

    @staticmethod
    def _require_fts5(connection: sqlite3.Connection) -> None:
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE temp.epg_fts_probe USING fts5(title, tokenize='unicode61')"
            )
            connection.execute("DROP TABLE temp.epg_fts_probe")
        except sqlite3.Error:
            raise CatalogStorageError("SQLite FTS5 support is required for EPG search") from None

    @staticmethod
    def _initialize_schema(connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, _SCHEMA_VERSION):
            raise CatalogStorageError("The EPG database schema version is unsupported")
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id TEXT PRIMARY KEY CHECK(channel_id != ''),
                    name TEXT NOT NULL,
                    epg_id TEXT NOT NULL,
                    epg_name TEXT NOT NULL,
                    stream_url TEXT NOT NULL,
                    shift_seconds INTEGER NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS programmes (
                    id INTEGER PRIMARY KEY,
                    channel_id TEXT NOT NULL REFERENCES channels(channel_id),
                    title TEXT NOT NULL,
                    normalized_title TEXT NOT NULL,
                    channel_name TEXT NOT NULL,
                    channel_sort TEXT NOT NULL,
                    starts_at REAL NOT NULL,
                    ends_at REAL NOT NULL CHECK(ends_at > starts_at),
                    UNIQUE(channel_id, starts_at)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY CHECK(key IN ('refreshed_at', 'source_fingerprint')),
                    value TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS programmes_channel_time
                ON programmes(channel_id, starts_at, ends_at)
            """)
            connection.execute("""
                CREATE INDEX IF NOT EXISTS programmes_current_window
                ON programmes(ends_at, starts_at)
            """)
            connection.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS programme_fts USING fts5(
                    normalized_title, content='programmes', content_rowid='id',
                    tokenize='unicode61 remove_diacritics 0'
                )
            """)
            connection.execute(
                "INSERT INTO programme_fts(programme_fts, rank) VALUES('integrity-check', 1)"
            )
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            with suppress(sqlite3.Error):
                connection.rollback()
            raise

    def _open_connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise CatalogStorageError("The EPG database is closed")
        return self._connection

    @property
    def refreshed_at(self) -> float | None:
        with self._lock:
            try:
                row = self._open_connection().execute(
                    "SELECT value FROM metadata WHERE key = 'refreshed_at'"
                ).fetchone()
                if row is None:
                    return None
                value = float(row[0])
                if not math.isfinite(value) or value < 0:
                    raise CatalogStorageError("EPG refresh metadata is invalid")
                return value
            except (sqlite3.Error, TypeError, ValueError, OverflowError):
                raise CatalogStorageError("EPG refresh metadata could not be read") from None

    @property
    def source_fingerprint(self) -> str | None:
        with self._lock:
            try:
                row = self._open_connection().execute(
                    "SELECT value FROM metadata WHERE key = 'source_fingerprint'"
                ).fetchone()
                if row is not None and not isinstance(row[0], str):
                    raise CatalogStorageError("EPG source metadata is invalid")
                return row[0] if row is not None else None
            except sqlite3.Error:
                raise CatalogStorageError("EPG source metadata could not be read") from None

    def replace(
        self, channels: list[Channel], programmes: list[ProgramMatch], refreshed_at: float,
        *, source_fingerprint: str | None = None,
    ) -> None:
        """Commit channels, programmes, index, and wall-clock freshness together.

        Repeated channel/start pairs use the last input programme, matching the
        existing catalogue's dictionary semantics. No intermediate snapshot is
        visible to readers; any validation or SQLite failure keeps the old one.
        """
        if (
            not isinstance(refreshed_at, (int, float)) or isinstance(refreshed_at, bool)
            or not math.isfinite(refreshed_at) or refreshed_at < 0
        ):
            raise CatalogStorageError("Invalid EPG refresh timestamp")
        if source_fingerprint is not None and not isinstance(source_fingerprint, str):
            raise CatalogStorageError("Invalid EPG source fingerprint")
        with self._lock:
            connection = self._open_connection()
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("INSERT INTO programme_fts(programme_fts) VALUES('delete-all')")
                connection.execute("DELETE FROM programmes")
                connection.execute("DELETE FROM channels")
                connection.executemany("""
                    INSERT INTO channels(channel_id, name, epg_id, epg_name, stream_url, shift_seconds)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(channel_id) DO UPDATE SET
                        name=excluded.name, epg_id=excluded.epg_id, epg_name=excluded.epg_name,
                        stream_url=excluded.stream_url, shift_seconds=excluded.shift_seconds
                """, (_channel_row(channel) for channel in channels))
                connection.executemany("""
                    INSERT INTO programmes(
                        channel_id, title, normalized_title, channel_name, channel_sort,
                        starts_at, ends_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(channel_id, starts_at) DO UPDATE SET
                        title=excluded.title, normalized_title=excluded.normalized_title,
                        channel_name=excluded.channel_name, channel_sort=excluded.channel_sort,
                        ends_at=excluded.ends_at
                """, (_programme_row(programme) for programme in programmes))
                connection.execute("INSERT INTO programme_fts(programme_fts) VALUES('rebuild')")
                connection.execute("""
                    INSERT INTO metadata(key, value) VALUES('refreshed_at', ?)
                    ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """, (float(refreshed_at),))
                if source_fingerprint is None:
                    connection.execute("DELETE FROM metadata WHERE key = 'source_fingerprint'")
                else:
                    connection.execute("""
                        INSERT INTO metadata(key, value) VALUES('source_fingerprint', ?)
                        ON CONFLICT(key) DO UPDATE SET value=excluded.value
                    """, (source_fingerprint,))
                connection.commit()
            except Exception as error:
                with suppress(sqlite3.Error):
                    connection.rollback()
                if isinstance(error, CatalogStorageError):
                    raise
                raise CatalogStorageError("The EPG snapshot could not be stored") from None

    def resolve(
        self, variants: set[str], instant: datetime, limit: int = _MAX_RESULTS,
        *, channel_id: str | None = None, exact_title: str | None = None,
    ) -> list[ProgramMatch]:
        """Return current phrase matches, sorted by case-folded channel and start.

        FTS narrows candidate rows. The normalized phrase guard preserves exact
        whole-phrase semantics, independently of the tokenizer's Unicode rules.
        Zero results may be requested; positive limits are capped at twenty.
        """
        if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
            raise CatalogStorageError("Invalid EPG search limit")
        if channel_id is not None and (not isinstance(channel_id, str) or not channel_id):
            raise CatalogStorageError("Invalid EPG channel request")
        if exact_title is not None and (not isinstance(exact_title, str) or not exact_title):
            raise CatalogStorageError("Invalid exact EPG title")
        try:
            phrases = sorted({phrase for value in variants if (phrase := normalize(value))})
            timestamp = _timestamp(instant)
        except (TypeError, AttributeError, ValueError, OverflowError):
            raise CatalogStorageError("Invalid EPG search request") from None
        if not phrases or limit == 0:
            return []
        match_expression = " OR ".join('"' + phrase.replace('"', '""') + '"' for phrase in phrases)
        guards = " OR ".join("instr(' ' || p.normalized_title || ' ', ?) > 0" for _ in phrases)
        query = f"""
            SELECT p.title, p.channel_id, p.channel_name, p.starts_at, p.ends_at
            FROM programme_fts JOIN programmes AS p ON p.id = programme_fts.rowid
            WHERE programme_fts MATCH ? AND p.starts_at <= ? AND p.ends_at > ?
                AND ({guards})
                {"AND p.channel_id = ?" if channel_id is not None else ""}
                {"AND p.normalized_title = ?" if exact_title is not None else ""}
            ORDER BY p.channel_sort, p.starts_at, p.channel_id
            LIMIT ?
        """
        parameters = [
            match_expression, timestamp, timestamp,
            *(" " + phrase + " " for phrase in phrases),
            *([channel_id] if channel_id is not None else []),
            *([normalize(exact_title)] if exact_title is not None else []), min(limit, _MAX_RESULTS),
        ]
        with self._lock:
            try:
                rows = self._open_connection().execute(query, parameters).fetchall()
                return [ProgramMatch(
                    row["title"], row["channel_id"], row["channel_name"],
                    datetime.fromtimestamp(row["starts_at"], UTC),
                    datetime.fromtimestamp(row["ends_at"], UTC),
                ) for row in rows]
            except (sqlite3.Error, ValueError, OverflowError):
                raise CatalogStorageError("EPG programmes could not be searched") from None

    def voice_candidates(self, instant: datetime, channel_ids: list[str]) -> list[ProgramMatch]:
        """Read at most 2,000 unambiguous current rows through the time index.

        A configured channel list is an ordered allowlist. Otherwise channel
        name and ID determine selection before truncation. Every overlapping
        channel is excluded, including overlaps beyond the candidate limit.
        Titles and stream URLs are never interpolated into SQL.
        """
        if (
            not isinstance(channel_ids, list) or len(channel_ids) > 200
            or any(not isinstance(value, str) or not value for value in channel_ids)
        ):
            raise CatalogStorageError("Invalid voice channel request")
        try:
            timestamp = _timestamp(instant)
        except (TypeError, AttributeError, ValueError, OverflowError):
            raise CatalogStorageError("Invalid voice catalogue request") from None
        allowed = " AND p.channel_id IN (" + ",".join("?" for _ in channel_ids) + ")" if channel_ids else ""
        priority = (
            "CASE p.channel_id " + " ".join("WHEN ? THEN " + str(index) for index, _ in enumerate(channel_ids)) + " END, "
            if channel_ids else ""
        )
        query = f"""
            SELECT p.title, p.channel_id, p.channel_name, p.starts_at, p.ends_at
            FROM programmes AS p INDEXED BY programmes_current_window
            WHERE p.starts_at <= ? AND p.ends_at > ? {allowed}
                AND NOT EXISTS (
                    SELECT 1 FROM programmes AS other
                    WHERE other.channel_id = p.channel_id AND other.id != p.id
                        AND other.starts_at <= ? AND other.ends_at > ?
                )
            ORDER BY {priority} p.channel_sort, p.channel_id, p.starts_at
            LIMIT ?
        """
        parameters = [timestamp, timestamp, *channel_ids, timestamp, timestamp, *channel_ids, VOICE_CANDIDATE_LIMIT]
        with self._lock:
            try:
                rows = self._open_connection().execute(query, parameters).fetchall()
                return [ProgramMatch(
                    row["title"], row["channel_id"], row["channel_name"],
                    datetime.fromtimestamp(row["starts_at"], UTC),
                    datetime.fromtimestamp(row["ends_at"], UTC),
                ) for row in rows]
            except (sqlite3.Error, ValueError, OverflowError):
                raise CatalogStorageError("The voice catalogue could not be read") from None

    def channel_candidates(
        self, instant: datetime, channel_ids: list[str],
    ) -> list[ChannelCandidate]:
        """Read only an explicit lineup and at most two current rows per channel.

        The primary key bounds channel reads to 200 configured IDs; the existing
        channel/time index bounds each current-count probe. No stream URL,
        provider EPG ID, programme title, or source metadata is selected.
        An empty allowlist deliberately publishes no channels.
        """
        if (
            not isinstance(channel_ids, list) or len(channel_ids) > MAX_CHANNEL_VOICE_IDS
            or any(not isinstance(value, str) or not value for value in channel_ids)
        ):
            raise CatalogStorageError("Invalid channel voice request")
        try:
            timestamp = _timestamp(instant)
        except (TypeError, AttributeError, ValueError, OverflowError):
            raise CatalogStorageError("Invalid channel catalogue request") from None
        channel_ids = list(dict.fromkeys(channel_ids))
        with self._lock:
            try:
                connection = self._open_connection()
                if not channel_ids:
                    return []
                placeholders = ",".join("?" for _ in channel_ids)
                priority = " ".join(
                    "WHEN ? THEN " + str(index) for index, _ in enumerate(channel_ids)
                )
                rows = connection.execute(f"""
                    SELECT c.channel_id, c.name, c.epg_name, (
                        SELECT COUNT(*) FROM (
                            SELECT 1 FROM programmes AS p INDEXED BY programmes_channel_time
                            WHERE p.channel_id = c.channel_id
                                AND p.starts_at <= ? AND p.ends_at > ?
                            LIMIT 2
                        )
                    ) AS current_count
                    FROM channels AS c
                    WHERE c.channel_id IN ({placeholders})
                    ORDER BY CASE c.channel_id {priority} END
                """, (timestamp, timestamp, *channel_ids, *channel_ids)).fetchall()
                return [ChannelCandidate(
                    row["channel_id"], row["name"], row["epg_name"], row["current_count"] == 1,
                ) for row in rows]
            except (sqlite3.Error, ValueError, OverflowError):
                raise CatalogStorageError("The channel catalogue could not be read") from None

    def current_on_channel(self, channel_id: str, instant: datetime) -> list[ProgramMatch]:
        """Read at most twenty current programmes through the channel/time index.

        Overlapping guide entries remain separate so callers can reject an
        ambiguous schedule instead of choosing a programme silently.
        """
        if not isinstance(channel_id, str) or not channel_id:
            raise CatalogStorageError("Invalid EPG channel request")
        try:
            timestamp = _timestamp(instant)
        except (TypeError, AttributeError, ValueError, OverflowError):
            raise CatalogStorageError("Invalid EPG channel request") from None
        with self._lock:
            try:
                rows = self._open_connection().execute("""
                    SELECT title, channel_id, channel_name, starts_at, ends_at
                    FROM programmes
                    WHERE channel_id = ? AND starts_at <= ? AND ends_at > ?
                    ORDER BY starts_at
                    LIMIT ?
                """, (channel_id, timestamp, timestamp, _MAX_RESULTS)).fetchall()
                return [ProgramMatch(
                    row["title"], row["channel_id"], row["channel_name"],
                    datetime.fromtimestamp(row["starts_at"], UTC),
                    datetime.fromtimestamp(row["ends_at"], UTC),
                ) for row in rows]
            except (sqlite3.Error, ValueError, OverflowError):
                raise CatalogStorageError("The current EPG programme could not be read") from None

    def stream_url(self, channel_id: str) -> str | None:
        with self._lock:
            try:
                row = self._open_connection().execute(
                    "SELECT stream_url FROM channels WHERE channel_id = ?", (channel_id,),
                ).fetchone()
                return row[0] if row is not None else None
            except sqlite3.Error:
                raise CatalogStorageError("The EPG channel could not be read") from None

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                with suppress(sqlite3.Error):
                    self._connection.close()
                self._connection = None
