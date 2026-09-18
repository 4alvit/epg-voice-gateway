"""Shared Unicode normalization for guide indexing and spoken title queries."""

import re
import unicodedata


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(re.sub(r"[\W_]+", " ", text).split())


def query_variants(query: str, aliases: dict[str, list[str]]) -> set[str]:
    """Expand the same whole-phrase aliases for search and voice preferences."""
    normalized = normalize(query)
    variants = {normalized} if normalized else set()
    for key, values in aliases.items():
        group = {normalize(key), *(normalize(alias) for alias in values)} - {""}
        if normalized in group:
            variants.update(group)
    return variants
