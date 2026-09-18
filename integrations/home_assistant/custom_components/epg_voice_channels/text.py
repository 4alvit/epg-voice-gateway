"""Validate free-text service data and keep grants out of Assist responses."""

import re
import unicodedata

from .protocol import ChannelError

MAX_QUERY_LENGTH = 200


def validate_query(value):
    if (
        not isinstance(value, str) or not value.strip() or len(value) > MAX_QUERY_LENGTH
        or any(unicodedata.category(c).startswith("C") for c in value)
    ):
        raise ValueError("Enter a media query between 1 and 200 characters.")
    return value.strip()


def _label(value):
    """Only bounded plain names may be returned to a caller or script trace."""
    if (
        not isinstance(value, str) or not value.strip() or len(value) > 200
        or any(unicodedata.category(c).startswith("C") for c in value)
        or re.search(r"(?:[a-z][a-z0-9+.-]*://|www\.|[<>])", value, re.IGNORECASE)
    ):
        raise ChannelError()
    return value.strip()


def grant_channel(data):
    if not isinstance(data, dict) or not isinstance(data.get("programme"), dict):
        raise ChannelError()
    channel_id = data["programme"].get("channel_id")
    if not isinstance(channel_id, str) or not re.fullmatch(r"[a-f0-9]{24}", channel_id):
        raise ChannelError()
    return channel_id, _label(data["programme"].get("channel_name"))


def failure(status):
    messages = {
        "not_authorized": "This user cannot control the configured display.",
        "busy": "Another channel change is in progress. Please try again.",
        "no_match": "No matching channel or current programme was found.",
        "unavailable": "The requested media or display is unavailable. Please try again.",
        "integration_unavailable": "The media integration is not loaded.",
    }
    return {"status": status, "message": messages[status], "matches": []}


def selection_response(status, data):
    """Never relay arbitrary backend errors, URLs, IDs, or query text."""
    if not isinstance(data, dict):
        raise ChannelError()
    if status == 404 and data.get("error") == "no_media_match":
        return failure("no_match")
    if status == 404 and data.get("error") == "media_unavailable":
        return failure("unavailable")
    if status != 409 or data.get("error") != "ambiguous_media":
        raise ChannelError()
    rows = data.get("matches")
    if not isinstance(rows, list) or not 2 <= len(rows) <= 20:
        raise ChannelError()
    names = []
    for row in rows:
        if not isinstance(row, dict):
            raise ChannelError()
        name = _label(row.get("channel_name"))
        if name not in names:
            names.append(name)
    if len(names) < 2:
        raise ChannelError()
    names = names[:5]
    return {
        "status": "ambiguous", "matches": names,
        "message": "More than one channel matches. Please repeat with a full channel name: "
        + "; ".join(names) + ".",
    }
