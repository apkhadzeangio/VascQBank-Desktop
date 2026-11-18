"""Choose a backup, see what it holds, confirm explicitly, restore it in the background.

Used from the backup page (the working database exists) and from the start-up recovery message
(it is missing). Nothing is restored without the confirmation box ticked and the restore button
pressed; cancel is the default button. Checking the selected backup (SHA-256, integrity, foreign
keys) and the restore itself run on worker threads; while the restore runs the dialog cannot be
closed. The result is only reported after the restored database was reopened and verified.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QKeyEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services import backup_service, restore_service
from vascqbank_desktop.services.backup_service import BackupEntry, BackupKind
from vascqbank_desktop.services.restore_service import (
    BackupSummary,
    RestoreProblem,
    RestoreRefused,
    RestoreResult,
)
from vascqbank_desktop.services.storage import StorageStatus
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.formatting import format_count, format_size, format_timestamp
from vascqbank_desktop.ui.widgets import make_label, repolish
from vascqbank_desktop.ui.workers import BackgroundJob, LatestOnlyRunner

log = logging.getLogger(__name__)

KIND_LABELS: dict[BackupKind, str] = {
    BackupKind.MANUAL: strings.BK_KIND_MANUAL,
    BackupKind.BEFORE_IMPORT: strings.BK_KIND_IMPORT,
    BackupKind.BEFORE_MIGRATION: strings.BK_KIND_MIGRATION,
    BackupKind.BEFORE_RESTORE: strings.BK_KIND_RESTORE,
    BackupKind.OTHER: strings.BK_KIND_OTHER,
}


def problem_text(error: object) -> str:
    """Georgian wording for a refused or failed restore."""
    if isinstance(error, RestoreRefused):
        return strings.RS_PROBLEMS.get(error.problem.value, error.problem.value)
    return strings.RS_PROBLEM_UNEXPECTED.format(detail=error)  # Phase 9b: Georgian, detail kept


def details_text(summary: BackupSummary, kind: BackupKind) -> str:
    return strings.RS_DETAILS.format(
        time=format_timestamp(summary.created_at),
        kind=KIND_LABELS[kind],
        questions=format_count(summary.questions),
        pending=format_count(summary.count(ReviewStatus.PENDING_REVIEW)),
        in_review=format_count(summary.count(ReviewStatus.IN_REVIEW)),
        approved=format_count(summary.count(ReviewStatus.APPROVED)),
        rejected=format_count(summary.count(ReviewStatus.REJECTED)),
        drafts=format_count(summary.drafts),
        revisions=format_count(summary.revisions),
        schema=summary.schema_version,
        size=format_size(summary.size),
    )


class RestoreDialog(QDialog):
    def __init__(
        self,
        paths: AppPaths,
        *,
        current: StorageStatus | None,
        current_counts: tuple[int, int] | None = None,  # (questions, approved) of the working database
        preselect: Path | None = None,
        before_restore: Callable[[], str | None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.RS_TITLE)
        self.setModal(True)
        self.setMinimumWidth(720)
        self._paths = paths
        self._current = current
        self._before_restore = before_restore
        self._entries: list[BackupEntry] = backup_service.list_backup_entries(paths)
        self._summary: BackupSummary | None = None
        self._restore_job: BackgroundJob | None = None
        self._checker = LatestOnlyRunner(self)
        self._generation = 0
        self.result_value: RestoreResult | None = None
        self.error: object | None = None

        column = QVBoxLayout(self)
        column.setContentsMargins(22, 18, 22, 16)
        column.setSpacing(10)
        column.addWidget(make_label(strings.RS_TITLE, "CardTitle"))
        replacing = current is not None
        intro = make_label(strings.RS_INTRO_REPLACE if replacing else strings.RS_INTRO_MISSING, "CardBody")
        column.addWidget(intro)
        if replacing and current_counts is not None:
            column.addWidget(
                make_label(
                    strings.RS_CURRENT.format(
                        questions=format_count(current_counts[0]), approved=format_count(current_counts[1])
                    ),
                    "FieldValue",
                )
            )

        column.addWidget(make_label(strings.RS_LIST_LABEL, "FieldLabel"))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            [strings.BK_COL_TIME, strings.BK_COL_KIND, strings.BK_COL_QUESTIONS, strings.BK_COL_SIZE]
        )
        self.table.setAccessibleName(strings.RS_LIST_LABEL)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for col in range(3):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.setMinimumHeight(160)
        column.addWidget(self.table, 1)
        self.empty_label = make_label(strings.RS_NO_BACKUPS, "EmptyListMessage")
        column.addWidget(self.empty_label)

        self.check_label = QLabel()
        self.check_label.setObjectName("InfoNotice")
        self.check_label.setWordWrap(True)
        self.check_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        column.addWidget(self.check_label)
        self.details_label = make_label("", "CardBody", selectable=True)
        column.addWidget(self.details_label)
        self.other_db_label = make_label(strings.RS_OTHER_DATABASE, "FieldValue")
        self.other_db_label.setObjectName("StatusWarning")
        column.addWidget(self.other_db_label)
        self.loss_label = make_label(strings.RS_LOSS_WARNING, "CardBody")
        self.loss_label.setVisible(replacing)
        column.addWidget(self.loss_label)

        self.confirm_box = QCheckBox(
            strings.RS_CONFIRM_CHECK if replacing else strings.RS_CONFIRM_CHECK_MISSING
        )
        self.confirm_box.toggled.connect(self._update_buttons)
        column.addWidget(self.confirm_box)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # busy indicator: the steps are named in the label below
        self.progress.setTextVisible(False)
        self.progress.hide()
        column.addWidget(self.progress)
        self.step_label = make_label("", "ResultInfo")
        self.step_label.hide()
        column.addWidget(self.step_label)
        self.error_label = QLabel()
        self.error_label.setObjectName("ErrorNotice")
        self.error_label.setWordWrap(True)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_label.hide()
        column.addWidget(self.error_label)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_button = QPushButton(strings.RS_CANCEL)
        self.cancel_button.setObjectName("SecondaryButton")
        self.cancel_button.setDefault(True)
        self.cancel_button.clicked.connect(self.reject)
        self.restore_button = QPushButton(strings.RS_RESTORE)
        self.restore_button.setObjectName("DangerButton")
        self.restore_button.setAutoDefault(False)
        self.restore_button.clicked.connect(self.start_restore)
        buttons.addWidget(self.restore_button)
        buttons.addWidget(self.cancel_button)
        column.addLayout(buttons)

        self._fill_table()
        self.table.itemSelectionChanged.connect(self._on_selection)
        row = self._row_of(preselect) if preselect is not None else (0 if self._entries else None)
        if row is not None:
            self.table.selectRow(row)
        self._on_selection()
        self.table.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------ list
    def _fill_table(self) -> None:
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            count = "—" if entry.question_count is None else format_count(entry.question_count)
            values = (
                format_timestamp(entry.created_at),
                KIND_LABELS[entry.kind],
                count,
                format_size(entry.size),
            )
            for col, text in enumerate(values):
                item = QTableWidgetItem(text)
                item.setToolTip(str(entry.path))
                if col in (2, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, col, item)
        self.table.setVisible(bool(self._entries))
        self.empty_label.setVisible(not self._entries)

    def _row_of(self, path: Path) -> int | None:
        for row, entry in enumerate(self._entries):
            if entry.path == path:
                return row
        return None

    @property
    def selected_entry(self) -> BackupEntry | None:
        rows = self.table.selectionModel().selectedRows() if self._entries else []
        return self._entries[rows[0].row()] if rows else None

    @property
    def summary(self) -> BackupSummary | None:
        return self._summary

    def is_checking(self) -> bool:
        return self._checker.is_busy()

    def is_restoring(self) -> bool:
        return self._restore_job is not None

    # ------------------------------------------------------------------ verification of the selection
    def _on_selection(self) -> None:
        self._generation += 1
        self._summary = None
        self.confirm_box.setChecked(False)
        self.details_label.setText("")
        self.other_db_label.hide()
        entry = self.selected_entry
        if entry is None:
            self._set_check("", error=False)
            self._update_buttons()
            return
        token = self._generation
        self._set_check(strings.RS_CHECKING, error=False)
        self._checker.submit(
            lambda: restore_service.inspect_backup(entry.path),
            lambda summary: self._on_checked(token, entry, summary),
            lambda error: self._on_check_failed(token, error),
        )
        self._update_buttons()

    def _on_checked(self, token: int, entry: BackupEntry, summary: object) -> None:
        if token != self._generation or not isinstance(summary, BackupSummary):
            return
        self._summary = summary
        self._set_check(strings.RS_VERIFIED, error=False, success=True)
        self.details_label.setText(details_text(summary, entry.kind))
        other = (
            self._current is not None
            and bool(summary.database_id)
            and (summary.database_id != self._current.database_id)
        )
        self.other_db_label.setVisible(other)
        self._update_buttons()

    def _on_check_failed(self, token: int, error: object) -> None:
        if token != self._generation:
            return
        self._set_check(strings.RS_REFUSED.format(reason=problem_text(error)), error=True)
        self._update_buttons()

    def _set_check(self, text: str, *, error: bool, success: bool = False) -> None:
        self.check_label.setObjectName(
            "ErrorNotice" if error else "SuccessNotice" if success else "InfoNotice"
        )
        repolish(self.check_label)
        self.check_label.setText(text)
        self.check_label.setVisible(bool(text))

    def _update_buttons(self) -> None:
        running = self.is_restoring()
        self.restore_button.setEnabled(
            not running and self._summary is not None and self.confirm_box.isChecked()
        )
        self.confirm_box.setEnabled(not running and self._summary is not None)
        self.table.setEnabled(not running)
        self.cancel_button.setEnabled(not running)

    # ------------------------------------------------------------------ restoring
    def start_restore(self) -> bool:
        summary = self._summary
        if summary is None or not self.confirm_box.isChecked() or self.is_restoring():
            return False
        self.error_label.hide()
        if self._before_restore is not None:
            blocked = self._before_restore()
            if blocked:
                self._show_error(blocked)
                return False
        self._checker.wait_idle()
        job = BackgroundJob(
            lambda report: restore_service.restore_backup(
                self._paths, summary.path, expected_sha256=summary.sha256, progress=report
            ),
            self,
            report_progress=True,
        )
        job.progress.connect(self._on_step)
        job.succeeded.connect(lambda result: self._on_restored(job, result))
        job.failed.connect(lambda error: self._on_restore_failed(job, error))
        self._restore_job = job
        self.progress.show()
        self._on_step("start")
        self._update_buttons()
        job.start()
        return True

    def _on_step(self, step: object) -> None:
        self.step_label.setText(strings.RS_STEPS.get(str(step), str(step)))
        self.step_label.show()

    def _on_restored(self, job: BackgroundJob, result: object) -> None:
        self._restore_job = None
        job.deleteLater()
        self.progress.hide()
        self.step_label.hide()
        assert isinstance(result, RestoreResult)
        self.result_value = result
        self.accept()

    def _on_restore_failed(self, job: BackgroundJob, error: object) -> None:
        self._restore_job = None
        job.deleteLater()
        self.progress.hide()
        self.step_label.hide()
        self.error = error
        log.error("Restore failed: %s", error)
        safe = not (isinstance(error, RestoreRefused) and error.problem is RestoreProblem.REOPEN_FAILED)
        text = strings.RS_FAILED.format(reason=problem_text(error))
        self._show_error(f"{text} {strings.RS_FAILED_SAFE}" if safe else text)
        self._entries = backup_service.list_backup_entries(self._paths)  # a safety backup may now exist
        selected = self.selected_entry.path if self.selected_entry else None
        self.table.blockSignals(True)
        self._fill_table()
        self.table.blockSignals(False)
        if selected is not None and (row := self._row_of(selected)) is not None:
            self.table.selectRow(row)
        self._on_selection()

    def _show_error(self, text: str) -> None:
        self.error_label.setText(text)
        self.error_label.show()
        self._update_buttons()

    # ------------------------------------------------------------------ closing
    def wait_for_restore(self, timeout_ms: int = 120_000) -> None:
        if self._restore_job is not None:
            self._restore_job.wait(timeout_ms)

    def reject(self) -> None:
        if self.is_restoring():
            self._show_error(strings.RS_RUNNING)
            return
        self._checker.shutdown()
        super().reject()

    def accept(self) -> None:
        self._checker.shutdown()
        super().accept()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.is_restoring():
            event.ignore()
            self._show_error(strings.RS_RUNNING)
            return
        super().closeEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape and self.is_restoring():
            event.ignore()
            return
        super().keyPressEvent(event)
