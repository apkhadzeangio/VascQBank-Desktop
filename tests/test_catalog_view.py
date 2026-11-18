"""Catalog page behaviour: listing, lazy loading, detail rendering, navigation, jump, filters, search."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6")

from bank import make_question, seed, seed_counts, set_status, simulate_edit, snapshot  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.answers import ANSWER_LETTERS, AnswerLetter  # noqa: E402
from vascqbank_desktop.domain.catalog import SearchMode, SortOrder  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.catalog_service import CatalogService  # noqa: E402
from vascqbank_desktop.services.question_stats import bank_counts  # noqa: E402
from vascqbank_desktop.services.session_service import ContextNotice, RestoredContext  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.catalog_view import CatalogView  # noqa: E402
from vascqbank_desktop.ui.question_list_model import MAX_CACHED_PAGES, PAGE_SIZE  # noqa: E402

pytestmark = pytest.mark.gui

WAIT_MS = 15_000


def make_view(qtbot, styled_qapp, app_paths: AppPaths) -> CatalogView:
    view = CatalogView(CatalogService(app_paths), SUBJECTS)
    qtbot.addWidget(view)
    view.resize(1100, 700)
    view.show()
    view.set_counts(bank_counts(app_paths))
    return view


@pytest.fixture
def view(qtbot, styled_qapp, app_paths: AppPaths) -> CatalogView:
    seed_counts(app_paths, {"ALI": 30, "VENOUS": 20, "PAD_CLTI": 10})
    return make_view(qtbot, styled_qapp, app_paths)


def settle(qtbot, view: CatalogView) -> None:
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)


def listed_uids(view: CatalogView) -> list[str]:
    summaries = (view.model.summary_at(row) for row in range(view.model.rowCount()))
    return [s.uid for s in summaries if s is not None]


# ------------------------------------------------------------------ empty and populated states


def test_empty_bank_shows_an_import_prompt(qtbot, styled_qapp, app_paths: AppPaths) -> None:
    seed(app_paths, [])
    view = make_view(qtbot, styled_qapp, app_paths)
    assert view.empty_label.text() == strings.CAT_EMPTY_BANK
    assert view.import_button.isVisibleTo(view)
    with qtbot.waitSignal(view.importRequested, timeout=1000):
        view.import_button.click()
    assert view.detail_view.question_uid is None
    assert not view.prev_button.isEnabled() and not view.next_button.isEnabled()


def test_populated_catalog_lists_every_question_with_status(view: CatalogView) -> None:
    assert view.model.rowCount() == 60 and view.result_label.text() == strings.CAT_COUNT.format(count=60)
    first = view.model.summary_at(0)
    assert first is not None and first.uid == "PAD_CLTI-0001"  # the app's subject order
    assert first.status is ReviewStatus.PENDING_REVIEW
    assert not view.import_button.isVisibleTo(view)
    assert view.detail_view.placeholder.isVisibleTo(view)  # nothing opened yet


def test_strip_shows_real_counts_for_the_bank_and_the_open_subject(
    view: CatalogView, app_paths: AppPaths
) -> None:
    set_status(app_paths, "ALI-0001", ReviewStatus.APPROVED)
    set_status(app_paths, "ALI-0002", ReviewStatus.IN_REVIEW)
    view.set_counts(bank_counts(app_paths))
    bank = view.strip.bank_line
    assert "60" in bank.total_label.text()
    assert "<b>1</b>" in bank.status_labels[ReviewStatus.APPROVED].text()
    assert "<b>58</b>" in bank.status_labels[ReviewStatus.PENDING_REVIEW].text()
    assert not view.strip.subject_line.total_label.isVisibleTo(view)  # no subject chosen yet
    view.set_subject("ALI")
    line = view.strip.subject_line
    assert line.scope_label.text() == "ALI" and "30" in line.total_label.text()
    assert "<b>28</b>" in line.status_labels[ReviewStatus.PENDING_REVIEW].text()
    assert "<b>1</b>" in line.status_labels[ReviewStatus.IN_REVIEW].text()


# ------------------------------------------------------------------ lazy loading


def test_rows_are_loaded_lazily_one_page_at_a_time(qtbot, styled_qapp, app_paths: AppPaths) -> None:
    seed_counts(app_paths, {"ALI": 900, "VENOUS": 900})  # 1,800 questions
    view = make_view(qtbot, styled_qapp, app_paths)
    qtbot.wait(50)
    assert view.model.rowCount() == 1800
    first_screen = view.model.pages_loaded
    assert 1 <= first_screen <= 2  # only what is visible, not 1,800 rows

    view.go_to("VENOUS-0850")  # far down the list
    qtbot.wait(50)
    assert view.current_uid == "VENOUS-0850"
    assert view.model.pages_loaded <= first_screen + 3  # a jump costs a page or two, not a scan
    assert view.model.cached_page_count <= MAX_CACHED_PAGES


def test_the_page_cache_is_bounded_however_far_the_user_scrolls(
    qtbot, styled_qapp, app_paths: AppPaths
) -> None:
    seed_counts(app_paths, {"ALI": 900, "VENOUS": 900, "PAD_CLTI": 900})  # 2,700 questions = 27 pages
    view = make_view(qtbot, styled_qapp, app_paths)
    for row in range(0, view.model.rowCount(), PAGE_SIZE):
        assert view.model.summary_at(row) is not None
    assert view.model.pages_loaded == 27
    assert view.model.cached_page_count == MAX_CACHED_PAGES


def test_rows_of_one_page_come_from_a_single_query(
    qtbot, styled_qapp, app_paths: AppPaths, monkeypatch
) -> None:
    seed_counts(app_paths, {"ALI": 300})
    view = make_view(qtbot, styled_qapp, app_paths)
    calls: list[int] = []
    real_rows = view.model._service.rows

    def counting(ids, terms=()):
        calls.append(len(ids))
        return real_rows(ids, terms)

    monkeypatch.setattr(view.model._service, "rows", counting)
    view.model.set_results(view.model.results)  # drop the cache
    for row in range(PAGE_SIZE):  # reading every row of the first page ...
        view.model.summary_at(row)
    assert calls == [PAGE_SIZE]  # ... costs exactly one query (no per-row queries)


# ------------------------------------------------------------------ detail view


@pytest.mark.parametrize("letter", ANSWER_LETTERS)
def test_the_correct_answer_is_highlighted_for_every_letter(
    qtbot, styled_qapp, app_paths: AppPaths, letter: str
) -> None:
    seed(app_paths, [make_question("ALI", 1, correct=AnswerLetter(letter))])
    view = make_view(qtbot, styled_qapp, app_paths)
    assert view.go_to("ALI-0001")
    detail = view.detail_view
    assert detail.correct_letter() == letter
    marked = [row for row in detail.option_rows if row.is_correct]
    assert [r.letter for r in marked] == [letter]
    for row in detail.option_rows:
        assert row.correct_tag.isVisibleTo(detail) == (
            row.letter == letter
        )  # also marked in words, not only colour
        assert row.badge.text() == row.letter
    assert [row.letter for row in detail.option_rows] == list(ANSWER_LETTERS)  # ა ბ გ დ ე in order


def test_detail_shows_every_required_field(qtbot, styled_qapp, app_paths: AppPaths) -> None:
    seed(app_paths, [make_question("VENOUS", 12, stem="ვენური კითხვა\n\nმეორე აბზაცი", explanation="ახსნა")])
    view = make_view(qtbot, styled_qapp, app_paths)
    view.go_to("VENOUS-0012")
    detail = view.detail_view
    assert detail.uid_label.text() == "VENOUS-0012"
    assert detail.subject_label.text() == "ვენური დაავადებები"
    assert detail.status_chip.text() == ReviewStatus.PENDING_REVIEW.label_ka
    assert detail.status_chip.status is ReviewStatus.PENDING_REVIEW
    assert detail.revision_badge.text() == strings.DET_REVISION.format(number=1)
    assert detail.stem_label.plain_text == "ვენური კითხვა\n\nმეორე აბზაცი"
    assert detail.explanation_label.plain_text == "ახსნა"
    assert [r.text_label.plain_text for r in detail.option_rows][2].endswith("VENOUS-12")
    meta = {key: label.text() for key, label in detail.metadata.items()}
    assert meta["source_file"] == "VENOUS_questions.txt : 84"
    assert meta["source_kind"] == strings.DET_KIND_PAIRED and meta["import_batch"] == "№ 1"
    assert meta["revision"] == "1" and meta["imported_at"] != "—" and meta["modified_at"] != "—"
    assert not detail.metadata["approved_at"].isVisibleTo(detail)  # only shown once approved


def test_approval_time_appears_once_approved(qtbot, styled_qapp, app_paths: AppPaths) -> None:
    seed(app_paths, [make_question("ALI", 1)])
    set_status(app_paths, "ALI-0001", ReviewStatus.APPROVED)
    view = make_view(qtbot, styled_qapp, app_paths)
    view.go_to("ALI-0001")
    assert view.detail_view.metadata["approved_at"].isVisibleTo(view.detail_view)
    assert view.detail_view.status_chip.status is ReviewStatus.APPROVED


def test_text_is_selectable_and_copyable_but_never_editable(view: CatalogView, styled_qapp) -> None:
    from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTextEdit

    view.go_to("ALI-0003")
    detail = view.detail_view
    selectable = Qt.TextInteractionFlag.TextSelectableByMouse
    for label in (detail.stem_label, detail.explanation_label, *(r.text_label for r in detail.option_rows)):
        assert label.textInteractionFlags() & selectable
        assert not label.textInteractionFlags() & Qt.TextInteractionFlag.TextEditable
    assert (
        detail.findChildren(QLineEdit) == [] and detail.findChildren(QTextEdit) == []
    )  # nothing to type into
    assert detail.findChildren(QPlainTextEdit) == []
    detail.copy_button.click()
    copied = QApplication.clipboard().text()
    assert copied.startswith("ALI-0003 · კიდურის მწვავე იშემია") and "სწორი პასუხი:" in copied
    assert detail.stem_label.plain_text in copied


def test_there_are_no_edit_or_approval_controls(view: CatalogView) -> None:
    from PySide6.QtWidgets import QAbstractButton

    view.go_to("ALI-0003")
    visible = {b.text() for b in view.findChildren(QAbstractButton) if b.isVisibleTo(view) and b.text()}
    assert visible == {
        strings.CAT_GOTO_BUTTON,
        strings.CAT_PREVIOUS,
        strings.CAT_NEXT,
        strings.DET_COPY,
        *strings.QF_LABELS,  # quick filters (Phase 8): they select, they never change a question
    }
    assert strings.DET_READ_ONLY in view.detail_view.read_only_note.text()


# ------------------------------------------------------------------ previous / next / position


def test_next_and_previous_follow_the_list(view: CatalogView) -> None:
    view.set_subject("ALI")
    view.go_to("ALI-0001")
    assert view.position_label.text() == strings.CAT_POSITION.format(position="1", total="30")
    assert not view.prev_button.isEnabled() and view.next_button.isEnabled()
    view.next_button.click()
    assert view.current_uid == "ALI-0002" and view.position_label.text().startswith("2 /")
    view.prev_button.click()
    assert view.current_uid == "ALI-0001"
    view.go_to("ALI-0030")
    assert (
        view.current_uid == "ALI-0030" and not view.next_button.isEnabled() and view.prev_button.isEnabled()
    )
    view.next_question()  # at the end: nothing happens
    assert view.current_uid == "ALI-0030"


def test_navigation_stays_inside_the_filtered_list(view: CatalogView, app_paths: AppPaths) -> None:
    for uid in ("ALI-0003", "ALI-0007", "ALI-0020"):
        set_status(app_paths, uid, ReviewStatus.IN_REVIEW)
    view.set_status(ReviewStatus.IN_REVIEW)
    assert listed_uids(view) == ["ALI-0003", "ALI-0007", "ALI-0020"]
    view.go_to("ALI-0003")
    view.next_question()
    assert view.current_uid == "ALI-0007"
    view.next_question()
    assert view.current_uid == "ALI-0020"
    assert not view.next_button.isEnabled()
    view.previous_question()
    assert view.current_uid == "ALI-0007" and view.position_label.text() == "2 / 3"


def test_navigation_when_the_open_question_is_outside_the_list(
    view: CatalogView, app_paths: AppPaths
) -> None:
    view.go_to("ALI-0005")
    set_status(app_paths, "VENOUS-0002", ReviewStatus.REJECTED)
    set_status(app_paths, "VENOUS-0009", ReviewStatus.REJECTED)
    view.set_status(ReviewStatus.REJECTED)  # ALI-0005 is not in this list
    assert view.current_uid == "ALI-0005"  # still displayed
    assert view.position_label.text() == strings.CAT_POSITION_OUTSIDE.format(total="2")
    view.next_question()
    assert view.current_uid == "VENOUS-0002"  # first of the list
    view.go_to("ALI-0005")  # (jump clears the filter) …
    view.set_status(ReviewStatus.REJECTED)
    view.previous_question()
    assert view.current_uid == "VENOUS-0009"  # … and previous from outside is the last one


def test_clicking_a_row_opens_that_question(qtbot, view: CatalogView) -> None:
    view.set_subject("VENOUS")
    index = view.model.index(4)
    view.list_view.setCurrentIndex(index)
    assert view.current_uid == "VENOUS-0005"
    assert view.detail_view.uid_label.text() == "VENOUS-0005"
    assert view.list_view.currentIndex().row() == 4


# ------------------------------------------------------------------ go to ID / number


@pytest.mark.parametrize("text", ["ALI-0007", "ali-0007", "  Ali 7 ", "ALI7"])
def test_go_to_by_id_in_any_reasonable_spelling(view: CatalogView, text: str) -> None:
    assert view.go_to(text)
    assert view.current_uid == "ALI-0007"
    assert view.detail_view.uid_label.text() == "ALI-0007"


def test_go_to_by_number_inside_the_open_subject(view: CatalogView) -> None:
    view.set_subject("VENOUS")
    view.goto_edit.setText("12")
    view.goto_button.click()
    assert view.current_uid == "VENOUS-0012"
    assert view.model.row_of(view.current_question_id) == 11


@pytest.mark.parametrize(
    ("text", "default_subject", "message"),
    [
        ("", None, strings.ERR_REF_EMPTY),
        ("12", None, strings.ERR_REF_NEEDS_SUBJECT),
        ("XYZ-0001", None, strings.ERR_REF_UNKNOWN_SUBJECT),
        ("ALI-", None, strings.ERR_REF_BAD_FORMAT),
        ("ALI-0999", None, strings.ERR_REF_NOT_FOUND.format(uid="ALI-0999")),
        ("99", "ALI", strings.ERR_REF_NOT_FOUND.format(uid="ALI-0099")),
    ],
)
def test_go_to_reports_problems_in_georgian_and_changes_nothing(
    view: CatalogView, text: str, default_subject: str | None, message: str
) -> None:
    view.go_to("ALI-0004")
    if default_subject:
        view.set_subject(default_subject)
    assert not view.go_to(text)
    assert view.notice_label.isVisibleTo(view) and view.notice_label.text() == message
    assert view.notice_label.objectName() == "ErrorNotice"
    assert view.goto_edit.property("invalid") is True
    assert view.current_uid == "ALI-0004"  # the open question is untouched
    view.goto_edit.textEdited.emit("x")  # typing again clears the error marker
    assert view.goto_edit.property("invalid") is False


def test_jump_to_a_question_outside_the_current_filter_clears_it_and_explains(
    view: CatalogView, app_paths: AppPaths
) -> None:
    set_status(app_paths, "VENOUS-0002", ReviewStatus.APPROVED)
    view.set_subject("VENOUS")
    view.set_status(ReviewStatus.APPROVED)
    assert listed_uids(view) == ["VENOUS-0002"]
    assert view.go_to("VENOUS-0015")
    assert view.current_filter.status is None and view.current_filter.subject_code == "VENOUS"
    assert view.current_uid == "VENOUS-0015" and view.model.rowCount() == 20
    assert view.notice_label.text() == strings.NOTICE_JUMP_FILTER_CLEARED.format(uid="VENOUS-0015")
    assert view.status_combo.currentIndex() == 0  # the controls show the cleared filter


def test_jump_to_another_subject_switches_the_subject_filter(view: CatalogView) -> None:
    view.set_subject("VENOUS")
    assert view.go_to("PAD_CLTI-0003")
    assert view.current_filter.subject_code == "PAD_CLTI" and view.current_uid == "PAD_CLTI-0003"
    view.set_subject(None)
    view.set_status(ReviewStatus.APPROVED)  # empty list; "all subjects" stays "all"
    assert view.go_to("ALI-0009")
    assert view.current_filter.subject_code is None and view.current_uid == "ALI-0009"


def test_jump_clears_an_active_search(qtbot, view: CatalogView) -> None:
    view.set_search_text("ვარიანტი")
    settle(qtbot, view)
    view.set_search_text("PAD_CLTI-0003")
    settle(qtbot, view)
    assert view.model.rowCount() == 1
    assert view.go_to("ALI-0002")
    assert view.search_edit.text() == "" and view.current_filter.text == ""
    assert view.current_uid == "ALI-0002" and view.model.rowCount() == 60


# ------------------------------------------------------------------ filters


def test_status_and_sort_combos_deliver_real_enums_not_strings(view: CatalogView) -> None:
    """Regression: Qt hands a StrEnum back from QComboBox as a plain str."""
    view.status_combo.setCurrentIndex(2)
    assert view.current_filter.status is ReviewStatus.IN_REVIEW
    assert isinstance(view.current_filter.status, ReviewStatus)
    view.sort_combo.setCurrentIndex(1)
    assert view.current_filter.sort is SortOrder.STATUS and isinstance(view.current_filter.sort, SortOrder)
    view.subject_combo.setCurrentIndex(2)
    assert view.current_filter.subject_code == SUBJECTS[1].code  # index 0 is "all subjects"


def test_filter_combos_and_the_list_agree(view: CatalogView, app_paths: AppPaths) -> None:
    set_status(app_paths, "ALI-0010", ReviewStatus.REJECTED)
    set_status(app_paths, "VENOUS-0001", ReviewStatus.REJECTED)
    view.set_status(ReviewStatus.REJECTED)
    assert view.status_combo.currentText() == ReviewStatus.REJECTED.label_ka
    assert listed_uids(view) == ["ALI-0010", "VENOUS-0001"]
    view.set_subject("ALI")
    assert view.subject_combo.currentText() == "კიდურის მწვავე იშემია"
    assert listed_uids(view) == ["ALI-0010"]
    view.set_status(None)
    view.set_subject(None)
    assert view.model.rowCount() == 60
    view.set_subject("NOT_A_SUBJECT")  # ignored: still all subjects
    assert view.current_filter.subject_code is None


def test_context_changes_are_announced_for_session_saving(qtbot, view: CatalogView) -> None:
    seen: list[tuple] = []
    view.contextChanged.connect(lambda *args: seen.append(args))
    view.set_subject("ALI")
    view.go_to("ALI-0004")
    view.set_status(ReviewStatus.PENDING_REVIEW)
    assert seen[0] == ("ALI", None, None)
    assert seen[-1] == ("ALI", ReviewStatus.PENDING_REVIEW, "ALI-0004")
    before = len(seen)
    view.set_sort(SortOrder.MODIFIED)  # sorting is not part of the remembered context
    assert len(seen) == before


def test_sorting_changes_order_but_not_identity(view: CatalogView, app_paths: AppPaths) -> None:
    set_status(app_paths, "ALI-0002", ReviewStatus.APPROVED)
    view.set_subject("ALI")
    by_number = listed_uids(view)
    view.set_sort(SortOrder.STATUS)
    by_status = listed_uids(view)
    assert by_status != by_number and sorted(by_status) == sorted(by_number)
    assert by_status[-1] == "ALI-0002"  # approved last
    view.go_to("ALI-0002")
    assert view.detail_view.uid_label.text() == "ALI-0002"  # same question whatever the order


# ------------------------------------------------------------------ search


def test_search_runs_off_the_ui_thread_and_does_not_block(qtbot, view: CatalogView, monkeypatch) -> None:
    threads: dict[str, str] = {}
    real_query = view._service.query

    def spy(flt):
        threads["search" if flt.is_search else "browse"] = threading.current_thread().name
        time.sleep(0.2)  # a slow search: the window must stay responsive meanwhile
        return real_query(flt)

    monkeypatch.setattr(view._service, "query", spy)
    started = time.perf_counter()
    view.search_edit.setText("ვარიანტი")
    assert time.perf_counter() - started < 0.1  # typing returned at once
    qtbot.waitUntil(lambda: view._runner.is_busy(), timeout=WAIT_MS)
    assert view.model.rowCount() == 60  # the previous list is still shown meanwhile
    assert view.result_label.text() == strings.CAT_SEARCHING
    ticks: list[int] = []
    from PySide6.QtCore import QTimer

    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(20)
    settle(qtbot, view)
    timer.stop()
    assert len(ticks) >= 3  # the event loop kept running while the search worked
    assert threads["search"] != threading.main_thread().name
    assert view.model.rowCount() == 60 and view.model.results.mode in (SearchMode.FTS, SearchMode.LIKE)


def test_search_results_link_to_the_right_question(qtbot, app_paths: AppPaths, styled_qapp) -> None:
    questions = [make_question("ALI", n) for n in range(1, 21)]
    questions[6] = make_question("ALI", 7, stem="უნიკალური ტერმინი სტენტგრაფტი გამოჩნდება აქ")
    seed(app_paths, questions)
    view = make_view(qtbot, styled_qapp, app_paths)
    view.set_search_text("სტენტგრაფტ")
    settle(qtbot, view)
    assert view.model.rowCount() == 1
    summary = view.model.summary_at(0)
    assert summary is not None and summary.uid == "ALI-0007"
    assert "სტენტგრაფტი" in summary.preview.text
    begin, end = summary.preview.spans[0]
    assert summary.preview.text[begin:end] == "სტენტგრაფტ"  # the match is what gets highlighted
    assert view.result_label.text().startswith(strings.CAT_COUNT_FOUND.format(count=1))
    view.list_view.setCurrentIndex(view.model.index(0))
    assert view.current_uid == "ALI-0007" and view.detail_view.uid_label.text() == "ALI-0007"
    assert "სტენტგრაფტი" in view.detail_view.stem_label.plain_text


def test_search_by_id_and_by_georgian_fragment_in_every_field(
    qtbot, app_paths: AppPaths, styled_qapp
) -> None:
    questions = [make_question("ALI", n) for n in range(1, 6)]
    questions[1] = make_question(
        "ALI", 2, explanation="განმარტება: პერკუტანული ტრანსლუმინალური ანგიოპლასტიკა"
    )
    questions[3] = make_question("ALI", 4, options=("ა", "ბ", "გ — ენდოვასკულარული გზა", "დ", "ე"))
    seed(app_paths, questions)
    view = make_view(qtbot, styled_qapp, app_paths)
    for text, expected in (("ტრანსლუმინ", "ALI-0002"), ("ენდოვასკულ", "ALI-0004"), ("ALI-0005", "ALI-0005")):
        view.set_search_text(text)
        settle(qtbot, view)
        assert listed_uids(view) == [expected], text


def test_clearing_the_search_restores_the_previous_catalog(
    qtbot, view: CatalogView, app_paths: AppPaths
) -> None:
    set_status(app_paths, "ALI-0004", ReviewStatus.IN_REVIEW)
    view.set_subject("ALI")
    view.set_status(ReviewStatus.PENDING_REVIEW)
    before = listed_uids(view)
    assert len(before) == 29
    view.go_to("ALI-0010")
    view.set_search_text("ALI-0011")
    settle(qtbot, view)
    assert listed_uids(view) == ["ALI-0011"]
    view.search_edit.clear()  # the clear button / Esc
    assert not view.is_busy()  # restoring is immediate, nothing to wait for
    assert listed_uids(view) == before
    assert view.current_filter.text == "" and view.result_label.text() == strings.CAT_COUNT.format(count=29)
    assert view.current_uid == "ALI-0010" and view.list_view.currentIndex().row() == before.index("ALI-0010")
    assert (
        view.current_filter.subject_code == "ALI"
        and view.current_filter.status is ReviewStatus.PENDING_REVIEW
    )


def test_escape_clears_the_search(qtbot, view: CatalogView) -> None:
    view.set_search_text("ვარიანტი")
    settle(qtbot, view)
    view.search_edit.setFocus()
    qtbot.keyClick(view.search_edit, Qt.Key.Key_Escape)
    assert view.search_edit.text() == "" and view.model.rowCount() == 60


def test_no_results_message_and_recovery(qtbot, view: CatalogView) -> None:
    view.set_search_text("ზედმეტიარარსებულიტექსტი")
    settle(qtbot, view)
    assert view.model.rowCount() == 0 and view.empty_label.text() == strings.CAT_NO_RESULTS
    assert view.empty_label.isVisibleTo(view) and not view.list_view.isVisibleTo(view)
    assert view.result_label.text() == strings.CAT_NO_RESULTS
    view.search_edit.clear()
    assert view.list_view.isVisibleTo(view) and view.model.rowCount() == 60


def _gate_first_search(view: CatalogView, monkeypatch) -> tuple[threading.Event, list[str]]:
    """Make the search for "ALI-0001" block until released; record every executed search."""
    release = threading.Event()
    executed: list[str] = []
    real_query = view._service.query

    def gated(flt):
        if flt.is_search:
            executed.append(flt.text)
        if flt.text == "ALI-0001":
            release.wait(10)
        return real_query(flt)

    monkeypatch.setattr(view._service, "query", gated)
    return release, executed


def _search_now(view: CatalogView, text: str) -> None:
    view.set_search_text(text)
    view._commit_search()  # skip the typing debounce


def test_only_the_newest_waiting_search_runs(qtbot, view: CatalogView, monkeypatch) -> None:
    release, executed = _gate_first_search(view, monkeypatch)
    _search_now(view, "ALI-0001")
    qtbot.waitUntil(lambda: executed == ["ALI-0001"], timeout=WAIT_MS)  # running, blocked
    _search_now(view, "VENOUS-0002")  # waits behind it ...
    _search_now(view, "PAD_CLTI-0003")  # ... and is replaced before it ever starts
    release.set()
    settle(qtbot, view)
    assert executed == ["ALI-0001", "PAD_CLTI-0003"]  # the obsolete middle request was dropped, never run
    assert listed_uids(view) == ["PAD_CLTI-0003"]


def test_clearing_the_search_while_it_runs_discards_its_late_result(
    qtbot, view: CatalogView, monkeypatch
) -> None:
    release, executed = _gate_first_search(view, monkeypatch)
    _search_now(view, "ALI-0001")
    qtbot.waitUntil(lambda: executed == ["ALI-0001"], timeout=WAIT_MS)
    view.search_edit.clear()  # the reviewer clears the box while the search is still working
    assert view.model.rowCount() == 60 and view.current_filter.text == ""  # catalog restored at once
    release.set()
    settle(qtbot, view)
    qtbot.wait(100)  # give the late result every chance to arrive
    assert view.model.rowCount() == 60  # it did not repaint "ALI-0001" over the restored catalog
    assert view.result_label.text() == strings.CAT_COUNT.format(count=60)
    assert not view.delegate.show_field_tags


def test_search_is_debounced_while_typing(qtbot, view: CatalogView, monkeypatch) -> None:
    submitted: list[str] = []
    real_query = view._service.query

    def counting(flt):
        submitted.append(flt.text)
        return real_query(flt)

    monkeypatch.setattr(view._service, "query", counting)
    for partial in ("V", "VE", "VEN", "VENO", "VENOUS"):
        view.search_edit.setText(partial)
        qtbot.wait(20)
    settle(qtbot, view)
    assert submitted.count("VENOUS") == 1 and len(submitted) <= 2  # keystrokes were coalesced


def test_search_respects_the_subject_filter(qtbot, view: CatalogView) -> None:
    view.set_subject("VENOUS")
    view.set_search_text("ვარიანტი")
    settle(qtbot, view)
    assert view.model.rowCount() == 20
    view.set_subject(None)
    settle(qtbot, view)
    assert view.model.rowCount() == 60


def test_changing_the_subject_keeps_text_that_is_still_being_typed(qtbot, view: CatalogView) -> None:
    view.search_edit.setText("ვარიანტ")  # typed, debounce not yet elapsed
    view.set_subject("ALI")
    assert view.search_edit.text() == "ვარიანტ"  # not overwritten by the old filter text
    settle(qtbot, view)
    assert view.current_filter.text == "ვარიანტ" and view.model.rowCount() == 30


def test_search_result_info_names_the_mode(qtbot, app_paths: AppPaths, styled_qapp) -> None:
    seed_counts(app_paths, {"ALI": 40})
    view = make_view(qtbot, styled_qapp, app_paths)
    view.set_search_text("სატესტო")
    settle(qtbot, view)
    assert view.model.results.mode is SearchMode.FTS and strings.CAT_MODE_FTS in view.result_label.text()
    assert view.delegate.show_field_tags
    view.set_search_text("ბა")  # two characters: the LIKE scan answers
    settle(qtbot, view)
    assert view.model.results.mode is SearchMode.LIKE and strings.CAT_MODE_LIKE in view.result_label.text()
    view.search_edit.clear()
    assert not view.delegate.show_field_tags


def test_edits_made_elsewhere_show_up_after_a_refresh(qtbot, view: CatalogView, app_paths: AppPaths) -> None:
    view.go_to("ALI-0006")
    simulate_edit(app_paths, "ALI-0006", stem="ცვლილების შემდეგ ახალი ფორმულირება")
    view.set_search_text("ახალი ფორმულირება")
    settle(qtbot, view)
    assert listed_uids(view) == ["ALI-0006"]
    view.open_question(view.current_question_id)
    assert view.detail_view.stem_label.plain_text == "ცვლილების შემდეგ ახალი ფორმულირება"
    assert view.detail_view.revision_badge.text() == strings.DET_REVISION.format(number=2)
    assert view.detail_view.status_chip.status is ReviewStatus.IN_REVIEW


# ------------------------------------------------------------------ restoring a context


@pytest.mark.parametrize(
    ("notice", "text"),
    [
        (ContextNotice.SUBJECT_RESET, strings.NOTICE_SUBJECT_RESET),
        (ContextNotice.QUESTION_FILTERED, strings.NOTICE_QUESTION_FILTERED.format(uid="ALI-0004")),
        (ContextNotice.FILTER_RESET, strings.NOTICE_FILTER_RESET),
        (ContextNotice.QUESTION_MISSING, strings.NOTICE_QUESTION_MISSING.format(uid="ALI-0004")),
    ],
)
def test_restoring_explains_every_fallback(view: CatalogView, notice: ContextNotice, text: str) -> None:
    view.restore(RestoredContext("ALI", None, view._service.locate("ALI-0005").id, "ALI-0004", notice))
    assert view.notice_label.isVisibleTo(view) and view.notice_label.text() == text
    assert view.current_uid == "ALI-0005" and view.current_filter.subject_code == "ALI"


def test_restoring_selects_scrolls_to_and_never_writes(view: CatalogView, app_paths: AppPaths) -> None:
    before = snapshot(app_paths)
    target = view._service.locate("VENOUS-0015")
    view.restore(RestoredContext("VENOUS", ReviewStatus.PENDING_REVIEW, target.id, "VENOUS-0015"))
    assert view.current_uid == "VENOUS-0015" and not view.notice_label.isVisibleTo(view)
    assert view.list_view.currentIndex().row() == 14
    assert view.status_combo.currentText() == ReviewStatus.PENDING_REVIEW.label_ka
    assert snapshot(app_paths) == before


def test_opening_questions_in_every_way_never_changes_the_database(
    qtbot, view: CatalogView, app_paths: AppPaths
) -> None:
    before = snapshot(app_paths)
    view.go_to("ALI-0003")
    view.next_question()
    view.next_question()
    view.previous_question()
    view.set_subject("VENOUS")
    view.list_view.setCurrentIndex(view.model.index(3))
    view.set_search_text("ვარიანტი")
    settle(qtbot, view)
    view.list_view.setCurrentIndex(view.model.index(1))
    view.set_status(ReviewStatus.PENDING_REVIEW)
    view.detail_view.copy_button.click()
    assert (
        snapshot(app_paths) == before
    )  # every status, modified_at, lock_version, revision and event unchanged
