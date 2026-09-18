"""Small Google protocol extension; existing devices remain stock HA's responsibility.

This module deliberately has no Home Assistant imports. Its tests exercise mixed
device requests and fail-closed selection without simulating physical playback.
"""

import re
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass
from urllib.parse import urlsplit

from .const import DEVICE_NAME, MAX_CHANNELS

SYNC = "action.devices.SYNC"
QUERY = "action.devices.QUERY"
EXECUTE = "action.devices.EXECUTE"
DISCONNECT = "action.devices.DISCONNECT"
SELECT = "action.devices.commands.selectChannel"


class ChannelError(Exception):
    """An intentionally non-sensitive protocol error."""

    def __init__(self, code="deviceOffline"):
        super().__init__(code)
        self.code = code


def origin(value, *, https=False):
    """Accept a fixed HTTP origin, never userinfo, paths, queries or fragments."""
    if not isinstance(value, str) or any(ord(c) < 33 for c in value):
        raise ValueError("Invalid origin")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in (("https",) if https else ("http", "https"))
        or not parsed.hostname or parsed.username is not None or parsed.password is not None
        or parsed.path not in ("", "/") or parsed.query or parsed.fragment
        or "\\" in value
    ):
        raise ValueError("Invalid origin")
    _ = parsed.port
    return value.rstrip("/")


@dataclass(frozen=True, repr=False)
class Settings:
    """Private settings are held by HA's protected config entry, never Google."""

    gateway_origin: str
    gateway_token: str
    signed_media_origin: str
    google_entry_id: str
    agent_user_id: str
    target_entity: str

    @classmethod
    def parse(cls, data):
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("Invalid configuration")
        values = dict(data)
        values["gateway_origin"] = origin(values["gateway_origin"])
        values["signed_media_origin"] = origin(values["signed_media_origin"], https=True)
        if not isinstance(values["gateway_token"], str) or not re.fullmatch(
            r"[\x21-\x7e]{16,4096}", values["gateway_token"]
        ):
            raise ValueError("Invalid token")
        for key in ("google_entry_id", "agent_user_id"):
            if not isinstance(values[key], str) or not re.fullmatch(r"[a-zA-Z0-9_-]{8,128}", values[key]):
                raise ValueError("Invalid identifier")
        if not isinstance(values["target_entity"], str) or not re.fullmatch(
            r"media_player\.[a-z0-9_]+", values["target_entity"]
        ):
            raise ValueError("Invalid target")
        return cls(**values)


def _name(value):
    if (
        not isinstance(value, str) or not value.strip() or len(value) > 100
        or any(unicodedata.category(c).startswith("C") for c in value)
        or re.search(r"(?:[a-z][a-z0-9+.-]*://|www\.)", value, re.IGNORECASE)
    ):
        raise ChannelError()
    return value


def parse_catalogue(data):
    """Validate all names and IDs before exposing or accepting any selection."""
    if (
        not isinstance(data, dict) or not isinstance(data.get("channels"), list)
        or len(data["channels"]) > MAX_CHANNELS
        or not isinstance(data.get("revision"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", data["revision"])
    ):
        raise ChannelError()
    channels = []
    ids = set()
    names = set()
    for row in data["channels"]:
        if (
            not isinstance(row, dict) or not isinstance(row.get("channel_id"), str)
            or not re.fullmatch(r"[a-f0-9]{24}", row["channel_id"])
            or row["channel_id"] in ids or not isinstance(row.get("available"), bool)
            or not isinstance(row.get("synonyms"), list) or len(row["synonyms"]) > 10
        ):
            raise ChannelError()
        labels = [_name(row.get("name")), *[_name(v) for v in row["synonyms"]]]
        for label in labels:
            key = unicodedata.normalize("NFKC", label).casefold()
            if key in names:
                raise ChannelError()
            names.add(key)
        ids.add(row["channel_id"])
        channels.append({"key": row["channel_id"], "names": labels, "available": row["available"]})
    return channels


def advertised_channels(channels):
    """EPG gaps affect selection, not a channel's stable advertised identity."""
    return [{"key": item["key"], "names": item["names"]} for item in channels]


def device_description(device_id, channels):
    return {
        "id": device_id,
        "type": "action.devices.types.TV",
        "traits": ["action.devices.traits.Channel"],
        "name": {"name": DEVICE_NAME},
        "willReportState": False,
        "attributes": {
            "availableChannels": advertised_channels(channels),
            "commandOnlyChannels": True,
        },
    }


def select_channel(executions, channels):
    """Accept one advertised ID; numbers and arbitrary names are not selectors."""
    if len(executions) != 1 or executions[0].get("command") != SELECT:
        raise ChannelError("functionNotSupported")
    params = executions[0].get("params")
    if not isinstance(params, dict) or set(params) - {"channelCode", "channelName", "channelNumber"}:
        raise ChannelError("noAvailableChannel")
    code = params.get("channelCode")
    selected = next((item for item in channels if item["key"] == code), None)
    if selected is None or "channelNumber" in params:
        raise ChannelError("noAvailableChannel")
    if "channelName" in params:
        name = params["channelName"]
        if not isinstance(name, str) or unicodedata.normalize("NFKC", name).casefold() not in {
            unicodedata.normalize("NFKC", item).casefold() for item in selected["names"]
        }:
            raise ChannelError("noAvailableChannel")
    if not selected["available"]:
        raise ChannelError("noAvailableChannel")
    return code


def media_url(data, allowed_origin, channel_id):
    if (
        not isinstance(data, dict) or data.get("status") != "ready"
        or not isinstance(data.get("programme"), dict)
        or data["programme"].get("channel_id") != channel_id
        or type(data.get("expires_at")) is not int
        or not time.time() < data["expires_at"] < 2**53
    ):
        raise ChannelError("channelSwitchFailed")
    value = data.get("media_url")
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 for c in value):
        raise ChannelError("channelSwitchFailed")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https" or f"{parsed.scheme}://{parsed.netloc}" != allowed_origin
        or parsed.username is not None or parsed.password is not None
        or parsed.query or parsed.fragment or "\\" in value
        or not re.fullmatch(
            r"/media/v1\.[a-zA-Z0-9_-]{32}\.[0-9]{1,12}\.[a-zA-Z0-9_-]{43}/[a-zA-Z0-9_-]{32}\.m3u8",
            parsed.path,
        )
    ):
        raise ChannelError("channelSwitchFailed")
    if int(parsed.path.split("/")[2].split(".")[2]) != data["expires_at"]:
        raise ChannelError("channelSwitchFailed")
    return value


def parse_json(raw):
    """Reject ambiguous duplicate keys and non-JSON numeric constants."""
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(_value):
        raise ValueError("Invalid JSON number")

    import json
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def validate_message(message):
    """Reject malformed envelopes before calling stock or custom code."""
    if (
        not isinstance(message, dict) or not isinstance(message.get("requestId"), str)
        or not 1 <= len(message["requestId"]) <= 256
        or not isinstance(message.get("inputs"), list) or len(message["inputs"]) != 1
        or not isinstance(message["inputs"][0], dict)
        or not isinstance(message["inputs"][0].get("intent"), str)
    ):
        return False
    item = message["inputs"][0]
    payload = item.get("payload", {})
    if not isinstance(payload, dict):
        return False
    if item["intent"] == QUERY:
        groups = [{"devices": payload.get("devices")}]
    elif item["intent"] == EXECUTE:
        groups = payload.get("commands")
        if not isinstance(groups, list) or not 1 <= len(groups) <= 32:
            return False
    else:
        return True
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("devices"), list):
            return False
        if not 1 <= len(group["devices"]) <= 128 or any(
            not isinstance(device, dict) or not isinstance(device.get("id"), str)
            or not 1 <= len(device["id"]) <= 256 for device in group["devices"]
        ):
            return False
        if item["intent"] == EXECUTE and (
            not isinstance(group.get("execution"), list) or not 1 <= len(group["execution"]) <= 32
            or any(not isinstance(e, dict) for e in group["execution"])
        ):
            return False
    return True


async def handle_message(message, device_id, authorized, delegate, catalogue, play):
    """Partition once, delegate once, and merge without changing stock results.

`catalogue` may return a last known list for SYNC only. `play` must recheck user
permissions and fetch a fresh list before creating a grant or dispatching Cast.
"""
    if not validate_message(message):
        return {"requestId": message.get("requestId", "") if isinstance(message, dict) else "",
                "payload": {"errorCode": "protocolError"}}
    intent = message["inputs"][0]["intent"]
    if intent == SYNC:
        result = await delegate(message)
        if authorized and result and "errorCode" not in result.get("payload", {}):
            try:
                channels = await catalogue()
            except ChannelError:
                channels = []
            if channels:
                result = deepcopy(result)
                result["payload"]["devices"].append(device_description(device_id, channels))
        return result
    if intent not in (QUERY, EXECUTE):
        return await delegate(message)
    forwarded = deepcopy(message)
    payload = forwarded["inputs"][0]["payload"]
    found = False
    executions = []
    if intent == QUERY:
        found = any(d["id"] == device_id for d in payload["devices"])
        payload["devices"] = [d for d in payload["devices"] if d["id"] != device_id]
        has_stock = bool(payload["devices"])
    else:
        commands = []
        for command in payload["commands"]:
            if any(d["id"] == device_id for d in command["devices"]):
                found = True
                executions.extend(command["execution"])
            command["devices"] = [d for d in command["devices"] if d["id"] != device_id]
            if command["devices"]:
                commands.append(command)
        payload["commands"] = commands
        has_stock = bool(commands)
    if not found:
        return await delegate(message)
    result = await delegate(forwarded) if has_stock else {
        "requestId": message["requestId"],
        "payload": {"devices": {}} if intent == QUERY else {"commands": []},
    }
    if not result or "errorCode" in result.get("payload", {}):
        return result
    result = deepcopy(result)
    if intent == QUERY:
        # commandOnlyChannels has no queryable channel state. Availability is
        # conservative and does not claim that the physical display is playing.
        online = False
        if authorized:
            try:
                online = bool(await catalogue())
            except ChannelError:
                pass
        result["payload"]["devices"][device_id] = {"online": online}
        return result
    item = {"ids": [device_id], "status": "ERROR", "errorCode": "deviceOffline"}
    if authorized:
        try:
            await play(executions)
            item = {"ids": [device_id], "status": "SUCCESS", "states": {"online": True}}
        except ChannelError as err:
            item["errorCode"] = err.code
    result["payload"]["commands"].append(item)
    return result
