"""Resolve bounded spoken text against channel names and current programmes.

This module only selects opaque catalogue identifiers. It performs no I/O,
creates no playback grants, and never treats playlist order as a preference.
The caller must provide a fresh current-programme snapshot and revalidate the
selected row immediately before starting playback.
"""

import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass

from .channel_catalogue import ChannelCandidate
from .models import ProgramMatch
from .search import normalize

MAX_QUERY_LENGTH = 200
MAX_CANDIDATES = 10_000
_MAX_ALIASES = 200
_ID = re.compile(r"[a-f0-9]{24}")
_URL = re.compile(r"(?:[a-z][a-z0-9+.-]*://|www\.|https?:|\b\d{1,3}(?:\.\d{1,3}){3}\b)", re.I)
_TRANSLITERATION = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "i", "к": "k", "л": "l",
    "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts", "ч": "ch",
    "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
})
_QUALITY = {
    "sd": "sd", "сд": "sd", "480i": "sd", "480p": "sd", "576i": "sd", "576p": "sd",
    "hd": "hd", "хд": "hd", "720i": "hd", "720p": "hd",
    "fhd": "fhd", "fullhd": "fhd", "2k": "fhd", "2к": "fhd",
    "1080p": "fhd", "1080i": "fhd",
    "uhd": "uhd", "ultrahd": "uhd", "4k": "uhd", "4к": "uhd",
    "2160i": "uhd", "2160p": "uhd",
    "8k": "8k", "8к": "8k", "4320i": "8k", "4320p": "8k",
}
_VERBS = (
    "change the channel to ", "switch the channel to ", "turn on ", "put on ",
    "tune to ", "switch to ", "play ", "watch ", "show ",
    "переключи на ", "переключить на ", "включи ", "включить ",
    "покажи ", "показать ", "играй ", "поставь ", "запусти ",
)
_KINDS = {
    "channel ": "channel", "the channel ": "channel", "канал ": "channel",
    "телеканал ": "channel", "programme ": "programme", "program ": "programme",
    "передачу ": "programme", "программу ": "programme", "сериал ": "programme",
}


def _safe_text(value: str, maximum: int = MAX_QUERY_LENGTH) -> bool:
    return (
        isinstance(value, str) and 0 < len(value) <= maximum
        and not any(unicodedata.category(character).startswith("C") for character in value)
        and not _URL.search(value) and bool(normalize(value))
    )


def _key(value: str) -> str:
    words = normalize(value).split()
    # Spelled English initialisms such as "C. N. N." are a name, not three
    # independent one-character fuzzy search terms.
    if 2 <= len(words) <= 6 and all(len(word) == 1 and word.isascii() for word in words):
        return "".join(words)
    return " ".join(words)


def _quality(value: str) -> tuple[str, str | None]:
    value = re.sub(r"\bfull hd\b", "fhd", _key(value))
    value = re.sub(r"\bultra hd\b", "uhd", value)
    words = value.split()
    qualities = {_QUALITY[word] for word in words if word in _QUALITY}
    if len(qualities) > 1:
        raise ValueError("Conflicting quality labels")
    return " ".join(word for word in words if word not in _QUALITY), next(iter(qualities), None)


def _parse_query(query: str) -> tuple[dict, str]:
    if not _safe_text(query):
        raise ValueError("Query must be a safe label of at most 200 characters")
    text = " ".join(query.split())
    key = _key(text)
    for polite in ("please ", "пожалуйста "):
        if key.startswith(polite):
            key = key[len(polite):]
    if key in {verb.strip() for verb in _VERBS}:
        raise ValueError("Query must contain a channel or programme name")
    for verb in _VERBS:
        if key.startswith(verb):
            key = key[len(verb):]
            break
    for polite in (" please", " пожалуйста"):
        if key.endswith(polite):
            key = key[:-len(polite)]
    kind = "auto"
    for prefix, selected_kind in _KINDS.items():
        if key.startswith(prefix):
            key, kind = key[len(prefix):], selected_kind
            break
    key, quality = _quality(key)
    if len(key) < 2 or len(key.split()) > 32:
        raise ValueError("Query must contain a channel or programme name")
    return {"text": text, "kind": kind, "quality": quality}, key


def _variants(key: str, aliases: dict[str, list[str]] | None) -> tuple[set[str], bool]:
    result = {key}
    expanded = False
    if aliases is None:
        return result, expanded
    if not isinstance(aliases, dict) or len(aliases) > _MAX_ALIASES:
        raise ValueError("Invalid alias bounds")
    count = 0
    for label, values in aliases.items():
        if not isinstance(values, list) or len(values) > 50:
            raise ValueError("Invalid alias bounds")
        count += len(values) + 1
        if count > 2200:
            raise ValueError("Invalid alias bounds")
        group = set()
        for value in (label, *values):
            if not _safe_text(value):
                raise ValueError("Aliases must contain safe display labels")
            normalized, _ = _quality(value)
            if normalized:
                group.add(normalized)
        if key in group:
            result.update(group)
            expanded = True
    return result, expanded


def _transliterate(value: str) -> str:
    return value.translate(_TRANSLITERATION)


def _one_typo(left: str, right: str) -> bool:
    """Permit one edit or adjacent transposition only in non-short words."""
    if left == right:
        return True
    if min(len(left), len(right)) < 5 or abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        changed = [index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]]
        return len(changed) == 1 or (
            len(changed) == 2 and changed[1] == changed[0] + 1
            and left[changed[0]] == right[changed[1]]
            and left[changed[1]] == right[changed[0]]
        )
    short, long = (left, right) if len(left) < len(right) else (right, left)
    index = next((i for i, pair in enumerate(zip(short, long)) if pair[0] != pair[1]), len(short))
    return short[index:] == long[index + 1:]


@dataclass(frozen=True)
class _Pattern:
    names: set[str]
    romanized: set[str]
    tokens_by_anchor: dict[str, list[set[str]]]
    original_tokens: set[str]


def _pattern(key: str, variants: set[str]) -> _Pattern:
    romanized = {_transliterate(variant) for variant in variants}
    words = [set(variant.split()) for variant in romanized]
    frequency = Counter(word for group in words for word in group)
    by_anchor = defaultdict(list)
    for group in words:
        anchor = min(group, key=lambda word: (frequency[word], word))
        by_anchor[anchor].append(group)
    return _Pattern(variants, romanized, dict(by_anchor), set(_transliterate(key).split()))


def _evidence(name: str, pattern: _Pattern, *, alias: bool, channel: bool) -> tuple:
    """Return evidence strength, confidence, and a stable public reason."""
    if name in pattern.names:
        if channel and alias:
            return 6, 1.0, "channel_alias"
        return 5, 0.99 if channel else 0.98, "alias" if alias else "exact_name"
    romanized = _transliterate(name)
    if romanized in pattern.romanized:
        return 4, 0.96, "transliteration"
    name_words = set(romanized.split())
    if any(
        words <= name_words
        for anchor in name_words for words in pattern.tokens_by_anchor.get(anchor, ())
    ):
        return 3, 0.87 if channel else 0.86, "name_tokens"
    # Fuzzy expansion applies only to the actual spoken name. Combining fuzzy
    # edits with every configured alias would broaden operator intent and
    # multiply work by the complete alias set for each imported channel.
    unmatched = pattern.original_tokens - name_words
    if len(unmatched) == 1 and len(pattern.original_tokens) <= len(name_words):
        remaining = name_words - pattern.original_tokens
        if any(_one_typo(next(iter(unmatched)), word) for word in remaining):
            return 2, 0.78, "conservative_typo"
    return 0, 0.0, "no_match"


@dataclass(frozen=True)
class _Ranked:
    strength: int
    data: dict
    available: bool


def resolve_media(
    query: str,
    channels: list[ChannelCandidate],
    programmes: list[ProgramMatch],
    *,
    aliases: dict[str, list[str]] | None = None,
    channel_aliases: dict[str, list[str]] | None = None,
    preferred_channel_ids: list[str] | None = None,
) -> dict:
    """Return a URL-free, deterministic resolution with at most five choices.

    Exact configured channel aliases are explicit operator intent. Otherwise
    exact names precede transliteration, complete query-token containment, and
    a single conservative typo. Similar matches remain ambiguous unless an
    explicitly ordered channel preference selects an available tied candidate.
    Quality qualifiers are hard channel constraints, including programme searches.
    Regional words are never removed, approximated as short acronyms, or inferred.
    """
    parsed, key = _parse_query(query)
    if (
        not isinstance(channels, list) or not isinstance(programmes, list)
        or len(channels) > MAX_CANDIDATES or len(programmes) > MAX_CANDIDATES
    ):
        raise ValueError("Invalid catalogue bounds")
    preferences = preferred_channel_ids or []
    if (
        not isinstance(preferences, list) or len(preferences) > MAX_CANDIDATES
        or any(not isinstance(value, str) or not _ID.fullmatch(value) for value in preferences)
        or len(set(preferences)) != len(preferences)
    ):
        raise ValueError("Invalid preferred channel identifiers")
    channel_variants, channel_alias = _variants(key, channel_aliases)
    title_variants, title_alias = _variants(key, aliases)
    channel_pattern = _pattern(key, channel_variants)
    title_pattern = _pattern(key, title_variants)
    current: dict[str, dict[tuple, ProgramMatch]] = defaultdict(dict)
    for programme in programmes:
        if (
            not isinstance(programme, ProgramMatch) or not _safe_text(programme.title)
            or programme.starts_at.tzinfo is None or programme.ends_at.tzinfo is None
            or programme.starts_at >= programme.ends_at
        ):
            continue
        current[programme.channel_id][
            (programme.title, programme.starts_at, programme.ends_at)
        ] = programme

    ranked = []
    seen_ids = set()
    for channel in channels:
        if not isinstance(channel, ChannelCandidate):
            raise ValueError("Invalid channel record")
        if channel.channel_id in seen_ids:
            raise ValueError("Duplicate channel identifier")
        seen_ids.add(channel.channel_id)
        if (
            not isinstance(channel.channel_id, str) or not _ID.fullmatch(channel.channel_id)
            or not _safe_text(channel.name) or not isinstance(channel.available, bool)
        ):
            continue
        try:
            channel_name, quality = _quality(channel.name)
        except ValueError:
            continue
        if parsed["quality"] is not None and parsed["quality"] != quality:
            continue
        rows = list(current[channel.channel_id].values())
        available = channel.available and len(rows) == 1
        channel_matches = []

        def add(name: str, pattern: _Pattern, alias: bool, kind: str, row=None) -> None:
            strength, score, reason = _evidence(name, pattern, alias=alias, channel=kind == "channel")
            if strength:
                channel_matches.append(_Ranked(strength, {
                    "channel_id": channel.channel_id, "channel_name": channel.name,
                    "title": row.title if row else None,
                    "starts_at": row.starts_at.isoformat() if row else None,
                    "ends_at": row.ends_at.isoformat() if row else None,
                    "match_kind": kind, "score": score, "reason": reason,
                }, available))

        if parsed["kind"] != "programme":
            add(channel_name, channel_pattern, channel_alias, "channel")
            if _safe_text(channel.epg_name):
                # Guide aliases may omit a stream's quality. The primary
                # playlist label remains authoritative for that constraint.
                try:
                    epg_name, _ = _quality(channel.epg_name)
                except ValueError:
                    epg_name = ""
                if epg_name:
                    add(epg_name, channel_pattern, channel_alias, "channel")
        if parsed["kind"] != "channel":
            for programme in rows:
                add(_key(programme.title), title_pattern, title_alias, "programme", programme)
        if channel_matches:
            ranked.append(max(channel_matches, key=lambda item: (
                item.strength, item.data["score"], item.data["match_kind"] == "channel",
                item.data["title"] or "",
            )))

    result = {"status": "no_match", "matches": [], "query": parsed}
    if not ranked:
        return result
    ranked.sort(key=lambda item: (
        -item.strength, -item.data["score"], _key(item.data["channel_name"]),
        item.data["channel_id"],
    ))
    best = ranked[0]
    tied = [item for item in ranked if (
        item.strength == best.strength and best.data["score"] - item.data["score"] <= 0.025
    )]
    if len(tied) > 1:
        by_id = {item.data["channel_id"]: item for item in tied if item.available}
        preferred = next((by_id[value] for value in preferences if value in by_id), None)
        if preferred is None:
            result.update(status="ambiguous", matches=[item.data for item in tied[:5]])
            return result
        best = preferred
    result.update(status="matched" if best.available else "unavailable", matches=[best.data])
    return result
