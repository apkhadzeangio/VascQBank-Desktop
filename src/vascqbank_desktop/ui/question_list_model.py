"""Lazy list model over the ordered ids of a catalog result.

The model knows only the ordered **ids** (10,000 small integers are cheap). The
text of a row is loaded when the view first asks for it, one page of
:data:`PAGE_SIZE` rows per query, and at most :data:`MAX_CACHED_PAGES` pages are
kept — scrolling through the whole bank never holds more than a screenful of
history in memory, and jumping to row 9,000 loads one page, not 9,000 rows.
"""

from __future__ import annotations

import logging
from collections import OrderedDict

from PySide6.QtCore import QAbstractListModel, QModelIndex, QObject, QPersistentModelIndex, Qt

from vascqbank_desktop.domain.catalog import QuestionSummary, ResultSet
from vascqbank_desktop.services.catalog_service import CatalogService

log = logging.getLogger(__name__)

PAGE_SIZE = 100
MAX_CACHED_PAGES = 16

QuestionIdRole = Qt.ItemDataRole.UserRole + 1
SummaryRole = Qt.ItemDataRole.UserRole + 2


class QuestionListModel(QAbstractListModel):
    def __init__(self, service: CatalogService, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._results = ResultSet()
        self._pages: OrderedDict[int, list[QuestionSummary | None]] = OrderedDict()
        self.pages_loaded = 0  # queries issued since creation (diagnostics and tests)

    # ------------------------------------------------------------------ results
    @property
    def results(self) -> ResultSet:
        return self._results

    def set_results(self, results: ResultSet) -> None:
        self.beginResetModel()
        self._results = results
        self._pages.clear()
        self.endResetModel()

    def row_of(self, question_id: int | None) -> int | None:
        return self._results.position_of(question_id)

    def question_id_at(self, row: int) -> int | None:
        return self._results.ids[row] if 0 <= row < len(self._results) else None

    @property
    def cached_page_count(self) -> int:
        return len(self._pages)

    # ------------------------------------------------------------------ rows
    def _page(self, page_number: int) -> list[QuestionSummary | None]:
        cached = self._pages.get(page_number)
        if cached is not None:
            self._pages.move_to_end(page_number)
            return cached
        start = page_number * PAGE_SIZE
        ids = self._results.ids[start : start + PAGE_SIZE]
        try:
            by_id = {row.id: row for row in self._service.rows(ids, self._results.terms)}
        except Exception:  # a failing page must not take the whole window down
            log.exception("Could not load catalog rows %s–%s", start, start + len(ids))
            by_id = {}
        self.pages_loaded += 1
        page = [by_id.get(question_id) for question_id in ids]
        self._pages[page_number] = page
        while len(self._pages) > MAX_CACHED_PAGES:
            self._pages.popitem(last=False)
        return page

    def summary_at(self, row: int) -> QuestionSummary | None:
        if not 0 <= row < len(self._results):
            return None
        return self._page(row // PAGE_SIZE)[row % PAGE_SIZE]

    # ------------------------------------------------------------------ Qt model API
    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._results)

    def data(
        self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole
    ) -> object:
        if not index.isValid():
            return None
        if role == QuestionIdRole:
            return self.question_id_at(index.row())
        summary = self.summary_at(index.row())
        if summary is None:
            return None
        if role == SummaryRole:
            return summary
        if role == Qt.ItemDataRole.DisplayRole:
            return f"{summary.uid}  {summary.preview.text}"
        if role == Qt.ItemDataRole.AccessibleTextRole:
            return f"{summary.uid}, {summary.status.label_ka}. {summary.preview.text}"
        return None
