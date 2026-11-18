"""Read-only comparison of a newer question source with the working database (File menu).

Nothing here writes: the dialog only shows what :mod:`vascqbank_desktop.services.seed_compare`
found. There is no merge button by design.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.services import seed_compare
from vascqbank_desktop.services.seed_compare import ComparisonReport, Difference
from vascqbank_desktop.services.seed_service import MANIFEST_NAME
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.formatting import format_count
from vascqbank_desktop.ui.restore_dialog import problem_text
from vascqbank_desktop.ui.widgets import make_label
from vascqbank_desktop.ui.workers import BackgroundJob


def compare_folder(paths: AppPaths, folder: Path) -> ComparisonReport:
    """A seed folder (with its manifest) or a folder of TXT source files."""
    if (folder / MANIFEST_NAME).is_file():
        return seed_compare.compare_seed(paths, folder)
    return seed_compare.compare_sources(paths, [folder])


def report_text(report: ComparisonReport) -> tuple[str, str]:
    """(summary, details) in Georgian."""
    lines = [strings.CMP_SOURCE.format(label=report.source_label)]
    for kind in Difference:
        lines.append(f"• {strings.CMP_KINDS[kind.value]}: {format_count(report.counts[kind])}")
    reviewed = sum(1 for c in report.conflicts if c.reviewed)
    if reviewed:
        lines.append(strings.CMP_REVIEWED.format(count=format_count(reviewed)))
    if report.invalid or report.duplicates:
        lines.append(strings.CMP_INVALID.format(invalid=report.invalid, duplicates=report.duplicates))
    details: list[str] = []
    for kind in Difference:
        ids = report.ids(kind)
        if ids:
            details.append(f"{strings.CMP_KINDS[kind.value]} ({len(ids)}):")
            details.append(", ".join(ids))
            details.append("")
    return "\n".join(lines), "\n".join(details)


class CompareDialog(QDialog):
    def __init__(self, paths: AppPaths, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.CMP_TITLE)
        self.setMinimumSize(680, 520)
        self._paths = paths
        self._job: BackgroundJob | None = None
        self.report: ComparisonReport | None = None
        column = QVBoxLayout(self)
        column.setContentsMargins(20, 16, 20, 14)
        column.addWidget(make_label(strings.CMP_INTRO, "CardBody"))
        row = QHBoxLayout()
        self.choose_button = QPushButton(strings.CMP_CHOOSE)
        self.choose_button.setObjectName("SecondaryButton")
        self.choose_button.clicked.connect(self.choose_folder)
        row.addWidget(self.choose_button)
        row.addStretch(1)
        column.addLayout(row)
        self.summary_label = make_label("", "CardBody", selectable=True)
        column.addWidget(self.summary_label)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setAccessibleName(strings.CMP_TITLE)
        column.addWidget(self.details, 1)
        column.addWidget(make_label(strings.CMP_NOTE, "ScopeNote"))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(strings.RS_CLOSE)
        buttons.rejected.connect(self.reject)
        column.addWidget(buttons)

    def choose_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, strings.CMP_CHOOSE)
        if folder:
            self.compare(Path(folder))

    def compare(self, folder: Path) -> None:
        if self._job is not None:
            return
        self.summary_label.setText(strings.CMP_RUNNING)
        self.details.clear()
        self.choose_button.setEnabled(False)
        job = BackgroundJob(lambda: compare_folder(self._paths, folder), self)
        job.succeeded.connect(lambda report: self._done(job, report))
        job.failed.connect(lambda error: self._failed(job, error))
        self._job = job
        job.start()

    def is_busy(self) -> bool:
        return self._job is not None

    def _done(self, job: BackgroundJob, report: object) -> None:
        self._job = None
        job.deleteLater()
        self.choose_button.setEnabled(True)
        assert isinstance(report, ComparisonReport)
        self.report = report
        summary, details = report_text(report)
        self.summary_label.setText(summary)
        self.details.setPlainText(details)

    def _failed(self, job: BackgroundJob, error: object) -> None:
        self._job = None
        job.deleteLater()
        self.choose_button.setEnabled(True)
        self.summary_label.setText(strings.CMP_FAILED.format(detail=problem_text(error)))

    def reject(self) -> None:
        if self._job is not None:
            self._job.wait()
        super().reject()
