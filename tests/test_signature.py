"""Real body cryptography; certificate retrieval/trust is an explicit test boundary."""

import base64
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from ask_sdk_webservice_support.verifier import RequestVerifier
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID

from epg_voice_gateway.signature import verify_amazon_request


@pytest.fixture(scope="module")
def signer():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "echo-api.amazon.com")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
            .not_valid_after(datetime.now(UTC) + timedelta(hours=1)).sign(key, hashes.SHA256()))
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
    with patch("epg_voice_gateway.signature.build_opener") as download:
        with pytest.raises(Exception):
            verify_amazon_request(headers, body)
        download.assert_not_called()
