"""Export page: choose subjects, formats and Word editions, preview, then export in the background.

The page never touches the database or the files itself. The preview is an
:meth:`~vascqbank_desktop.services.export_service.ExportService.plan` computed on a worker thread
whenever a choice changes; the export is :meth:`ExportService.run` on another worker with live
progress and a cancel button. Success is shown only after the service returns — that is, after
every file was verified and the export folder was published and recorded.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.editing import EditField
from vascqbank_desktop.domain.subjects import Subject
from vascqbank_desktop.importexport.docx_export import FONT_CHOICES, DocxEdition
from vascqbank_desktop.services.export_service import (
    ExportBlocked,
    ExportCancelled,
    ExportEmpty,
    ExportFormat,
    ExportPhase,
    ExportPlan,
    ExportRequest,
    ExportResult,
    ExportService,
    ExportStorageError,
    ExportVerificationFailed,
)
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.formatting import format_count
from vascqbank_desktop.ui.question_editor_view import storage_text
from vascqbank_desktop.ui.widgets import make_label, repolish
from vascqbank_desktop.ui.workers import BackgroundJob, LatestOnlyRunner

log = logging.getLogger(__name__)

PREVIEW_DELAY_MS = 150
_EDITION_LABELS = {
    DocxEdition.REVIEW: strings.EX_REVIEW.split(" — ")[0],
    DocxEdition.EXAM: strings.EX_EXAM.split(" — ")[0],
    DocxEdition.ANSWER_KEY: strings.EX_KEY.split(" — ")[0],
}


def field_label(field: str) -> str:
    if field == EditField.STEM.value:
        return strings.ED_FIELD_STEM
    if field == EditField.EXPLANATION.value:
        return strings.ED_FIELD_EXPLANATION
    letter = EditField(field).option_letter if field.startswith("option_") else field
    return strings.ED_FIELD_OPTION.format(letter=letter)


def describe_error(error: object) -> str:
    """One Georgian sentence for a failed export (details in English stay in the log)."""
    if isinstance(error, ExportCancelled):
        return strings.EX_CANCELLED
    if isinstance(error, ExportEmpty):
        return strings.EX_FAILED.format(detail=strings.EX_EMPTY)
    if isinstance(error, ExportBlocked):
        return strings.EX_FAILED.format(detail=blocked_text(error.txt_issues, error.violations))
    if isinstance(error, ExportVerificationFailed):
        return strings.EX_FAILED.format(
            detail=strings.EX_ERR_VERIFY.format(detail="; ".join(error.problems[:3]))
        )
    if isinstance(error, ExportStorageError):
        return strings.EX_FAILED.format(
            detail=strings.EX_ERR_STORAGE.format(detail=storage_text(error.failure))
        )
    return strings.EX_FAILED.format(detail=strings.EX_ERR_OTHER.format(detail=error))


def blocked_text(txt_issues, violations) -> str:  # noqa: ANN001
    lines: list[str] = []
    if violations:
        lines.append(strings.EX_BLOCKED_CONTRACT)
        lines += [f"• {v.uid}: {v.problem}" for v in violations[:10]]
    if txt_issues:
        lines.append(strings.EX_BLOCKED_TXT)
        lines += [
            f"• {i.uid} — {field_label(i.field)}: {strings.EX_TXT_PROBLEM[i.problem.value]}"
            for i in txt_issues[:15]
        ]
        if len(txt_issues) > 15:
            lines.append(f"… (+{len(txt_issues) - 15})")
    return "\n".join(lines)


class ExportView(QWidget):
    exportFinished = Signal(object)  # ExportResult
    runningChanged = Signal(bool)

    def __init__(
        self,
        service: ExportService,
        subjects: Sequence[Subject],
        parent: QWidget | None = None,
        *,
        preview_delay_ms: int = PREVIEW_DELAY_MS,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ContentArea")
        self._service = service
        self._subjects = list(subjects)
        self._plan: ExportPlan | None = None
        self._generation = 0
        self._runner = LatestOnlyRunner(self)
        self._job: BackgroundJob | None = None
        self._cancel: threading.Event | None = None
        self._last_result: ExportResult | None = None
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(preview_delay_ms)
        self._preview_timer.timeout.connect(self._compute_preview)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        body.setObjectName("ContentArea")
        scroll.setWidget(body)
        outer.addWidget(scroll)
        page = QVBoxLayout(body)
        page.setContentsMargins(40, 30, 40, 24)
        page.setSpacing(12)
        page.addWidget(make_label(strings.EX_TITLE, "PageHeading"))
        page.addWidget(make_label(strings.EX_INTRO, "PageTagline"))

        # Two columns so that the choices, the preview and the export button fit on one screen.
        columns = QHBoxLayout()
        columns.setSpacing(16)
        left, right = QVBoxLayout(), QVBoxLayout()
        left.setSpacing(12)
        right.setSpacing(12)
        left.addWidget(self._build_subjects())
        left.addWidget(self._build_preview(), 1)
        right.addWidget(self._build_options())
        right.addWidget(self._build_run_row())
        right.addStretch(1)
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        page.addLayout(columns, 1)
        self.request_preview()

    # ------------------------------------------------------------------ construction
    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Card")
        column = QVBoxLayout(frame)
        column.setContentsMargins(18, 14, 18, 14)
        column.setSpacing(8)
        column.addWidget(make_label(title, "SectionTitle", wrap=False))
        return frame, column

    def _build_subjects(self) -> QFrame:
        frame, column = self._card(strings.EX_SUBJECTS)
        self.subject_list = QListWidget()
        self.subject_list.setObjectName("ExportSubjects")
        self.subject_list.setMinimumHeight(240)
        self.subject_list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.subject_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        for subject in self._subjects:
            item = QListWidgetItem(f"{subject.code} — {subject.name_ka}")
            item.setData(Qt.ItemDataRole.UserRole, subject.code)
            item.setToolTip(f"{subject.code} — {subject.name_ka}")
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            self.subject_list.addItem(item)
        self.subject_list.itemChanged.connect(lambda _item: self.request_preview())
        column.addWidget(self.subject_list, 1)
        row = QHBoxLayout()
        self.all_button = QPushButton(strings.EX_ALL)
        self.all_button.setObjectName("SecondaryButton")
        self.all_button.clicked.connect(lambda: self.set_all_subjects(True))
        self.none_button = QPushButton(strings.EX_NONE)
        self.none_button.setObjectName("SecondaryButton")
        self.none_button.clicked.connect(lambda: self.set_all_subjects(False))
        row.addWidget(self.all_button)
        row.addWidget(self.none_button)
        row.addStretch(1)
        column.addLayout(row)
        return frame

    def _check(self, text: str, checked: bool) -> QCheckBox:
        box = QCheckBox(text)
        box.setChecked(checked)
        box.toggled.connect(lambda _on: self.request_preview())
        return box

    def _build_options(self) -> QFrame:
        frame, column = self._card(strings.EX_FORMATS)
        self.docx_check = self._check(strings.EX_DOCX, True)
        self.txt_check = self._check(strings.EX_TXT, False)
        column.addWidget(self.docx_check)
        column.addWidget(self.txt_check)
        column.addWidget(make_label(strings.EX_EDITIONS, "SectionTitle", wrap=False))
        self.review_check = self._check(strings.EX_REVIEW, True)
        self.exam_check = self._check(strings.EX_EXAM, False)
        self.key_check = self._check(strings.EX_KEY, False)
        for box in (self.review_check, self.exam_check, self.key_check):
            column.addWidget(box)
        column.addWidget(make_label(strings.EX_LAYOUT, "SectionTitle", wrap=False))
        self.per_subject_check = self._check(strings.EX_PER_SUBJECT, True)
        self.combined_check = self._check(strings.EX_COMBINED, False)
        column.addWidget(self.per_subject_check)
        column.addWidget(self.combined_check)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.addWidget(make_label(strings.EX_FONT, "FieldName", wrap=False), 0, 0)
        self.font_combo = QComboBox()
        self.font_combo.addItems(list(FONT_CHOICES))
        self.font_combo.currentIndexChanged.connect(lambda _i: self.request_preview())
        grid.addWidget(self.font_combo, 0, 1, 1, 2)
        grid.addWidget(make_label(strings.EX_DESTINATION, "FieldName", wrap=False), 1, 0)
        self.destination_edit = QLineEdit(str(self._service.default_destination))
        self.destination_edit.textChanged.connect(lambda _t: self.request_preview())
        grid.addWidget(self.destination_edit, 1, 1)
        self.choose_button = QPushButton(strings.EX_CHOOSE)
        self.choose_button.setObjectName("SecondaryButton")
        self.choose_button.clicked.connect(self.choose_destination)
        grid.addWidget(self.choose_button, 1, 2)
        grid.setColumnStretch(1, 1)
        column.addLayout(grid)
        column.addStretch(1)
        return frame

    def _build_preview(self) -> QFrame:
        frame, column = self._card(strings.EX_PREVIEW)
        self.summary_label = make_label(strings.EX_PREVIEW_LOADING, "CardBody", selectable=True)
        self.summary_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        column.addWidget(self.summary_label)
        self.files_view = QPlainTextEdit()
        self.files_view.setReadOnly(True)
        self.files_view.setMinimumHeight(90)
        self.files_view.setMaximumHeight(150)
        column.addWidget(self.files_view)
        self.problem_label = make_label("", "ErrorNotice", selectable=True)
        self.problem_label.hide()
        column.addWidget(self.problem_label)
        column.addStretch(1)
        return frame

    def _build_run_row(self) -> QFrame:
        frame, column = self._card(strings.EX_START)
        row = QHBoxLayout()
        self.export_button = QPushButton(strings.EX_START)
        self.export_button.setObjectName("PrimaryButton")
        self.export_button.clicked.connect(self.start_export)
        self.cancel_button = QPushButton(strings.EX_CANCEL)
        self.cancel_button.setObjectName("DangerButton")
        self.cancel_button.clicked.connect(self.cancel_export)
        self.cancel_button.setEnabled(False)
        self.open_button = QPushButton(strings.EX_OPEN_FOLDER)
        self.open_button.setObjectName("SecondaryButton")
        self.open_button.clicked.connect(self.open_result_folder)
        self.open_button.setEnabled(False)
        for button in (self.export_button, self.cancel_button, self.open_button):
            row.addWidget(button)
        row.addStretch(1)
        column.addLayout(row)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        column.addWidget(self.progress_bar)
        self.phase_label = make_label("", "ResultInfo")
        column.addWidget(self.phase_label)
        self.result_label = make_label("", "InfoNotice", selectable=True)
        self.result_label.hide()
        column.addWidget(self.result_label)
        return frame

    # ------------------------------------------------------------------ choices
    def selected_subjects(self) -> tuple[str, ...]:
        return tuple(
            self.subject_list.item(row).data(Qt.ItemDataRole.UserRole)
            for row in range(self.subject_list.count())
            if self.subject_list.item(row).checkState() == Qt.CheckState.Checked
        )

    def set_all_subjects(self, checked: bool) -> None:
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        self.subject_list.blockSignals(True)
        for row in range(self.subject_list.count()):
            self.subject_list.item(row).setCheckState(state)
        self.subject_list.blockSignals(False)
        self.request_preview()

    def select_subjects(self, codes: Sequence[str]) -> None:
        wanted = set(codes)
        self.subject_list.blockSignals(True)
        for row in range(self.subject_list.count()):
            item = self.subject_list.item(row)
            checked = item.data(Qt.ItemDataRole.UserRole) in wanted
            item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.subject_list.blockSignals(False)
        self.request_preview()

    def current_request(self) -> ExportRequest:
        formats = set()
        if self.docx_check.isChecked():
            formats.add(ExportFormat.DOCX)
        if self.txt_check.isChecked():
            formats.add(ExportFormat.TXT)
        editions = tuple(
            edition
            for edition, box in (
                (DocxEdition.REVIEW, self.review_check),
                (DocxEdition.EXAM, self.exam_check),
                (DocxEdition.ANSWER_KEY, self.key_check),
            )
            if box.isChecked()
        )
        return ExportRequest(
            subjects=self.selected_subjects(),
            formats=frozenset(formats),
            destination=Path(self.destination_edit.text().strip() or str(self._service.default_destination)),
            editions=editions,
            per_subject_docx=self.per_subject_check.isChecked(),
            combined_docx=self.combined_check.isChecked(),
            font=self.font_combo.currentText(),
        )

    def choose_destination(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, strings.EX_CHOOSE_TITLE, self.destination_edit.text())
        if folder:
            self.destination_edit.setText(folder)

    def _selection_incomplete(self, request: ExportRequest) -> bool:
        docx = ExportFormat.DOCX in request.formats
        return (
            not request.subjects
            or not request.formats
            or (docx and not request.editions)
            or (docx and not (request.per_subject_docx or request.combined_docx))
        )

    # ------------------------------------------------------------------ preview
    @property
    def plan(self) -> ExportPlan | None:
        return self._plan

    def is_previewing(self) -> bool:
        return self._preview_timer.isActive() or self._runner.is_busy()

    def request_preview(self) -> None:
        """Recompute the preview shortly (several quick clicks make one computation)."""
        self._generation += 1
        self._plan = None
        self._update_buttons()
        self.summary_label.setText(strings.EX_PREVIEW_LOADING)
        self._preview_timer.start()

    def _compute_preview(self) -> None:
        request = self.current_request()
        generation = self._generation
        if self._selection_incomplete(request):
            self._show_plan(None, request)
            return
        self._runner.submit(
            lambda: self._service.plan(request),
            lambda plan: self._on_plan(generation, plan),
            lambda error: self._on_plan_failed(generation, error),
        )

    def _on_plan(self, generation: int, plan: object) -> None:
        if generation != self._generation or not isinstance(plan, ExportPlan):
            return  # a newer choice is being computed
        self._plan = plan
        self._show_plan(plan, plan.request)

    def _on_plan_failed(self, generation: int, error: object) -> None:
        if generation == self._generation:
            self.summary_label.setText(describe_error(error))

    def _show_plan(self, plan: ExportPlan | None, request: ExportRequest) -> None:
        self.problem_label.hide()
        if plan is None:
            self.summary_label.setText(strings.EX_NOTHING_SELECTED)
            self.files_view.setPlainText("")
            self._update_buttons()
            return
        modes = (
            ", ".join(_EDITION_LABELS[e] for e in request.editions)
            if ExportFormat.DOCX in request.formats
            else "—"
        )
        names = ", ".join(s.code for s in plan.subjects)
        lines = [
            strings.EX_P_SUBJECTS.format(subjects=names),
            strings.EX_P_APPROVED.format(count=format_count(plan.approved)),
            strings.EX_P_EXCLUDED.format(count=format_count(plan.excluded)),
            strings.EX_P_FORMATS.format(formats=", ".join(sorted(f.value for f in request.formats))),
            strings.EX_P_MODE.format(modes=modes),
            strings.EX_P_DESTINATION.format(folder=request.destination),
            strings.EX_P_FOLDER,
        ]
        self.summary_label.setText("\n".join(lines))
        self.files_view.setPlainText("\n".join(plan.files))
        if plan.is_empty:
            self.problem_label.setText(strings.EX_EMPTY)
            self.problem_label.show()
        elif plan.blocked:
            self.problem_label.setText(blocked_text(plan.txt_issues, plan.violations))
            self.problem_label.show()
        self._update_buttons()

    # ------------------------------------------------------------------ running
    def is_running(self) -> bool:
        return self._job is not None

    def _update_buttons(self) -> None:
        running = self.is_running()
        plan = self._plan
        ready = plan is not None and not plan.is_empty and not plan.blocked
        self.export_button.setEnabled(ready and not running)
        self.cancel_button.setEnabled(running)
        for widget in (
            self.subject_list, self.all_button, self.none_button, self.docx_check, self.txt_check,
            self.review_check, self.exam_check, self.key_check, self.per_subject_check,
            self.combined_check, self.font_combo, self.destination_edit, self.choose_button,
        ):  # fmt: skip
            widget.setEnabled(not running)

    def start_export(self) -> bool:
        """Run the export in the background; ``False`` if it cannot start."""
        plan = self._plan
        if self.is_running() or plan is None or plan.is_empty or plan.blocked:
            return False
        request = self.current_request()
        cancel = threading.Event()
        service = self._service

        def work(report):  # noqa: ANN001, ANN202
            return service.run(
                request,
                progress=lambda phase, done, total, detail: report((phase, done, total, detail)),
                cancel=cancel,
            )

        job = BackgroundJob(work, self, report_progress=True)
        job.progress.connect(self._on_progress)
        job.succeeded.connect(self._on_done)
        job.failed.connect(self._on_failed)
        self._job, self._cancel = job, cancel
        self._last_result = None
        self.open_button.setEnabled(False)
        self.result_label.hide()
        self.progress_bar.setValue(0)
        self.phase_label.setText(strings.EX_PHASES["SNAPSHOT"])
        self._update_buttons()
        self.runningChanged.emit(True)
        job.start()
        return True

    def cancel_export(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
            self.cancel_button.setEnabled(False)

    def _on_progress(self, item: object) -> None:
        phase, done, total, _detail = item  # type: ignore[misc]
        phase = ExportPhase(phase)
        label = strings.EX_PHASES[phase.value]
        if total:
            self.progress_bar.setValue(int(100 * done / total))
        self.phase_label.setText(
            strings.EX_PROGRESS.format(phase=label, done=format_count(done), total=format_count(total))
            if total > 1
            else label
        )

    def _finish(self) -> None:
        job = self._job
        self._job = None
        self._cancel = None
        if job is not None:
            job.deleteLater()
        self._update_buttons()
        self.runningChanged.emit(False)

    def _on_done(self, result: object) -> None:
        assert isinstance(result, ExportResult)
        self._last_result = result
        self._finish()
        self.progress_bar.setValue(100)
        self.phase_label.setText(strings.EX_PHASES["DONE"])
        self._show_result(
            strings.EX_DONE.format(
                count=format_count(result.approved), files=len(result.files), folder=result.folder
            ),
            error=False,
        )
        self.open_button.setEnabled(True)
        self.exportFinished.emit(result)
        self.request_preview()

    def _on_failed(self, error: object) -> None:
        self._finish()
        if not isinstance(error, ExportCancelled):
            log.error("Export failed: %s", error)
        self.progress_bar.setValue(0)
        self.phase_label.setText("")
        self._show_result(describe_error(error), error=not isinstance(error, ExportCancelled))
        self.request_preview()

    def _show_result(self, text: str, *, error: bool) -> None:
        self.result_label.setObjectName("ErrorNotice" if error else "SuccessNotice")
        repolish(self.result_label)
        self.result_label.setText(text)
        self.result_label.show()

    @property
    def last_result(self) -> ExportResult | None:
        return self._last_result

    def open_result_folder(self) -> None:
        if self._last_result is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._last_result.folder)))

    # ------------------------------------------------------------------ closing
    def ask_stop_on_close(self) -> bool:
        box = QMessageBox(
            QMessageBox.Icon.Warning, strings.EX_CLOSE_TITLE, strings.EX_CLOSE_TEXT, parent=self
        )
        stop = box.addButton(strings.EX_CLOSE_STOP, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(strings.EX_CLOSE_WAIT, QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is stop

    def prepare_close(self) -> bool:
        """``False`` keeps the window open (an export runs and the reviewer wants it to finish)."""
        if not self.is_running():
            return True
        if not self.ask_stop_on_close():
            return False
        self.shutdown()
        return True

    def wait_idle(self) -> None:
        """Let a running preview computation finish (before the database is replaced)."""
        self._preview_timer.stop()
        self._runner.wait_idle()

    def shutdown(self, timeout_ms: int = 60_000) -> None:
        """Cancel a running export and wait until its worker has cleaned up."""
        self._preview_timer.stop()
        self._runner.shutdown()
        job = self._job
        if job is not None:
            self.cancel_export()
            if job.wait(timeout_ms):  # the worker removed its staging folder before returning
                self._finish()
