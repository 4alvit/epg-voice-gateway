"""Amazon request verification using the official ASK verifier."""

from collections import OrderedDict
from collections.abc import Callable
from threading import Lock
from time import monotonic
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

CERTIFICATE_CACHE_ENTRIES = 8
CERTIFICATE_CACHE_TTL_SECONDS = 300


class _CertificateCache:
    """Share validated PEM bytes without serializing request signature checks."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = monotonic,
        max_entries: int = CERTIFICATE_CACHE_ENTRIES,
        ttl: float = CERTIFICATE_CACHE_TTL_SECONDS,
    ) -> None:
        self._clock = clock
        self._max_entries = max_entries
        self._ttl = ttl
        self._entries: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
        self._lock = Lock()
        # Fixed stripes bound lock memory even for many distinct valid Amazon URLs.
        self._fetch_locks = tuple(Lock() for _ in range(CERTIFICATE_CACHE_ENTRIES))

    def _lookup(self, url: str) -> bytes | None:
        with self._lock:
            entry = self._entries.get(url)
            if entry is None:
                return None
            expires_at, certificate = entry
            if self._clock() >= expires_at:
                del self._entries[url]
                return None
            self._entries.move_to_end(url)
            return certificate

    def get(self, url: str, fetch_verified: Callable[[], bytes]) -> bytes:
        certificate = self._lookup(url)
        if certificate is not None:
            return certificate
        # Only cold fetches wait here. A timeout in the HTTP handler does not
        # release its verification slot while this synchronous work is running.
        with self._fetch_locks[hash(url) % len(self._fetch_locks)]:
            certificate = self._lookup(url)
            if certificate is not None:
                return certificate
            certificate = fetch_verified()
            # Publish only after download and SDK trust/date/SAN validation pass.
            with self._lock:
                self._entries[url] = (self._clock() + self._ttl, certificate)
                self._entries.move_to_end(url)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
            return certificate


_CERTIFICATES = _CertificateCache()


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_amazon_request(headers: dict[str, str], body: str) -> None:
    from ask_sdk_core.serialize import DefaultSerializer
    from ask_sdk_model import RequestEnvelope
    from ask_sdk_webservice_support.verifier import RequestVerifier, TimestampVerifier
    from cryptography import x509
    from cryptography.hazmat.primitives.hashes import SHA256

    class BoundedVerifier(RequestVerifier):
        def _load_cert_chain(self, cert_url):
            # ASK validates the URL before cache lookup. Its normal verification
            # flow validates the returned chain and end certificate on every use.
            return _CERTIFICATES.get(cert_url, lambda: self._download_verified_chain(cert_url))

        def _download_verified_chain(self, cert_url):
            # Socket timeouts do not bound DNS or the complete exchange. The
            # gateway also caps in-flight verification threads and HTTP duration.
            with build_opener(ProxyHandler({}), _NoRedirects()).open(cert_url, timeout=2) as response:
                certificate = response.read(16385)
                if response.status != 200 or len(certificate) > 16384:
                    raise ValueError("Invalid certificate response")
            self._validate_cert_chain(certificate)
            self._validate_end_certificate(x509.load_pem_x509_certificate(certificate))
            return certificate

    envelope = DefaultSerializer().deserialize(body, RequestEnvelope)
    TimestampVerifier(tolerance_in_millis=150000).verify(headers, body, envelope)
    BoundedVerifier(
        signature_cert_chain_url_key="signaturecertchainurl",
        signature_key="signature-256",
        hash_algorithm=SHA256(),
    ).verify(headers, body, envelope)
