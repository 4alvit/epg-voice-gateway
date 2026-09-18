"""Real body cryptography; certificate retrieval/trust is an explicit test boundary."""

import base64
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier, Event, Lock
from unittest.mock import Mock, patch

import pytest
from ask_sdk_webservice_support.verifier import RequestVerifier, VerificationException
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from epg_voice_gateway import signature as signature_module
from epg_voice_gateway.signature import verify_amazon_request


@pytest.fixture(autouse=True)
def isolated_certificate_cache(monkeypatch):
    clock = [100.0]
    cache = signature_module._CertificateCache(clock=lambda: clock[0])
    monkeypatch.setattr(signature_module, "_CERTIFICATES", cache)
    return clock, cache


@pytest.fixture(scope="module")
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "echo-api.amazon.com")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("echo-api.amazon.com")]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    return key, cert


def signed(key, when=None):
    body = json.dumps({"version": "1.0", "request": {
        "type": "LaunchRequest", "requestId": "synthetic-request", "locale": "en-US",
        "timestamp": (when or datetime.now(UTC)).isoformat(),
    }})
    signature = key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256())
    return {"signaturecertchainurl": "https://s3.amazonaws.com/echo.api/test.pem",
            "signature-256": base64.b64encode(signature).decode()}, body


def test_real_signature_accepts_original_rejects_tampered_body(signer):
    key, cert = signer
    headers, body = signed(key)
    with patch.object(RequestVerifier, "_retrieve_and_validate_certificate_chain", return_value=cert):
        verify_amazon_request(headers, body)
        with pytest.raises(Exception):
            verify_amazon_request(headers, body.replace("synthetic-request", "tampered-request"))


def test_expired_request_rejected_before_certificate_fetch(signer):
    headers, body = signed(signer[0], datetime.now(UTC) - timedelta(minutes=5))
    with patch("epg_voice_gateway.signature.build_opener") as download:
        with pytest.raises(Exception):
            verify_amazon_request(headers, body)
        download.assert_not_called()


@pytest.mark.parametrize("url", [
    "https://attacker.example/echo.api/cert.pem",
    "http://s3.amazonaws.com/echo.api/cert.pem",
    "https://s3.amazonaws.com:444/echo.api/cert.pem",
    "https://s3.amazonaws.com/echo.api/../../private",
])
def test_invalid_certificate_location_rejected_before_network(signer, url):
    headers, body = signed(signer[0])
    headers["signaturecertchainurl"] = url
    with (
        patch("epg_voice_gateway.signature.build_opener") as download,
        patch.object(signature_module._CERTIFICATES, "get") as cache,
    ):
        with pytest.raises(Exception):
            verify_amazon_request(headers, body)
        download.assert_not_called()
        cache.assert_not_called()


@pytest.fixture
def certificate_transport(signer):
    """Mock TLS retrieval and CA trust only; keep SDK SAN/date and RSA checks."""
    response = Mock(status=200)
    response.read.return_value = signer[1].public_bytes(serialization.Encoding.PEM)
    context = Mock()
    context.__enter__ = Mock(return_value=response)
    context.__exit__ = Mock(return_value=False)
    opener = Mock()
    opener.open.return_value = context
    with (
        patch("epg_voice_gateway.signature.build_opener", return_value=opener),
        patch.object(RequestVerifier, "_validate_cert_chain") as trust,
    ):
        yield opener, response, trust


def test_cached_certificate_still_checks_trust_dates_and_each_body(signer, certificate_transport):
    opener, _, trust = certificate_transport
    headers, body = signed(signer[0])
    with patch.object(
        RequestVerifier, "_validate_end_certificate",
        wraps=RequestVerifier()._validate_end_certificate,
    ) as end_certificate:
        verify_amazon_request(headers, body)
        verify_amazon_request(headers, body)
        with pytest.raises(VerificationException):
            verify_amazon_request(headers, body.replace("synthetic-request", "tampered-request"))
        bad_headers = {**headers, "signature-256": base64.b64encode(b"invalid-signature").decode()}
        with pytest.raises(VerificationException):
            verify_amazon_request(bad_headers, body)
        # Once before cache publication and once for every request, including hits.
        assert trust.call_count == end_certificate.call_count == 5
    assert opener.open.call_count == 1
    opener.open.assert_called_with(headers["signaturecertchainurl"], timeout=2)


def test_cached_certificate_does_not_bypass_timestamp(signer, certificate_transport):
    opener, _, trust = certificate_transport
    verify_amazon_request(*signed(signer[0]))
    checks = trust.call_count
    with pytest.raises(VerificationException):
        verify_amazon_request(*signed(signer[0], datetime.now(UTC) - timedelta(minutes=5)))
    assert trust.call_count == checks
    assert opener.open.call_count == 1


@pytest.mark.parametrize("validation", ["_validate_cert_chain", "_validate_end_certificate"])
def test_cached_certificate_is_rejected_when_validation_fails(
    signer, certificate_transport, validation,
):
    opener, _, _ = certificate_transport
    headers, body = signed(signer[0])
    verify_amazon_request(headers, body)
    with patch.object(RequestVerifier, validation, side_effect=VerificationException("Rejected")):
        with pytest.raises(VerificationException):
            verify_amazon_request(headers, body)
    assert opener.open.call_count == 1


def test_expired_certificate_is_never_published(signer, certificate_transport):
    opener, response, _ = certificate_transport
    key, original = signer
    expired = (x509.CertificateBuilder().subject_name(original.subject).issuer_name(original.issuer)
               .public_key(key.public_key()).serial_number(x509.random_serial_number())
               .not_valid_before(datetime.now(UTC) - timedelta(hours=2))
               .not_valid_after(datetime.now(UTC) - timedelta(hours=1))
               .add_extension(
                   x509.SubjectAlternativeName([x509.DNSName("echo-api.amazon.com")]), critical=False,
               ).sign(key, hashes.SHA256()))
    response.read.return_value = expired.public_bytes(serialization.Encoding.PEM)
    headers, body = signed(key)
    for _ in range(2):
        with pytest.raises(VerificationException):
            verify_amazon_request(headers, body)
    assert opener.open.call_count == 2
    assert not signature_module._CERTIFICATES._entries


def test_untrusted_certificate_is_not_cached(signer, certificate_transport):
    opener, _, trust = certificate_transport
    trust.side_effect = VerificationException("Untrusted certificate")
    headers, body = signed(signer[0])
    for _ in range(2):
        with pytest.raises(VerificationException):
            verify_amazon_request(headers, body)
    assert opener.open.call_count == 2
    assert not signature_module._CERTIFICATES._entries


def test_expired_cache_refresh_failure_cannot_use_stale_certificate(
    signer, certificate_transport, isolated_certificate_cache,
):
    clock, _ = isolated_certificate_cache
    opener, _, _ = certificate_transport
    headers, body = signed(signer[0])
    verify_amazon_request(headers, body)
    clock[0] += signature_module.CERTIFICATE_CACHE_TTL_SECONDS
    opener.open.side_effect = OSError("Synthetic download failure")
    for _ in range(2):
        with pytest.raises(OSError):
            verify_amazon_request(headers, body)
    assert opener.open.call_count == 3
    assert not signature_module._CERTIFICATES._entries


def test_ttl_refresh_uses_rotated_certificate(
    signer, certificate_transport, isolated_certificate_cache,
):
    clock, _ = isolated_certificate_cache
    opener, response, _ = certificate_transport
    headers, body = signed(signer[0])
    verify_amazon_request(headers, body)
    clock[0] += signature_module.CERTIFICATE_CACHE_TTL_SECONDS
    replacement_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    replacement = (x509.CertificateBuilder().subject_name(signer[1].subject)
                   .issuer_name(signer[1].issuer).public_key(replacement_key.public_key())
                   .serial_number(x509.random_serial_number())
                   .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
                   .not_valid_after(datetime.now(UTC) + timedelta(hours=1))
                   .add_extension(
                       x509.SubjectAlternativeName([x509.DNSName("echo-api.amazon.com")]),
                       critical=False,
                   ).sign(replacement_key, hashes.SHA256()))
    response.read.return_value = replacement.public_bytes(serialization.Encoding.PEM)
    verify_amazon_request(*signed(replacement_key))
    with pytest.raises(VerificationException):
        verify_amazon_request(headers, body)
    assert opener.open.call_count == 2


@pytest.mark.parametrize("status,body", [(500, b"failure"), (200, b"x" * 16385), (200, b"invalid")])
def test_invalid_download_is_not_cached(signer, certificate_transport, status, body):
    opener, response, _ = certificate_transport
    response.status = status
    response.read.return_value = body
    for _ in range(2):
        with pytest.raises(ValueError):
            verify_amazon_request(*signed(signer[0]))
    assert opener.open.call_count == 2
    assert not signature_module._CERTIFICATES._entries


def test_concurrent_requests_download_certificate_once(signer, certificate_transport):
    opener, response, trust = certificate_transport
    body = response.read.return_value
    entered = Event()
    release = Event()
    start = Barrier(5)

    def read_certificate(_limit):
        entered.set()
        assert release.wait(2)
        return body

    def verify():
        headers, request_body = signed(signer[0])
        start.wait(timeout=2)
        verify_amazon_request(headers, request_body)

    response.read.side_effect = read_certificate
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(verify) for _ in range(4)]
        start.wait(timeout=2)
        try:
            assert entered.wait(2)
            assert opener.open.call_count == 1
        finally:
            release.set()
        for future in futures:
            future.result(timeout=2)
    assert opener.open.call_count == 1
    assert trust.call_count == 5


def test_cache_bounds_entries_and_evicts_least_recently_used(isolated_certificate_cache):
    _, cache = isolated_certificate_cache
    loads = []

    def get(url):
        def fetch():
            loads.append(url)
            return url.encode()
        return cache.get(url, fetch)

    for index in range(signature_module.CERTIFICATE_CACHE_ENTRIES):
        assert get(str(index)) == str(index).encode()
    get("0")
    get("replacement")
    assert len(cache._entries) == signature_module.CERTIFICATE_CACHE_ENTRIES
    assert "0" in cache._entries
    assert "1" not in cache._entries
    get("1")
    assert loads.count("1") == 2
    assert len(cache._fetch_locks) == signature_module.CERTIFICATE_CACHE_ENTRIES


def test_distinct_fetch_stripes_do_not_serialize_network_work(isolated_certificate_cache):
    _, cache = isolated_certificate_cache
    first = "first"
    second = next(str(i) for i in range(1000)
                  if hash(str(i)) % len(cache._fetch_locks) != hash(first) % len(cache._fetch_locks))
    both_fetching = Barrier(2)
    seen = []
    seen_lock = Lock()

    def get(url):
        def fetch():
            with seen_lock:
                seen.append(url)
            both_fetching.wait(timeout=2)
            return url.encode()
        return cache.get(url, fetch)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(get, url) for url in (first, second)]
        assert [future.result(timeout=2) for future in futures] == [first.encode(), second.encode()]
    assert set(seen) == {first, second}
