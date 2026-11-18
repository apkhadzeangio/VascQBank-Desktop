"""Backup page: create a verified backup, see every backup and whether it still verifies.

Creating and verifying run on background threads (a backup of a large database
takes a moment). "Restore" only *asks* the main window to open the restore dialog
(:mod:`vascqbank_desktop.ui.restore_dialog`), which verifies the backup again,
shows what it holds and needs an explicit confirmation; this page never changes
the working database itself.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.services import backup_service
from vascqbank_desktop.services.backup_service import (
    BackupEntry,
    BackupKind,
    BackupProblem,
    VerificationStatus,
)
from vascqbank_desktop.ui import strings, theme
from vascqbank_desktop.ui.formatting import format_count, format_size, format_timestamp
from vascqbank_desktop.ui.widgets import make_label, repolish
from vascqbank_desktop.ui.workers import BackgroundJob

log = logging.getLogger(__name__)

_KIND_LABELS: dict[BackupKind, str] = {
    BackupKind.MANUAL: strings.BK_KIND_MANUAL,
    BackupKind.BEFORE_IMPORT: strings.BK_KIND_IMPORT,
    BackupKind.BEFORE_MIGRATION: strings.BK_KIND_MIGRATION,
    BackupKind.BEFORE_RESTORE: strings.BK_KIND_RESTORE,
    BackupKind.OTHER: strings.BK_KIND_OTHER,
}
_PROBLEM_LABELS: dict[BackupProblem, str] = {
    BackupProblem.MISSING_FILE: strings.BK_PROBLEM_MISSING_FILE,
    BackupProblem.SIZE_MISMATCH: strings.BK_PROBLEM_SIZE_MISMATCH,
    BackupProblem.CHECKSUM_MISMATCH: strings.BK_PROBLEM_CHECKSUM_MISMATCH,
    BackupProblem.UNREADABLE: strings.BK_PROBLEM_UNREADABLE,
    BackupProblem.COUNTS_MISMATCH: strings.BK_PROBLEM_COUNTS_MISMATCH,
}

COL_TIME, COL_KIND, COL_QUESTIONS, COL_SIZE, COL_STATUS, COL_FILE = range(6)


def status_text(entry: BackupEntry, *, checking: bool = False) -> str:
    """The wording shown in the verification column."""
    if checking:
        return strings.BK_STATUS_CHECKING
    if entry.status is VerificationStatus.UNCHECKED:
        return strings.BK_STATUS_UNCHECKED
    if entry.status is VerificationStatus.VERIFIED:
        return strings.BK_STATUS_VERIFIED if entry.has_manifest else strings.BK_STATUS_VERIFIED_NO_MANIFEST
    problem = _PROBLEM_LABELS.get(entry.problem, entry.detail)
    return f"{strings.BK_STATUS_FAILED} — {problem}"


class BackupView(QWidget):
    backupCreated = Signal(object)  # BackupEntry
    restoreRequested = Signal(object)  # Path of the selected backup (or None)

    def __init__(self, paths: AppPaths, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContentArea")
        self._paths = paths
        self._entries: list[BackupEntry] = []
        # name -> last verification; reused while the file size is unchanged
        self._verified: dict[str, BackupEntry] = {}
        self._pending: set[str] = set()  # names being verified right now
        self._create_job: BackgroundJob | None = None
        self._verify_job: BackgroundJob | None = None

        page = QVBoxLayout(self)
        page.setContentsMargins(40, 34, 40, 26)
        page.setSpacing(12)
        page.addWidget(make_label(strings.BK_TITLE, "PageHeading"))
        page.addWidget(make_label(strings.BK_INTRO, "PageTagline"))

        buttons = QHBoxLayout()
        self.create_button = QPushButton(strings.BK_CREATE)
        self.create_button.setObjectName("PrimaryButton")
        self.create_button.clicked.connect(self.create_backup)
        self.verify_button = QPushButton(strings.BK_VERIFY_ALL)
        self.verify_button.setObjectName("SecondaryButton")
        self.verify_button.clicked.connect(lambda: self.verify_all(force=True))
        self.folder_button = QPushButton(strings.BK_OPEN_FOLDER)
        self.folder_button.setObjectName("SecondaryButton")
        self.folder_button.clicked.connect(self.open_folder)
        self.restore_button = QPushButton(strings.BK_RESTORE)
        self.restore_button.setObjectName("SecondaryButton")
        self.restore_button.setToolTip(strings.BK_RESTORE_TOOLTIP)
        self.restore_button.clicked.connect(self.request_restore)
        for button in (self.create_button, self.verify_button, self.folder_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self.restore_button)
        page.addLayout(buttons)

        self.status_label = QLabel()
        self.status_label.setObjectName("InfoNotice")
        self.status_label.setWordWrap(True)
        self.status_label.hide()
        page.addWidget(self.status_label)

        self.summary_label = make_label("", "ResultInfo", wrap=False)
        page.addWidget(self.summary_label)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            [
                strings.BK_COL_TIME,
                strings.BK_COL_KIND,
                strings.BK_COL_QUESTIONS,
                strings.BK_COL_SIZE,
                strings.BK_COL_STATUS,
                strings.BK_COL_FILE,
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setAlternatingRowColors(False)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for column in (COL_TIME, COL_KIND, COL_QUESTIONS, COL_SIZE):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Stretch)
        self.table.setAccessibleName(strings.BK_TITLE)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.doubleClicked.connect(lambda _index: self.request_restore())
        page.addWidget(self.table, 1)
        self.selected_label = make_label(strings.BK_SELECT_HINT, "ResultInfo")
        page.addWidget(self.selected_label)

        self.empty_label = make_label(strings.BK_EMPTY, "EmptyListMessage")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        page.addWidget(self.empty_label)

        self.folder_label = make_label("", "FieldValue", selectable=True)
        page.addWidget(self.folder_label)
        note = QFrame()
        note.setObjectName("Card")
        note_layout = QVBoxLayout(note)
        note_layout.setContentsMargins(18, 12, 18, 12)
        self.restore_note = make_label(strings.BK_RESTORE_NOTE, "CardBody")
        note_layout.addWidget(self.restore_note)
        page.addWidget(note)
        self.refresh()

    # ------------------------------------------------------------------ listing
    @property
    def entries(self) -> list[BackupEntry]:
        return list(self._entries)

    def is_busy(self) -> bool:
        return self._create_job is not None or self._verify_job is not None

    def is_creating(self) -> bool:
        """A backup of the live database is being written (verifying only reads backup files)."""
        return self._create_job is not None

    def refresh(self, *, auto_verify: bool = True) -> None:
        """Re-read the backup folder; backups not verified yet are verified in the background."""
        listed = backup_service.list_backup_entries(self._paths)
        merged: list[BackupEntry] = []
        for entry in listed:
            known = self._verified.get(entry.name)
            merged.append(known if known is not None and known.size == entry.size else entry)
        self._entries = merged
        self.folder_label.setText(strings.BK_FOLDER.format(path=self._paths.backups_dir))
        self._render()
        if auto_verify:
            self.verify_all(force=False)

    def _render(self) -> None:
        selected = self.selected_entry
        self.table.blockSignals(True)
        self.table.setRowCount(len(self._entries))
        for row, entry in enumerate(self._entries):
            self._fill_row(row, entry)
            if selected is not None and entry.name == selected.name:
                self.table.selectRow(row)
        self.table.blockSignals(False)
        self._on_selection()
        self.empty_label.setVisible(not self._entries)
        self.table.setVisible(bool(self._entries))
        self.summary_label.setText(strings.BK_SUMMARY.format(count=format_count(len(self._entries))))
        self._update_buttons()

    def _fill_row(self, row: int, entry: BackupEntry) -> None:
        checking = entry.name in self._pending
        count = "—" if entry.question_count is None else format_count(entry.question_count)
        values = {
            COL_TIME: format_timestamp(entry.created_at),
            COL_KIND: _KIND_LABELS[entry.kind],
            COL_QUESTIONS: count,
            COL_SIZE: format_size(entry.size),
            COL_STATUS: status_text(entry, checking=checking),
            COL_FILE: entry.name,
        }
        for column, text in values.items():
            item = QTableWidgetItem(text)
            item.setToolTip(str(entry.path) if column == COL_FILE else text)
            if column == COL_STATUS:
                color = {
                    VerificationStatus.VERIFIED: theme.SUCCESS,
                    VerificationStatus.FAILED: theme.DANGER,
                    VerificationStatus.UNCHECKED: theme.TEXT_MUTED,
                }[entry.status]
                item.setForeground(QBrush(QColor(color)))
            if column in (COL_QUESTIONS, COL_SIZE):
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            self.table.setItem(row, column, item)

    def _update_buttons(self) -> None:
        busy = self.is_busy()
        self.create_button.setEnabled(self._create_job is None)
        self.verify_button.setEnabled(not busy and bool(self._entries))
        entry = self.selected_entry
        self.restore_button.setEnabled(
            not self.is_creating() and entry is not None and entry.status is not VerificationStatus.FAILED
        )

    # ------------------------------------------------------------------ selection / restore
    @property
    def selected_entry(self) -> BackupEntry | None:
        rows = self.table.selectionModel().selectedRows()
        if not rows or not 0 <= rows[0].row() < len(self._entries):
            return None
        return self._entries[rows[0].row()]

    def select_backup(self, name: str) -> bool:
        for row, entry in enumerate(self._entries):
            if entry.name == name:
                self.table.selectRow(row)
                return True
        return False

    def _on_selection(self) -> None:
        entry = self.selected_entry
        if entry is None:
            self.selected_label.setText(strings.BK_SELECT_HINT)
        else:
            count = "—" if entry.question_count is None else format_count(entry.question_count)
            self.selected_label.setText(
                strings.BK_SELECTED.format(
                    time=format_timestamp(entry.created_at), kind=_KIND_LABELS[entry.kind], questions=count
                )
            )
        self._update_buttons()

    def request_restore(self) -> None:
        """Ask for the restore dialog with the selected backup (the dialog checks and confirms)."""
        entry = self.selected_entry
        if entry is None or self.is_creating():
            return
        self.restoreRequested.emit(entry.path)

    def show_message(self, text: str, *, error: bool = False) -> None:
        self._set_status(text, error=error)

    # ------------------------------------------------------------------ verification
    def verify_all(self, *, force: bool) -> None:
        """Verify backups in the background; with *force* even those verified before."""
        if self._verify_job is not None:
            return
        todo = [
            e for e in self._entries if force or e.status is VerificationStatus.UNCHECKED
        ]
        if not todo:
            return
        self._pending = {e.name for e in todo}
        total = len(todo)
        job = BackgroundJob(
            lambda report: backup_service.verify_all(todo, progress=lambda i, e: report((i, e))),
            self,
            report_progress=True,
        )
        job.progress.connect(lambda payload: self._on_verified(payload, total))
        job.succeeded.connect(lambda results: self._on_verify_done(job, results))
        job.failed.connect(lambda error: self._on_verify_failed(job, error))
        self._verify_job = job
        self._set_status(strings.BK_VERIFYING.format(done=0, total=total))
        self._render()
        job.start()

    def _on_verified(self, payload: object, total: int) -> None:
        index, entry = payload  # type: ignore[misc]
        self._verified[entry.name] = entry
        self._pending.discard(entry.name)
        for row, current in enumerate(self._entries):
            if current.name == entry.name:
                self._entries[row] = entry
                self._fill_row(row, entry)
        self._set_status(strings.BK_VERIFYING.format(done=index + 1, total=total))

    def _on_verify_done(self, job: BackgroundJob, results: object) -> None:
        self._verify_job = None
        job.deleteLater()
        self._pending.clear()
        results = list(results)  # type: ignore[call-overload]
        ok = sum(1 for e in results if e.status is VerificationStatus.VERIFIED)
        bad = len(results) - ok
        self._set_status(strings.BK_VERIFY_DONE.format(ok=ok, bad=bad), error=bool(bad))
        self._render()

    def _on_verify_failed(self, job: BackgroundJob, error: object) -> None:
        self._verify_job = None
        job.deleteLater()
        self._pending.clear()
        log.error("Backup verification failed: %s", error)
        self._set_status(str(error), error=True)
        self._render()

    # ------------------------------------------------------------------ creating
    def create_backup(self) -> None:
        if self._create_job is not None:
            return
        job = BackgroundJob(lambda: backup_service.create_manual_backup(self._paths), self)
        job.succeeded.connect(lambda entry: self._on_created(job, entry))
        job.failed.connect(lambda error: self._on_create_failed(job, error))
        self._create_job = job
        self._set_status(strings.BK_CREATING)
        self._update_buttons()
        job.start()

    def _on_created(self, job: BackgroundJob, entry: object) -> None:
        assert isinstance(entry, BackupEntry)
        self._create_job = None
        job.deleteLater()
        self._verified[entry.name] = entry  # creating a backup already verified it
        self.refresh(auto_verify=False)
        self._set_status(strings.BK_CREATED.format(name=entry.name))
        self.backupCreated.emit(entry)

    def _on_create_failed(self, job: BackgroundJob, error: object) -> None:
        self._create_job = None
        job.deleteLater()
        log.error("Manual backup failed: %s", error)
        self._set_status(strings.BK_CREATE_FAILED.format(detail=error), error=True)
        self._update_buttons()

    # ------------------------------------------------------------------ misc
    def open_folder(self) -> None:
        self._paths.backups_dir.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self._paths.backups_dir))))

    def _set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setObjectName("ErrorNotice" if error else "InfoNotice")
        repolish(self.status_label)
        self.status_label.setText(text)
        self.status_label.setVisible(bool(text))

    def shutdown(self) -> None:
        for job in (self._create_job, self._verify_job):
            if job is not None:
                job.stop_and_wait()
        self._create_job = None
        self._verify_job = None

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.shutdown()
        super().closeEvent(event)
