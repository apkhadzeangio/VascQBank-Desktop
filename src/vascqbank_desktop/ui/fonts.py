"""Font registration.

Two bundled families render every character the same way on Windows and Linux:

* **Noto Sans** — Latin letters, digits, punctuation (e.g. "EVAR", "mmHg", „“, ×10⁹/ლ);
* **Noto Sans Georgian** — Mkhedruli letters. It contains *only* Georgian
  glyphs, which is why Noto Sans is bundled alongside it.

Qt picks glyphs per character from the family list, so mixed Georgian/Latin
text uses the right font for each run. System fonts with Georgian coverage
(Segoe UI and Sylfaen on Windows, DejaVu Sans on Linux) remain as fallbacks.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QByteArray
from PySide6.QtGui import QFont, QFontDatabase

from vascqbank_desktop import resources

log = logging.getLogger(__name__)

LATIN_FAMILY = "Noto Sans"
GEORGIAN_FAMILY = "Noto Sans Georgian"
SYSTEM_FALLBACKS: tuple[str, ...] = ("Segoe UI", "Sylfaen", "DejaVu Sans", "Arial")

UI_POINT_SIZE = 10.0


def register_bundled_fonts() -> list[str]:
    """Register bundled TTF files with Qt; return the families that loaded."""
    families: set[str] = set()
    for name in resources.font_files():
        data = resources.resource_bytes("fonts", name)
        font_id = QFontDatabase.addApplicationFontFromData(QByteArray(data))
        if font_id < 0:
            log.warning("Could not load bundled font %s", name)
            continue
        families.update(QFontDatabase.applicationFontFamilies(font_id))
    return sorted(families)


def font_family_chain() -> list[str]:
    return [LATIN_FAMILY, GEORGIAN_FAMILY, *SYSTEM_FALLBACKS]


def build_ui_font(point_size: float = UI_POINT_SIZE) -> QFont:
    font = QFont()
    font.setFamilies(font_family_chain())
    font.setPointSizeF(point_size)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return font
