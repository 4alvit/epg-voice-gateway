"""Complete local guide -> authenticated resolution -> HLS media over real TLS."""
import asyncio
import socket
import ssl
import threading
import time
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import uvicorn
from pydantic import ValidationError
from test_tls_policy import make_chains

from epg_voice_gateway.app import create_http_client
from epg_voice_gateway.config import Settings
from epg_voice_gateway.server import server_config


def settings(**overrides):
    values = dict(api_token="synthetic-api-token-of-more-than-32-bytes",
                  signing_key="synthetic-signing-key-of-more-than-32-bytes",
                  hls_proxy_url="https://localhost", media_base_url="https://localhost",
                  xmltv_urls=["https://localhost/guide.xml"], require_https=True)
    return Settings(**(values | overrides))


@pytest.mark.parametrize("field,value", [
    ("hls_proxy_url", "http://localhost"),
    ("xmltv_urls", ["http://localhost/guide.xml"]),
    ("ha_url", "http://localhost"),
])
def test_https_mode_rejects_plaintext_upstreams(field, value):
    with pytest.raises(ValidationError, match="HTTPS-only"):
        settings(**{field: value})


def test_plaintext_bearer_media_url_is_always_rejected():
    with pytest.raises(ValidationError, match="credential-free"):
        settings(media_base_url="http://localhost")


def test_server_rejects_weak_configured_certificate(tmp_path):
    chains = make_chains(tmp_path)
    with pytest.raises(ValueError, match="security minimum"):
        server_config(settings(), *chains["weak-2047-leaf"][:2])


def test_complete_private_https_workflow(tmp_path, monkeypatch):
    chain = make_chains(tmp_path)["strong"]
    monkeypatch.setenv("SSL_CERT_FILE", str(chain[2]))
    now = datetime.now(UTC)
    start = (now - timedelta(minutes=5)).strftime("%Y%m%d%H%M%S +0000")
    stop = (now + timedelta(hours=1)).strftime("%Y%m%d%H%M%S +0000")
    paths = []

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            bodies = {
                "/playlist.m3u8": b'#EXTM3U\n#EXTINF:-1 tvg-id="test",Fixture channel\n/channel/fixture/index.m3u8\n',
                "/guide.xml": f'<tv><channel id="test"><display-name>Fixture channel</display-name></channel><programme start="{start}" stop="{stop}" channel="test"><title>Fixture programme</title></programme></tv>'.encode(),
                "/channel/fixture/index.m3u8": b'#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nsegment.ts\n#EXT-X-ENDLIST\n',
                "/channel/fixture/segment.ts": b'synthetic-media-segment',
            }
            body = bodies.get(self.path, b'')
            self.send_response(200 if body else 404)
            content_type = 'application/vnd.apple.mpegurl' if self.path.endswith('m3u8') else 'video/mp2t' if self.path.endswith('.ts') else 'application/xml'
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(*chain[:2])
    upstream.socket = context.wrap_socket(upstream.socket, server_side=True)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    base = f'https://localhost:{listener.getsockname()[1]}'
    origin = f'https://localhost:{upstream.server_port}'
    config = server_config(settings(hls_proxy_url=origin, xmltv_urls=[origin+'/guide.xml'],
                                    media_base_url=base), *chain[:2])
    server = uvicorn.Server(config)
    gateway = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    gateway.start()
    try:
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started

        async def exercise():
            async with create_http_client() as client:
                for _ in range(100):
                    if (await client.get(base+'/readyz')).status_code == 200:
                        break
                    await asyncio.sleep(0.02)
                else:
                    pytest.fail('Real HTTPS catalogue did not become ready')
                assert (await client.post(base+'/v1/resolve', json={'query':'Fixture programme'})).status_code == 401
                auth = {'Authorization':'Bearer '+settings().api_token.get_secret_value()}
                resolved = await client.post(base+'/v1/resolve', json={'query':'Fixture programme'}, headers=auth)
                assert resolved.status_code == 200, resolved.text
                played = await client.post(base+'/v1/play', json={'query':'Fixture programme'}, headers=auth)
                assert played.status_code == 200, played.text
                media = played.json()['media_url']
                assert media.startswith(base+'/media/')
                playlist = await client.get(media)
                assert playlist.status_code == 200, playlist.text
                segment_url = next(line for line in playlist.text.splitlines() if line and not line.startswith('#'))
                if segment_url.startswith('/'):
                    segment_url = base+segment_url
                segment = await client.get(segment_url)
                assert segment.status_code == 200, segment.text
                assert segment.content == b'synthetic-media-segment'
        asyncio.run(exercise())
        assert '/guide.xml' in paths and '/channel/fixture/segment.ts' in paths
    finally:
        server.should_exit = True
        gateway.join(timeout=5)
        listener.close()
        upstream.shutdown()
        upstream.server_close()
        worker.join(timeout=5)
        assert not gateway.is_alive()
