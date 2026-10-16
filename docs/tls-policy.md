# Private HTTPS API profile

The existing direct API supports programme search and a playable expiring media URL without Home Assistant, a cloud worker or a physical Alexa/Cast device. This profile runs that complete flow over private HTTPS. It is for one household; it is not account linking or public multi-tenant authorization.

Owned outbound clients verify the certificate chain and hostname, then inspect every certificate including the selected trust anchor before HTTP data. RSA must be at least 2048 bits, EC at least 224 bits and DSA at least 2048/224 bits. Standard Ed25519/Ed448 keys are recognized; unsupported encodings fail closed. TLS 1.2 is the minimum. The implementation is adapted under MIT from the reviewed victron-venus inverter-dashboard policy. Linux/Apple Silicon use cryptography; the Intel macOS decoder uses Security.framework. The supported build uses CPython 3.12+ on Linux.

The explicit launcher checks every configured server PEM certificate before loading the private key or opening its loopback listener. It enables HTTPS-only mode, rejecting HTTP HLS, XMLTV and configured HA upstreams. Outbound redirects are disabled; certificate fetches have a 16KiB bound. Existing application size/time/session limits remain enforced.

## Run the complete direct API

Run CONTRIBUTING.md installation first. Have the household's HLS proxy and XMLTV service available over verified HTTPS. Use an operator CA whose keys meet these minimums and a server certificate containing the actual endpoint hostname; never disable certificate verification. For a local trial, create a new private directory and temporary self-signed localhost certificate:

```sh
umask 077
mkdir /PRIVATE/epg-tls
openssl req -x509 -newkey rsa:3072 -sha256 -days 7 -nodes \
  -keyout /PRIVATE/epg-tls/server.key -out /PRIVATE/epg-tls/server.crt \
  -subj /CN=localhost -addext subjectAltName=DNS:localhost
```

Generate two independent random secrets with Python secrets.token_urlsafe(32). Set EPG_API_TOKEN, EPG_SIGNING_KEY, EPG_HLS_PROXY_URL=https://your-private-proxy, EPG_XMLTV_URLS='["https://your-private-guide/guide.xml"]' and EPG_MEDIA_BASE_URL=https://localhost:8443 in a private mode-0600 environment file. Use actual trusted local hosts; the example names are placeholders. If an upstream uses a private CA, set SSL_CERT_FILE to a bundle including that CA and any roots it needs. Do not place private keys in the CA bundle.

Save those assignments in a trusted shell-format `/PRIVATE/epg.env` file, quote JSON values, and load it into the launch process explicitly:

```sh
set -a
. /PRIVATE/epg.env
set +a
uv run --frozen python -m epg_voice_gateway.server \
  --cert /PRIVATE/epg-tls/server.crt --key /PRIVATE/epg-tls/server.key
```

The server binds only 127.0.0.1:8443, keeps one worker for in-memory sessions and disables access logging because media URLs are bearer credentials. In a second process, use the API token without printing it or writing it in shell history:

```python
import os
import ssl
import httpx
from epg_voice_gateway.tls_policy import enforce_peer_key_policy
context = ssl.create_default_context(cafile='/PRIVATE/epg-tls/server.crt')
with httpx.Client(base_url='https://localhost:8443', verify=enforce_peer_key_policy(context)) as client:
    client.get('/readyz').raise_for_status()
    auth = {'Authorization': 'Bearer ' + os.environ['EPG_API_TOKEN']}
    result = client.post('/v1/play', headers=auth, json={'query': 'Programme title'})
    result.raise_for_status()
    playlist = client.get(result.json()['media_url'])
    playlist.raise_for_status()
    # Consume the returned manifest with a trusted local HLS client that verifies this CA.
```

Select a current title present in the guide. Expired/unknown/ambiguous requests are rejected according to the documented API. The complete test in tests/test_https_workflow.py also follows the rewritten manifest and retrieves a synthetic segment using the production guarded client.

## Other supported configurations

Private LAN HTTP remains an explicit legacy option for existing deployments; it does not provide transport encryption. HA owns its integration HTTP stack; Alexa signing trust and Worker/edge TLS are separate boundaries. They remain in full source scans and tests but are not covered by this direct-API key-size qualification. Universal forward secrecy, every optional transport path and all native dependencies are not claimed. The service stores bearer/HMAC secrets, not password verifiers; keep them independently random and rotate by replacing private configuration and restarting (existing sessions are invalidated).
