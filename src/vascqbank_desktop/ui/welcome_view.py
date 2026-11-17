"""Home page: welcome text, empty state and real local-storage facts."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.storage import StorageStatus
from vascqbank_desktop.ui import strings


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


class WelcomeView(QWidget):
    """Home page: empty state or bank summary, storage facts, roadmap."""

    importRequested = Signal()

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
        self.bank_card = self._build_bank_card()
        page.addWidget(self.empty_card)
        page.addWidget(self.bank_card)
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

    def _build_bank_card(self) -> QFrame:
        card, layout = _card()
        layout.addWidget(_label(strings.BANK_TITLE, "CardTitle"))
        self.bank_total_label = _label("", "BankTotal")
        layout.addWidget(self.bank_total_label)
        self.bank_status_labels: dict[ReviewStatus, QLabel] = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(28)
        for column, status in enumerate(ReviewStatus):
            label = _label("", "FieldValue", wrap=False)
            self.bank_status_labels[status] = label
            grid.addWidget(label, 0, column)
        grid.setColumnStretch(len(ReviewStatus), 1)
        layout.addLayout(grid)
        layout.addWidget(_label(strings.BANK_NOTE, "CardBody"))
        self.bank_import_button = QPushButton(strings.IMPORT_BUTTON)
        self.bank_import_button.setObjectName("SecondaryButton")
        self.bank_import_button.clicked.connect(self.importRequested)
        row = QHBoxLayout()
        row.addWidget(self.bank_import_button)
        row.addStretch(1)
        layout.addLayout(row)
        return card

    def set_bank_counts(self, total: int, subjects_with_questions: int,
                        by_status: dict[ReviewStatus, int]) -> None:
        """Switch between the empty state and the bank summary."""
        has_questions = total > 0
        self.empty_card.setVisible(not has_questions)
        self.bank_card.setVisible(has_questions)
        self.bank_total_label.setText(
            strings.BANK_TOTAL.format(total=f"{total:,}".replace(",", "\u00a0"),
                                      subjects=subjects_with_questions)
        )
        for status, label in self.bank_status_labels.items():
            label.setText(strings.BANK_STATUS_ROW.format(label=status.label_ka,
                                                         count=by_status.get(status, 0)))

    def _build_storage_card(self, status: StorageStatus) -> QFrame:
        card, layout = _card()
        layout.addWidget(_label(strings.STORAGE_TITLE, "CardTitle"))

        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)

        opened = strings.STORAGE_CREATED_NOW if status.created_new else strings.STORAGE_OPENED
        integrity_text = (
            strings.STORAGE_INTEGRITY_OK if status.integrity_ok else strings.STORAGE_INTEGRITY_FAILED
        )
        rows: list[tuple[str, QLabel]] = [
            (
                strings.STORAGE_LOCATION,
                _label(str(status.database_path), "FieldValue", selectable=True),
            ),
            (
                strings.STORAGE_SCHEMA,
                _label(f"v{status.schema_version} · {opened}", "FieldValue", wrap=False),
            ),
            (
                strings.STORAGE_INTEGRITY,
                _label(
                    integrity_text,
                    "StatusOk" if status.integrity_ok else "StatusBad",
                    wrap=False,
                ),
            ),
            (
                strings.STORAGE_DATABASE_ID,
                _label(status.database_id, "FieldValue", wrap=False, selectable=True),
            ),
        ]
        for row, (name, value) in enumerate(rows):
            grid.addWidget(
                _label(name, "FieldName", wrap=False), row, 0, Qt.AlignmentFlag.AlignTop
            )
            grid.addWidget(value, row, 1)
        self.storage_path_label = rows[0][1]
        layout.addLayout(grid)
        return card

    def _build_roadmap(self) -> QFrame:
        card, layout = _card()
        layout.addWidget(_label(strings.ROADMAP_TITLE, "CardTitle"))
        for item in strings.ROADMAP_ITEMS:
            layout.addWidget(_label(f"•  {item}", "RoadmapItem"))
        return card
