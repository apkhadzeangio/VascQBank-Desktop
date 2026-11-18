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
    QFileDialog,
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
from vascqbank_desktop.services import auto_backup, backup_portability, backup_service
from vascqbank_desktop.services.auto_backup import AutoBackupOutcome, AutoBackupResult
from vascqbank_desktop.services.backup_portability import BackupCopyError, BackupCopyResult
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
    BackupKind.AUTOMATIC: strings.BK_KIND_AUTOMATIC,
    BackupKind.IMPORTED: strings.BK_KIND_IMPORTED,
    BackupKind.OTHER: strings.BK_KIND_OTHER,
}
_PROBLEM_LABELS: dict[BackupProblem, str] = {
    BackupProblem.MISSING_FILE: strings.BK_PROBLEM_MISSING_FILE,
    BackupProblem.SIZE_MISMATCH: strings.BK_PROBLEM_SIZE_MISMATCH,
    BackupProblem.CHECKSUM_MISMATCH: strings.BK_PROBLEM_CHECKSUM_MISMATCH,
    BackupProblem.UNREADABLE: strings.BK_PROBLEM_UNREADABLE,
    BackupProblem.COUNTS_MISMATCH: strings.BK_PROBLEM_COUNTS_MISMATCH,
    BackupProblem.MANIFEST_INVALID: strings.BK_PROBLEM_MANIFEST_INVALID,
}


def copy_problem_text(error: object) -> str:
    """Georgian wording for a failed copy / import (technical detail only goes to the log)."""
    if isinstance(error, BackupCopyError):
        return strings.BK_COPY_PROBLEMS.get(error.problem.value, error.problem.value)
    return strings.BK_COPY_PROBLEMS["WRITE_FAILED"]

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
    automaticBackupFinished = Signal(object)  # AutoBackupResult (Phase 9)

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
        self._file_job: BackgroundJob | None = None  # save copy / add external (Phase 9)

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

        # Phase 9: a second row, so that the first one still fits at 1280×720.
        portable = QHBoxLayout()
        self.save_copy_button = QPushButton(strings.BK_SAVE_COPY)
        self.save_copy_button.setObjectName("SecondaryButton")
        self.save_copy_button.setToolTip(strings.BK_SAVE_COPY_TOOLTIP)
        self.save_copy_button.clicked.connect(lambda: self.save_copy())
        self.add_external_button = QPushButton(strings.BK_ADD_EXTERNAL)
        self.add_external_button.setObjectName("SecondaryButton")
        self.add_external_button.setToolTip(strings.BK_ADD_EXTERNAL_TOOLTIP)
        self.add_external_button.clicked.connect(lambda: self.add_external())
        portable.addWidget(self.save_copy_button)
        portable.addWidget(self.add_external_button)
        portable.addStretch(1)
        page.addLayout(portable)

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
        return self._create_job is not None or self._verify_job is not None or self._file_job is not None

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

    # ------------------------------------------------------------------ automatic (Phase 9)
    def start_automatic_backup(self) -> bool:
        """Apply the daily start-up policy in the background (a no-op while a backup is written)."""
        if self._create_job is not None:
            return False
        job = BackgroundJob(lambda: auto_backup.run_automatic_backup(self._paths), self)
        job.succeeded.connect(lambda result: self._on_automatic_done(job, result))
        job.failed.connect(
            lambda error: self._on_automatic_done(
                job, AutoBackupResult(AutoBackupOutcome.FAILED, detail=str(error))
            )
        )
        self._create_job = job  # blocks a restore and a second backup meanwhile
        self._update_buttons()
        job.start()
        return True

    def _on_automatic_done(self, job: BackgroundJob, result: object) -> None:
        assert isinstance(result, AutoBackupResult)
        self._create_job = None
        job.deleteLater()
        if result.outcome is AutoBackupOutcome.CREATED:
            self.refresh()
            self._set_status(strings.BK_AUTO_CREATED.format(name=result.path.name if result.path else ""))
        elif result.outcome is AutoBackupOutcome.FAILED:
            self._set_status(strings.BK_AUTO_FAILED.format(detail=result.detail), error=True)
        self._update_buttons()
        self.automaticBackupFinished.emit(result)

    # ------------------------------------------------------------------ portable copies (Phase 9)
    def save_copy(self, destination: Path | None = None) -> bool:
        """Write the selected backup and its manifest to another folder (asked if not given)."""
        entry = self.selected_entry
        if entry is None or self._file_job is not None:
            return False
        if destination is None:
            folder = QFileDialog.getExistingDirectory(self, strings.BK_SAVE_COPY_TITLE)
            if not folder:
                return False
            destination = Path(folder)
        source = entry.path
        target = destination
        self._start_file_job(
            lambda: backup_portability.save_backup_copy(source, target),
            strings.BK_SAVING_COPY,
            self._on_copy_saved,
            strings.BK_COPY_FAILED,
        )
        return True

    def add_external(self, source: Path | None = None) -> bool:
        """Verify an externally stored backup and copy it into the list (the database is untouched)."""
        if self._file_job is not None:
            return False
        if source is None:
            file_name, _ = QFileDialog.getOpenFileName(
                self, strings.BK_ADD_EXTERNAL_TITLE, "", strings.BK_ADD_EXTERNAL_FILTER
            )
            if not file_name:
                return False
            source = Path(file_name)
        chosen = source
        self._start_file_job(
            lambda: backup_portability.import_external_backup(self._paths, chosen),
            strings.BK_ADDING_EXTERNAL,
            self._on_external_added,
            strings.BK_EXTERNAL_FAILED,
        )
        return True

    def _start_file_job(self, fn, busy_text: str, on_success, failure_text: str) -> None:  # noqa: ANN001
        job = BackgroundJob(fn, self)
        job.succeeded.connect(lambda result: self._on_file_job_done(job, on_success, result))
        job.failed.connect(lambda error: self._on_file_job_failed(job, failure_text, error))
        self._file_job = job
        self._set_status(busy_text)
        self._update_buttons()
        job.start()

    def _on_file_job_done(self, job: BackgroundJob, on_success, result: object) -> None:  # noqa: ANN001
        self._file_job = None
        job.deleteLater()
        on_success(result)
        self._update_buttons()

    def _on_file_job_failed(self, job: BackgroundJob, failure_text: str, error: object) -> None:
        self._file_job = None
        job.deleteLater()
        log.error("Backup copy failed: %s", error)
        self._set_status(failure_text.format(reason=copy_problem_text(error)), error=True)
        self._update_buttons()

    def _on_copy_saved(self, result: object) -> None:
        assert isinstance(result, BackupCopyResult)
        self._set_status(strings.BK_COPY_SAVED.format(path=result.backup))

    def _on_external_added(self, result: object) -> None:
        assert isinstance(result, BackupCopyResult)
        self.refresh()
        self.select_backup(result.backup.name)
        template = strings.BK_EXTERNAL_PRESENT if result.already_present else strings.BK_EXTERNAL_ADDED
        self._set_status(template.format(name=result.backup.name))

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
        for job in (self._create_job, self._verify_job, self._file_job):
            if job is not None:
                job.stop_and_wait()
        self._create_job = None
        self._verify_job = None
        self._file_job = None

    def closeEvent(self, event) -> None:  # noqa: ANN001, N802
        self.shutdown()
        super().closeEvent(event)
