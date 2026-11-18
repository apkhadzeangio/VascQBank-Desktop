"""UI helpers that need little or no window: formatting, geometry safety, text rendering, previews."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QRect  # noqa: E402
from PySide6.QtGui import QFont, QFontMetrics, QTextDocument  # noqa: E402

from vascqbank_desktop.domain.answers import AnswerLetter  # noqa: E402
from vascqbank_desktop.domain.catalog import QuestionDetail  # noqa: E402
from vascqbank_desktop.domain.question import QuestionContent, SourceKind  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.services.question_stats import (  # noqa: E402
    BankCounts,
    ProgressSummary,
    SubjectCounts,
    summarize,
)
from vascqbank_desktop.ui.formatting import (  # noqa: E402
    format_count,
    format_percent,
    format_size,
    format_timestamp,
    question_plain_text,
)
from vascqbank_desktop.ui.progress_panel import StatusBar  # noqa: E402
from vascqbank_desktop.ui.question_delegate import fit_preview  # noqa: E402
from vascqbank_desktop.ui.widgets import paragraphs_html  # noqa: E402
from vascqbank_desktop.ui.window_state import is_reachable, restore_geometry  # noqa: E402

pytestmark = pytest.mark.gui


# ------------------------------------------------------------------ formatting


def test_format_count_uses_non_breaking_spaces() -> None:
    assert format_count(0) == "0" and format_count(900) == "900"
    assert format_count(10_000) == "10 000" and format_count(1_234_567) == "1 234 567"


def test_format_percent_and_size() -> None:
    assert format_percent(0) == "0%" and format_percent(1) == "100%"
    assert format_percent(0.1234) == "12.3%" and format_percent(0.5) == "50%"
    assert format_size(512) == "512 B" and format_size(2048) == "2.0 KB"
    assert format_size(35_168_256) == "33.5 MB"


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset() is POSIX-only (absent on Windows)")
def test_timestamps_are_shown_in_the_local_time_zone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TZ", "Asia/Tbilisi")  # UTC+4, no daylight saving
    time.tzset()
    try:
        assert format_timestamp("2026-11-17T17:20:00+00:00") == "2026-11-17 21:20"
        assert format_timestamp("2026-11-17T23:30:00+00:00") == "2026-11-18 03:30"  # date rolls over
    finally:
        monkeypatch.undo()
        time.tzset()
    assert format_timestamp(None) == "—" and format_timestamp("") == "—"
    assert format_timestamp("not a time") == "not a time"  # never raises on odd data


# ------------------------------------------------------------------ geometry safety


def test_window_geometry_must_stay_reachable_on_a_connected_screen() -> None:
    screens = [QRect(0, 0, 1920, 1080)]
    assert is_reachable(QRect(100, 100, 1200, 800), screens)
    assert is_reachable(QRect(1700, 500, 900, 600), screens)  # mostly off the right edge, still grabbable
    assert not is_reachable(QRect(3000, 100, 1200, 800), screens)  # left on a monitor that is gone
    assert not is_reachable(QRect(-5000, -5000, 1200, 800), screens)
    assert not is_reachable(QRect(1900, 1070, 800, 600), screens)  # only a sliver visible
    two = [QRect(0, 0, 1920, 1080), QRect(1920, 0, 1920, 1080)]
    assert is_reachable(QRect(2500, 100, 1200, 800), two)


def test_restore_geometry_rejects_garbage(styled_qapp) -> None:
    from PySide6.QtWidgets import QWidget

    widget = QWidget()
    assert not restore_geometry(widget, None)
    assert not restore_geometry(widget, "")
    assert not restore_geometry(widget, "not base64 !!!")
    assert not restore_geometry(widget, "QUJD")  # valid base64, not a Qt geometry blob


def test_geometry_round_trip(styled_qapp, qtbot) -> None:
    from PySide6.QtWidgets import QWidget

    from vascqbank_desktop.ui.window_state import encode_geometry

    first = QWidget()
    qtbot.addWidget(first)
    first.resize(600, 400)  # the offscreen test screen is 800x600; Qt clamps anything larger itself
    first.show()
    encoded = encode_geometry(first)
    assert encoded.isascii()
    second = QWidget()
    qtbot.addWidget(second)
    assert restore_geometry(second, encoded)
    assert (second.width(), second.height()) == (first.width(), first.height())


# ------------------------------------------------------------------ safe text rendering


@pytest.mark.parametrize(
    "text",
    [
        "PaO2 < 60 mmHg & SaO2 > 90%",
        "<b>not bold</b> <script>alert(1)</script> &amp; &lt;",
        "მრავალი   ინტერვალი  და\nახალი ხაზი",  # runs of spaces and a line break are kept
        "ანევრიზმა ≥ 5,5 cm · ΔP × 10⁹/ლ „ციტატა“",
        "  წინა და უკანა ინტერვალი  ",
    ],
)
def test_clinical_text_is_displayed_exactly_and_never_as_markup(text: str) -> None:
    document = QTextDocument()
    document.setHtml(paragraphs_html(text))
    assert document.toPlainText() == text


def test_blank_lines_separate_paragraphs() -> None:
    document = QTextDocument()
    document.setHtml(paragraphs_html("პირველი აბზაცი\n\nმეორე აბზაცი\n \nმესამე"))
    assert document.toPlainText().split("\n") == ["პირველი აბზაცი", "მეორე აბზაცი", "მესამე"]
    assert document.blockCount() == 3


def test_paragraphs_html_escapes_everything() -> None:
    html = paragraphs_html('<img src=x onerror=1> & "quoted"')
    assert "<img" not in html and "&lt;img" in html and "&amp;" in html


# ------------------------------------------------------------------ preview fitting


def test_preview_text_that_fits_is_left_alone(styled_qapp) -> None:
    font = QFont()
    assert fit_preview("მოკლე ტექსტი", ((0, 5),), font, 600) == ("მოკლე ტექსტი", ((0, 5),))


def test_long_preview_is_elided_after_two_lines_and_spans_are_clipped(styled_qapp) -> None:
    font = QFont()
    font.setPointSize(10)
    text = "ეს არის საკმაოდ გრძელი სატესტო ტექსტი " * 12
    spans = ((4, 7), (len(text) - 10, len(text) - 5))
    fitted, clipped = fit_preview(text, spans, font, 220, max_lines=2)
    assert fitted.endswith("…") and len(fitted) < len(text)
    assert clipped == ((4, 7),)  # the span beyond the visible part is dropped
    assert QFontMetrics(font).horizontalAdvance(fitted.split("\n")[-1]) > 0


# ------------------------------------------------------------------ progress


def _counts() -> BankCounts:
    pending, review = ReviewStatus.PENDING_REVIEW, ReviewStatus.IN_REVIEW
    approved, rejected = ReviewStatus.APPROVED, ReviewStatus.REJECTED
    return BankCounts(
        {
            "ALI": SubjectCounts("ALI", 10, {pending: 6, review: 2, approved: 1, rejected: 1}),
            "VENOUS": SubjectCounts("VENOUS", 5, {pending: 5}),
        }
    )


def test_progress_summary_uses_the_real_counts() -> None:
    bank = _counts()
    whole = summarize(bank)
    assert whole.total == 15 and whole.count(ReviewStatus.PENDING_REVIEW) == 11
    assert whole.count(ReviewStatus.APPROVED) == 1
    ali = summarize(bank, "ALI")
    assert ali.total == 10 and ali.count(ReviewStatus.IN_REVIEW) == 2
    assert ali.approved_share == pytest.approx(0.1)
    assert summarize(bank, "MESENTERIC_RENAL") == ProgressSummary(0, {s: 0 for s in ReviewStatus})
    assert summarize(bank, "MESENTERIC_RENAL").approved_share == 0.0


def test_status_bar_segments_are_proportional(styled_qapp) -> None:
    bar = StatusBar()
    assert bar.segments() == []
    bar.set_summary(summarize(_counts(), "ALI"))
    shares = dict(bar.segments())
    assert shares[ReviewStatus.PENDING_REVIEW] == pytest.approx(0.6)
    assert shares[ReviewStatus.APPROVED] == pytest.approx(0.1)
    assert sum(shares.values()) == pytest.approx(1.0)
    assert [s for s, _ in bar.segments()] == [  # drawing order: finished work first
        ReviewStatus.APPROVED,
        ReviewStatus.IN_REVIEW,
        ReviewStatus.REJECTED,
        ReviewStatus.PENDING_REVIEW,
    ]


# ------------------------------------------------------------------ clipboard text


def test_question_plain_text_for_the_clipboard() -> None:
    detail = QuestionDetail(
        id=1,
        uid="ALI-0042",
        subject_code="ALI",
        subject_name_ka="კიდურის მწვავე იშემია",
        source_number=42,
        status=ReviewStatus.PENDING_REVIEW,
        content=QuestionContent(
            "კითხვის ტექსტი", ("ა1", "ბ2", "გ3", "დ4", "ე5"), AnswerLetter.C, "განმარტება!"
        ),
        revision_id=1,
        revision_no=1,
        lock_version=0,
        source_kind=SourceKind.PAIRED_TXT,
        source_file="ALI_questions.txt",
        source_line=7,
        import_batch_id=1,
        created_at="2026-11-17T17:20:00+00:00",
        modified_at="2026-11-17T17:20:00+00:00",
    )
    text = question_plain_text(detail)
    assert text.startswith("ALI-0042 · კიდურის მწვავე იშემია")
    assert "ა) ა1\nბ) ბ2\nგ) გ3\nდ) დ4\nე) ე5" in text
    assert "სწორი პასუხი: გ) გ3" in text and text.endswith("განმარტება:\nგანმარტება!")
