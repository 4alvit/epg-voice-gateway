"""Publish a finite channel lineup without provider identifiers or playback URLs."""

import hashlib
import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from .search import normalize

MAX_CHANNEL_VOICE_IDS = 200


@dataclass(frozen=True)
class ChannelCandidate:
    """Only display names and current availability cross the storage boundary."""

    channel_id: str
    name: str
    epg_name: str
    available: bool


def _safe_name(value: str) -> str:
    if not isinstance(value, str) or any(
        unicodedata.category(character).startswith("C") for character in value
    ):
        return ""
    value = " ".join(unicodedata.normalize("NFKC", value).split())
    if (
        not normalize(value) or len(value) > 100
        or re.search(r"(?:[a-z][a-z0-9+.-]*://|www\.)", value, re.IGNORECASE)
    ):
        return ""
    return value


def build_channel_catalogue(candidates: list[ChannelCandidate], limit: int) -> dict:
    """Keep configured order and reject ambiguous spoken names before limiting.

    Duplicate normalized channel names exclude every conflicting channel. A
    canonical name takes precedence over another channel's synonym; synonyms
    shared by multiple channels are omitted everywhere. Availability never
    removes a configured channel, so a guide gap cannot shift the lineup.
    Channel numbers are absent because no verified numbering is stored.
    """
    if (
        not isinstance(limit, int) or isinstance(limit, bool)
        or not 1 <= limit <= MAX_CHANNEL_VOICE_IDS
        or len(candidates) > MAX_CHANNEL_VOICE_IDS
    ):
        raise ValueError("Invalid channel catalogue bounds")

    rows = []
    seen_ids = set()
    for candidate in candidates:
        if (
            not re.fullmatch(r"[a-f0-9]{24}", candidate.channel_id)
            or candidate.channel_id in seen_ids or not isinstance(candidate.available, bool)
        ):
            continue
        name = _safe_name(candidate.name)
        if not name:
            continue
        seen_ids.add(candidate.channel_id)
        synonym = _safe_name(candidate.epg_name)
        if normalize(synonym) == normalize(name):
            synonym = ""
        rows.append((candidate, name, synonym))

    name_counts = Counter(normalize(name) for _, name, _ in rows)
    rows = [row for row in rows if name_counts[normalize(row[1])] == 1]
    synonym_counts = Counter(normalize(synonym) for _, _, synonym in rows if synonym)
    channels = []
    for candidate, name, synonym in rows[:limit]:
        key = normalize(synonym)
        synonyms = [synonym] if (
            synonym and key not in name_counts and synonym_counts[key] == 1
        ) else []
        channels.append({
            "channel_id": candidate.channel_id, "name": name,
            "synonyms": synonyms, "available": candidate.available,
        })

    encoded = json.dumps(
        channels, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    return {"channels": channels, "revision": hashlib.sha256(encoded).hexdigest()}
