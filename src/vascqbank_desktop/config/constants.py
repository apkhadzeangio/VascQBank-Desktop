"""Application-wide constants."""

from typing import Final

# Latin identifiers: used for folders, the Windows AppUserModelID and the
# Linux desktop-file name. Keep them ASCII so they are safe everywhere.
APP_ID: Final = "VascQBank-Desktop"
APP_SLUG: Final = "vascqbank-desktop"
WINDOWS_APP_USER_MODEL_ID: Final = "Apkhadze.VascQBank.Desktop"

# Georgian display names.
APP_BRAND: Final = "VascQBank"
APP_NAME_KA: Final = "საგამოცდო კითხვების რეცენზირება"
APP_TITLE_KA: Final = f"{APP_BRAND} — {APP_NAME_KA}"
APP_TAGLINE_KA: Final = (
    "სისხლძარღვთა ქირურგიის საგამოცდო კითხვების ოფლაინ რეცენზირება და დამტკიცება"
)

# Database file and identity.
DATABASE_FILENAME: Final = "vascqbank.sqlite3"
# SQLite PRAGMA application_id stamped into every database this app creates
# (ASCII "VQBD"). A non-zero, different value means the file belongs to some
# other program and must not be opened or migrated.
SQLITE_APPLICATION_ID: Final = 0x56514244

# Local, per-installation files inside the data directory. Neither belongs to
# the source repository.
SETTINGS_FILENAME: Final = "settings.json"
# Derived full-text index (rebuildable at any time; never authoritative data).
SEARCH_INDEX_FILENAME: Final = "search-index.sqlite3"

# Environment variable that overrides the data directory (tests, portable use).
DATA_DIR_ENV_VAR: Final = "VASCQBANK_DATA_DIR"
