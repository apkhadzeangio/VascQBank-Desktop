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

> **Status: Phase 3 of 10 — question browser, search, reviewer profile and
> session persistence.** Working: importing questions (Phase 2), browsing all
> 12 subjects, a read-only question detail view with the correct answer
> highlighted, full-text search (SQLite FTS5 trigram with a LIKE fallback),
> review-progress statistics, a local reviewer profile, return-to-where-you-stopped
> on the next start, and manual verified backups. Editing, approval and export
> arrive in later phases (see [ARCHITECTURE.md](ARCHITECTURE.md)).

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

Inside: `database/vascqbank.sqlite3` (the authoritative database),
`database/search-index.sqlite3` (a derived search index — safe to delete, rebuilt
automatically), `backups/` (verified backups, each with a JSON manifest),
`exports/`, `logs/` and `settings.json` (reviewer name, last subject / question /
status filter, window geometry; written atomically, never in the repository).
The menu **ფაილი → მონაცემთა საქაღალდის გახსნა** opens it.
Do not place the data folder inside OneDrive/Dropbox/Google Drive sync folders
while the application is running.

## Importing questions

**ფაილი → კითხვების იმპორტი…** (Ctrl+I) or the button on the home page:

1. choose `<CODE>_questions.txt` / `<CODE>_explanations.txt` files, a folder
   containing them, or `REFINED_MASTER_QBANK.txt`;
2. **შემოწმება** validates everything without changing the database and lists
   errors and warnings in Georgian;
3. **იმპორტი (N კითხვა)** asks for confirmation, creates a verified backup and
   adds only new questions, all as „განხილვის მოლოდინში“.

Existing questions are never changed by an import. Use one source format per
question ID — see ARCHITECTURE.md §7.1.

## Browsing, search and sessions

* **Catalog** (*კითხვები*): choose a subject in the sidebar (or *ყველა თემა*), filter by
  review status, sort, and click a question. The list is lazy — only the ordered
  IDs are resolved up front and the text of a page of rows is read when it scrolls
  into view — so it stays instant with all 10,000 questions.
* **Detail view**: ID, Georgian subject, full question, the five options ა–ე with
  the correct one highlighted (colour *and* the words „სწორი პასუხი“), full
  explanation, status, current revision number, source file/line, import time and
  last-modified time. Text can be selected and copied; there are no edit or
  approval controls yet. Opening a question never changes anything in the database.
* **Navigation**: *← წინა* / *შემდეგი →* (Alt+←/→), and *გადასვლა* (Ctrl+G) for an ID such as
  `ALI-0042` or a bare number inside the open subject.
* **Search** (Ctrl+F): works on Georgian word fragments across question text,
  answers, explanations and IDs; several words must all match. It runs in the
  background and never blocks the window; clearing the box (or Esc) restores the
  previous list. SQLite FTS5 with the trigram tokenizer is used when available and
  a LIKE scan otherwise (always for terms shorter than three characters); the result
  list says which one answered.
* **Progress**: counts of total / pending / in review / approved / rejected for the
  whole bank and the open subject, read from the database.
* **Reviewer profile** (*რეცენზენტი*): pick *რამაზი აფხაძე*, *დავით ფარღალავა* or type any
  name. No password or account; the name is stored locally for future edit and
  approval records and never rewrites history.
* **Session**: the last subject, status filter, question and window geometry are
  restored on the next start. If the remembered question no longer matches the
  filter, the nearest following one is shown with an explanation.
* **Backups** (*სარეზერვო ასლები*): create a verified backup now, see every backup with
  its time, type, size and verification result. Restoring is deliberately not
  offered yet.

### Screenshots

Rendered from the real window with **synthetic** data (10,000 artificial questions) by
`tools/capture_screenshots.py`; see [docs/screenshots/phase3](docs/screenshots/phase3):
home dashboard, catalog with the correct answer highlighted, search results with highlighted
previews, a filtered subject after a jump, backups and the reviewer profile.

## Tests

```bash
.venv/bin/python -m pytest                 # all tests, incl. the 10,000-question benchmark
.venv/bin/python -m pytest -m "not perf"   # skip the benchmark
ruff check src tests
```

Test data is synthetic (`tests/synthetic.py`); no real question content is used.

GUI tests run on Qt's `offscreen` platform by default, so they also work
without a display. To run them against a real desktop session, set
`QT_QPA_PLATFORM` (`wayland`, `xcb` or `windows`) before running pytest.

## Project layout

```
src/vascqbank_desktop/
├── app/           startup: arguments, logging, wiring
├── config/        constants and cross-platform paths
├── domain/        subjects, answer letters, statuses, question IDs, ImportedQuestion
├── db/            SQLite policy, ownership check, migrations, schema, backups, repositories, search index
├── services/      use cases: storage init, import, counts, catalog/search, settings, session, backups
├── validation/    text normalisation, issue catalogue (Georgian), content rules
├── importexport/  file discovery, paired-TXT and refined-master parsers, UTF-8 I/O
├── ui/            PySide6 widgets: catalog, detail, backups, reviewer, import; strings, theme, workers
└── resources/     bundled fonts (Noto Sans, Noto Sans Georgian) and icon
tools/             capture_screenshots.py — renders the real window with synthetic data
tests/             pytest suite, incl. layer-boundary, offline and 10,000-question checks
```

## Data protection

Real question files, databases, exports, backups, logs and secrets are
excluded by `.gitignore` and must never be committed.

## Third-party assets

Noto Sans and Noto Sans Georgian are bundled under the SIL Open Font License 1.1
(`src/vascqbank_desktop/resources/fonts/OFL.txt`).
