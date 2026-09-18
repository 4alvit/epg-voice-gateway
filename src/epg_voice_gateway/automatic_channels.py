"""Build a bounded Google lineup from all indexed channel display names."""

import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from .channel_catalogue import MAX_CHANNEL_VOICE_IDS, ChannelCandidate
from .search import normalize

MAX_AUTOMATIC_CHANNEL_CANDIDATES = 10_000
_QUALITY_SUFFIX = re.compile(
    r"(?:[\s._-]+|\s*[\[(]\s*)"
    r"(?P<quality>SD|HD|FHD|FULL[\s._-]*HD|UHD|ULTRA[\s._-]*HD|[248]K|"
    r"(?:480|576|720|1080|2160|4320)[pi])\s*[\])]?\s*$",
    re.IGNORECASE,
)
_QUALITY_RANK = {
    "sd": 0, "480i": 0, "480p": 0, "576i": 0, "576p": 0,
    "hd": 2, "720i": 2, "720p": 2,
    "fhd": 3, "fullhd": 3, "1080i": 3, "1080p": 3, "2k": 3,
    "uhd": 4, "ultrahd": 4, "2160i": 4, "2160p": 4, "4k": 4,
    "4320i": 5, "4320p": 5, "8k": 5,
}
_ALIAS_QUALITY_RANK = _QUALITY_RANK | {"сд": 0, "хд": 2, "4к": 4, "8к": 5}
_ALIAS_QUALITY = re.compile(
    r"\b(?:full\s*hd|ultra\s*hd|sd|сд|hd|хд|fhd|uhd|[248]k|[48]к|"
    r"(?:480|576|720|1080|2160|4320)[pi])\b",
    re.IGNORECASE,
)
_CYRILLIC = dict(zip(
    "абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ",
    ("a", "b", "v", "g", "d", "e", "yo", "zh", "z", "i", "y", "k", "l", "m", "n",
     "o", "p", "r", "s", "t", "u", "f", "kh", "ts", "ch", "sh", "shch", "", "y", "",
     "e", "yu", "ya", "i", "yi", "ye", "g"),
    strict=True,
))


@dataclass(frozen=True)
class _Member:
    candidate: ChannelCandidate
    name: str
    epg_name: str
    base: str
    quality: int


@dataclass(frozen=True)
class _Row:
    group: str
    selected: _Member
    members: tuple[_Member, ...]


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


def _base_name(name: str) -> tuple[str, int]:
    """Remove only trailing quality labels; regional and language words remain."""
    base, ranks = name, []
    while match := _QUALITY_SUFFIX.search(base):
        remainder = base[:match.start()].strip()
        if not _safe_name(remainder):
            break
        ranks.append(_QUALITY_RANK[re.sub(r"[\s._-]", "", match["quality"]).casefold()])
        base = remainder
    return base, max(ranks) if ranks else 1


def _automatic_synonyms(name: str) -> set[str]:
    """Suggest whole labels, never combinations of commands and device names."""
    aliases = set()
    if any(character.casefold() in _CYRILLIC for character in name):
        letters = []
        for character in name:
            replacement = _CYRILLIC.get(character.casefold(), character)
            letters.append(replacement.capitalize() if character.isupper() else replacement)
        transliterated = "".join(letters)
        aliases.add(transliterated)
    words = name.split()
    if len(words) > 1 and re.fullmatch(r"[A-Z][A-Z0-9]{1,7}", words[0]):
        aliases.add(words[0])
    if 2 <= len(words) <= 8 and all(re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", w) for w in words):
        aliases.add("".join(word[0].upper() for word in words))
    return {safe for alias in aliases if (safe := _safe_name(alias))}


def _compatible_quality(label: str, selected: _Member) -> bool:
    """An alias cannot promise a stream quality that the selected name does not state."""
    qualities = {
        _ALIAS_QUALITY_RANK[match[0].replace(" ", "")]
        for match in _ALIAS_QUALITY.finditer(normalize(label))
    }
    return not qualities or qualities == {selected.quality}


def _members(candidates: list[ChannelCandidate]) -> dict[str, list[_Member]]:
    by_id: dict[str, set[ChannelCandidate]] = defaultdict(set)
    for candidate in candidates:
        if (
            isinstance(candidate.channel_id, str)
            and re.fullmatch(r"[a-f0-9]{24}", candidate.channel_id)
            and isinstance(candidate.available, bool)
            and _safe_name(candidate.name)
        ):
            by_id[candidate.channel_id].add(candidate)
    groups: dict[str, list[_Member]] = defaultdict(list)
    for versions in by_id.values():
        # Conflicting rows with the same identifier cannot be resolved by input order.
        if len(versions) != 1:
            continue
        candidate = next(iter(versions))
        name = _safe_name(candidate.name)
        base, quality = _base_name(name)
        member = _Member(candidate, name, _safe_name(candidate.epg_name), base, quality)
        groups[base.casefold()].append(member)
    return groups


def _select_rows(groups: dict[str, list[_Member]], preferred: dict[str, int]) -> list[_Row]:
    rows = []
    for group, members in groups.items():
        selected = min(members, key=lambda member: (
            preferred.get(member.candidate.channel_id, len(preferred)), member.quality,
            member.candidate.channel_id,
        ))
        if selected.candidate.channel_id not in preferred and sum(
            member.quality == selected.quality for member in members
        ) > 1:
            # Identical-quality duplicates require an explicit operator preference.
            continue
        rows.append(_Row(group, selected, tuple(members)))
    return rows


def build_automatic_channels(
    candidates: list[ChannelCandidate], *, preferred_channel_ids: list[str] | None = None,
    aliases: dict[str, list[str]] | None = None, limit: int = 30,
) -> dict:
    """Publish deterministic, unambiguous names for a finite automatic lineup.

    All candidate names participate in collision checks before the output limit.
    Quality variants share a base label; preferences take precedence over the
    conservative SD, unmarked, HD, FHD, UHD order. Availability never affects
    selection. Identically ranked duplicates are omitted without a preference.
    Explicit aliases are whole-phrase equivalence groups, like search aliases.
    An alias spanning regional editions needs exactly one preferred edition.
    """
    if (
        not isinstance(limit, int) or isinstance(limit, bool)
        or not 1 <= limit <= MAX_CHANNEL_VOICE_IDS
        or len(candidates) > MAX_AUTOMATIC_CHANNEL_CANDIDATES
    ):
        raise ValueError("Invalid automatic channel catalogue bounds")
    preferred: dict[str, int] = {}
    for channel_id in preferred_channel_ids or []:
        preferred.setdefault(channel_id, len(preferred))
    groups = _members(candidates)
    rows = _select_rows(groups, preferred)
    # Reserve labels even for omitted duplicates and channels below the output limit.
    source_owners: dict[str, set[str]] = defaultdict(set)
    base_owners: dict[str, set[str]] = defaultdict(set)
    for group, members in groups.items():
        for member in members:
            base_owners[normalize(member.base)].add(group)
            for label in (member.base, member.name, member.epg_name):
                if label:
                    source_owners[normalize(label)].add(group)
    rows = [row for row in rows if len(base_owners[normalize(row.selected.base)]) == 1]
    rows_by_group = {row.group: row for row in rows}
    proposals: dict[str, list[tuple[int, str]]] = defaultdict(list)
    explicit_owners: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        proposals[row.group].extend((priority, label) for priority, label in (
            (1, row.selected.epg_name), (2, row.selected.name),
        ) if label)
        proposals[row.group].extend(
            (3, label) for label in _automatic_synonyms(row.selected.base)
        )
    for name, equivalents in (aliases or {}).items():
        labels = {safe for value in [name, *equivalents] if (safe := _safe_name(value))}
        owners = set().union(*(source_owners[normalize(label)] for label in labels))
        if len(owners) > 1:
            preferred_owners = {
                group for group in owners if any(
                    member.candidate.channel_id in preferred for member in groups[group]
                )
            }
            if len(preferred_owners) == 1:
                owners = preferred_owners
        # Contradictory alias groups still reserve their names, including groups
        # excluded from publication. Another config entry cannot win by ordering.
        for label in labels:
            explicit_owners[normalize(label)].update(owners)
        if len(owners) == 1:
            owner = next(iter(owners))
            if owner in rows_by_group:
                proposals[owner].extend((0, label) for label in labels)
    alias_owners: dict[str, set[str]] = defaultdict(set)
    for group, members in groups.items():
        # Even an omitted duplicate can make a short name or transliteration ambiguous.
        for member in members:
            for label in _automatic_synonyms(member.base):
                alias_owners[normalize(label)].add(group)
    for group, labels in proposals.items():
        for _, label in labels:
            alias_owners[normalize(label)].add(group)
    rows.sort(key=lambda row: (
        preferred.get(row.selected.candidate.channel_id, len(preferred)),
        normalize(row.selected.base), row.selected.base, row.selected.candidate.channel_id,
    ))
    channels = []
    for row in rows[:limit]:
        synonyms, seen = [], {normalize(row.selected.base)}
        for _, label in sorted(proposals[row.group], key=lambda item: (
            item[0], normalize(item[1]), item[1],
        )):
            key = normalize(label)
            # A configured whole-phrase alias may resolve a generated abbreviation.
            # It may never take another channel's actual name or EPG label.
            owners = explicit_owners[key] or alias_owners[key]
            if (
                key not in seen and owners == {row.group}
                and source_owners[key] <= {row.group}
                and _compatible_quality(label, row.selected)
            ):
                synonyms.append(label)
                seen.add(key)
        channels.append({
            "channel_id": row.selected.candidate.channel_id,
            "name": row.selected.base, "synonyms": synonyms[:10],
            "available": row.selected.candidate.available,
        })
    encoded = json.dumps(
        channels, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode()
    return {"channels": channels, "revision": hashlib.sha256(encoded).hexdigest()}
