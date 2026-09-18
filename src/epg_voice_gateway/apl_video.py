"""Capability-gated APL video documents and controls for verified Alexa requests."""

import hashlib
import hmac
import re
import secrets
from collections.abc import Mapping
from html import escape
from typing import Any

APL_INTERFACE = "Alexa.Presentation.APL"
DOCUMENT_VERSION = "1.8"
VIDEO_ID = "epgVideo"
STATUS_ID = "epgVideoStatus"
EVENT_NAMESPACE = "epg-video-v1"
_TOKEN_PATTERN = re.compile(r"epg-video-v1:([0-9a-f]{32}):([0-9a-f]{64})")
_VERSION_PATTERN = re.compile(r"([1-9][0-9]{0,3})\.(0|[1-9][0-9]?)")
_CODECS = frozenset({"H_264_41", "H_264_42"})
_ACTIONS = {"pause": "pause", "resume": "play", "stop": "pause"}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _interfaces(payload: Any) -> Mapping[str, Any]:
    context = _mapping(_mapping(payload).get("context"))
    system = _mapping(context.get("System"))
    return _mapping(_mapping(system.get("device")).get("supportedInterfaces"))


def compatible_version(payload: Any) -> str | None:
    """Select our tested baseline only when the runtime explicitly supports it."""
    interface = _mapping(_interfaces(payload).get(APL_INTERFACE))
    maximum = _mapping(interface.get("runtime")).get("maxVersion")
    if not isinstance(maximum, str) or not (match := _VERSION_PATTERN.fullmatch(maximum)):
        return None
    version = tuple(int(part) for part in match.groups())
    # Older APL video documentation explicitly excludes live content. Version
    # 1.8 adds media error handling; device-level HLS support still needs testing.
    return DOCUMENT_VERSION if version >= (1, 8) else None


def capability_summary(payload: Any) -> dict[str, bool]:
    """Return only fixed capability booleans, safe for optional diagnostics."""
    interfaces = _interfaces(payload)
    context = _mapping(_mapping(payload).get("context"))
    video = _mapping(_mapping(context.get("Viewport")).get("video"))
    codecs = video.get("codecs")
    compatible_codec = isinstance(codecs, list) and any(
        isinstance(codec, str) and codec in _CODECS for codec in codecs
    )
    return {
        "video_app": "VideoApp" in interfaces,
        "apl": APL_INTERFACE in interfaces,
        "apl_video": compatible_version(payload) is not None and compatible_codec,
    }


def _media_command(action: str) -> dict[str, str]:
    return {"type": "ControlMedia", "componentId": VIDEO_ID, "command": _ACTIONS[action]}


def _button(action: str, label: str) -> dict:
    commands: list[dict] = [
        _media_command(action),
        {"type": "SendEvent", "arguments": [EVENT_NAMESPACE, action]},
    ]
    if action == "stop":
        commands.append({"type": "Finish"})
    return {
        "type": "TouchWrapper",
        "id": "epgVideo" + action.title(),
        "accessibilityLabel": label,
        "grow": 1,
        "height": 64,
        "onPress": commands,
        "item": {
            "type": "Frame", "backgroundColor": "#24344A", "borderRadius": 8,
            "paddingLeft": 12, "paddingRight": 12, "height": "100%",
            "item": {
                "type": "Text", "text": label, "color": "#FFFFFF",
                "fontSize": 22, "textAlign": "center", "textAlignVertical": "center",
                "height": "100%", "width": "100%",
            },
        },
    }


class APLVideo:
    """Own document tokens without storing identities, media URLs, or user state."""

    def __init__(self) -> None:
        self._token_key = secrets.token_bytes(32)

    def _token(self) -> str:
        nonce = secrets.token_hex(16)
        signature = hmac.new(self._token_key, nonce.encode(), hashlib.sha256).hexdigest()
        return f"{EVENT_NAMESPACE}:{nonce}:{signature}"

    def current_token(self, payload: Any) -> str | None:
        if not capability_summary(payload)["apl_video"]:
            return None
        context = _mapping(_mapping(payload).get("context"))
        token = _mapping(context.get(APL_INTERFACE)).get("token")
        if not isinstance(token, str) or not (match := _TOKEN_PATTERN.fullmatch(token)):
            return None
        expected = hmac.new(self._token_key, match[1].encode(), hashlib.sha256).hexdigest()
        return token if hmac.compare_digest(expected, match[2]) else None

    def event_action(self, payload: Any) -> str | None:
        """Accept only our fixed controls from the currently displayed document."""
        token = self.current_token(payload)
        request = _mapping(_mapping(payload).get("request"))
        if (
            token is None or request.get("type") != APL_INTERFACE + ".UserEvent"
            or request.get("token") != token or request.get("presentationUri") is not None
        ):
            return None
        arguments = request.get("arguments")
        if not isinstance(arguments, list) or len(arguments) != 2:
            return None
        namespace, action = arguments
        if namespace != EVENT_NAMESPACE or not isinstance(action, str):
            return None
        source = _mapping(request.get("source"))
        expected = (
            ("TouchWrapper", "Press", "epgVideo" + action.title())
            if action in _ACTIONS else ("Video", "End", VIDEO_ID)
            if action == "ended" else None
        )
        actual = (source.get("type"), source.get("handler"), source.get("id"))
        return action if expected is not None and actual == expected else None

    def control(self, token: str, action: str) -> dict:
        commands = [_media_command(action)]
        if action == "stop":
            commands.append({"type": "Finish"})
        return {"type": APL_INTERFACE + ".ExecuteCommands", "token": token, "commands": commands}

    def render(self, source: str, title: str, channel: str) -> dict:
        """Use fixed expressions; values from the catalogue remain literal data."""
        return {
            "type": APL_INTERFACE + ".RenderDocument",
            "token": self._token(),
            "document": {
                "type": "APL", "version": DOCUMENT_VERSION,
                "mainTemplate": {
                    "parameters": ["payload"],
                    "items": [{
                        "type": "Frame", "width": "100vw", "height": "100vh",
                        "backgroundColor": "#09111C",
                        "item": {
                        "type": "Container", "width": "100%", "height": "100%",
                        "paddingLeft": 16, "paddingRight": 16, "paddingTop": 12,
                        "paddingBottom": 12,
                        "items": [
                            {"type": "Text", "text": "${payload.media.title}",
                             "color": "#FFFFFF", "fontSize": 26, "maxLines": 1},
                            {"type": "Text", "text": "${payload.media.channel}",
                             "color": "#B7C5D9", "fontSize": 18, "maxLines": 1},
                            {"type": "Video", "id": VIDEO_ID, "width": "100%",
                             "height": "60vh", "grow": 1, "shrink": 1,
                             "source": "${payload.media.source}", "autoplay": True,
                             "audioTrack": "background", "scale": "best-fit",
                             "onEnd": [{"type": "SendEvent",
                                        "arguments": [EVENT_NAMESPACE, "ended"]}],
                             # This handler runs in fast mode, which ignores SendEvent.
                             "onTrackFail": [{"type": "SetValue", "componentId": STATUS_ID,
                                              "property": "text",
                                              "value": "Video could not be played. "
                                                       "Please try another channel."}]},
                            {"type": "Text", "id": STATUS_ID,
                             "text": "Say Alexa, pause, resume, or stop.",
                             "color": "#B7C5D9", "fontSize": 18, "maxLines": 2},
                            {"type": "Container", "direction": "row", "height": 64,
                             "width": "100%", "items": [
                                 _button("pause", "Pause"), _button("resume", "Resume"),
                                 _button("stop", "Stop"),
                             ]},
                        ],
                        },
                    }],
                },
            },
            "datasources": {"media": {
                "title": escape(title, quote=False), "channel": escape(channel, quote=False),
                "source": source,
            }},
        }
