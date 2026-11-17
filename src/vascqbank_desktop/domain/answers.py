"""Georgian answer letters ა–ე.

The source TXT convention marks the correct option with an ASCII asterisk
immediately before the letter (``*გ)``); parsing that convention belongs to the
import layer, not here.
"""

from __future__ import annotations

from enum import StrEnum


class AnswerLetter(StrEnum):
    """The five answer positions, in display order."""

    A = "ა"
    B = "ბ"
    C = "გ"
    D = "დ"
    E = "ე"

    @property
    def index(self) -> int:
        """Zero-based position (ა → 0 … ე → 4)."""
        return _ORDER.index(self)

    @classmethod
    def from_letter(cls, letter: str) -> AnswerLetter:
        """Parse a single Georgian letter; surrounding whitespace is ignored."""
        cleaned = letter.strip()
        for member in cls:
            if member.value == cleaned:
                return member
        raise ValueError(f"Not a Georgian answer letter (ა–ე): {letter!r}")


_ORDER: tuple[AnswerLetter, ...] = tuple(AnswerLetter)
ANSWER_LETTERS: tuple[str, ...] = tuple(m.value for m in _ORDER)
OPTION_COUNT: int = len(_ORDER)
