# VascQBank Review Desktop

Offline desktop application for reviewing, editing and approving the
10,000 Georgian-language vascular surgery exam questions of
[VascQBank-10K](https://github.com/apkhadzeangio/VascQBank-10K).
The final deliverable of the review process is a set of **approved Word (.docx)
and TXT files**.

* Python 3.12+ · PySide6 (Qt 6) · SQLite — Windows 10/11 and Linux (incl. CachyOS)
* Entirely offline: no server, no cloud database, no login, no synchronisation.
  Each installation keeps its own local database.
* The application **never generates questions**; it only works with imported ones.
* The user interface is Georgian; code and technical documentation are English.

> **Status: Phase 1 of 10 — foundation.** The window, navigation shell,
> Georgian fonts, local storage and schema versioning work. Import, editing,
> approval, history, backup and export are designed in
> [ARCHITECTURE.md](ARCHITECTURE.md) and arrive in later phases.

---

## Install and run (from source)

Requires Python **3.12 or newer** (PySide6 6.12 wheels support 3.10–3.15).

### Windows 10/11 (PowerShell)

```powershell
git clone https://github.com/apkhadzeangio/VascQBank-Desktop.git
cd VascQBank-Desktop
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -e ".[dev]"

# start the application
.venv\Scripts\python -m vascqbank_desktop
# or, without a console window:
.venv\Scripts\vascqbank-desktop.exe
```

### Linux / CachyOS (bash, fish or zsh)

```bash
git clone https://github.com/apkhadzeangio/VascQBank-Desktop.git
cd VascQBank-Desktop
python -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e ".[dev]"

.venv/bin/python -m vascqbank_desktop
```

Qt chooses Wayland or X11 automatically. On an X11 session, Qt 6 needs the
`xcb-cursor` library: `sudo pacman -S xcb-util-cursor` (Arch/CachyOS) or
`sudo apt install libxcb-cursor0` (Debian/Ubuntu).

### Command-line options

| Command | Purpose |
|---|---|
| `python -m vascqbank_desktop` | Start the application |
| `python -m vascqbank_desktop --self-check` | Prepare local storage, print a diagnostic report, exit (no window) |
| `python -m vascqbank_desktop --data-dir PATH` | Use another data folder (e.g. a test copy) |
| `python -m vascqbank_desktop --version` | Print the version |

The data folder can also be set with the `VASCQBANK_DATA_DIR` environment variable.

## Where data is stored

| OS | Default data folder |
|---|---|
| Windows | `%LOCALAPPDATA%\VascQBank-Desktop` |
| Linux | `$XDG_DATA_HOME/vascqbank-desktop` (usually `~/.local/share/vascqbank-desktop`) |

Inside: `database/vascqbank.sqlite3`, `backups/`, `exports/`, `logs/`.
The menu **ფაილი → მონაცემთა საქაღალდის გახსნა** opens it.
Do not place the data folder inside OneDrive/Dropbox/Google Drive sync folders
while the application is running.

## Tests

```bash
.venv/bin/python -m pytest          # Windows: .venv\Scripts\python -m pytest
```

GUI tests run on Qt's `offscreen` platform by default, so they also work
without a display. To run them against a real desktop session, set
`QT_QPA_PLATFORM` (`wayland`, `xcb` or `windows`) before running pytest.

## Project layout

```
src/vascqbank_desktop/
├── app/           startup: arguments, logging, wiring
├── config/        constants and cross-platform paths
├── domain/        subjects, answer letters ა–ე, review statuses, question IDs
├── db/            SQLite connection policy, ownership check, migrations
├── services/      use cases called by the UI (storage initialisation)
├── validation/    Unicode/UTF-8 text normalisation
├── importexport/  strict UTF-8 reading, atomic writing (parsers/exporters later)
├── ui/            PySide6 widgets, Georgian strings, fonts, theme
└── resources/     bundled fonts (Noto Sans, Noto Sans Georgian) and icon
tests/             pytest suite, incl. layer-boundary and offline checks
```

## Data protection

Real question files, databases, exports, backups, logs and secrets are
excluded by `.gitignore` and must never be committed.

## Third-party assets

Noto Sans and Noto Sans Georgian are bundled under the SIL Open Font License 1.1
(`src/vascqbank_desktop/resources/fonts/OFL.txt`).
