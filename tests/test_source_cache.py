"""Synthetic HTTP cache tests; no source credentials or production hosts."""

import gzip
import json
import stat

import httpx
import pytest

from epg_voice_gateway.errors import CatalogUnavailable
from epg_voice_gateway.source_cache import SourceCache

URL = "https://guide.example.test/private-guide.xml.gz?token=synthetic-secret"
MODIFIED = "Wed, 16 Sep 2026 12:00:00 GMT"


class RawBody(httpx.AsyncByteStream):
    def __init__(self, body):
        self.body = body
        self.closed = False

    async def __aiter__(self):
        for index in range(0, len(self.body), 17):
            yield self.body[index:index + 17]

    async def aclose(self):
        self.closed = True


async def test_conditional_request_reuses_verified_raw_gzip_and_private_files(tmp_path):
    cache = SourceCache(tmp_path / "sources")
    body = gzip.compress(b"<tv>synthetic guide body</tv>")
    calls = []

    def handler(request):
        calls.append(request)
        assert request.headers["Accept-Encoding"] == "identity"
        if len(calls) == 1:
            assert "If-None-Match" not in request.headers
            return httpx.Response(200, stream=RawBody(body), headers={
                "ETag": '"version-1"', "Last-Modified": MODIFIED, "Content-Encoding": "gzip",
            })
        assert request.headers["If-None-Match"] == '"version-1"'
        assert request.headers["If-Modified-Since"] == MODIFIED
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await cache.fetch(client, URL, 1024) == body
        assert await cache.fetch(client, URL, 1024) == body
    assert stat.S_IMODE(cache.directory.stat().st_mode) == 0o700
    files = list(cache.directory.iterdir())
    assert len(files) == 2
    for path in files:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert "synthetic-secret" not in path.name
        if path.suffix == ".json":
            assert "synthetic-secret" not in path.read_text()
            assert "guide.example" not in path.read_text()


async def test_304_without_valid_cache_is_rejected(tmp_path):
    cache = SourceCache(tmp_path)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(304))) as client:
        with pytest.raises(CatalogUnavailable):
            await cache.fetch(client, URL, 1024)


@pytest.mark.parametrize("damage", ["missing", "corrupt", "oversized", "metadata"])
async def test_invalid_cached_body_forces_unconditional_request(tmp_path, damage):
    cache = SourceCache(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        assert "If-None-Match" not in request.headers
        return httpx.Response(200, stream=RawBody(b"original"), headers={"ETag": '"version-1"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await cache.fetch(client, URL, 32)
        body = next(tmp_path.glob("*.body"))
        if damage == "missing":
            body.unlink()
        elif damage == "corrupt":
            body.write_bytes(b"replaced")
        elif damage == "oversized":
            body.write_bytes(b"x" * 33)
        else:
            next(tmp_path.glob("*.json")).write_text("{}")
        assert await cache.fetch(client, URL, 32) == b"original"
    assert len(calls) == 2


async def test_invalidation_prevents_bad_xml_from_being_revalidated(tmp_path):
    cache = SourceCache(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        assert "If-None-Match" not in request.headers
        return httpx.Response(200, stream=RawBody(b"not valid XML"), headers={"ETag": '"invalid-version"'})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await cache.fetch(client, URL, 1024)
        cache.invalidate(URL)
        assert not list(tmp_path.iterdir())
        await cache.fetch(client, URL, 1024)
    assert len(calls) == 2


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 500])
async def test_redirects_and_failed_responses_do_not_replace_cache_or_expose_details(tmp_path, status):
    cache = SourceCache(tmp_path)
    calls = []
    failed = RawBody(b"synthetic-private-error")

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=RawBody(b"good"), headers={"ETag": '"good"'})
        return httpx.Response(status, stream=failed, headers={"Location": "http://127.0.0.1/private"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        await cache.fetch(client, URL, 1024)
        before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
        with pytest.raises(CatalogUnavailable) as error:
            await cache.fetch(client, URL, 1024)
        assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before
    assert len(calls) == 2
    assert failed.closed
    assert "synthetic" not in str(error.value)
    assert "127.0.0.1" not in str(error.value)


async def test_transfer_limit_rejects_body_without_writing_cache(tmp_path):
    cache = SourceCache(tmp_path)
    stream = RawBody(b"x" * 100)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream))) as client:
        with pytest.raises(CatalogUnavailable):
            await cache.fetch(client, URL, 99)
    assert stream.closed
    assert not list(tmp_path.iterdir())


async def test_cache_digest_is_rechecked_if_body_changes_during_304_request(tmp_path):
    cache = SourceCache(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, stream=RawBody(b"original"), headers={"ETag": '"version-1"'})
        next(tmp_path.glob("*.body")).write_bytes(b"replaced")
        return httpx.Response(304)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await cache.fetch(client, URL, 1024)
        with pytest.raises(CatalogUnavailable):
            await cache.fetch(client, URL, 1024)


async def test_unsafe_validators_are_not_stored_or_sent(tmp_path):
    cache = SourceCache(tmp_path)
    calls = []

    def handler(request):
        calls.append(request)
        assert "If-None-Match" not in request.headers
        assert "If-Modified-Since" not in request.headers
        return httpx.Response(200, stream=RawBody(b"source"), headers={"ETag": "invalid-etag", "Last-Modified": "invalid date"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await cache.fetch(client, URL, 1024)
        await cache.fetch(client, URL, 1024)
    metadata = json.loads(next(tmp_path.glob("*.json")).read_text())
    assert metadata["etag"] is None
    assert metadata["last_modified"] is None
