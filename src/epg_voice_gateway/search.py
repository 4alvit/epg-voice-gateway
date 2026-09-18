"""Shared Unicode normalization for guide indexing and spoken title queries."""

import re
import unicodedata


def normalize(value: str) -> str:
    text = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    return " ".join(re.sub(r"[\W_]+", " ", text).split())
