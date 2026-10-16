"""Real disposable TLS chains, including unserved weak trust anchors."""
import asyncio
import http.client
import os
import socket
import ssl
import tempfile
import threading
import unittest
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from itertools import product
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import URLError

import httpx
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from native_tls_support import assert_native_ec192_unsupported

from epg_voice_gateway.app import create_http_client
from epg_voice_gateway.signature import _download_certificate_bytes

Key = rsa.RSAPrivateKey | ec.EllipticCurvePrivateKey
CHAIN_CASES = ("strong", "strong-ec", "weak-leaf", "weak-intermediate", "weak-root",
               "weak-2047-root", "weak-2047-leaf", "weak-ec-root")


def certificate(
    key: Key, name: str, issuer: x509.Certificate | None, issuer_key: Key, *, ca: bool
) -> x509.Certificate:
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.now(UTC)
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer.subject if issuer else subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(hours=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=not ca,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=ca,
                crl_sign=ca,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()), False
        )
    )
    if not ca:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.DNSName("sub.localhost")]),
            False,
        ).add_extension(
            x509.ExtendedKeyUsage(
                [ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH]
            ),
            False,
        )
    return builder.sign(issuer_key, hashes.SHA256())

def make_chains(directory: Path) -> dict[str, tuple[Path, Path, Path]]:
    result = {}
    for case in CHAIN_CASES:
        root_bits = {"weak-root": 1024, "weak-2047-root": 2047}.get(case, 2048)
        root_key: Key
        if case in ("strong-ec", "weak-ec-root"):
            curve = ec.SECP192R1() if case == "weak-ec-root" else ec.SECP256R1()
            root_key = ec.generate_private_key(curve)
        else:
            root_key = rsa.generate_private_key(65537, root_bits)
        root = certificate(root_key, case, None, root_key, ca=True)
        issuer, issuer_key = root, root_key
        intermediate = b""
        if case == "weak-intermediate":
            # Deliberately weak, disposable certificate for rejection testing.
            issuer_key = rsa.generate_private_key(65537, 1024)  # nosec B505
            issuer = certificate(issuer_key, "intermediate", root, root_key, ca=True)
            intermediate = issuer.public_bytes(serialization.Encoding.PEM)
        leaf_key: Key = (
            ec.generate_private_key(ec.SECP256R1())
            if case == "strong-ec"
            else rsa.generate_private_key(65537, 1024 if case == "weak-leaf" else 2047 if case == "weak-2047-leaf" else 2048)
        )
        leaf = certificate(leaf_key, "localhost", issuer, issuer_key, ca=False)
        cert_file, key_file, ca_file = (
            directory / f"{case}.{suffix}" for suffix in ("pem", "key", "ca")
        )
        cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM) + intermediate)
        key_file.write_bytes(
            leaf_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        ca_file.write_bytes(root.public_bytes(serialization.Encoding.PEM))
        result[case] = cert_file, key_file, ca_file
    return result

@contextmanager
def peer(
    chain: tuple[Path, Path, Path],
    version: ssl.TLSVersion,
    *,
    redirect: str | None = None,
    client_auth: bool = False,
    response_body: bytes = b"",
    content_type: str = "application/json",
) -> Iterator[tuple[int, dict[str, Any]]]:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = context.maximum_version = version
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_cert_chain(chain[0], chain[1])
    context.set_alpn_protocols(["h2", "http/1.1"])
    if client_auth:
        context.load_verify_locations(chain[2])
        context.verify_mode = ssl.CERT_REQUIRED
    result: dict[str, Any] = {"application_bytes": b""}
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(5)

        def worker() -> None:
            try:
                raw, _ = listener.accept()
                with raw:
                    raw.settimeout(5)
                    with context.wrap_socket(raw, server_side=True) as connection:
                        result["tls"] = connection.version()
                        result["alpn"] = connection.selected_alpn_protocol()
                        if client_auth:
                            result["client_certificate"] = connection.getpeercert(binary_form=True)
                        data = b""
                        while b"\r\n\r\n" not in data:
                            part = connection.recv(8192)
                            if not part:
                                if not data:
                                    result["closed_before_http"] = True
                                    return
                                raise EOFError("Expected HTTP headers")
                            data += part
                            result["application_bytes"] = data
                        headers, body = data.split(b"\r\n\r\n", 1)
                        length = next(
                            (
                                int(line.split(b":", 1)[1])
                                for line in headers.split(b"\r\n")
                                if line.lower().startswith(b"content-length:")
                            ),
                            0,
                        )
                        while len(body) < length:
                            part = connection.recv(8192)
                            if not part:
                                raise EOFError("Expected complete HTTP body")
                            body += part
                        result["application_bytes"] = headers + b"\r\n\r\n" + body
                        response = (
                            f"HTTP/1.1 307 Temporary Redirect\r\nLocation: {redirect}\r\n"
                            if redirect
                            else "HTTP/1.1 200 OK\r\n"
                        )
                        connection.sendall(
                            (
                                response
                                + f"Content-Length: {len(response_body)}\r\n"
                                + f"Content-Type: {content_type}\r\nConnection: close\r\n\r\n"
                            ).encode()
                            + response_body
                        )
            except ssl.SSLError as error:
                result["handshake_error"] = str(error)
            except (ConnectionResetError, BrokenPipeError) as error:
                if result["application_bytes"]:
                    result["unexpected_error"] = repr(error)
                else:
                    result["closed_before_http"] = True
            except Exception as error:
                result["unexpected_error"] = repr(error)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            yield listener.getsockname()[1], result
        finally:
            thread.join(6)
            assert not thread.is_alive()
            assert "unexpected_error" not in result, result

def calibrate(chain: tuple[Path, Path, Path], version: ssl.TLSVersion) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.set_ciphers("DEFAULT:@SECLEVEL=0")
    context.load_verify_locations(chain[2])
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
    with peer(chain, version) as (port, observed):
        connection = http.client.HTTPSConnection("localhost", port, context=context, timeout=5)
        try:
            connection.request("POST", "/oracle", body=b"calibration")
            assert connection.getresponse().status == 200
        finally:
            connection.close()
    assert observed["application_bytes"].endswith(b"calibration")

class TLSClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(prefix="epg-test-pki-")
        cls.addClassCleanup(cls.directory.cleanup)
        cls.chains = make_chains(Path(cls.directory.name))

    def test_owned_clients_check_keys_before_application_data(self):
        clean = {k:v for k,v in os.environ.items() if not k.upper().endswith("_PROXY") and k not in ("SSL_CERT_FILE", "SSL_CERT_DIR")}
        for operation, case, version in product(
            ("async", "certificate"),
            (*CHAIN_CASES, "untrusted", "wrong-host"),
            (ssl.TLSVersion.TLSv1_2, ssl.TLSVersion.TLSv1_3),
        ):
            chain = self.chains.get(case, self.chains["strong"])
            with self.subTest(operation=operation, case=case, version=version):
                try:
                    calibrate(chain, version)
                except ssl.SSLCertVerificationError as failure:
                    self.assertEqual(case, "weak-ec-root")
                    assert_native_ec192_unsupported(self.chains, failure)
                ca = self.chains["strong-ec"][2] if case == "untrusted" else chain[2]
                env = clean | {"SSL_CERT_FILE":str(ca), "SSL_CERT_DIR":str(Path(self.directory.name)/"empty")}
                with patch.dict(os.environ, env, clear=True), peer(chain, version, response_body=b"synthetic") as (port, observed):
                    host = "127.0.0.1" if case == "wrong-host" else "localhost"
                    url = f"https://{host}:{port}"
                    async def fetch():
                        async with create_http_client() as client:
                            response = await client.get(url, headers={"Authorization":"Bearer synthetic"})
                            return response.content
                    def request():
                        return asyncio.run(fetch()) if operation == "async" else _download_certificate_bytes(url)
                    if case.startswith("strong"):
                        self.assertEqual(request(), b"synthetic")
                    else:
                        error = httpx.ConnectError if operation == "async" else URLError
                        with self.assertRaises(error):
                            request()
                self.assertEqual(bool(observed["application_bytes"]), case.startswith("strong"))
