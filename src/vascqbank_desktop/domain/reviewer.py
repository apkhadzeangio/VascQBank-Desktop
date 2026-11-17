"""Reviewer identity.

There are no accounts, passwords or permissions: a reviewer is just a display
name chosen on this computer. The name is stamped onto *new* revisions and
review events by the future edit and approval services; events that already
exist are append-only and are never rewritten when the name changes.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum

# Offered in the profile page. Any other name may be typed instead.
REVIEWER_PRESETS: tuple[str, ...] = ("რამაზი აფხაძე", "დავით ფარღალავა")

MAX_REVIEWER_NAME_LENGTH = 80
MAX_RECENT_REVIEWERS = 10


class ReviewerNameProblem(StrEnum):
    EMPTY = "EMPTY"
    TOO_LONG = "TOO_LONG"
    INVALID_CHARACTERS = "INVALID_CHARACTERS"


class InvalidReviewerName(ValueError):
    def __init__(self, problem: ReviewerNameProblem) -> None:
        self.problem = problem
        super().__init__(problem.value)


# Control, format (zero-width, bidi marks), surrogate, private-use and unassigned
# code points have no place in a person's name and can hide in pasted text.
_FORBIDDEN_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})


def normalize_reviewer_name(raw: str) -> str:
    """Return the canonical form of *raw* or raise :class:`InvalidReviewerName`.

    Canonical: Unicode NFC, outer whitespace removed, inner whitespace runs
    collapsed to one space. Georgian letters are never altered.
    """
    text = unicodedata.normalize("NFC", raw)
    if any(unicodedata.category(c) in _FORBIDDEN_CATEGORIES and not c.isspace() for c in text):
        raise InvalidReviewerName(ReviewerNameProblem.INVALID_CHARACTERS)
    text = " ".join(text.split())
    if not text:
        raise InvalidReviewerName(ReviewerNameProblem.EMPTY)
    if len(text) > MAX_REVIEWER_NAME_LENGTH:
        raise InvalidReviewerName(ReviewerNameProblem.TOO_LONG)
    return text


def remember_reviewer(recent: tuple[str, ...], name: str) -> tuple[str, ...]:
    """Most-recent-first list of names used on this computer, without duplicates."""
    kept = [n for n in recent if n != name]
    return tuple([name, *kept][:MAX_RECENT_REVIEWERS])
