"""Display formatting that does not need Qt (and is therefore unit-testable)."""

from __future__ import annotations

from datetime import datetime

from vascqbank_desktop.domain.answers import ANSWER_LETTERS
from vascqbank_desktop.domain.catalog import QuestionDetail
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.ui import strings

NBSP = " "


def format_count(value: int) -> str:
    """12345 -> ``12 345`` with a non-breaking space."""
    return f"{value:,}".replace(",", NBSP)


def format_percent(fraction: float) -> str:
    """0.1234 -> ``12.3%``; whole numbers are shown without a decimal."""
    percent = round(fraction * 100, 1)
    return f"{percent:.0f}%" if percent == int(percent) else f"{percent:.1f}%"


def format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"  # pragma: no cover


def format_timestamp(iso: str | None) -> str:
    """UTC ISO-8601 (as stored) -> the computer's local time, ``2026-11-17 21:20``."""
    if not iso:
        return "—"
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    if moment.tzinfo is None:  # stored values always carry an offset; be forgiving anyway
        return moment.strftime("%Y-%m-%d %H:%M")
    return moment.astimezone().strftime("%Y-%m-%d %H:%M")


def content_plain_text(content: QuestionContent) -> str:
    """Question, options ა–ე, the correct answer and the explanation as plain text."""
    options = [f"{letter}) {text}" for letter, text in zip(ANSWER_LETTERS, content.options, strict=True)]
    correct = strings.DET_COPY_CORRECT.format(letter=f"{content.correct.value}) {content.correct_text}")
    return "\n\n".join(
        [
            content.stem,
            "\n".join(options),
            correct,
            f"{strings.DET_COPY_EXPLANATION}\n{content.explanation}",
        ]
    )


def question_plain_text(detail: QuestionDetail) -> str:
    """The whole question as plain text, for the clipboard."""
    return f"{detail.uid} · {detail.subject_name_ka}\n\n{content_plain_text(detail.content)}"


def format_clock(iso: str | None) -> str:
    """UTC ISO-8601 -> the computer's local time of day, ``21:20:05``."""
    if not iso:
        return "—"
    try:
        moment = datetime.fromisoformat(iso)
    except ValueError:
        return iso
    return (moment.astimezone() if moment.tzinfo else moment).strftime("%H:%M:%S")
