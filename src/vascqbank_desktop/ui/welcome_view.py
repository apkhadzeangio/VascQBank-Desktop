"""Home page: welcome text, empty state and real local-storage facts."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import SUBJECTS
from vascqbank_desktop.services.question_stats import BankCounts, ProgressSummary
from vascqbank_desktop.services.storage import StorageStatus
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.formatting import format_timestamp
from vascqbank_desktop.ui.progress_panel import ProgressPanel
from vascqbank_desktop.ui.widgets import repolish


def _card(object_name: str = "Card") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(object_name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 18, 22, 18)
    layout.setSpacing(8)
    return frame, layout


def _label(text: str, object_name: str, *, wrap: bool = True, selectable: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(wrap)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def seed_text(status: StorageStatus) -> str:
    """Where this database's questions came from (bundled seed or manual import)."""
    if not status.seed_version:
        return strings.STORAGE_SEED_NONE
    kind = strings.SEED_KIND_DEVELOPMENT if status.from_development_seed else strings.SEED_KIND_PRODUCTION
    return strings.STORAGE_SEED_VALUE.format(
        version=status.seed_version, kind=kind, time=format_timestamp(status.seed_installed_at)
    )


class WelcomeView(QWidget):
    """Home page: empty state or bank summary, storage facts, roadmap."""

    importRequested = Signal()
    resumeRequested = Signal()
    draftsRequested = Signal()

    def __init__(self, status: StorageStatus, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContentArea")

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("ContentArea")
        scroll.setWidget(inner)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        page = QVBoxLayout(inner)
        page.setContentsMargins(40, 34, 40, 34)
        page.setSpacing(18)

        self.heading = _label(strings.WELCOME_HEADING, "PageHeading")
        page.addWidget(self.heading)
        page.addWidget(_label(strings.WELCOME_TAGLINE, "PageTagline"))
        page.addSpacing(6)

        self.empty_card = self._build_empty_state()
        self.drafts_card = self._build_drafts_card()
        self.resume_card = self._build_resume_card()
        self.bank_card = self._build_bank_card()
        page.addWidget(self.empty_card)
        page.addWidget(self.drafts_card)
        page.addWidget(self.resume_card)
        page.addWidget(self.bank_card)
        self.subjects_card = self._build_subjects_card()
        page.addWidget(self.subjects_card)
        page.addWidget(self._build_storage_card(status))
        page.addWidget(self._build_roadmap())
        page.addStretch(1)

    def _build_empty_state(self) -> QFrame:
        card, layout = _card("EmptyStateCard")
        self.empty_title = _label(strings.EMPTY_TITLE, "CardTitle")
        layout.addWidget(self.empty_title)
        layout.addWidget(_label(strings.EMPTY_BODY, "CardBody"))
        self.import_button = QPushButton(strings.IMPORT_BUTTON)
        self.import_button.setObjectName("PrimaryButton")
        self.import_button.clicked.connect(self.importRequested)
        row = QHBoxLayout()
        row.addWidget(self.import_button)
        row.addStretch(1)
        layout.addSpacing(4)
        layout.addLayout(row)
        return card

    def _build_drafts_card(self) -> QFrame:
        card, layout = _card("EmptyStateCard")
        layout.addWidget(_label(strings.DR_LIST_TITLE, "CardTitle"))
        self.drafts_label = _label("", "CardBody")
        layout.addWidget(self.drafts_label)
        self.drafts_button = QPushButton(strings.DR_BANNER_BUTTON)
        self.drafts_button.setObjectName("PrimaryButton")
        self.drafts_button.clicked.connect(self.draftsRequested)
        row = QHBoxLayout()
        row.addWidget(self.drafts_button)
        row.addStretch(1)
        layout.addLayout(row)
        card.hide()
        return card

    def set_drafts(self, count: int) -> None:
        """Remind the reviewer of unfinished drafts (e.g. after the program stopped unexpectedly)."""
        self.drafts_card.setVisible(count > 0)
        self.drafts_label.setText(strings.DR_BANNER.format(count=count))

    def _build_resume_card(self) -> QFrame:
        card, layout = _card("EmptyStateCard")
        layout.addWidget(_label(strings.HOME_RESUME_TITLE, "CardTitle"))
        self.resume_label = _label("", "CardBody")
        layout.addWidget(self.resume_label)
        self.resume_button = QPushButton(strings.HOME_RESUME_BUTTON)
        self.resume_button.setObjectName("PrimaryButton")
        self.resume_button.clicked.connect(self.resumeRequested)
        row = QHBoxLayout()
        row.addWidget(self.resume_button)
        row.addStretch(1)
        layout.addLayout(row)
        card.hide()
        return card

    def set_resume(self, uid: str | None) -> None:
        """Offer to continue at the question the reviewer last had open."""
        self.resume_card.setVisible(bool(uid))
        if uid:
            self.resume_label.setText(strings.HOME_RESUME_BODY.format(uid=uid))

    def _build_bank_card(self) -> QFrame:
        card, layout = _card()
        layout.addWidget(_label(strings.BANK_TITLE, "CardTitle"))
        self.bank_total_label = _label("", "BankTotal")
        layout.addWidget(self.bank_total_label)
        self.progress_panel = ProgressPanel(framed=False)
        layout.addWidget(self.progress_panel)
        layout.addWidget(_label(strings.BANK_NOTE, "CardBody"))
        self.bank_import_button = QPushButton(strings.IMPORT_BUTTON)
        self.bank_import_button.setObjectName("SecondaryButton")
        self.bank_import_button.clicked.connect(self.importRequested)
        row = QHBoxLayout()
        row.addWidget(self.bank_import_button)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def set_bank_counts(self, total: int, subjects_with_questions: int) -> None:
        """Switch between the empty state and the bank summary."""
        has_questions = total > 0
        self.empty_card.setVisible(not has_questions)
        self.bank_card.setVisible(has_questions)
        self.bank_total_label.setText(
            strings.BANK_TOTAL.format(total=f"{total:,}".replace(",", "\u00a0"),
                                      subjects=subjects_with_questions)
        )

    def _build_subjects_card(self) -> QFrame:
        card, layout = _card()
        layout.addWidget(_label(strings.HOME_SUBJECTS_TITLE, "CardTitle"))
        headers = [
            strings.HOME_COL_SUBJECT,
            strings.HOME_COL_IMPORTED,
            *(status.label_ka for status in ReviewStatus),
            strings.HOME_COL_DRAFTS,
            strings.HOME_COL_TARGET,
        ]
        self.subjects_table = QTableWidget(len(SUBJECTS), len(headers))
        self.subjects_table.setAccessibleName(strings.HOME_SUBJECTS_TITLE)
        self.subjects_table.setHorizontalHeaderLabels(headers)
        self.subjects_table.verticalHeader().setVisible(False)
        self.subjects_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.subjects_table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.subjects_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.subjects_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.subjects_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(headers)):
            self.subjects_table.horizontalHeader().setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        for row, subject in enumerate(SUBJECTS):
            item = QTableWidgetItem(subject.name_ka)
            item.setToolTip(f"{subject.code} — {subject.name_ka}")
            self.subjects_table.setItem(row, 0, item)
        self.subjects_table.resizeRowsToContents()
        layout.addWidget(self.subjects_table)
        layout.addWidget(_label(strings.HOME_SUBJECTS_NOTE, "ScopeNote"))
        card.setVisible(False)
        return card

    def set_subject_counts(self, bank: BankCounts) -> None:
        """Per-subject numbers, all read from the database; the target is shown only as a target."""
        table = self.subjects_table
        for row, subject in enumerate(SUBJECTS):
            counts = bank.subjects.get(subject.code)
            values = [
                counts.total if counts else 0,
                *((counts.count(status) if counts else 0) for status in ReviewStatus),
                counts.drafts if counts else 0,
                subject.target_count,
            ]
            for column, value in enumerate(values, start=1):
                item = QTableWidgetItem(f"{value:,}".replace(",", "\u00a0"))
                item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                table.setItem(row, column, item)
        table.resizeRowsToContents()
        height = table.horizontalHeader().height() + sum(table.rowHeight(r) for r in range(table.rowCount()))
        table.setFixedHeight(height + 2 * table.frameWidth())
        self.subjects_card.setVisible(bank.total > 0)

    def set_progress(
        self, bank: ProgressSummary, subject: ProgressSummary | None, subject_name: str | None
    ) -> None:
        self.progress_panel.set_progress(bank, subject, subject_name)

    def _build_storage_card(self, status: StorageStatus) -> QFrame:
        card, layout = _card()
        layout.addWidget(_label(strings.STORAGE_TITLE, "CardTitle"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self._storage_values: dict[str, QLabel] = {
            "location": _label("", "FieldValue", selectable=True),
            "schema": _label("", "FieldValue", wrap=False),
            "integrity": _label("", "StatusOk", wrap=False),
            "database_id": _label("", "FieldValue", wrap=False, selectable=True),
            "seed": _label("", "FieldValue", selectable=True),
            "restored": _label("", "FieldValue", selectable=True),
        }
        names = {
            "location": strings.STORAGE_LOCATION,
            "schema": strings.STORAGE_SCHEMA,
            "integrity": strings.STORAGE_INTEGRITY,
            "database_id": strings.STORAGE_DATABASE_ID,
            "seed": strings.STORAGE_SEED,
            "restored": strings.STORAGE_RESTORED,
        }
        self._storage_names: dict[str, QLabel] = {}
        for row, (key, value) in enumerate(self._storage_values.items()):
            name = _label(names[key], "FieldName", wrap=False)
            self._storage_names[key] = name
            grid.addWidget(name, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
        self.storage_path_label = self._storage_values["location"]
        layout.addLayout(grid)
        self.set_status(status)
        return card

    def set_status(self, status: StorageStatus) -> None:
        """Show the facts of the working database (again after a restore)."""
        opened = strings.STORAGE_CREATED_NOW if status.created_new else strings.STORAGE_OPENED
        values = self._storage_values
        values["location"].setText(str(status.database_path))
        values["schema"].setText(f"v{status.schema_version} · {opened}")
        values["integrity"].setText(
            strings.STORAGE_INTEGRITY_OK if status.integrity_ok else strings.STORAGE_INTEGRITY_FAILED
        )
        values["integrity"].setObjectName("StatusOk" if status.integrity_ok else "StatusBad")
        repolish(values["integrity"])
        values["database_id"].setText(status.database_id)
        values["seed"].setText(seed_text(status))
        restored = bool(status.restored_from)
        values["restored"].setText(
            strings.STORAGE_RESTORED_VALUE.format(
                name=status.restored_from, time=format_timestamp(status.restored_at)
            )
            if restored
            else ""
        )
        values["restored"].setVisible(restored)
        self._storage_names["restored"].setVisible(restored)

    def _build_roadmap(self) -> QFrame:
        card, layout = _card()
        layout.addWidget(_label(strings.ROADMAP_TITLE, "CardTitle"))
        for item in strings.ROADMAP_ITEMS:
            layout.addWidget(_label(f"•  {item}", "RoadmapItem"))
        return card
