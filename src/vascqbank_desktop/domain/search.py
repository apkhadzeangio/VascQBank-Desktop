"""Search vocabulary shared by the FTS5 index and the LIKE fallback.

What a reviewer types is normalised to NFC and split on whitespace into
*terms*. A question matches when **every** term occurs somewhere in its
searchable text (substring match, so Georgian word fragments work). The
searchable text of a question is its immutable ID plus the *current* stem, the
five options and the explanation, joined by newlines — see :func:`document_text`.
Both back ends implement exactly this definition, which a test enforces.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from vascqbank_desktop.domain.answers import ANSWER_LETTERS

MAX_QUERY_LENGTH = 200
MAX_TERMS = 6
# The FTS5 trigram tokenizer cannot match fewer than three characters.
MIN_FTS_TERM_LENGTH = 3

PREVIEW_WIDTH = 150
BROWSE_PREVIEW_WIDTH = 220
MAX_HIGHLIGHTS = 12
ELLIPSIS = "…"

_WHITESPACE = re.compile(r"\s+")


def normalize_query(text: str) -> str:
    """NFC, whitespace runs collapsed, trimmed and length-limited."""
    collapsed = _WHITESPACE.sub(" ", unicodedata.normalize("NFC", text)).strip()
    return collapsed[:MAX_QUERY_LENGTH].strip()


def split_terms(query: str) -> tuple[str, ...]:
    """Distinct terms in typed order (compared case-insensitively), at most :data:`MAX_TERMS`."""
    seen: set[str] = set()
    terms: list[str] = []
    for word in normalize_query(query).split(" "):
        key = word.lower()
        if word and key not in seen:
            seen.add(key)
            terms.append(word)
        if len(terms) == MAX_TERMS:
            break
    return tuple(terms)


def fts_supports(terms: tuple[str, ...]) -> bool:
    """Whether the trigram index can answer these terms on its own."""
    return bool(terms) and all(len(t) >= MIN_FTS_TERM_LENGTH for t in terms)


def fts_match_expression(terms: tuple[str, ...]) -> str:
    """FTS5 query: each term as a quoted string (so ``* - AND NEAR :`` are literal), ANDed."""
    return " AND ".join('"' + term.replace('"', '""') + '"' for term in terms)


def like_pattern(term: str) -> str:
    """``%term%`` with LIKE wildcards in *term* escaped (use with ``ESCAPE '\\'``)."""
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def document_text(uid: str, stem: str, options: tuple[str, ...], explanation: str) -> str:
    """The text a question is searched by; newline-separated, ID first."""
    return "\n".join((uid, stem, *options, explanation))


# --------------------------------------------------------------------------
# Previews
# --------------------------------------------------------------------------
class PreviewField(StrEnum):
    ID = "ID"
    STEM = "STEM"
    OPTION = "OPTION"
    EXPLANATION = "EXPLANATION"


@dataclass(frozen=True, slots=True)
class Preview:
    """A short single-line excerpt, with the character ranges to emphasise."""

    text: str
    field: PreviewField = PreviewField.STEM
    option_letter: str | None = None
    spans: tuple[tuple[int, int], ...] = ()


def _flat(text: str) -> str:
    return _WHITESPACE.sub(" ", text).strip()


def _lower_same_length(text: str) -> str:
    """``text.lower()`` unless that changes the length (offsets must stay valid)."""
    lowered = text.lower()
    return lowered if len(lowered) == len(text) else text


def _occurrences(text: str, terms: tuple[str, ...]) -> list[tuple[int, int]]:
    haystack = _lower_same_length(text)
    found: list[tuple[int, int]] = []
    for term in terms:
        needle = _lower_same_length(term)
        start = haystack.find(needle)
        while start != -1 and len(found) < MAX_HIGHLIGHTS * 4:
            found.append((start, start + len(needle)))
            start = haystack.find(needle, start + len(needle))
    found.sort()
    merged: list[tuple[int, int]] = []
    for begin, end in found:
        if merged and begin <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((begin, end))
    return merged[:MAX_HIGHLIGHTS]


def _window(text: str, anchor: int, width: int) -> str:
    """Cut *text* to about *width* characters around *anchor*, marking cut ends with ``…``."""
    if len(text) <= width:
        return text
    start = max(0, anchor - width // 3)
    end = min(len(text), start + width)
    start = max(0, end - width)
    # Begin and end on a word boundary when one is close by.
    if start > 0:
        nearby = text.find(" ", start, start + 14)
        if nearby != -1:
            start = nearby + 1
    if end < len(text):
        nearby = text.rfind(" ", max(start, end - 14), end)
        if nearby != -1:
            end = nearby
    prefix = ELLIPSIS if start > 0 else ""
    suffix = ELLIPSIS if end < len(text) else ""
    return prefix + text[start:end] + suffix


def browse_preview(stem: str, width: int = BROWSE_PREVIEW_WIDTH) -> Preview:
    """Start of the question text, for the unfiltered catalog."""
    text = _flat(stem)
    if len(text) > width:
        text = text[:width].rstrip() + ELLIPSIS
    return Preview(text=text, field=PreviewField.STEM)


def match_preview(
    uid: str,
    stem: str,
    options: tuple[str, ...],
    explanation: str,
    terms: tuple[str, ...],
    width: int = PREVIEW_WIDTH,
) -> Preview:
    """Excerpt around the first place the search terms occur.

    Fields are tried in reading order (ID, stem, options ა…ე, explanation); the
    first one that contains any term supplies the excerpt. If none does (the
    terms matched across fields), the start of the stem is shown instead.
    """
    candidates: list[tuple[PreviewField, str | None, str]] = [
        (PreviewField.ID, None, uid),
        (PreviewField.STEM, None, _flat(stem)),
        *(
            (PreviewField.OPTION, letter, _flat(text))
            for letter, text in zip(ANSWER_LETTERS, options, strict=False)
        ),
        (PreviewField.EXPLANATION, None, _flat(explanation)),
    ]
    for field, letter, text in candidates:
        hits = _occurrences(text, terms)
        if not hits:
            continue
        excerpt = _window(text, hits[0][0], width)
        # Spans are measured on the excerpt itself, so they stay valid whatever was cut.
        return Preview(
            text=excerpt, field=field, option_letter=letter, spans=tuple(_occurrences(excerpt, terms))
        )
    return browse_preview(stem, width)
