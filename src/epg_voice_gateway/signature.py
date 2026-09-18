"""Amazon request verification using the official ASK verifier."""

from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_amazon_request(headers: dict[str, str], body: str) -> None:
    from ask_sdk_core.serialize import DefaultSerializer
    from ask_sdk_model import RequestEnvelope
    from ask_sdk_webservice_support.verifier import RequestVerifier, TimestampVerifier
    from cryptography.hazmat.primitives.hashes import SHA256

    class BoundedVerifier(RequestVerifier):
        def _load_cert_chain(self, cert_url):
            # ASK validates the Amazon certificate URL before invoking this transport.
            with build_opener(ProxyHandler({}), _NoRedirects()).open(cert_url, timeout=2) as response:
                certificate = response.read(16385)
                if response.status != 200 or len(certificate) > 16384:
                    raise ValueError("Invalid certificate response")
                return certificate

    envelope = DefaultSerializer().deserialize(body, RequestEnvelope)
    TimestampVerifier(tolerance_in_millis=150000).verify(headers, body, envelope)
    BoundedVerifier(
        signature_cert_chain_url_key="signaturecertchainurl",
        signature_key="signature-256",
        hash_algorithm=SHA256(),
    ).verify(headers, body, envelope)
