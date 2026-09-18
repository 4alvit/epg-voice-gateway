"""Persistent and atomic indexed EPG storage, using synthetic catalogue data."""

import os
import sqlite3
import stat
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime, timedelta, timezone

import pytest

from epg_voice_gateway import storage
from epg_voice_gateway.models import Channel, ProgramMatch
from epg_voice_gateway.storage import CatalogStorageError, CatalogStore

NOW = datetime(2026, 1, 1, 12, tzinfo=UTC)
PRIVATE_URL = "http://proxy.example.test/channel/kids.m3u8?token=synthetic-private-token"


def channel(identifier="kids", name="Kids"):
    return Channel(identifier, name, identifier, name, PRIVATE_URL)


def programme(title="Кукутики", identifier="kids", name="Kids", *, start=None, end=None):
    return ProgramMatch(
        title, identifier, name,
        start if start is not None else NOW - timedelta(minutes=5),
        end if end is not None else NOW + timedelta(minutes=25),
    )


def assert_private_directory(path):
    mode = stat.S_IMODE(path.stat().st_mode)
    # Linux may inherit setgid from the volume without granting group access.
    assert mode & 0o777 == 0o700
    assert mode & (stat.S_ISUID | stat.S_ISVTX) == 0


@pytest.fixture
def store():
    with closing(CatalogStore(":memory:")) as database:
        yield database


def test_empty_database_has_no_freshness_channels_or_matches(store):
    assert store.refreshed_at is None
    assert store.source_fingerprint is None
    assert store.stream_url("missing") is None
    assert store.resolve({"Кукутики"}, NOW) == []


def test_restart_recovers_programmes_urls_and_wall_clock_metadata(tmp_path):
    path = tmp_path / "private" / "nested" / "guide.sqlite3"
    match = programme()
    with closing(CatalogStore(str(path))) as first:
        first.replace([channel()], [match], 123456.25, source_fingerprint="a" * 64)
    with closing(CatalogStore(str(path))) as restarted:
        assert restarted.refreshed_at == 123456.25
        assert restarted.source_fingerprint == "a" * 64
        assert restarted.resolve({"кукутики"}, NOW) == [match]
        assert restarted.stream_url("kids") == PRIVATE_URL
        assert "private" not in repr(restarted)
        assert "synthetic-private" not in repr(restarted.resolve({"кукутики"}, NOW))
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert_private_directory(path.parent)
    assert_private_directory(path.parent.parent)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux directory setgid inheritance")
def test_setgid_volume_preserves_inheritance_without_granting_group_access(tmp_path):
    directory = tmp_path / "setgid-volume"
    directory.mkdir(mode=0o700)
    os.chmod(directory, 0o2700)
    path = directory / "private" / "nested" / "guide.sqlite3"
    match = programme()

    with closing(CatalogStore(str(path))) as database:
        database.replace([channel()], [match], 1)
        assert database.resolve({"кукутики"}, NOW) == [match]
        assert stat.S_IMODE(directory.stat().st_mode) == 0o2700
        for created in (path.parent, path.parent.parent):
            assert_private_directory(created)
            assert created.stat().st_mode & stat.S_ISGID
        files = list(path.parent.iterdir())
        assert {item.name for item in files} == {
            "guide.sqlite3", "guide.sqlite3-wal", "guide.sqlite3-shm",
        }
        for item in files:
            assert stat.S_IMODE(item.stat().st_mode) == 0o600


def test_existing_parent_permissions_are_preserved_and_database_is_private(tmp_path):
    directory = tmp_path / "existing-volume"
    directory.mkdir(mode=0o750)
    os.chmod(directory, 0o750)
    path = directory / "guide.sqlite3"
    path.touch(mode=0o644)
    with closing(CatalogStore(str(path))) as database:
        database.replace([channel()], [programme()], 1)
        assert stat.S_IMODE(directory.stat().st_mode) == 0o750
        for item in directory.iterdir():
            assert stat.S_IMODE(item.stat().st_mode) == 0o600
        with sqlite3.connect(path) as inspect:
            assert inspect.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_failed_replacement_rolls_back_partial_rows_fts_and_freshness(tmp_path):
    path = str(tmp_path / "guide.sqlite3")
    with closing(CatalogStore(path)) as database:
        database.replace([channel()], [programme("Previous show")], 10, source_fingerprint="old")
        with pytest.raises(CatalogStorageError) as failed:
            database.replace(
                [channel("new", "New")],
                [programme("New show", "new", "New"), programme("Invalid", "missing")],
                20, source_fingerprint="new",
            )
        assert "synthetic-private" not in str(failed.value)
        assert database.refreshed_at == 10
        assert database.source_fingerprint == "old"
        assert database.resolve({"Previous show"}, NOW) == [programme("Previous show")]
        assert database.resolve({"New show"}, NOW) == []
        assert database.stream_url("new") is None
    with closing(CatalogStore(path)) as restarted:
        assert restarted.refreshed_at == 10
        assert restarted.source_fingerprint == "old"
        assert len(restarted.resolve({"Previous show"}, NOW)) == 1


def test_successful_replacement_removes_old_index_rows_and_optional_source_binding(store):
    store.replace([channel()], [programme("Old show")], 1, source_fingerprint="old")
    store.replace([channel()], [programme("New show")], 2)
    assert store.resolve({"Old show"}, NOW) == []
    assert store.resolve({"New show"}, NOW) == [programme("New show")]
    assert store.refreshed_at == 2
    assert store.source_fingerprint is None


def test_repeated_channel_start_uses_last_programme_and_updates_index(store):
    last = programme("Final title", end=NOW + timedelta(minutes=40))
    store.replace([channel()], [programme("Earlier title"), last], 1)
    assert store.resolve({"Earlier title"}, NOW) == []
    assert store.resolve({"Final title"}, NOW) == [last]


def test_search_requires_current_programme_including_exact_start_excluding_exact_end(store):
    records = [
        programme("Show", "current", "Current", start=NOW),
        programme("Show", "ended", "Ended", end=NOW),
        programme("Show", "future", "Future", start=NOW + timedelta(seconds=1)),
    ]
    store.replace([channel(item.channel_id, item.channel_name) for item in records], records, 1)
    assert store.resolve({"Show"}, NOW) == [records[0]]
    assert store.resolve({"Show"}, NOW.astimezone(timezone(timedelta(hours=3)))) == [records[0]]


def test_saved_channel_tracks_programme_changes_with_exact_time_boundaries(store):
    previous = programme("Previous", start=NOW - timedelta(hours=1), end=NOW)
    current = programme("Current", start=NOW, end=NOW + timedelta(minutes=30))
    following = programme("Next", start=current.ends_at, end=NOW + timedelta(hours=1))
    unrelated = programme("Current", "other", "Other")
    store.replace([channel(), channel("other", "Other"), channel("unscheduled")],
                  [previous, current, following, unrelated], 1)

    assert store.current_on_channel("kids", NOW) == [current]
    assert store.current_on_channel("kids", NOW.astimezone(timezone(timedelta(hours=3)))) == [current]
    assert store.current_on_channel("kids", current.ends_at) == [following]
    assert store.current_on_channel("kids", following.ends_at) == []
    assert store.current_on_channel("unscheduled", NOW) == []
    assert store.current_on_channel("unknown", NOW) == []
    assert store.current_on_channel("kids' OR 1=1 --", NOW) == []


def test_saved_channel_preserves_ambiguity_and_bounds_overlapping_results(store):
    records = [programme(f"Overlapping {number}", start=NOW - timedelta(minutes=number + 1))
               for number in range(25)]
    store.replace([channel()], records, 1)
    matches = store.current_on_channel("kids", NOW)
    assert len(matches) == 20
    assert matches == list(reversed(records))[:20]


@pytest.mark.parametrize("query,expected", [
    ("кукутики", True), ("ЕЛКА КУКУТИКИ", True), ("Кукутики: песенки", True),
    ("кукут", False), ("кукутики песня", False), ("песенки кукутики", False),
])
def test_russian_normalization_and_whole_ordered_phrase_matching(store, query, expected):
    match = programme("Ёлка — КУКУТИКИ. Песенки")
    store.replace([channel()], [match], 1)
    assert bool(store.resolve({query}, NOW)) is expected


def test_multiple_aliases_match_without_duplicate_results(store):
    records = [programme("Кукутики"), programme("Kukutiki", "latin", "Latin")]
    store.replace([channel(), channel("latin", "Latin")], records, 1)
    assert store.resolve({"Кукутики", "кукутики", "Kukutiki"}, NOW) == records


def test_punctuation_normalizes_but_does_not_turn_into_fts_operators(store):
    records = [
        programme("Tom & Jerry"),
        programme("Kukutiki", "one", "One"),
        programme("News", "two", "Two"),
        programme("Kukutiki OR News", "literal", "Literal"),
    ]
    store.replace([channel(item.channel_id, item.channel_name) for item in records], records, 1)
    assert store.resolve({"tom-jerry"}, NOW) == [records[0]]
    assert store.resolve({'Kukutiki" OR "News'}, NOW) == [records[3]]
    assert store.resolve({"'; DROP TABLE channels; --"}, NOW) == []
    assert store.resolve({"NEAR(Kukutiki News)"}, NOW) == []
    assert store.resolve({'"*"'}, NOW) == []
    assert store.stream_url("kids") == PRIVATE_URL


def test_latin_diacritics_preserve_existing_normalized_phrase_semantics(store):
    store.replace([channel()], [programme("Café"), programme("Cafe", start=NOW)], 1)
    assert [match.title for match in store.resolve({"Cafe"}, NOW)] == ["Cafe"]
    assert [match.title for match in store.resolve({"Café"}, NOW)] == ["Café"]


def test_result_sorting_and_limits_are_bounded(store):
    records = [
        programme("Show", f"channel-{number:02}", f"Channel {number:02}")
        for number in reversed(range(25))
    ]
    store.replace([channel(item.channel_id, item.channel_name) for item in records], records, 1)
    assert len(store.resolve({"Show"}, NOW, limit=10000)) == 20
    assert [match.channel_id for match in store.resolve({"Show"}, NOW, limit=3)] == [
        "channel-00", "channel-01", "channel-02",
    ]
    assert store.resolve({"Show"}, NOW, limit=0) == []


def test_channel_sort_uses_unicode_casefold_then_start(store):
    records = [
        programme("Show", "b", "Бета"),
        programme("Show", "a", "альфа", start=NOW - timedelta(minutes=1)),
        programme("Show", "a", "альфа", start=NOW - timedelta(minutes=2)),
    ]
    store.replace([channel("a", "альфа"), channel("b", "Бета")], records, 1)
    assert store.resolve({"Show"}, NOW) == [records[2], records[1], records[0]]


@pytest.mark.parametrize("record", [
    programme(start=NOW.replace(tzinfo=None)),
    programme(end=NOW - timedelta(hours=1)),
])
def test_invalid_programme_does_not_destroy_last_good_snapshot(store, record):
    store.replace([channel()], [programme("Previous")], 1)
    with pytest.raises(CatalogStorageError):
        store.replace([channel()], [record], 2)
    assert store.refreshed_at == 1
    assert len(store.resolve({"Previous"}, NOW)) == 1


@pytest.mark.parametrize("timestamp", [float("nan"), float("inf"), -1, True, "private-token"])
def test_invalid_refresh_timestamp_is_rejected_without_echoing_input(store, timestamp):
    with pytest.raises(CatalogStorageError) as error:
        store.replace([channel()], [programme()], timestamp)
    assert "private-token" not in str(error.value)
    assert store.refreshed_at is None


def test_concurrent_searches_observe_complete_snapshots(store):
    channels = [channel(str(number), f"Channel {number:02}") for number in range(25)]

    def records(version):
        return [programme(f"Show {version}", item.channel_id, item.name) for item in channels]

    store.replace(channels, records("old"), 1)

    def refresh():
        for number in range(12):
            store.replace(channels, records(str(number)), number + 2)

    def search():
        for _ in range(15):
            matches = store.resolve({"Show"}, NOW)
            assert len(matches) == 20
            assert len({match.title for match in matches}) == 1

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(refresh), *(pool.submit(search) for _ in range(3))]
        for future in futures:
            future.result(timeout=10)


def test_missing_fts5_fails_at_startup_with_actionable_safe_message(monkeypatch):
    connect = sqlite3.connect

    def without_virtual_tables(*args, **kwargs):
        connection = connect(*args, **kwargs)

        def authorizer(operation, *args):
            return sqlite3.SQLITE_DENY if operation == sqlite3.SQLITE_CREATE_VTABLE else sqlite3.SQLITE_OK

        connection.set_authorizer(authorizer)
        return connection

    monkeypatch.setattr(storage.sqlite3, "connect", without_virtual_tables)
    with pytest.raises(CatalogStorageError, match="SQLite FTS5 support is required"):
        CatalogStore(":memory:")


def test_corrupt_database_error_never_exposes_path_or_stored_credentials(tmp_path):
    path = tmp_path / "synthetic-private-database.sqlite3"
    original = PRIVATE_URL.encode() * 20
    path.write_bytes(original)
    with pytest.raises(CatalogStorageError) as error:
        CatalogStore(str(path))
    rendered = "".join(traceback.format_exception(error.value))
    assert str(path) not in str(error.value)
    assert "synthetic-private-token" not in rendered
    assert path.read_bytes() == original


def test_corrupt_freshness_metadata_is_rejected_without_echoing_value(tmp_path):
    path = tmp_path / "guide.sqlite3"
    with closing(CatalogStore(str(path))) as database:
        database.replace([channel()], [programme()], 1)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE metadata SET value = ? WHERE key = 'refreshed_at'", (PRIVATE_URL,))
    with pytest.raises(CatalogStorageError) as error:
        CatalogStore(str(path))
    assert PRIVATE_URL not in str(error.value)
    assert "synthetic-private-token" not in "".join(traceback.format_exception(error.value))


def test_unknown_schema_version_is_not_overwritten(tmp_path):
    path = tmp_path / "guide.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(CatalogStorageError, match="schema version is unsupported"):
        CatalogStore(str(path))
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999


def test_close_is_idempotent_and_operations_fail_with_safe_message():
    database = CatalogStore(":memory:")
    database.close()
    database.close()
    with pytest.raises(CatalogStorageError, match="database is closed"):
        database.stream_url("kids")
    with pytest.raises(CatalogStorageError, match="database is closed"):
        database.resolve({"Show"}, NOW)
