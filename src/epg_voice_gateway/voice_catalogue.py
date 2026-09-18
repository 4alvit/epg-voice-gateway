"""Build finite voice selections without publishing playback URLs or credentials."""

import hashlib
import json
import re
import unicodedata

from .models import ProgramMatch
from .search import normalize, query_variants

_RESERVED_LABELS = {"ready", "unavailable"}


def _safe_text(value: str, maximum: int) -> str:
    if any(unicodedata.category(character).startswith("C") for character in value):
        return ""
    text = " ".join(value.split())
    if (
        not normalize(text) or len(text) > maximum
        or normalize(text) in _RESERVED_LABELS
        or re.search(r"https?://", text, re.IGNORECASE)
    ):
        return ""
    return text


def build_voice_catalogue(
    candidates: list[ProgramMatch], preferred_queries: list[str],
    aliases: dict[str, list[str]], limit: int,
) -> dict:
    """Choose one deterministic channel per title from storage-ordered rows.

    Preferences use their configured label and the complete matching EPG title
    as the playback query. Remaining titles follow channel order. A long title
    may appear under a short preferred label, but queries never exceed 200
    characters. Labels are unique after the same Unicode normalization used by
    playback search. No truncated title is ever turned into a broader query.
    """
    representatives = {}
    for match in candidates:
        title = _safe_text(match.title, 200)
        if not title or not re.fullmatch(r"[a-f0-9]{24}", match.channel_id):
            continue
        representatives.setdefault(normalize(title), (title, match.channel_id))

    options = []
    used_titles = set()
    used_labels = set()

    def append(label: str, title_key: str) -> bool:
        label = _safe_text(label, 100)
        label_key = normalize(label)
        if not label or label_key in used_labels or title_key in used_titles:
            return False
        title, channel_id = representatives[title_key]
        options.append({"label": label, "query": title, "channel_id": channel_id})
        used_labels.add(label_key)
        used_titles.add(title_key)
        return True

    for preference in preferred_queries:
        variants = query_variants(preference, aliases)
        for title_key in representatives:
            if title_key not in used_titles and any(
                " " + variant + " " in " " + title_key + " " for variant in variants
            ):
                if append(preference, title_key):
                    break
        if len(options) >= limit:
            break
    for title_key, (title, _) in representatives.items():
        if len(options) >= limit:
            break
        append(title, title_key)

    encoded = json.dumps(options, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return {"options": options, "revision": hashlib.sha256(encoded).hexdigest()}
