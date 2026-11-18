"""Georgian for the texts Qt itself draws (standard buttons, "Show Details...", context menus).

The application's own wording lives in :mod:`vascqbank_desktop.ui.strings`. What Qt draws —
``QMessageBox``'s "Show Details...", the OK / Cancel / Yes / No buttons, the copy/paste context
menu of text fields — comes from Qt's translation catalogue ``qtbase_ka.qm``, which ships with
PySide6. It is loaded from Qt's translations folder (or a bundled ``resources/translations``
copy). A small override layer on top fixes a few renderings that read wrongly in this program
(Qt's catalogue renders "OK" as „დიახ" — "yes"), and also supplies the essential texts if the
catalogue is missing, so no English button text ever appears.

Source question content is never translated: only Qt's own user-interface strings pass through.
"""

from __future__ import annotations

import contextlib
import logging
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QLibraryInfo, QLocale, QTranslator

from vascqbank_desktop import resources

log = logging.getLogger(__name__)

LOCALE_NAME = "ka_GE"

# (context or "*", source text) -> Georgian. Keys use Qt's exact English source strings.
OVERRIDES: dict[tuple[str, str], str] = {
    ("*", "OK"): "კარგი",
    ("*", "&OK"): "&კარგი",
    ("*", "Cancel"): "გაუქმება",
    ("*", "&Cancel"): "&გაუქმება",
    ("*", "Close"): "დახურვა",
    ("*", "&Close"): "&დახურვა",
    ("*", "Yes"): "დიახ",
    ("*", "&Yes"): "&დიახ",
    ("*", "No"): "არა",
    ("*", "&No"): "&არა",
    ("*", "Save"): "შენახვა",
    ("*", "Discard"): "ცვლილებების გაუქმება",
    ("*", "Apply"): "გამოყენება",
    ("*", "Help"): "დახმარება",
    ("*", "Abort"): "შეწყვეტა",
    ("*", "Retry"): "ხელახლა ცდა",
    ("*", "Ignore"): "უგულებელყოფა",
    ("QMessageBox", "Show Details..."): "დეტალების ჩვენება…",
    ("QMessageBox", "Hide Details..."): "დეტალების დამალვა…",
    ("*", "&Undo"): "&დაბრუნება",
    ("*", "&Redo"): "&გამეორება",
    ("*", "Cu&t"): "&ამოჭრა",
    ("*", "&Copy"): "&კოპირება",
    ("*", "&Paste"): "&ჩასმა",
    ("*", "Delete"): "წაშლა",
    ("*", "Select All"): "ყველაფრის მონიშვნა",
    ("*", "Copy &Link Location"): "ბმულის &კოპირება",
}


class _Overrides(QTranslator):
    """Highest-priority translator: only the texts in :data:`OVERRIDES`, everything else falls
    through to Qt's catalogue (returning ``None`` means "not translated here")."""

    def translate(  # noqa: N802 - Qt's virtual
        self, context: str, source_text: str, disambiguation: str | None = None, n: int = -1
    ) -> str | None:
        return OVERRIDES.get((context, source_text)) or OVERRIDES.get(("*", source_text))

    def isEmpty(self) -> bool:  # noqa: N802 - Qt's virtual
        return False


_INSTALLED: list[QTranslator] = []  # Qt does not own installed translators: keep them alive


def _catalogue_folders() -> list[Path]:
    folders: list[Path] = []
    # a copy bundled by a distribution build (Phase 10) wins over Qt's folder
    with contextlib.suppress(OSError, ValueError):
        folders.append(resources.resource_path("translations"))
    qt_folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if qt_folder:
        folders.append(Path(qt_folder))
    return folders


def install_translations(app: QCoreApplication) -> bool:
    """Install Georgian for Qt's own texts; ``True`` if Qt's catalogue was found (the overrides are
    installed either way). Safe to call more than once."""
    QLocale.setDefault(QLocale(LOCALE_NAME))
    for translator in _INSTALLED:
        app.removeTranslator(translator)
    _INSTALLED.clear()
    catalogue = QTranslator()
    found = False
    for folder in _catalogue_folders():
        if folder.is_dir() and catalogue.load("qtbase_ka", str(folder)):
            found = True
            break
    if found:
        app.installTranslator(catalogue)
        _INSTALLED.append(catalogue)
    else:
        log.warning("Qt's Georgian catalogue (qtbase_ka.qm) was not found; using the built-in essentials")
    overrides = _Overrides()
    app.installTranslator(overrides)  # installed last = asked first
    _INSTALLED.append(overrides)
    return found
