"""Name matching: accent/case-insensitive, word-order agnostic, partial-aware."""

from __future__ import annotations

import re
import unicodedata

# Short connector words ("de", "la", "du"...) are ignored as standalone tokens
# so "Jean de la Fontaine" doesn't match every text containing the word "de".
MIN_TOKEN_LEN = 3


def normalize(text: str) -> str:
    """Lowercase + strip accents so 'Élodie' matches 'elodie'."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def tokenize_name(name: str) -> list[str]:
    """Split a full name into its individual searchable parts."""
    parts = re.split(r"[\s\-]+", normalize(name))
    return [p for p in parts if len(p) >= MIN_TOKEN_LEN]


def find_names(haystack: str, names: list[str]) -> list[dict]:
    """
    For each name in `names`, report whether it appears in `haystack` -- as the
    full name (in either word order, e.g. "MARSAOUI Lobna" or "Lobna Marsaoui"),
    or as just one part of it (first name only, or surname only).

    Returns one dict per name with at least one token match::

        {"name": "Lobna Marsaoui", "level": "full" | "partial",
         "matched_tokens": ["lobna", "marsaoui"]}
    """
    norm_hay = normalize(haystack)
    results: list[dict] = []
    for name in names:
        tokens = tokenize_name(name)
        if not tokens:
            continue
        matched = [t for t in tokens if re.search(rf"\b{re.escape(t)}\b", norm_hay)]
        if matched:
            level = "full" if len(matched) == len(tokens) else "partial"
            results.append({"name": name, "level": level, "matched_tokens": matched})
    return results


def find_snippet(original_text: str, token: str, context: int = 60) -> str:
    """Best-effort excerpt of `original_text` around the first occurrence of
    `token` (case-insensitive), for display. Returns '' if not found verbatim
    (e.g. the match relied on accent-stripping)."""
    m = re.search(re.escape(token), original_text, re.IGNORECASE)
    if not m:
        return ""
    start = max(0, m.start() - context)
    end = min(len(original_text), m.end() + context)
    snippet = original_text[start:end].replace("\n", " ").strip()
    return ("…" if start > 0 else "") + snippet + ("…" if end < len(original_text) else "")
