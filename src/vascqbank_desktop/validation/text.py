"""Unicode text normalisation for Georgian content.

Rules applied to every piece of text before it is stored or compared:

* a leading UTF-8 byte-order mark is removed;
* Windows (CRLF) and old Mac (CR) line endings become LF;
* text is normalised to Unicode NFC, so visually identical strings compare
  and hash identically regardless of the editor that produced them.

Georgian Mkhedruli letters (U+10D0–U+10FF) have no decomposed forms, so NFC
never alters them; it matters for punctuation and any Latin/Greek fragments
(e.g. units, eponyms) embedded in the medical text.
"""

from __future__ import annotations

import unicodedata

BOM = "﻿"

# Mkhedruli (modern Georgian script) letters, plus Mtavruli capitals.
_MKHEDRULI = range(0x10D0, 0x1100)
_MTAVRULI = range(0x1C90, 0x1CC0)


def normalize_text(text: str) -> str:
    """Return *text* with BOM removed, LF line endings and NFC normalisation."""
    if text.startswith(BOM):
        text = text[1:]
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", text)


def is_georgian_char(char: str) -> bool:
    if len(char) != 1:
        raise ValueError("expected a single character")
    code = ord(char)
    return code in _MKHEDRULI or code in _MTAVRULI


def contains_georgian(text: str) -> bool:
    return any(is_georgian_char(c) for c in text)
