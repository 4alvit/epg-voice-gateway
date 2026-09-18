from urllib.parse import urlsplit

import pytest

from epg_voice_gateway.media import MediaError, MediaSessionStore

PROXY = "http://hls-proxy.example.test:8080"
PUBLIC = "https://media.example.test"
MASTER = f"{PROXY}/channel/demo/index.m3u8?credential=synthetic-secret"
KEY = b"synthetic-signing-key-for-unit-tests-only"


@pytest.fixture
def clock():
    return [1000.0]


@pytest.fixture
def store(clock):
    return MediaSessionStore(PROXY, PUBLIC, KEY, clock=lambda: clock[0])


def parts(url):
    path = urlsplit(url).path.split("/")
    return path[-2], path[-1]


def test_master_and_nested_media_resources_remain_opaque_and_session_bound(store):
    playback = store.create(MASTER)
    master = '#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",URI="audio.m3u8"\n#EXT-X-STREAM-INF:BANDWIDTH=1000\nvideo.m3u8\n'
    rewritten = store.rewrite_manifest(playback.token, MASTER, master)
    video_url = rewritten.splitlines()[-1]
    video_resource = store.resolve(*parts(video_url))
    assert video_resource.upstream_url == f"{PROXY}/channel/demo/video.m3u8"
    assert video_resource.content_kind == "manifest"
    media = f'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="{PROXY}/key/demo/a.key?sig=synthetic"\n#EXT-X-MAP:URI="/map/demo/init.mp4",BYTERANGE="64@0"\n#EXTINF:10,\n{PROXY}/ts/demo/one.ts?sig=synthetic\n#EXT-X-ENDLIST\n'
    result = store.rewrite_manifest(playback.token, video_resource.upstream_url, media)
    assert PROXY not in rewritten + result
    assert "synthetic" not in rewritten + result + playback.url
    assert "credential" not in playback.url
    assert 'BYTERANGE="64@0"' in result
    urls = [line for line in result.splitlines() if not line.startswith("#")]
    urls += [line.split('URI="', 1)[1].split('"', 1)[0] for line in result.splitlines() if 'URI="' in line]
    resources = [store.resolve(*parts(url)) for url in urls]
    assert {item.content_kind for item in resources} == {"segment", "key", "map"}
    assert all(url.startswith(f"{PUBLIC}/media/{playback.token}/") for url in urls)
    other = store.create(MASTER)
    for url in urls:
        with pytest.raises(MediaError, match="media_resource_not_found"):
            store.resolve(other.token, parts(url)[1])


def test_signature_tampering_and_expiry_changes_are_rejected(store):
    playback = store.create(MASTER)
    _, name = parts(playback.url)
    items = playback.token.split(".")
    items[2] = str(int(items[2]) + 10)
    with pytest.raises(MediaError, match="invalid_media_session"):
        store.resolve(".".join(items), name)
    other_key = MediaSessionStore(PROXY, PUBLIC, b"a-different-signing-key-for-testing")
    with pytest.raises(MediaError, match="invalid_media_session"):
        other_key.resolve(playback.token, name)
    assert "synthetic" not in repr(playback)
    assert "synthetic" not in repr(store.resolve(playback.token, name))


def test_expiry_does_not_extend_when_live_playlist_refreshes(store, clock):
    playback = store.create(MASTER)
    clock[0] = playback.expires_at - 1
    rewritten = store.rewrite_manifest(playback.token, MASTER, "#EXTM3U\n/ts/demo/new.ts\n")
    segment = rewritten.splitlines()[-1]
    assert store.resolve(*parts(segment)).content_kind == "segment"
    clock[0] = playback.expires_at
    with pytest.raises(MediaError, match="media_session_expired") as error:
        store.resolve(*parts(segment))
    assert error.value.status_code == 410


def test_revoke_and_restart_invalidate_entire_session(store, clock):
    playback = store.create(MASTER)
    store.revoke(playback.token)
    with pytest.raises(MediaError, match="media_session_not_found"):
        store.resolve(*parts(playback.url))
    playback = store.create(MASTER)
    restarted = MediaSessionStore(PROXY, PUBLIC, KEY, clock=lambda: clock[0])
    with pytest.raises(MediaError, match="media_session_not_found"):
        restarted.resolve(*parts(playback.url))


@pytest.mark.parametrize("url", [
    "https://other.example.test/ts/one.ts",
    "http://hls-proxy.example.test:8081/ts/one.ts",
    "http://hls-proxy.example.test:0/ts/one.ts",
    "https://hls-proxy.example.test:8080/ts/one.ts",
    "http://127.0.0.1:8080/ts/one.ts",
    "http://user:password@hls-proxy.example.test:8080/ts/one.ts",
    "file:///ts/one.ts",
    f"{PROXY}/admin/config",
    f"{PROXY}/channel/../admin",
    f"{PROXY}/channel/%2e%2e/admin",
    f"{PROXY}/channel/%252e%252e/admin",
    f"{PROXY}/channel/%2f..%2fadmin",
    f"{PROXY}/channel/%5c..%5cadmin",
    f"{PROXY}/channel/one.m3u8#fragment",
    f"{PROXY}/channel/one.m3u8\r\nInjected: true",
])
def test_initial_url_allowlist_blocks_unsafe_origins_paths_and_parser_ambiguity(store, url):
    with pytest.raises(MediaError) as error:
        store.create(url)
    assert url not in str(error.value)


@pytest.mark.parametrize("reference", [
    "https://other.example.test/ts/one.ts",
    "//other.example.test/ts/one.ts",
    "../admin.m3u8",
    "%2e%2e/admin.m3u8",
    "/admin/config",
    "http://user:password@hls-proxy.example.test:8080/ts/one.ts",
    "data:application/octet-stream;base64,AAAA",
    "{$unsafe}/one.ts",
    "http://127.0.0.1:8080/ts/one.ts",
])
def test_manifest_references_fail_closed_for_lines_and_uri_attributes(store, reference):
    playback = store.create(MASTER)
    for text in (f"#EXTM3U\n{reference}\n", f'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="{reference}"\n'):
        with pytest.raises(MediaError):
            store.rewrite_manifest(playback.token, MASTER, text)


def test_source_manifest_must_belong_to_session(store):
    playback = store.create(MASTER)
    with pytest.raises(MediaError, match="manifest_not_in_session"):
        store.rewrite_manifest(playback.token, f"{PROXY}/play/other.m3u8", "#EXTM3U\n/ts/one.ts\n")
    with pytest.raises(MediaError, match="media_resource_not_found"):
        store.resolve(playback.token, f"{PROXY}/ts/one.ts")


def test_repeated_manifest_refresh_deduplicates_resources(clock):
    store = MediaSessionStore(PROXY, PUBLIC, KEY, max_resources_per_session=2, clock=lambda: clock[0])
    playback = store.create(MASTER)
    manifest = "#EXTM3U\n/ts/demo/one.ts\n"
    first = store.rewrite_manifest(playback.token, MASTER, manifest)
    assert store.rewrite_manifest(playback.token, MASTER, manifest) == first
    with pytest.raises(MediaError, match="media_resource_capacity_exceeded"):
        store.rewrite_manifest(playback.token, MASTER, "#EXTM3U\n/ts/demo/two.ts\n")
    assert store.resolve(*parts(first.splitlines()[-1])).content_kind == "segment"


def test_failed_manifest_validation_does_not_consume_resource_capacity(clock):
    store = MediaSessionStore(PROXY, PUBLIC, KEY, max_resources_per_session=2, clock=lambda: clock[0])
    playback = store.create(MASTER)
    with pytest.raises(MediaError):
        store.rewrite_manifest(playback.token, MASTER, "#EXTM3U\n/ts/demo/one.ts\nhttps://other.example.test/no.ts\n")
    assert "/media/" in store.rewrite_manifest(playback.token, MASTER, "#EXTM3U\n/ts/demo/two.ts\n")


def test_capacity_does_not_evict_live_sessions_and_expired_space_is_reused(clock):
    store = MediaSessionStore(PROXY, PUBLIC, KEY, lifetime_seconds=2, max_sessions=1, clock=lambda: clock[0])
    first = store.create(MASTER)
    with pytest.raises(MediaError, match="media_session_capacity_exceeded"):
        store.create(MASTER)
    assert store.resolve(*parts(first.url)).content_kind == "manifest"
    clock[0] += 2
    second = store.create(MASTER)
    assert second.token != first.token


@pytest.mark.parametrize("text", [
    "not an HLS playlist",
    '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI=/key/unquoted\n',
    '#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="unterminated\n',
    '#EXTM3U\n#EXT-X-CONTENT-STEERING:SERVER-URI="https://other.example.test"\n',
    '#EXTM3U\n#EXT-X-DEFINE:NAME="variable",VALUE="value"\n',
    '#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=1000\n',
])
def test_malformed_or_unsupported_manifests_fail_closed(store, text):
    playback = store.create(MASTER)
    with pytest.raises(MediaError):
        store.rewrite_manifest(playback.token, MASTER, text)


@pytest.mark.parametrize("public", ["http://media.example.test", "https://user:secret@media.example.test", "https://media.example.test/prefix"])
def test_public_origin_requires_unambiguous_https(public):
    with pytest.raises(MediaError):
        MediaSessionStore(PROXY, public, KEY)
