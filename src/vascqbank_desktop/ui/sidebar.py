"""Left sidebar: brand, main navigation and the 12 subjects."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QKeyEvent, QMouseEvent
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
from vascqbank_desktop.ui.widgets import repolish

SIDEBAR_WIDTH = 320

# Page keys used by the navigation list and the main window's page stack.
PAGE_HOME = "home"
PAGE_CATALOG = "catalog"
PAGE_BACKUPS = "backups"
PAGE_REVIEWER = "reviewer"
PAGE_EXPORT = "export"
_PAGES: tuple[tuple[str, str], ...] = (
    (PAGE_HOME, strings.NAV_HOME),
    (PAGE_CATALOG, strings.NAV_CATALOG),
    (PAGE_BACKUPS, strings.NAV_BACKUPS),
    (PAGE_REVIEWER, strings.NAV_REVIEWER),
    (PAGE_EXPORT, strings.NAV_EXPORT),
)


def _fmt(n: int) -> str:
    return f"{n:,}".replace(",", "\u00a0")


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


class SubjectRow(QFrame):
    """A clickable, keyboard-operable subject entry: name above its "imported / target" count.

    Disabled while the subject has no questions, so nothing in the sidebar
    pretends to work when it does not.
    """

    clicked = Signal()

    def __init__(self, name: str, tooltip: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SubjectRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setToolTip(tooltip)
        self.setProperty("selected", False)
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 2, 0, 2)
        column.setSpacing(0)
        self.name_label = QLabel(name)
        self.name_label.setObjectName("SubjectItem")
        self.name_label.setWordWrap(True)
        self.name_label.setToolTip(tooltip)
        self.count_label = QLabel()
        self.count_label.setObjectName("SubjectCount")
        self.count_label.setToolTip(tooltip)
        column.addWidget(self.name_label)
        column.addWidget(self.count_label)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        repolish(self)

    @property
    def is_selected(self) -> bool:
        return bool(self.property("selected"))

    def setEnabled(self, enabled: bool) -> None:  # noqa: N802
        super().setEnabled(enabled)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus if enabled else Qt.FocusPolicy.NoFocus)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if self.isEnabled() and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class Sidebar(QWidget):
    """Navigation sidebar: pages and the subject list."""

    pageRequested = Signal(str)
    subjectRequested = Signal(object)  # subject code, or None for all subjects

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

        self._subjects = tuple(subjects)
        self.subject_area = self._build_subject_rows(subjects)
        layout.addWidget(self.subject_area, 1)

        self.totals_label = QLabel()
        self.totals_label.setObjectName("SidebarHint")
        self.totals_label.setWordWrap(True)
        layout.addWidget(self.totals_label)
        self._selected_subject: str | None = None
        self.set_counts({})
        self.set_selected_subject(None)

    # -- counts -----------------------------------------------------------
    def set_counts(self, imported: Mapping[str, int]) -> None:
        """Show how many questions of each subject are in the database."""
        for subject in self._subjects:
            n = imported.get(subject.code, 0)
            self.subject_count_labels[subject.code].setText(
                strings.SUBJECT_COUNT.format(imported=_fmt(n), target=_fmt(subject.target_count))
            )
            self.subject_rows[subject.code].setEnabled(n > 0)
        total_target = sum(s.target_count for s in self._subjects)
        total_imported = sum(imported.get(s.code, 0) for s in self._subjects)
        self.all_subjects_row.count_label.setText(
            strings.SUBJECT_COUNT.format(imported=_fmt(total_imported), target=_fmt(total_target))
        )
        self.all_subjects_row.setEnabled(total_imported > 0)
        self.totals_label.setText(
            strings.SUBJECTS_TOTAL.format(
                count=len(self._subjects),
                imported=_fmt(sum(imported.values())),
                total=_fmt(total_target),
            )
        )

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
        for key, title in _PAGES:
            item = QListWidgetItem(title)
            item.setData(Qt.ItemDataRole.UserRole, key)
            nav.addItem(item)
        nav.setCurrentRow(0)
        nav.setFixedHeight(nav.sizeHintForRow(0) * nav.count() + 8)
        nav.currentItemChanged.connect(self._on_nav_changed)
        return nav

    def _build_subject_rows(self, subjects: Sequence[Subject]) -> QScrollArea:
        container = QWidget()
        container.setObjectName("SubjectPlaceholders")
        column = QVBoxLayout(container)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(2)

        self.all_subjects_row = SubjectRow(strings.SUBJECT_ALL, strings.SUBJECT_ALL_TOOLTIP)
        self.all_subjects_row.clicked.connect(lambda: self.subjectRequested.emit(None))
        column.addWidget(self.all_subjects_row)

        self.subject_labels: list[QLabel] = []
        self.subject_count_labels: dict[str, QLabel] = {}
        self.subject_rows: dict[str, SubjectRow] = {}
        for subject in subjects:
            tooltip = strings.SUBJECT_TOOLTIP.format(
                name=subject.name_ka, code=subject.code, target=subject.target_count
            )
            row = SubjectRow(subject.name_ka, tooltip)
            row.name_label.setProperty("subjectCode", subject.code)
            row.clicked.connect(lambda code=subject.code: self.subjectRequested.emit(code))
            column.addWidget(row)
            self.subject_rows[subject.code] = row
            self.subject_labels.append(row.name_label)
            self.subject_count_labels[subject.code] = row.count_label
        column.addStretch(1)

        area = QScrollArea()
        area.setObjectName("SubjectScroll")
        area.setWidgetResizable(True)
        area.setFrameShape(QFrame.Shape.NoFrame)
        area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area.setWidget(container)
        return area

    # -- selection -----------------------------------------------------------
    def set_selected_subject(self, code: str | None) -> None:
        """Highlight the subject being browsed (``None`` = all subjects)."""
        self._selected_subject = code
        self.all_subjects_row.set_selected(code is None)
        for subject_code, row in self.subject_rows.items():
            row.set_selected(subject_code == code)

    @property
    def selected_subject(self) -> str | None:
        return self._selected_subject

    def select_page(self, key: str) -> None:
        """Show *key* as the current page without emitting ``pageRequested``."""
        for row in range(self.nav_list.count()):
            item = self.nav_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                self.nav_list.blockSignals(True)
                self.nav_list.setCurrentItem(item)
                self.nav_list.blockSignals(False)
                return

    # -- slots -----------------------------------------------------------
    def _on_nav_changed(self, current: QListWidgetItem | None, _previous: object) -> None:
        if current is not None:
            self.pageRequested.emit(str(current.data(Qt.ItemDataRole.UserRole)))
