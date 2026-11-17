"""The question catalog page: filters, search, a lazy list and the read-only detail panel.

Responsiveness rules:

* the list holds only ordered ids; row text is loaded a page at a time by the
  model (see :mod:`vascqbank_desktop.ui.question_list_model`);
* plain filtering resolves ids in a couple of milliseconds on the UI thread;
* **search runs on a background thread**, debounced while typing, and only the
  newest request matters — an older result that arrives late is discarded;
* opening a question reads one row and never writes anything.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import replace

from PySide6.QtCore import QItemSelectionModel, QModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QPushButton,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.catalog import (
    CatalogFilter,
    QuestionLocation,
    ResultSet,
    SearchMode,
    SortOrder,
)
from vascqbank_desktop.domain.question_id import InvalidReference, ReferenceProblem
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.search import normalize_query
from vascqbank_desktop.domain.subjects import Subject
from vascqbank_desktop.services.catalog_service import CatalogService, QuestionNotFound
from vascqbank_desktop.services.question_stats import BankCounts, summarize
from vascqbank_desktop.services.session_service import ContextNotice, RestoredContext
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.formatting import format_count
from vascqbank_desktop.ui.progress_panel import ProgressStrip
from vascqbank_desktop.ui.question_delegate import QuestionDelegate
from vascqbank_desktop.ui.question_detail_view import QuestionDetailView
from vascqbank_desktop.ui.question_list_model import QuestionListModel
from vascqbank_desktop.ui.widgets import make_label, repolish
from vascqbank_desktop.ui.workers import LatestOnlyRunner

log = logging.getLogger(__name__)

SEARCH_DEBOUNCE_MS = 250
NOTICE_VISIBLE_MS = 15_000

_SORT_LABELS: dict[SortOrder, str] = {
    SortOrder.NUMBER: strings.CAT_SORT_NUMBER,
    SortOrder.STATUS: strings.CAT_SORT_STATUS,
    SortOrder.MODIFIED: strings.CAT_SORT_MODIFIED,
}
_REFERENCE_ERRORS: dict[ReferenceProblem, str] = {
    ReferenceProblem.EMPTY: strings.ERR_REF_EMPTY,
    ReferenceProblem.NEEDS_SUBJECT: strings.ERR_REF_NEEDS_SUBJECT,
    ReferenceProblem.UNKNOWN_SUBJECT: strings.ERR_REF_UNKNOWN_SUBJECT,
    ReferenceProblem.BAD_FORMAT: strings.ERR_REF_BAD_FORMAT,
}
_CONTEXT_NOTICES: dict[ContextNotice, str] = {
    ContextNotice.SUBJECT_RESET: strings.NOTICE_SUBJECT_RESET,
    ContextNotice.QUESTION_FILTERED: strings.NOTICE_QUESTION_FILTERED,
    ContextNotice.FILTER_RESET: strings.NOTICE_FILTER_RESET,
    ContextNotice.QUESTION_MISSING: strings.NOTICE_QUESTION_MISSING,
}


class CatalogView(QWidget):
    questionOpened = Signal(str)  # uid of the question now shown
    contextChanged = Signal(object, object, object)  # subject code, status, uid (each may be None)
    importRequested = Signal()

    def __init__(
        self, service: CatalogService, subjects: Sequence[Subject], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ContentArea")
        self._service = service
        self._subjects = {s.code: s for s in subjects}
        self._bank: BankCounts | None = None
        self._filter = CatalogFilter()
        self._current_id: int | None = None
        self._generation = 0
        self._updating_controls = False
        self._runner = LatestOnlyRunner(self)

        self.model = QuestionListModel(service, self)
        self._search_timer = QTimer(self)
        self._search_timer.setSingleShot(True)
        self._search_timer.setInterval(SEARCH_DEBOUNCE_MS)
        self._search_timer.timeout.connect(self._commit_search)
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_VISIBLE_MS)
        self._notice_timer.timeout.connect(self.clear_notice)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 28, 18)
        root.setSpacing(10)
        self.strip = ProgressStrip()
        root.addWidget(self.strip)
        root.addLayout(self._build_search_row())
        root.addLayout(self._build_filter_row())
        self.notice_label = QLabel()
        self.notice_label.setObjectName("InfoNotice")
        self.notice_label.setWordWrap(True)
        self.notice_label.hide()
        root.addWidget(self.notice_label)

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self._build_list_panel())
        self.splitter.addWidget(self._build_detail_panel())
        self.splitter.setStretchFactor(0, 4)
        self.splitter.setStretchFactor(1, 6)
        self.splitter.setSizes([430, 640])
        root.addWidget(self.splitter, 1)

        clear_search = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.search_edit)
        clear_search.setContext(Qt.ShortcutContext.WidgetShortcut)
        clear_search.activated.connect(self.search_edit.clear)

        self._sync_controls()
        self._apply_filter()
        self._update_navigation()

    # ------------------------------------------------------------------ construction
    def _build_search_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("SearchBox")
        self.search_edit.setPlaceholderText(strings.CAT_SEARCH_PLACEHOLDER)
        self.search_edit.setToolTip(strings.CAT_SEARCH_TOOLTIP)
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMaxLength(200)
        self.search_edit.textChanged.connect(self._on_search_text_changed)
        self.search_edit.returnPressed.connect(self._commit_search)
        row.addWidget(self.search_edit, 1)

        self.goto_edit = QLineEdit()
        self.goto_edit.setObjectName("GotoBox")
        self.goto_edit.setPlaceholderText(strings.CAT_GOTO_PLACEHOLDER)
        self.goto_edit.setToolTip(strings.CAT_GOTO_TOOLTIP)
        self.goto_edit.setFixedWidth(210)
        self.goto_edit.setMaxLength(64)
        self.goto_edit.returnPressed.connect(self._go_to_typed)
        self.goto_edit.textEdited.connect(self._clear_goto_error)
        row.addWidget(self.goto_edit)
        self.goto_button = QPushButton(strings.CAT_GOTO_BUTTON)
        self.goto_button.setObjectName("NavButton")
        self.goto_button.clicked.connect(self._go_to_typed)
        row.addWidget(self.goto_button)
        return row

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)
        self.subject_combo = QComboBox()
        self.subject_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self.subject_combo.setMinimumContentsLength(16)
        self.subject_combo.addItem(strings.SUBJECT_ALL, None)
        for code, subject in self._subjects.items():
            self.subject_combo.addItem(subject.name_ka, code)
            last = self.subject_combo.count() - 1
            self.subject_combo.setItemData(last, subject.name_ka, Qt.ItemDataRole.ToolTipRole)
        self.subject_combo.view().setMinimumWidth(520)
        self.subject_combo.currentIndexChanged.connect(self._on_subject_combo)

        # Enums are mapped by position: Qt would hand a StrEnum back as a plain str.
        self._status_options: list[ReviewStatus | None] = [None, *ReviewStatus]
        self.status_combo = QComboBox()
        self.status_combo.addItem(strings.CAT_STATUS_ALL)
        for status in ReviewStatus:
            self.status_combo.addItem(status.label_ka)
        self.status_combo.currentIndexChanged.connect(self._on_status_combo)

        self._sort_options: list[SortOrder] = list(_SORT_LABELS)
        self.sort_combo = QComboBox()
        for label in _SORT_LABELS.values():
            self.sort_combo.addItem(label)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_combo)

        row.addWidget(self.subject_combo, 3)
        row.addWidget(self.status_combo, 2)
        row.addWidget(self.sort_combo, 2)
        return row

    def _build_list_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("CatalogPanel")
        panel.setMinimumWidth(340)
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 0, 8, 0)
        column.setSpacing(6)
        self.result_label = make_label("", "ResultInfo", wrap=False)
        column.addWidget(self.result_label)

        self._list_stack = QStackedWidget()
        self.list_view = QListView()
        self.list_view.setObjectName("QuestionList")
        self.list_view.setModel(self.model)
        self.delegate = QuestionDelegate(self.list_view)
        self.list_view.setItemDelegate(self.delegate)
        self.list_view.setUniformItemSizes(True)
        self.list_view.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.list_view.setMouseTracking(True)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_view.selectionModel().currentChanged.connect(self._on_list_current_changed)
        self._list_stack.addWidget(self.list_view)

        empty = QWidget()
        empty_column = QVBoxLayout(empty)
        empty_column.setContentsMargins(24, 24, 24, 24)
        empty_column.addStretch(1)
        self.empty_label = make_label("", "EmptyListMessage")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_column.addWidget(self.empty_label)
        self.empty_hint = make_label("", "CardBody")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_column.addWidget(self.empty_hint)
        self.import_button = QPushButton(strings.IMPORT_BUTTON)
        self.import_button.setObjectName("PrimaryButton")
        self.import_button.clicked.connect(self.importRequested)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(self.import_button)
        row.addStretch(1)
        empty_column.addLayout(row)
        empty_column.addStretch(1)
        self._list_stack.addWidget(empty)
        self._empty_page = empty
        column.addWidget(self._list_stack, 1)
        return panel

    def _build_detail_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("CatalogPanel")
        panel.setMinimumWidth(460)
        column = QVBoxLayout(panel)
        column.setContentsMargins(8, 0, 0, 0)
        column.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(8)
        self.prev_button = QPushButton(strings.CAT_PREVIOUS)
        self.prev_button.setObjectName("NavButton")
        self.prev_button.clicked.connect(self.previous_question)
        self.next_button = QPushButton(strings.CAT_NEXT)
        self.next_button.setObjectName("NavButton")
        self.next_button.clicked.connect(self.next_question)
        self.position_label = make_label("", "PositionLabel", wrap=False)
        bar.addWidget(self.prev_button)
        bar.addWidget(self.next_button)
        bar.addWidget(self.position_label)
        bar.addStretch(1)
        column.addLayout(bar)
        self.detail_view = QuestionDetailView()
        column.addWidget(self.detail_view, 1)
        return panel

    # ------------------------------------------------------------------ public state
    @property
    def current_filter(self) -> CatalogFilter:
        return self._filter

    @property
    def current_question_id(self) -> int | None:
        return self._current_id

    @property
    def current_uid(self) -> str | None:
        return self.detail_view.question_uid

    def is_busy(self) -> bool:
        """A search is typed-but-not-started, queued or running."""
        return self._search_timer.isActive() or self._runner.is_busy()

    def shutdown(self) -> None:
        self._search_timer.stop()
        self._runner.shutdown()

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.shutdown()
        super().closeEvent(event)

    # ------------------------------------------------------------------ progress counts
    def set_counts(self, bank: BankCounts) -> None:
        self._bank = bank
        self._update_strip()
        self._update_empty_state()

    def _update_strip(self) -> None:
        if self._bank is None:
            return
        code = self._filter.subject_code
        subject = self._subjects.get(code) if code else None
        self.strip.set_progress(
            summarize(self._bank, None),
            summarize(self._bank, code) if subject else None,
            subject,
        )

    # ------------------------------------------------------------------ controls <-> filter
    def _sync_controls(self, *, text: bool = False) -> None:
        """Make the combo boxes show the filter. The search box is only rewritten when
        *text* is true: otherwise text the reviewer is still typing would be lost."""
        self._updating_controls = True
        try:
            code = self._filter.subject_code
            self.subject_combo.setCurrentIndex(max(0, self.subject_combo.findData(code)))
            self.status_combo.setCurrentIndex(self._status_options.index(self._filter.status))
            self.sort_combo.setCurrentIndex(self._sort_options.index(self._filter.sort))
            if text and self.search_edit.text() != self._filter.text:
                self.search_edit.setText(self._filter.text)
        finally:
            self._updating_controls = False

    def _on_subject_combo(self, _index: int) -> None:
        if not self._updating_controls:
            self.set_subject(self.subject_combo.currentData())

    def _on_status_combo(self, index: int) -> None:
        if not self._updating_controls and 0 <= index < len(self._status_options):
            self.set_status(self._status_options[index])

    def _on_sort_combo(self, index: int) -> None:
        if not self._updating_controls and 0 <= index < len(self._sort_options):
            self.set_sort(self._sort_options[index])

    def set_subject(self, code: str | None) -> None:
        if code is not None and code not in self._subjects:
            code = None
        if code == self._filter.subject_code:
            return
        self._change_filter(replace(self._filter, subject_code=code), announce=True)

    def set_status(self, status: ReviewStatus | None) -> None:
        if status == self._filter.status:
            return
        self._change_filter(replace(self._filter, status=status), announce=True)

    def set_sort(self, sort: SortOrder) -> None:
        if sort == self._filter.sort:
            return
        self._change_filter(replace(self._filter, sort=sort), announce=False)

    def set_search_text(self, text: str) -> None:
        """Programmatic equivalent of typing (the debounce still applies)."""
        self.search_edit.setText(text)

    def _change_filter(self, new_filter: CatalogFilter, *, announce: bool) -> None:
        self.clear_notice()
        self._search_timer.stop()  # typed-but-pending search text is applied together with the new filter
        self._filter = replace(new_filter, text=self.search_edit.text())
        self._sync_controls()
        self._apply_filter()
        self._update_strip()
        self._update_navigation()
        if announce:
            self._emit_context()

    # ------------------------------------------------------------------ search typing
    def _on_search_text_changed(self, text: str) -> None:
        if self._updating_controls:
            return
        if normalize_query(text) == normalize_query(self._filter.text):
            self._search_timer.stop()
            return
        if not normalize_query(text):
            self._search_timer.stop()
            self._commit_search()  # clearing the search restores the catalog at once
        else:
            self._search_timer.start()

    def _commit_search(self) -> None:
        self._search_timer.stop()
        text = self.search_edit.text()
        if normalize_query(text) == normalize_query(self._filter.text):
            return
        self.clear_notice()
        self._filter = replace(self._filter, text=text)
        self._apply_filter()
        self._update_navigation()

    # ------------------------------------------------------------------ results
    def _apply_filter(self) -> None:
        self._generation += 1
        token = self._generation
        flt = self._filter
        if flt.is_search:
            self._set_result_info(strings.CAT_SEARCHING)
            self._runner.submit(
                lambda: self._service.query(flt),
                lambda results: self._on_search_results(token, results),
                lambda error: self._on_search_failed(token, error),
            )
            return
        try:
            results = self._service.query(flt)
        except Exception as exc:  # the catalog must stay usable if one query fails
            log.exception("Catalog query failed")
            self.show_notice(strings.CAT_SEARCH_ERROR.format(detail=exc), error=True)
            results = ResultSet()
        self._apply_results(results)

    def _on_search_results(self, token: int, results: object) -> None:
        if token != self._generation or not isinstance(results, ResultSet):
            return  # superseded by a newer request or by clearing the search
        self._apply_results(results)

    def _on_search_failed(self, token: int, error: object) -> None:
        if token != self._generation:
            return
        log.error("Search failed: %s", error)
        self.show_notice(strings.CAT_SEARCH_ERROR.format(detail=error), error=True)
        self._set_result_info("")

    def _apply_results(self, results: ResultSet) -> None:
        self.delegate.show_field_tags = results.mode is not SearchMode.BROWSE
        self.model.set_results(results)
        self._update_empty_state()
        self._select_current_in_list(scroll=True)
        if len(results):
            self._set_result_info(self._result_text(results))
        elif self._filter.is_search:
            self._set_result_info(strings.CAT_NO_RESULTS)
        else:
            self._set_result_info(strings.CAT_COUNT.format(count=0))
        self._update_navigation()

    def _result_text(self, results: ResultSet) -> str:
        if results.mode is SearchMode.BROWSE:
            return strings.CAT_COUNT.format(count=format_count(len(results)))
        mode = strings.CAT_MODE_FTS if results.mode is SearchMode.FTS else strings.CAT_MODE_LIKE
        return f"{strings.CAT_COUNT_FOUND.format(count=format_count(len(results)))} · {mode}"

    def _set_result_info(self, text: str) -> None:
        self.result_label.setText(text)

    def _update_empty_state(self) -> None:
        bank_empty = self._bank is not None and self._bank.total == 0
        no_rows = len(self.model.results) == 0
        if bank_empty:
            self.empty_label.setText(strings.CAT_EMPTY_BANK)
            self.empty_hint.setText("")
        elif no_rows:
            self.empty_label.setText(strings.CAT_NO_RESULTS)
            self.empty_hint.setText(strings.CAT_NO_RESULTS_HINT)
        self.import_button.setVisible(bank_empty)
        self._list_stack.setCurrentWidget(self._empty_page if (bank_empty or no_rows) else self.list_view)

    # ------------------------------------------------------------------ selection & opening
    def _select_current_in_list(self, *, scroll: bool) -> None:
        selection = self.list_view.selectionModel()
        selection.blockSignals(True)
        try:
            row = self.model.row_of(self._current_id)
            if row is None:
                selection.clearSelection()
                selection.setCurrentIndex(QModelIndex(), QItemSelectionModel.SelectionFlag.NoUpdate)
                if scroll:
                    self.list_view.scrollToTop()
            else:
                index = self.model.index(row)
                selection.setCurrentIndex(index, QItemSelectionModel.SelectionFlag.ClearAndSelect)
                if scroll:
                    self.list_view.scrollTo(index, QAbstractItemView.ScrollHint.PositionAtCenter)
        finally:
            selection.blockSignals(False)

    def _on_list_current_changed(self, current: QModelIndex, _previous: QModelIndex) -> None:
        question_id = self.model.question_id_at(current.row()) if current.isValid() else None
        if question_id is not None and question_id != self._current_id:
            self.open_question(question_id, scroll=False)

    def open_question(self, question_id: int, *, scroll: bool = True) -> bool:
        """Show a question read-only. Never writes: no status or timestamp changes."""
        try:
            detail = self._service.detail(question_id)
        except Exception as exc:
            log.exception("Could not open question %s", question_id)
            self.show_notice(strings.CAT_SEARCH_ERROR.format(detail=exc), error=True)
            return False
        if detail is None:
            return False
        self._current_id = question_id
        self.detail_view.show_question(detail)
        self._select_current_in_list(scroll=scroll)
        self._update_navigation()
        self.questionOpened.emit(detail.uid)
        self._emit_context()
        return True

    def _emit_context(self) -> None:
        self.contextChanged.emit(self._filter.subject_code, self._filter.status, self.current_uid)

    # ------------------------------------------------------------------ previous / next
    def next_question(self) -> None:
        target = self.model.results.next_id(self._current_id)
        if target is not None:
            self.open_question(target)

    def previous_question(self) -> None:
        target = self.model.results.previous_id(self._current_id)
        if target is not None:
            self.open_question(target)

    def _update_navigation(self) -> None:
        results = self.model.results
        self.next_button.setEnabled(results.next_id(self._current_id) is not None)
        self.prev_button.setEnabled(results.previous_id(self._current_id) is not None)
        position = results.position_of(self._current_id)
        total = format_count(len(results))
        if self._current_id is None or not len(results):
            self.position_label.setText("")
        elif position is None:
            self.position_label.setText(strings.CAT_POSITION_OUTSIDE.format(total=total))
        else:
            self.position_label.setText(
                strings.CAT_POSITION.format(position=format_count(position + 1), total=total)
            )

    # ------------------------------------------------------------------ go to a question
    def focus_search(self) -> None:
        self.search_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.search_edit.selectAll()

    def focus_goto(self) -> None:
        self.goto_edit.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.goto_edit.selectAll()

    def _go_to_typed(self) -> None:
        self.go_to(self.goto_edit.text())

    def go_to(self, text: str) -> bool:
        """Open the question named by *text* (ID such as ``ALI-0042`` or a number)."""
        self.clear_notice()
        try:
            location = self._service.resolve_reference(text, self._filter.subject_code)
        except InvalidReference as exc:
            self._show_goto_error(_REFERENCE_ERRORS[exc.problem])
            return False
        except QuestionNotFound as exc:
            self._show_goto_error(strings.ERR_REF_NOT_FOUND.format(uid=exc.uid))
            return False
        self._clear_goto_error()
        self._jump_to(location)
        return True

    def _jump_to(self, location: QuestionLocation) -> None:
        # The list only reflects the filter once no search is pending or running.
        listed = not self.is_busy() and location.id in self.model.results
        if not listed:
            # Show the question in its own context: filters and search cleared; the subject
            # filter stays unless it is another subject's.
            subject = self._filter.subject_code
            if subject not in (None, location.subject_code):
                subject = location.subject_code
            self._search_timer.stop()
            self._filter = CatalogFilter(subject_code=subject, sort=self._filter.sort)
            self._sync_controls(text=True)
            self._apply_filter()
            self._update_strip()
            self.show_notice(strings.NOTICE_JUMP_FILTER_CLEARED.format(uid=location.uid))
        self.open_question(location.id, scroll=True)

    def _show_goto_error(self, message: str) -> None:
        self.goto_edit.setProperty("invalid", True)
        repolish(self.goto_edit)
        self.show_notice(message, error=True)

    def _clear_goto_error(self, _text: str = "") -> None:
        if self.goto_edit.property("invalid"):
            self.goto_edit.setProperty("invalid", False)
            repolish(self.goto_edit)

    # ------------------------------------------------------------------ notices
    def show_notice(self, text: str, *, error: bool = False) -> None:
        self.notice_label.setObjectName("ErrorNotice" if error else "InfoNotice")
        repolish(self.notice_label)
        self.notice_label.setText(text)
        self.notice_label.show()
        self._notice_timer.start()

    def clear_notice(self) -> None:
        self._notice_timer.stop()
        self.notice_label.hide()
        self.notice_label.setText("")

    # ------------------------------------------------------------------ session restore / refresh
    def restore(self, context: RestoredContext) -> None:
        """Show the remembered working context (filters and question); read-only."""
        self._filter = CatalogFilter(subject_code=context.subject_code, status=context.status)
        self._current_id = None
        self._search_timer.stop()
        self._sync_controls(text=True)
        self._apply_filter()
        self._update_strip()
        if context.question_id is not None:
            self.open_question(context.question_id, scroll=True)
        else:
            self._update_navigation()
            self._emit_context()
        if context.notice is not ContextNotice.NONE:
            text = _CONTEXT_NOTICES[context.notice].format(uid=context.requested_uid or "")
            self.show_notice(text)

    def refresh(self) -> None:
        """Re-run the current filter (after an import); the open question stays open."""
        self._apply_filter()
        self._update_navigation()
