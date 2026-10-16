"""Run the private gateway over HTTPS with checked operator certificate keys."""

import argparse
import ssl
from pathlib import Path

import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding

from .app import create_app
from .config import Settings
from .tls_policy import _certificate_key_ok


def server_config(settings: Settings, cert: Path, key: Path, *, port: int = 8443):
    """Validate the complete PEM chain before loading keys or opening a listener."""
    certificates = x509.load_pem_x509_certificates(cert.read_bytes())
    if not certificates or any(
        not _certificate_key_ok(item.public_bytes(Encoding.DER)) for item in certificates
    ):
        raise ValueError("Server certificate key is below the supported security minimum")
    config = uvicorn.Config(
        create_app(settings), host="127.0.0.1", port=port, workers=1,
        ssl_certfile=str(cert), ssl_keyfile=str(key), access_log=False,
    )
    config.load()
    config.ssl.minimum_version = ssl.TLSVersion.TLSv1_2
    return config


def main():
    """Start a loopback-only HTTPS service using HTTPS upstreams."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cert", required=True, type=Path)
    parser.add_argument("--key", required=True, type=Path)
    parser.add_argument("--port", type=int, default=8443)
    args = parser.parse_args()
    settings = Settings(require_https=True)
    uvicorn.Server(server_config(settings, args.cert, args.key, port=args.port)).run()


if __name__ == "__main__":
    main()
