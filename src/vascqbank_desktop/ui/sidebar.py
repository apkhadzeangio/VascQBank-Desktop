"""Left sidebar: brand, main navigation and the 12-subject placeholder list."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.subjects import Subject
from vascqbank_desktop.ui import strings

SIDEBAR_WIDTH = 320

# Page keys used by the navigation list and the main window's page stack.
PAGE_HOME = "home"


def _section_label(text: str) -> QLabel:
    # No str.upper(): for Georgian it produces Mtavruli capitals (U+1C90…),
    # which many installed fonts do not cover.
    label = QLabel(text)
    label.setObjectName("SectionLabel")
    return label


def _divider() -> QFrame:
    line = QFrame()
    line.setObjectName("SidebarDivider")
    line.setFrameShape(QFrame.Shape.HLine)
    return line


class Sidebar(QWidget):
    """Navigation sidebar.

    Only implemented pages are selectable. Subjects are listed for orientation
    but stay disabled until question data exists (Phase 2), so nothing in the
    sidebar pretends to work when it does not.
    """

    pageRequested = Signal(str)

    def __init__(
        self,
        subjects: Sequence[Subject],
        app_icon: QIcon | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("Sidebar")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFixedWidth(SIDEBAR_WIDTH)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 18, 12, 14)
        layout.setSpacing(6)

        layout.addLayout(self._build_brand(app_icon))
        layout.addSpacing(10)
        layout.addWidget(_divider())

        layout.addWidget(_section_label(strings.NAV_SECTION))
        self.nav_list = self._build_nav_list()
        layout.addWidget(self.nav_list)

        layout.addWidget(_divider())
        layout.addWidget(_section_label(strings.SUBJECTS_SECTION))
        hint = QLabel(strings.SUBJECTS_HINT)
        hint.setObjectName("SidebarHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.subject_area = self._build_subject_placeholders(subjects)
        layout.addWidget(self.subject_area, 1)

        total = sum(s.target_count for s in subjects)
        totals = QLabel(
            strings.SUBJECTS_TOTAL.format(count=len(subjects), total=f"{total:,}".replace(",", " "))
        )
        totals.setObjectName("SidebarHint")
        layout.addWidget(totals)

    # -- construction helpers -------------------------------------------
    def _build_brand(self, app_icon: QIcon | None) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(10)
        if app_icon is not None and not app_icon.isNull():
            icon_label = QLabel()
            icon_label.setPixmap(app_icon.pixmap(QSize(40, 40)))
            row.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)
        text_col = QVBoxLayout()
        text_col.setSpacing(0)
        title = QLabel(strings.BRAND)
        title.setObjectName("BrandTitle")
        subtitle = QLabel(strings.BRAND_SUBTITLE)
        subtitle.setObjectName("BrandSubtitle")
        text_col.addWidget(title)
        text_col.addWidget(subtitle)
        row.addLayout(text_col, 1)
        return row

    def _build_nav_list(self) -> QListWidget:
        nav = QListWidget()
        nav.setObjectName("NavList")
        nav.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        nav.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        nav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        home = QListWidgetItem(strings.NAV_HOME)
        home.setData(Qt.ItemDataRole.UserRole, PAGE_HOME)
        nav.addItem(home)
        nav.setCurrentItem(home)
        nav.setFixedHeight(nav.sizeHintForRow(0) * nav.count() + 8)
        nav.currentItemChanged.connect(self._on_nav_changed)
        return nav

    def _build_subject_placeholders(self, subjects: Sequence[Subject]) -> QScrollArea:
        """Plain labels, not a list control: there is nothing to click yet.

        Phase 2 replaces this with a model-backed list that opens a subject.
        """
        container = QWidget()
        container.setObjectName("SubjectPlaceholders")
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)
        self.subject_labels: list[QLabel] = []
        for subject in subjects:
            label = QLabel(subject.name_ka)
            label.setObjectName("SubjectItem")
            label.setWordWrap(True)
            label.setProperty("subjectCode", subject.code)
            label.setToolTip(
                strings.SUBJECT_TOOLTIP.format(
                    name=subject.name_ka, code=subject.code, target=subject.target_count
                )
            )
            column.addWidget(label)
            self.subject_labels.append(label)
        column.addStretch(1)

        area = QScrollArea()
        area.setObjectName("SubjectScroll")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(container)
        return area

    # -- slots -----------------------------------------------------------
    def _on_nav_changed(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is not None:
            self.pageRequested.emit(str(current.data(Qt.ItemDataRole.UserRole)))
