"""Send approved playback sessions to fixed Home Assistant Cast targets.

Google voice entry uses an exposed Home Assistant script or a Google Routine.
This module is not a Google Conversational Action or a free-form voice endpoint.
"""

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

from .errors import PlaybackUnavailable


class HomeAssistantCaster:
    """Call only media_player.play_media, with administrator-defined targets."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        base_url: str,
        token: str,
        targets: Mapping[str, str],
    ) -> None:
        parsed = urlsplit(base_url)
        # Validate configured explicit ports before any network operation.
        _ = parsed.port
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or any(char.isspace() or ord(char) < 32 for char in base_url)
        ):
            raise ValueError("Home Assistant requires a configured HTTP or HTTPS base URL")
        if not token or not re.fullmatch(r"[!-~]+", token):
            raise ValueError("Home Assistant requires a valid bearer token")
        if any(
            not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", alias)
            or not re.fullmatch(r"media_player\.[a-z0-9_]+", entity)
            for alias, entity in targets.items()
        ):
            raise ValueError("Cast targets must map fixed aliases to media_player entity IDs")
        self._client = client
        self._url = f"{base_url.rstrip('/')}/api/services/media_player/play_media"
        self._token = token
        self._targets = dict(targets)

    async def play(self, target_alias: str, media_url: str) -> None:
        entity = self._targets.get(target_alias)
        if entity is None:
            raise PlaybackUnavailable("The requested playback target is not configured")
        try:
            parsed = urlsplit(media_url)
            _ = parsed.port
            valid = (
                parsed.scheme == "https"
                and bool(parsed.hostname)
                and parsed.username is None
                and parsed.password is None
                and not parsed.fragment
                and parsed.path.lower().endswith(".m3u8")
                and not any(char.isspace() or ord(char) < 32 for char in media_url)
            )
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise PlaybackUnavailable("The playback session is not a valid HTTPS HLS URL")
        try:
            response = await self._client.post(
                self._url,
                headers={"Authorization": f"Bearer {self._token}"},
                json={
                    "entity_id": entity,
                    "media_content_id": media_url,
                    "media_content_type": "application/vnd.apple.mpegurl",
                },
                timeout=8.0,
                follow_redirects=False,
            )
        except httpx.HTTPError:
            raise PlaybackUnavailable("Home Assistant could not accept playback") from None
        if not response.is_success:
            # Do not propagate URLs, response text, tokens, or request headers.
            raise PlaybackUnavailable("Home Assistant could not accept playback")
