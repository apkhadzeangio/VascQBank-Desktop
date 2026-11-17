"""Import dialog: select files → validate (dry run) → confirm → result.

Nothing is written to the database until the user confirms on step 2; the
confirmation itself triggers a verified backup before any insert.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.question import SourceKind
from vascqbank_desktop.domain.subjects import get_subject, is_known_subject
from vascqbank_desktop.services import import_service
from vascqbank_desktop.services.import_service import (
    DryRunReport,
    ImportAbortedError,
    ImportResult,
)
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.workers import BackgroundJob
from vascqbank_desktop.validation.issues import Severity, ValidationIssue

log = logging.getLogger(__name__)

MAX_ISSUE_ROWS = 2000


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _label(text: str, name: str, wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName(name)
    label.setWordWrap(wrap)
    return label


def _button(text: str, name: str = "SecondaryButton") -> QPushButton:
    button = QPushButton(text)
    button.setObjectName(name)
    return button


def _table(headers: list[str]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setWordWrap(True)
    table.horizontalHeader().setStretchLastSection(True)
    return table


class ImportDialog(QDialog):
    validationFinished = Signal(object)  # DryRunReport
    importFinished = Signal(object)  # ImportResult
    importFailed = Signal(object)  # Exception

    def __init__(self, paths: AppPaths, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._paths = paths
        self._selected: list[Path] = []
        self._report: DryRunReport | None = None
        self._result: ImportResult | None = None
        self._job: BackgroundJob | None = None

        self.setWindowTitle(strings.IMP_TITLE)
        self.setModal(True)
        self.resize(980, 680)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)

        steps = QHBoxLayout()
        self.step_labels = [
            _label(text, "StepInactive", wrap=False)
            for text in (strings.IMP_STEP_SELECT, strings.IMP_STEP_CHECK, strings.IMP_STEP_DONE)
        ]
        for i, label in enumerate(self.step_labels):
            if i:
                steps.addWidget(_label("›", "StepInactive", wrap=False))
            steps.addWidget(label)
        steps.addStretch(1)
        root.addLayout(steps)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._build_select_page())
        self.pages.addWidget(self._build_check_page())
        self.pages.addWidget(self._build_done_page())
        root.addWidget(self.pages, 1)

        self.busy_label = _label("", "CardBody")
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setTextVisible(False)
        self.busy_bar.setMaximumHeight(6)
        for widget in (self.busy_label, self.busy_bar):
            widget.hide()
            root.addWidget(widget)

        self._show_step(0)
        self._refresh_sources()

    # ------------------------------------------------------------------
    # page construction
    # ------------------------------------------------------------------
    def _build_select_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_label(strings.IMP_SELECT_INTRO, "CardBody"))

        row = QHBoxLayout()
        self.add_files_button = _button(strings.IMP_ADD_FILES)
        self.add_folder_button = _button(strings.IMP_ADD_FOLDER)
        self.clear_button = _button(strings.IMP_CLEAR)
        self.add_files_button.clicked.connect(self._choose_files)
        self.add_folder_button.clicked.connect(self._choose_folder)
        self.clear_button.clicked.connect(self.clear_selection)
        for b in (self.add_files_button, self.add_folder_button, self.clear_button):
            row.addWidget(b)
        row.addStretch(1)
        layout.addLayout(row)

        self.sources_table = _table(
            [strings.IMP_COL_SUBJECT, strings.IMP_COL_FORMAT, strings.IMP_COL_FILES, strings.IMP_COL_STATE]
        )
        header = self.sources_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.sources_table, 1)
        self.sources_note = _label("", "CardBody")
        layout.addWidget(self.sources_note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close = _button(strings.IMP_CLOSE)
        close.clicked.connect(self.reject)
        self.validate_button = _button(strings.IMP_VALIDATE, "PrimaryButton")
        self.validate_button.clicked.connect(self.run_validation)
        buttons.addWidget(close)
        buttons.addWidget(self.validate_button)
        layout.addLayout(buttons)
        return page

    def _build_check_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)

        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(18, 14, 18, 14)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(2)
        self.summary_values: dict[str, QLabel] = {}
        cells = [
            ("total_parsed", strings.IMP_SUM_PARSED, "SummaryNumber"),
            ("valid", strings.IMP_SUM_VALID, "SummaryNumber"),
            ("invalid", strings.IMP_SUM_INVALID, "SummaryNumberBad"),
            ("duplicates_in_selection", strings.IMP_SUM_DUPLICATES, "SummaryNumberBad"),
            ("identical_existing", strings.IMP_SUM_IDENTICAL, "SummaryNumber"),
            ("conflicts", strings.IMP_SUM_CONFLICTS, "SummaryNumberBad"),
            ("errors", strings.IMP_SUM_ERRORS, "SummaryNumberBad"),
            ("warnings", strings.IMP_SUM_WARNINGS, "SummaryNumber"),
            ("eligible", strings.IMP_SUM_ELIGIBLE, "SummaryNumberGood"),
        ]
        for i, (key, caption, style) in enumerate(cells):
            value = _label("0", style, wrap=False)
            self.summary_values[key] = value
            r, c = divmod(i, 5)
            grid.addWidget(value, r * 2, c)
            grid.addWidget(_label(caption, "FieldName"), r * 2 + 1, c, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(card)

        self.issues_title = _label(strings.IMP_ISSUES_TITLE, "CardTitle")
        layout.addWidget(self.issues_title)
        self.issues_table = _table(
            [strings.IMP_COL_SEVERITY, strings.IMP_COL_ID, strings.IMP_COL_LOCATION, strings.IMP_COL_MESSAGE]
        )
        header = self.issues_table.horizontalHeader()
        for col in (0, 1, 2):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.issues_table, 1)
        self.issues_note = _label("", "CardBody")
        layout.addWidget(self.issues_note)

        buttons = QHBoxLayout()
        self.back_button = _button(strings.IMP_BACK)
        self.back_button.clicked.connect(lambda: self._show_step(0))
        buttons.addWidget(self.back_button)
        buttons.addStretch(1)
        close = _button(strings.IMP_CLOSE)
        close.clicked.connect(self.reject)
        self.confirm_button = _button(strings.IMP_CONFIRM_BUTTON.format(count=0), "PrimaryButton")
        self.confirm_button.clicked.connect(self.confirm_import)
        buttons.addWidget(close)
        buttons.addWidget(self.confirm_button)
        layout.addLayout(buttons)
        return page

    def _build_done_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_label(strings.IMP_RESULT_TITLE, "PageHeading"))
        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(18, 14, 18, 14)
        grid.setHorizontalSpacing(18)
        grid.setColumnStretch(1, 1)
        self.result_values: dict[str, QLabel] = {}
        rows = [
            ("created", strings.IMP_RES_CREATED),
            ("identical", strings.IMP_RES_IDENTICAL),
            ("conflicts", strings.IMP_RES_CONFLICTS),
            ("race", strings.IMP_RES_RACE),
            ("invalid", strings.IMP_RES_INVALID),
            ("batch", strings.IMP_RES_BATCH),
            ("backup", strings.IMP_RES_BACKUP),
            ("duration", strings.IMP_RES_DURATION),
        ]
        for r, (key, caption) in enumerate(rows):
            grid.addWidget(_label(caption, "FieldName", wrap=False), r, 0, Qt.AlignmentFlag.AlignTop)
            value = _label("", "FieldValue")
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            self.result_values[key] = value
            grid.addWidget(value, r, 1)
        layout.addWidget(card)
        layout.addStretch(1)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        done = _button(strings.IMP_CLOSE, "PrimaryButton")
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        layout.addLayout(buttons)
        return page

    # ------------------------------------------------------------------
    # step 1 — selection
    # ------------------------------------------------------------------
    def _choose_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, strings.IMP_ADD_FILES, "", strings.IMP_FILE_FILTER)
        self.add_paths(Path(f) for f in files)

    def _choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, strings.IMP_ADD_FOLDER)
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths: Iterable[Path]) -> None:
        for path in paths:
            if path not in self._selected:
                self._selected.append(path)
        self._refresh_sources()

    def clear_selection(self) -> None:
        self._selected.clear()
        self._refresh_sources()

    def _refresh_sources(self) -> None:
        discovery = import_service.discover_selection(self._selected)
        table = self.sources_table
        table.setRowCount(0)
        for source in discovery.sources:
            row = table.rowCount()
            table.insertRow(row)
            if source.kind is SourceKind.REFINED_MASTER:
                subject, fmt = "—", strings.IMP_FORMAT_MASTER
                state = strings.IMP_STATE_READY
            else:
                code = source.subject_code or ""
                subject = f"{code} · {get_subject(code).name_ka}" if is_known_subject(code) else code
                fmt = strings.IMP_FORMAT_PAIRED
                if not is_known_subject(code):
                    state = strings.IMP_STATE_UNKNOWN
                elif source.explanations_path is None:
                    state = strings.IMP_STATE_MISSING_EXPL
                elif source.questions_path is None:
                    state = strings.IMP_STATE_MISSING_Q
                else:
                    state = strings.IMP_STATE_READY
            files = ", ".join(p.name for p in source.files)
            for col, text in enumerate((subject, fmt, files, state)):
                item = QTableWidgetItem(text)
                item.setToolTip("\n".join(str(p) for p in source.files) if col == 2 else text)
                table.setItem(row, col, item)
        notes = []
        if not discovery.sources:
            notes.append(strings.IMP_NOTHING_FOUND)
        if discovery.issues:
            notes.append(strings.IMP_SKIPPED_FILES.format(count=len(discovery.issues)) + ": "
                         + ", ".join(i.file or "" for i in discovery.issues[:8]))
        self.sources_note.setText("\n".join(notes))
        self.validate_button.setEnabled(bool(discovery.sources))
        self.clear_button.setEnabled(bool(self._selected))

    # ------------------------------------------------------------------
    # step 2 — dry run
    # ------------------------------------------------------------------
    def run_validation(self) -> None:
        selected = list(self._selected)
        self._start_job(
            lambda: import_service.dry_run(self._paths, selected),
            strings.IMP_WORKING_VALIDATE,
            self._on_validated,
            self._on_validation_failed,
        )

    def _on_validated(self, report: object) -> None:
        assert isinstance(report, DryRunReport)
        self._report = report
        counts = report.counts
        attention = {"invalid", "duplicates_in_selection", "conflicts", "errors"}
        for key, label in self.summary_values.items():
            value = getattr(counts, key)
            label.setText(_fmt(value))
            if key in attention:
                style = "SummaryNumberBad" if value else "SummaryNumber"
            elif key == "eligible":
                style = "SummaryNumberGood" if value else "SummaryNumber"
            else:
                style = "SummaryNumber"
            label.setObjectName(style)
            label.style().unpolish(label)
            label.style().polish(label)
        self._fill_issues(report.all_issues())
        self.confirm_button.setText(strings.IMP_CONFIRM_BUTTON.format(count=_fmt(counts.eligible)))
        self.confirm_button.setEnabled(counts.eligible > 0)
        self.confirm_button.setToolTip("" if counts.eligible else strings.IMP_NOTHING_ELIGIBLE)
        self._show_step(1)
        self.validationFinished.emit(report)

    def _on_validation_failed(self, exc: object) -> None:
        log.error("Dry run failed: %s", exc)
        self._show_error(str(exc))

    def _fill_issues(self, issues: list[ValidationIssue]) -> None:
        ordered = sorted(issues, key=lambda i: (i.severity is not Severity.ERROR, i.file or "", i.line or 0))
        shown = ordered[:MAX_ISSUE_ROWS]
        table = self.issues_table
        table.setUpdatesEnabled(False)
        table.setRowCount(len(shown))
        for row, issue in enumerate(shown):
            severity = strings.IMP_SEV_ERROR if issue.is_error else strings.IMP_SEV_WARNING
            values = (severity, issue.question_id or "", issue.location(), issue.message)
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(issue.message if col == 3 else text)
                table.setItem(row, col, item)
        table.resizeRowsToContents()  # full messages, wrapped
        table.setUpdatesEnabled(True)
        if not issues:
            self.issues_note.setText(strings.IMP_ISSUES_NONE)
        elif len(issues) > len(shown):
            self.issues_note.setText(f"ნაჩვენებია პირველი {_fmt(len(shown))} / {_fmt(len(issues))}.")
        else:
            self.issues_note.setText("")

    # ------------------------------------------------------------------
    # step 3 — confirmed import
    # ------------------------------------------------------------------
    def confirm_import(self) -> None:
        if self._report is None or not self._report.eligible:
            return
        if not self._confirm(len(self._report.eligible)):
            return
        report = self._report
        self._start_job(
            lambda: import_service.commit_import(self._paths, report),
            strings.IMP_WORKING_IMPORT,
            self._on_imported,
            self._on_import_failed,
        )

    def _confirm(self, count: int) -> bool:
        answer = QMessageBox.question(
            self,
            strings.IMP_CONFIRM_TITLE,
            strings.IMP_CONFIRM_TEXT.format(count=_fmt(count)),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_imported(self, result: object) -> None:
        assert isinstance(result, ImportResult)
        self._result = result
        values = {
            "created": _fmt(len(result.created)),
            "identical": _fmt(result.skipped_identical),
            "conflicts": _fmt(result.skipped_conflicts),
            "race": _fmt(result.skipped_existing_at_commit),
            "invalid": _fmt(result.invalid),
            "batch": f"#{result.batch_id}",
            "backup": str(result.backup.path),
            "duration": f"{result.duration_s:.1f} წმ",
        }
        for key, text in values.items():
            self.result_values[key].setText(text)
        self._show_step(2)
        self.importFinished.emit(result)

    def _on_import_failed(self, exc: object) -> None:
        log.error("Import failed: %s", exc)
        detail = str(exc)
        if isinstance(exc, ImportAbortedError) and "changed" in detail:
            detail = strings.IMP_ERROR_SOURCES_CHANGED + "\n\n" + detail
        self._show_error(detail)
        self.importFailed.emit(exc)

    def _show_error(self, detail: str) -> None:
        box = QMessageBox(
            QMessageBox.Icon.Critical, strings.IMP_ERROR_TITLE, strings.IMP_ERROR_TEXT, parent=self
        )
        box.setDetailedText(detail)
        box.exec()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @property
    def report(self) -> DryRunReport | None:
        return self._report

    @property
    def result(self) -> ImportResult | None:
        return self._result

    def is_busy(self) -> bool:
        return self._job is not None

    def _show_step(self, index: int) -> None:
        self.pages.setCurrentIndex(index)
        for i, label in enumerate(self.step_labels):
            label.setObjectName("StepActive" if i == index else "StepInactive")
            label.style().unpolish(label)
            label.style().polish(label)

    def _set_busy(self, message: str | None) -> None:
        busy = message is not None
        self.busy_label.setText(message or "")
        self.busy_label.setVisible(busy)
        self.busy_bar.setVisible(busy)
        self.pages.setEnabled(not busy)

    def _start_job(self, fn, message: str, on_success, on_failure) -> None:  # noqa: ANN001
        if self._job is not None:
            return
        self._set_busy(message)
        job = BackgroundJob(fn, self)

        def finished(handler):  # noqa: ANN001, ANN202
            def slot(value: object) -> None:
                self._job = None
                self._set_busy(None)
                handler(value)
            return slot

        job.succeeded.connect(finished(on_success))
        job.failed.connect(finished(on_failure))
        self._job = job
        job.start()

    def reject(self) -> None:  # closing while a job runs would orphan the thread
        if self._job is not None:
            return
        super().reject()
