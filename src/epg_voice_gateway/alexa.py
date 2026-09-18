"""Translate verified Alexa requests into current-program video launches.

The HTTP boundary must verify the Amazon request before calling this module.
Device capability checks here are independent of request authentication.
"""

from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from .errors import CatalogUnavailable, GatewayError
from .models import ProgramMatch

Resolver = Callable[[str], Awaitable[list[ProgramMatch]]]
SessionUrl = Callable[[ProgramMatch], Awaitable[str]]

HELP = "Tell me which program to play. For example, say, play Kukutiki."
UNAVAILABLE = "I could not start that channel right now. Please try again later."
GUIDE_UNAVAILABLE = "The program guide is unavailable right now. Please try again later."


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _speech(text: str, *, end_session: bool = True) -> dict[str, Any]:
    response: dict[str, Any] = {
        "outputSpeech": {"type": "PlainText", "text": text},
        "shouldEndSession": end_session,
    }
    if not end_session:
        response["reprompt"] = {"outputSpeech": {"type": "PlainText", "text": HELP}}
    return {"version": "1.0", "response": response}


def _metadata(value: str) -> str:
    return " ".join(value.split())[:200]


def _valid_media_url(value: str) -> bool:
    try:
        parsed = urlsplit(value)
        # Reading port rejects malformed or out-of-range explicit ports.
        _ = parsed.port
        return (
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and parsed.path.lower().endswith(".m3u8")
            and not any(char.isspace() or ord(char) < 32 for char in value)
        )
    except (TypeError, ValueError):
        return False


class AlexaAdapter:
    """Render the custom-skill contract without exposing provider addresses."""

    def __init__(
        self,
        resolver: Resolver,
        session_url: SessionUrl,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._resolver = resolver
        self._session_url = session_url
        self._now = now or (lambda: datetime.now(UTC))

    async def handle(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        request = _mapping(payload.get("request"))
        request_type = request.get("type")
        if request_type == "SessionEndedRequest":
            return {"version": "1.0", "response": {}}
        if request_type == "LaunchRequest":
            return _speech(HELP, end_session=False)
        if request_type != "IntentRequest":
            return _speech(HELP, end_session=False)

        intent = _mapping(request.get("intent"))
        name = intent.get("name")
        if name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
            return _speech("Goodbye.")
        if name != "PlayProgramIntent":
            return _speech(HELP, end_session=False)

        system = _mapping(_mapping(payload.get("context")).get("System"))
        device = _mapping(system.get("device"))
        interfaces = _mapping(device.get("supportedInterfaces"))
        if "VideoApp" not in interfaces:
            return _speech("This device does not support video playback from this skill.")

        slots = _mapping(intent.get("slots"))
        query = _mapping(slots.get("Query")).get("value")
        if not isinstance(query, str) or not query.strip():
            return _speech(HELP, end_session=False)
        query = " ".join(query.split())
        if len(query) > 200:
            return _speech("Please use a shorter program title.", end_session=False)

        try:
            matches = await self._resolver(query)
            if not matches:
                return _speech("I could not find that program airing right now.")
            if len(matches) > 1:
                return _speech(
                    "I found more than one current match. Please ask for a more specific "
                    "program title.",
                    end_session=False,
                )

            match = matches[0]
            now = self._now()
            if not (
                match.starts_at.tzinfo is not None
                and match.ends_at.tzinfo is not None
                and match.starts_at <= now < match.ends_at
            ):
                return _speech("That program is not currently airing. Please try another title.")
            source = await self._session_url(match)
            if not _valid_media_url(source):
                return _speech(UNAVAILABLE)
        except CatalogUnavailable:
            return _speech(GUIDE_UNAVAILABLE)
        except GatewayError:
            return _speech(UNAVAILABLE)

        # VideoApp forbids shouldEndSession, including a null value.
        return {
            "version": "1.0",
            "response": {
                "directives": [
                    {
                        "type": "VideoApp.Launch",
                        "videoItem": {
                            "source": source,
                            "metadata": {
                                "title": _metadata(match.title),
                                "subtitle": _metadata(match.channel_name),
                            },
                        },
                    }
                ]
            },
        }
