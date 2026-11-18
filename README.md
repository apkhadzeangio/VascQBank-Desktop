# VascQBank Review Desktop

Offline desktop application for reviewing, editing and approving the
10,000 Georgian-language vascular surgery exam questions of
[VascQBank-10K](https://github.com/apkhadzeangio/VascQBank-10K).
The final deliverable of the review process is a set of **approved Word (.docx)
and TXT files**.

* Python 3.12+ · PySide6 (Qt 6) · SQLite · python-docx — Windows 10/11 and Linux (incl. CachyOS)
* Entirely offline: no server, no cloud database, no login, no synchronisation.
  Each installation keeps its own local database.
* The application **never generates questions**; it only works with imported ones.
* The user interface is Georgian; code and technical documentation are English.

> **Status: Phase 7 of 10 — embedded question bank and safe first launch.** Working: import,
> browsing and search, the editor with autosave and crash recovery, approval/rejection/restore, and the
> verified DOCX/TXT export (Phases 2–6); and now a **bundled seed database**: a distribution can carry
> the validated bank so that the catalog is ready on first launch, without any import — installed
> only into an empty installation, never over existing work, and never when traces of a lost database
> exist. Export publication is now crash-consistent (ARCHITECTURE.md §24–§25). The real bank is not
> in this repository; everything is tested with synthetic data.

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
| `python -m vascqbank_desktop --seed-dir PATH` | Use this seed folder for a first launch (default: the bundled seed, if any; also `VASCQBANK_SEED_DIR`) |

The data folder can also be set with the `VASCQBANK_DATA_DIR` environment variable.

## Where data is stored

| OS | Default data folder |
|---|---|
| Windows | `%LOCALAPPDATA%\VascQBank-Desktop` |
| Linux | `$XDG_DATA_HOME/vascqbank-desktop` (usually `~/.local/share/vascqbank-desktop`) |

Inside: `database/vascqbank.sqlite3` (the authoritative database),
`database/search-index.sqlite3` (a derived search index — safe to delete, rebuilt
automatically), `backups/` (verified backups, each with a JSON manifest),
`exports/`, `logs/`, `journal/` (short-lived export publication intents, §25) and `settings.json` (reviewer name, last subject / question /
status filter, window geometry; written atomically, never in the repository).
The menu **ფაილი → მონაცემთა საქაღალდის გახსნა** opens it.
Do not place the data folder inside OneDrive/Dropbox/Google Drive sync folders
while the application is running.

## First launch and the bundled question bank (Phase 7)

* **With a bundled seed** (a distribution build): on the very first start the verified bank is copied
  into the data folder with its own database ID — the catalog opens with every question
  *განხილვის მოლოდინში*; choose a reviewer name and start. Later starts simply open that database; an
  update of the application never resets, replaces or merges it.
* **Without a seed** (a source checkout, as in this repository): an empty database and the manual TXT
  import below, exactly as before. The home page says which applies.
* **Database missing but traces found** (backups, `-wal` files, exports, settings): the program creates
  **nothing** and explains what it found. Restore by copying a backup (`backups/vascqbank-….sqlite3`) to
  `database/vascqbank.sqlite3` while the program is closed, or — after a second confirmation — start a
  new database; backups are never touched. (Restoring from inside the program is not implemented yet.)
* A **development seed** (synthetic or incomplete test data) is always labelled
  „⚠ სინთეზური სატესტო ბანკი“ in the status bar.
* A damaged bundled seed is refused with a message; nothing is created.

**Building a seed (developers):** `python tools/build_seed.py SOURCE_DIR OUTPUT_DIR --version 2027.1
--production` builds it through the normal import pipeline and enforces the production gates (12
subjects, exact target counts, 10,000 questions, no errors, duplicates or conflicts); without
`--production` the result is a DEVELOPMENT seed. `python tools/prepare_package_seed.py OUTPUT_DIR`
verifies it and copies it into the package data for a distribution build (DEVELOPMENT seeds only with
`--allow-development-seed`). **Never commit a seed or the real source files** — `.gitignore` excludes
them; details in ARCHITECTURE.md §24.7.

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

## Exporting approved questions (Phase 6)

**ექსპორტი** in the sidebar (or **ფაილი → დამტკიცებული კითხვების ექსპორტი…**, Ctrl+Shift+E):

1. choose subjects (all, one or several), **Word (.docx)** and/or **TXT**, the Word edition(s) —
   *განხილვის ვერსია* (answers marked, explanations; default), *საგამოცდო ვერსია* (no answers),
   *პასუხების გასაღები* (answers + explanations) — one file per subject and/or one combined file, the
   font (Sylfaen by default, DejaVu Sans) and the destination folder;
2. the preview lists the subjects, how many approved questions will be exported, how many are left out
   because they are not approved, and every file name;
3. **ექსპორტი** runs in the background with progress and a cancel button.

```
<destination>/VascQBank_Export_20261118-091502-7a0ebb/
  DOCX/ALI_review.docx  …  DOCX/VascQBank_All_Approved_exam.docx  DOCX/…_answer_key.docx
  TXT/ALI_questions.txt  TXT/ALI_explanations.txt  …
  export-manifest.json
```

* Only the **exact approved revision** of **approved** questions is exported — never drafts,
  pending, in-review or rejected questions, never an older or newer revision. Nothing in the
  database changes; the export is recorded in `export_runs`.
* Every file is checked before it is published: each Word document is reopened and compared with the
  approved text question by question (and the exam edition is proven not to reveal answers); each TXT
  pair is read back with the real importer. A failed, cancelled or interrupted export publishes nothing
  and never touches earlier exports; a new export never overwrites an old folder.
* **Numbering**: Word shows `12. ALI-0042` — a running number for the document and the permanent
  question ID; the exam and its answer key share the numbers. TXT keeps the original numbers, so a
  partial export has gaps (the importer accepts them).
* **TXT limits**: the TXT format cannot hold a blank line inside a field, `*` in a question or option,
  or lines that look like `ბ) …`. Such questions are named in the preview and the TXT export is refused
  until the text is corrected and re-approved (Word is not affected).
* Fonts are named, not embedded: use Sylfaen on Windows; on Linux/LibreOffice choose DejaVu Sans.

Synthetic sample output (artificial questions): `python tools/make_export_samples.py OUT_DIR` writes a
complete verified export. Generated `.docx`/TXT files are deliberately kept out of git by `.gitignore`
(the same rule that protects real question files); the sample's manifest and page images rendered by
LibreOffice are in [docs/samples/phase6](docs/samples/phase6).

### Screenshots

Rendered from the real window with **synthetic** data (10,000 artificial questions) by
`tools/capture_screenshots.py`, `tools/capture_screenshots_phase4.py` and
`tools/capture_screenshots_phase5.py`, `…_phase6.py` and `…_phase7.py`; see
[docs/screenshots/phase3](docs/screenshots/phase3), [docs/screenshots/phase4](docs/screenshots/phase4),
[docs/screenshots/phase5](docs/screenshots/phase5), [docs/screenshots/phase6](docs/screenshots/phase6) and
[docs/screenshots/phase7](docs/screenshots/phase7) (first launch from a synthetic seed, the recovery and
damaged-seed messages)
(the export page, its options, a blocked TXT export, progress and completion) (workflow bar, approve, approve-and-next in a
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
├── services/      use cases: storage init, import, counts, catalog/search, settings, session, backups, drafts, edits, review decisions, approved-text contract, export
├── validation/    text normalisation, issue catalogue (Georgian), content rules
├── importexport/  file discovery, paired-TXT and refined-master parsers, UTF-8 I/O, TXT and Word writers, Word verifier
├── ui/            PySide6 widgets: catalog, detail, editor, autosave, workflow, history, drafts, export, backups, reviewer, import; strings, theme, workers
└── resources/     bundled fonts (Noto Sans, Noto Sans Georgian) and icon
tools/             build_seed.py, prepare_package_seed.py — seed build and packaging; capture_screenshots*.py,
                   make_export_samples.py — screenshots and samples (synthetic data)
tests/             pytest suite, incl. layer-boundary, offline, crash-recovery (isolated SIGKILL children on temporary databases) and 10,000-question checks
```

## Data protection

Real question files, databases, exports, backups, logs and secrets are
excluded by `.gitignore` and must never be committed.

## Third-party assets

Noto Sans and Noto Sans Georgian are bundled under the SIL Open Font License 1.1
(`src/vascqbank_desktop/resources/fonts/OFL.txt`).
