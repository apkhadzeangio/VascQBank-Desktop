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

> **Status: Phase 5 of 10 — approval, rejection, reopening and revision
> restoration.** Working: importing questions (Phase 2); browsing all 12 subjects,
> full-text search, progress statistics, a local reviewer profile, session restore
> and verified backups (Phase 3); editing every part of a question with autosaved
> drafts, explicit saves, conflict detection, revision history and crash recovery
> (Phase 4); and now the **complete editorial workflow**: **approve** the exact saved
> revision (also *approve and next*), **reject** with a mandatory reason, **reopen**,
> **restore an earlier revision** as a new one, live statistics, and a read-only
> **export contract** for the Phase 6 DOCX/TXT exporter, which is the next phase
> (see [ARCHITECTURE.md](ARCHITECTURE.md)).

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
  last-modified time. Text can be selected and copied. Opening a question never
  changes anything in the database; editing starts only when you press *რედაქტირება*.
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
  offered yet. Take one before a long editing session.

## Approval workflow (Phase 5)

Each question shows only the actions its status allows:

| Button | Shortcut | What it does |
|---|---|---|
| დამტკიცება და შემდეგი → | Ctrl+Enter | approves the displayed saved revision, then moves to the next question |
| დამტკიცება | Ctrl+Shift+Enter | approves and stays |
| უარყოფა | Ctrl+Shift+R | rejects; a written reason is mandatory |
| ხელახლა გახსნა | Ctrl+Shift+O | reopens an approved or rejected question (confirmation, optional note) |
| განხილვის დაწყება | — | pending → in review, without changing the text |
| წინა რედაქციის დაბრუნება | Ctrl+Shift+B | in the history: restores the selected earlier revision as a new one |

* **Approval is bound to one exact revision** and recorded in the append-only event log with
  your name and the time. Nothing is approved merely because a draft was autosaved: if the
  question has an unsaved draft the program asks you to open it first; if you approve while
  editing, your text is saved as a new revision first and *that* revision is approved. If the
  question changed elsewhere meanwhile you get a clear message and nothing is approved.
* **Approve and next** moves on only after the approval is safely stored. Inside a status filter
  it continues with the question that followed the approved one.
* **Rejecting** keeps the question and its whole history in the database; an approved question
  loses its approval. **Reopening** returns a question to *განხილვის პროცესში*; earlier approvals
  stay in the history.
* **Restoring** never changes or deletes an old revision: the old text is added as a new revision
  (labelled *აღდგენა (რედაქცია N-დან)*). A restored previously-approved text is not approved
  automatically; editing or restoring an approved question revokes its approval after a warning.
  A draft is never lost by a restore — you choose to keep or discard it.
* Counters on the catalog strip, home page and sidebar refresh after every decision; successful
  decisions show a green message, failures a red one and change nothing.
* **Export (Phase 6)** will read only the *approved revision* of approved questions through
  `ApprovedQuestionsService`; drafts, pending, in-review, rejected questions and superseded
  revisions can never be exported (ARCHITECTURE.md §19). There is no bulk approval.

### Screenshots

Rendered from the real window with **synthetic** data (10,000 artificial questions) by
`tools/capture_screenshots.py`, `tools/capture_screenshots_phase4.py` and
`tools/capture_screenshots_phase5.py`; see [docs/screenshots/phase3](docs/screenshots/phase3),
[docs/screenshots/phase4](docs/screenshots/phase4) and
[docs/screenshots/phase5](docs/screenshots/phase5) (workflow bar, approve, approve-and-next in a
filter, reject and reopen dialogs, restore with a draft, the restored history): home dashboard, catalog with the correct
answer highlighted, search results, backups, the reviewer profile, and (Phase 4) the editor,
validation, the approved-question warning, a conflict, draft recovery, the draft list and the
revision history with a comparison.

## Editing, drafts and crash recovery (Phase 4)

Choose a reviewer name first (*რეცენზენტი*); editing is attributed to it. Then open a question
and press **რედაქტირება** (Ctrl+E).

| Key | Action |
|---|---|
| Ctrl+E | edit the open question |
| Ctrl+S | save as a new revision |
| Esc | leave edit mode (asks what to do with unsaved changes) |
| Ctrl+1 … Ctrl+5 | mark answer ა … ე as the correct one |
| Tab | next field (never inserts a tab character) |
| Ctrl+H | revision history of the open question |

* **Fields**: question, five answers, exactly one correct answer (highlighted), explanation and an
  optional note. Plain text, multi-line, Georgian Unicode stored exactly as typed.
* **Footer state**: *no change · changing · draft saving · draft saved HH:MM:SS · saving · saved as
  revision N · error · conflict*. "Saved" always means the database transaction has committed.
* **Autosave**: about one second after you stop typing the text is written as a **draft** — kept
  apart from the revision history, never a new revision, never a status change. Leaving the
  question, switching subject, changing page, leaving edit mode and closing the window write
  the draft immediately. If a write fails (disk full, locked database) the text stays on screen,
  an error explains why, and the write is retried; the program asks before it would close.
* **Saving** (Ctrl+S) validates the text and, in one transaction, adds a revision, moves the
  current-revision pointer, updates the status (*pending → in review*; editing an **approved**
  question revokes the approval after a confirmation) and records the event. Saving identical
  text creates nothing. If the question was changed elsewhere meanwhile, you choose: compare,
  keep your text as a draft, or save it on top (the other revision stays in the history).
* **After a crash or power cut** the next start shows *დაუსრულებელი მონახაზი: N*, marks those
  questions *✎ მონახაზი* in the list, and offers **აღდგენა რედაქტორში / შედარება / გაუქმება**
  on the question that was open. Nothing is restored or deleted behind your back; drafts are
  never removed because a question is filtered out. A deliberate discard is archived.
* **History** (Ctrl+H): every revision with editor, time, note and status change, its full text and
  a comparison with the current version. History is append-only; an earlier revision can be
  brought back as a **new** revision (see *Approval workflow* below).
* **Limits**: the last second of typing before an abrupt stop can be lost (what is on disk is
  then the previous confirmed draft); nothing protects against disk failure — keep backups
  (*სარეზერვო ასლები*). Details: [ARCHITECTURE.md §17](ARCHITECTURE.md).

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
├── db/            SQLite policy, ownership check, migrations (schema v2, v3), backups, repositories, search index
├── services/      use cases: storage init, import, counts, catalog/search, settings, session, backups, drafts, edits, review decisions, approved-text contract
├── validation/    text normalisation, issue catalogue (Georgian), content rules
├── importexport/  file discovery, paired-TXT and refined-master parsers, UTF-8 I/O
├── ui/            PySide6 widgets: catalog, detail, editor, autosave, workflow, history, drafts, backups, reviewer, import; strings, theme, workers
└── resources/     bundled fonts (Noto Sans, Noto Sans Georgian) and icon
tools/             capture_screenshots*.py — render the real window with synthetic data
tests/             pytest suite, incl. layer-boundary, offline, crash-recovery (isolated SIGKILL children on temporary databases) and 10,000-question checks
```

## Data protection

Real question files, databases, exports, backups, logs and secrets are
excluded by `.gitignore` and must never be committed.

## Third-party assets

Noto Sans and Noto Sans Georgian are bundled under the SIL Open Font License 1.1
(`src/vascqbank_desktop/resources/fonts/OFL.txt`).
