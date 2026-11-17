# VascQBank Review Desktop — Architecture

This document describes the architecture implemented in Phase 1 and the design
that later phases implement. Sections marked **(implemented)** describe code
that exists and is tested; sections marked **(design)** are the plan.

## 1. Goals and constraints

* Review, edit and approve 10,000 imported Georgian exam questions
  (12 subjects); deliver **approved Word and TXT files**.
* Entirely offline and local: no server, API, cloud database, login or
  synchronisation. Every installation is independent.
* The application **never creates questions**. Questions enter only through
  import; everything else is editing, reviewing and exporting existing ones.
* Windows 10/11 and Linux (including CachyOS); finally shipped as a
  double-clickable Windows EXE and a Linux AppImage.
* Georgian Unicode is preserved byte-exactly through import → edit → export.

## 2. Layers (implemented)

```
                ┌──────────────┐
                │     app      │  startup, CLI flags, logging, wiring
                └──────┬───────┘
          ┌────────────┴────────────┐
          ▼                         ▼
   ┌─────────────┐           ┌─────────────┐
   │     ui      │──────────▶│  services   │  use cases; return dataclasses
   │  (PySide6)  │           └──────┬──────┘
   └─────────────┘                  │
          │              ┌──────────┼──────────────┐
          │              ▼          ▼              ▼
          │        ┌──────────┐ ┌────────┐ ┌──────────────┐
          │        │    db    │ │ import │ │  validation  │
          │        │ (sqlite3)│ │ export │ │              │
          │        └────┬─────┘ └───┬────┘ └──────┬───────┘
          ▼             ▼           ▼             ▼
   ┌──────────────────────────────────────────────────────┐
   │              domain  ·  config  (pure Python)        │
   └──────────────────────────────────────────────────────┘
```

| Package | Responsibility | Must not import |
|---|---|---|
| `config` | constants, cross-platform paths | Qt, sqlite3, any upper layer |
| `domain` | subjects, answer letters ა–ე, review statuses, question IDs; later entities and rules | Qt, sqlite3, db, services, ui, app, importexport |
| `validation` | text normalisation; later structural question checks | Qt, sqlite3, upper layers |
| `importexport` | strict UTF-8 I/O; later TXT/master parsers, TXT/DOCX writers | Qt, ui, app |
| `db` | connection policy, ownership check, migrations; later repositories | Qt, services, ui, app |
| `services` | use cases (storage init; later import, edit, approve, backup, export) | Qt, ui, app |
| `ui` | widgets, Georgian strings, fonts, theme | sqlite3, db, app |
| `app` | entry point and composition root | — |

`tests/test_architecture.py` parses every module's imports with `ast` and fails
the build if a rule above is broken. It also fails on any networking import
(`socket`, `urllib`, `http`, `QtNetwork`, …), which keeps the application
provably offline.

The UI never receives a `sqlite3` connection: services return immutable
dataclasses (e.g. `StorageStatus`). This keeps widgets testable without a
database and keeps SQL out of the UI.

## 3. Startup sequence (implemented)

1. Parse arguments (`--data-dir`, `--self-check`, `--version`).
2. Resolve paths: `--data-dir` > `VASCQBANK_DATA_DIR` > platform default
   (`%LOCALAPPDATA%\VascQBank-Desktop`, `$XDG_DATA_HOME/vascqbank-desktop`).
3. Configure logging: rotating UTF-8 log in `<data>/logs`; console handler only
   if a console exists (windowed Windows builds have `sys.stderr is None`).
4. `--self-check` stops here after storage initialisation and prints a report;
   Qt is never imported, so it runs on machines without a display.
5. GUI: set Windows AppUserModelID / Linux desktop-file name, create
   `QApplication`, register bundled fonts, apply theme, initialise storage
   (error dialog and exit code 2 on failure — nothing is modified), show window.

## 4. Storage foundation (implemented)

### 4.1 Connection policy — `db/connection.py`

Every connection goes through `open_database()`:

| Setting | Why |
|---|---|
| `isolation_level=None` | no hidden transactions; writers use explicit `BEGIN IMMEDIATE` |
| `foreign_keys=ON` | referential integrity (off by default in SQLite) |
| `busy_timeout=5000` | short lock waits instead of immediate failure |
| ownership check | a foreign SQLite file is rejected **before** anything persistent is written |
| `encoding='UTF-8'` | text storage encoding, fixed at creation |
| `journal_mode=WAL` | readers never block the writer; crash-safe commits |
| `synchronous=NORMAL` | recommended durability level for WAL |

Minimum SQLite: 3.37 (STRICT tables), checked at start-up with a clear error.
Python 3.12+ builds ship newer versions (3.53 measured in the test
environment); Ubuntu 22.04's system SQLite 3.37.2 is the oldest relevant case.

### 4.2 Database identity — `db/identity.py`

* `PRAGMA application_id = 0x56514244` ("VQBD") stamps our files.
* A file with another non-zero application id, or with tables but no id, is
  refused byte-for-byte unchanged (tested). A random non-SQLite file raises
  `sqlite3.DatabaseError` and is not modified either.
* `app_metadata.database_id` (UUID4) identifies the installation's database;
  it will appear in backup manifests and export manifests.

### 4.3 Schema versioning — `db/migrations.py`

* `PRAGMA user_version` is the authoritative schema version.
* `schema_migrations(version, name, applied_at, app_version)` is the audit trail.
* Migrations are numbered 1…N with no gaps, applied in order, each in a single
  `BEGIN IMMEDIATE` transaction together with the version bump. A failing
  migration rolls back completely (tested with a deliberately broken migration).
* A database newer than the application (`user_version > LATEST_VERSION`) is
  refused with `SchemaTooNewError` — no silent downgrade.
* Released migrations are never edited; changes always add a new version.
* All tables are `STRICT` (column types enforced).
* From Phase 2 on, a backup is taken automatically before any migration runs
  (§10).

Migration 1: `schema_migrations`, `app_metadata`. Migration 2: the question
schema (§5). Existing Phase 1 databases are backed up and then migrated.

## 5. Data model (implemented — migration 2, `db/schema_v2.py`)

### 5.1 Identity

* `question_uid` = `<SUBJECT>-<NNNN>` (e.g. `ALI-0001`), from subject code and
  original source number. Immutable, unique, identical to the VascQBank-Review
  web application's `external_id`; enforced by a `CHECK` that recomputes it
  from `subject_code` and `source_number`.
* `source_fingerprint` (SHA-256 of the normalised imported content, including
  refined-master extras) detects upstream changes; it is never used as identity.

### 5.2 Tables

| Table | Holds |
|---|---|
| `subjects` | the 12 subjects (code, Georgian name, target count, order), seeded by the migration |
| `import_batches` | one row per confirmed import: counts, backup file name, full JSON manifest (sources with SHA-256, every question's outcome and fingerprint, every issue) |
| `questions` | identity, `status`, provenance (`source_kind`, `source_file`, `source_line`, `import_batch_id`), `source_fingerprint`, **`original_content_json`** (the complete imported content, immutable), `current_revision_id`, `approved_revision_id`, `lock_version`, `created_at`/`modified_at`/`approved_at` |
| `question_revisions` | full content per revision: stem, `option_a`…`option_e` (ა–ე), `correct_letter`, explanation, `origin` (`IMPORT`/`EDIT`/`RESTORE`), author, note, `content_hash`, `created_at` |
| `review_events` | editorial history: action, from/to status, revision, actor, note, time |
| `export_runs` | prepared for the export phases (format, scope, files, manifest) |

All tables are `STRICT`. Enforced by the database itself (each rule has a test):

* a new question must be `PENDING_REVIEW` with no approval — nothing can be
  imported as approved;
* `status = 'APPROVED'` ⇔ approval revision and timestamp are set;
* identity, provenance and `original_content_json` can never be updated;
  questions can never be deleted;
* `question_revisions`, `review_events`, `import_batches`, `export_runs` are
  append-only; revision numbers per question are contiguous (1, 2, 3 …);
* `current_revision_id` / `approved_revision_id` must point to a revision of
  the same question;
* `current_revision_id` is `NOT NULL`; it and `import_batch_id` are
  `DEFERRABLE INITIALLY DEFERRED` so a question, its first revision and the
  (append-only, written-last) batch row can be inserted in one transaction;
* only `db/question_repository.py` inserts into `questions` (architecture test).

Indexes: `questions(subject_code, status)`, `questions(status)`,
`questions(import_batch_id)`, `question_revisions(question_id, revision_no DESC)`,
`review_events(question_id, id)`, and **every foreign-key child column**
(`current_revision_id`, `approved_revision_id`, `restored_from_revision_id`,
`review_events.revision_id`). The last group matters: without it each inserted
revision made SQLite scan `questions` for deferred references, and the
10,000-question import took 27.7 s instead of 1.0 s. A test now fails if any
foreign-key column lacks an index.

The full-text search table moves to the browsing/search phase (migration 3).

### 5.3 Text and time

* All imported text passes `normalize_text()` (no BOM, LF, NFC). NFC never
  alters Georgian letters; Georgian text is stored byte-identical (tested).
* Timestamps: UTC ISO-8601 with offset (`2026-11-17T17:20:00+00:00`), shown in
  the computer's local time zone.

## 6. Performance at 10,000 records (measured)

`tests/test_performance.py` generates the full synthetic bank — all 12
subjects at their target counts, exactly 10,000 questions — and runs the real
services. Measured in the development environment (Linux, Python 3.13):

| Operation | Measured | Test budget |
|---|---:|---:|
| Dry run: discover, parse, validate 10,000 questions + compare with DB | 0.4 s | < 10 s |
| Confirmed import of 10,000 incl. verified pre-import backup | 1.0 s | < 15 s |
| Per-subject/status counts (sidebar) | < 0.01 s | < 0.5 s |
| Dry run against a database already holding 10,000 | 0.6 s | < 10 s |
| Database size with 10,000 questions | 33.5 MB | — |

Dry runs and imports run on a background `QThread` (`ui/workers.py`) with
their own SQLite connection, so the window stays responsive. Browsing and
search design (summary model, `fetchMore`, FTS5 trigram) is unchanged and
arrives in Phase 3.

## 7. Question import and validation (implemented)

### 7.1 Sources and adapters

Both adapters produce the same `domain.question.ImportedQuestion`
(`QuestionId`, `QuestionContent` with stem, five options, correct letter,
explanation, `SourceLocation`, ordered `extras`).

1. **Paired TXT** — `importexport/paired_txt.py`: `<CODE>_questions.txt` +
   `<CODE>_explanations.txt`.
2. **Refined master** — `importexport/refined_master.py`:
   `REFINED_MASTER_QBANK.txt`. The grammar was **verified against the real
   file** (VascQBank-10K, inspected 2026-11-17: 384 records, LF, no BOM, one
   identical field order in every record, every section single-line except
   `EVIDENCE`, which can contain blank lines) and matches VascQBank-Review's
   `docs/UPSTREAM_FORMAT.md`. `ACADEMIC EXPLANATION` becomes the explanation;
   `SOURCE FINGERPRINT`, `SOURCE COMMIT`, `STATUS`, `QA SCORE`, `REVIEWED`,
   `CLINICAL REASONING`, `DISTRACTOR ANALYSIS`, `EVIDENCE`, `CLINICAL PEARL`
   and `QA GATES` are kept in order in `extras` and in `original_content_json`.
   Only records with `STATUS: APPROVED` (= refined) are accepted; that status
   never grants reviewer approval. Gaps in `ORIGINAL ID` are normal there.

`importexport/discovery.py` turns selected files/folders into sources. Folders
are scanned non-recursively, so the VascQBank-10K root yields the 24 paired
files but not `ACADEMIC_REVIEW/`; the master must be chosen explicitly.

**Choose one source per question.** Both formats describe the same IDs
(`ALI-0001` …) with different wording (raw vs. refined). Selecting both at once
marks every overlapping ID as a duplicate and imports none of them. Importing
one format first and the other later makes the second one's overlapping IDs
*conflicts*, which are never imported (insert-only). Decide the authoritative
source before the first real import.

A read-only check of the real VascQBank-10K files (counts only) found all
1,700 paired questions and all 384 master records structurally valid.

### 7.2 Paired TXT grammar

```
questions:     N. <stem, may continue on following lines>
               ა) <option>                (may continue on following lines)
               ბ) <option>
               *გ) <option>               ← ASCII '*' directly before the letter, exactly once
               დ) <option>
               ე) <option>
               <blank line>
explanations:  N. გ) <text, may continue on following lines>
               <blank line>
```

Parsing is line-based: a numbered line starts a new question when it is the
first item, follows a blank line, or follows answer options. Stray blank lines
inside a question (present in the real files) are therefore harmless, and a
missing blank line between questions is only a warning.

### 7.3 Validation (`validation/issues.py`, Georgian messages)

Errors exclude the affected question (or, for file-level errors, the file):

| Area | Codes |
|---|---|
| Files | `FILE_NOT_UTF8` (line/column), `FILE_UNREADABLE`, `FILE_EMPTY`, `UNKNOWN_SUBJECT`, `MISSING_EXPLANATIONS_FILE`, `MISSING_QUESTIONS_FILE`, `DUPLICATE_FILE`, `UNEXPECTED_TEXT` |
| Numbering / IDs | `INVALID_NUMBER`, `DUPLICATE_NUMBER`, `NUMBER_ABOVE_TARGET`, `DUPLICATE_ID_IN_SELECTION` |
| Structure | `EMPTY_STEM`, `OPTION_COUNT`, `OPTION_LETTERS`, `OPTION_FORMAT`, `EMPTY_OPTION`, `NO_CORRECT_ANSWER`, `MULTIPLE_CORRECT_ANSWERS`, `MISPLACED_MARKER` |
| Explanations | `MISSING_EXPLANATION`, `ORPHAN_EXPLANATION`, `DUPLICATE_EXPLANATION`, `EXPLANATION_FORMAT`, `EXPLANATION_MARKER`, `EXPLANATION_LETTER_MISMATCH`, `EMPTY_EXPLANATION` |
| Refined master | `MASTER_FIELD_MISSING`, `MASTER_FIELD_ORDER`, `MASTER_UNEXPECTED_LINE`, `MASTER_NOT_REFINED`, `MASTER_ANSWER_MISMATCH` (letter **and** text), `MASTER_NO_RECORDS` |

Warnings never reject a question: `EXPLANATION_SENTENCES` (paired format
only — not applied to the refined academic explanation), `DUPLICATE_STEM`
(within the selection and against the database), `DUPLICATE_OPTIONS`,
`NUMBER_GAP`, `NUMBER_ORDER`, `STRAY_ASTERISK`, `MARKDOWN_REMNANT`,
`MISSING_BLANK_LINE`, `FILE_UNRECOGNIZED`, and `CONFLICT_EXISTING` (see 7.4).
No rule judges medical correctness.

### 7.4 Two-stage import and re-import policy (`services/import_service.py`)

**Stage A — dry run** parses and validates and compares with the database
without writing anything (tested byte-for-byte). Report: parsed, structurally
valid, invalid, duplicates in selection, identical existing, conflicts,
errors, warnings, eligible.

| Situation | Result |
|---|---|
| ID not in database, valid | **eligible** → revision 1 (`IMPORT`), `PENDING_REVIEW`, `IMPORTED` event |
| ID present, same fingerprint | skipped as identical |
| ID present, different fingerprint | **conflict**: never imported; existing record and reviewer edits untouched |
| ID appears twice in the selection | error, neither copy imported |

**Stage B — confirmed import**, only after the user confirms:

1. every source file is re-hashed; if any changed since the dry run, abort;
2. a verified backup is created (§10); if that fails, abort;
3. one transaction inserts questions, revisions, events and finally the batch
   row with its manifest (`INSERT … ON CONFLICT DO NOTHING`, so an ID that
   appeared after the dry run is skipped, never duplicated or overwritten);
4. revision pointers are re-checked before `COMMIT`; any failure rolls back
   everything (tested by forcing a constraint failure on the 4th question).

The UI (`ui/import_dialog.py`) follows the same three steps: select files or
a folder → validation summary and issue table (errors first) → confirmation
dialog → result (created, skipped, backup path, batch number). The sidebar and
home page then show real per-subject and per-status counts.

## 8. Editorial revision history (design)

* Revisions are append-only; `questions.current_revision_id` points to the
  latest. Saving content identical to the current revision creates nothing.
* **Restore** never rewrites history: it creates a new revision (`RESTORE`)
  copying the chosen one and recording `restored_from_revision_id`.
* **Optimistic concurrency**: a save carries the `lock_version` the editor
  loaded; `UPDATE … WHERE id=? AND lock_version=?` affecting 0 rows means
  the question changed meanwhile (second window or instance) and the user is
  asked to reload — no silent lost update.
* Every revision stores `author` (the reviewer name set for this installation —
  there is no login) and an optional note.
* History view: list of revisions with word-level diff between any two.

## 9. Approval states (design)

Status codes and Georgian labels match the VascQBank-Review web application
(implemented in `domain.review_status`).

| From | Action | To | Rule |
|---|---|---|---|
| `PENDING_REVIEW` | first edit saved / "start review" | `IN_REVIEW` | |
| `PENDING_REVIEW`, `IN_REVIEW` | approve | `APPROVED` | content must pass validation; `approved_revision_id = current_revision_id` |
| `PENDING_REVIEW`, `IN_REVIEW`, `APPROVED` | reject (note required) | `REJECTED` | question stays in the database |
| `APPROVED` | content edited | `IN_REVIEW` | approval is bound to an exact revision, so any edit revokes it |
| `APPROVED`, `REJECTED` | reopen | `IN_REVIEW` | |

Every transition writes a `review_events` row in the same transaction. Exports
read `approved_revision_id`, so only the exact approved text can ever be
exported.

## 10. Local backup and recovery

Implemented (`db/backup.py`):

* **Mechanism**: SQLite's online backup API (`Connection.backup`) — a
  consistent snapshot even in WAL mode; the live file is never copied directly.
* **When**: automatically before every migration of an existing database
  (`pre-migration-v<N>`) and before every confirmed import (`pre-import`).
  If the backup cannot be created and verified, the migration/import does not run.
* **Verification**: written to `.partial`, switched to rollback-journal mode
  (one self-contained file), reopened read-only, application id, schema
  version, `PRAGMA integrity_check` and per-table row counts compared with the
  source, then atomically renamed.
* **Where**: `<data>/backups/vascqbank-<UTC timestamp>-<reason>.sqlite3` plus a
  JSON manifest (SHA-256, size, schema version, row counts, database id).
* **Retention**: the 20 most recent automatic backups are kept; manual ones
  are never pruned.

Design (later phase): manual "create backup now" / "save backup as…",
start-up backup if the last one is older than 24 h, and **restore**: validate
the chosen backup → safety backup of the current database → close
connections → atomic replace → remove stale `-wal`/`-shm` → reopen and migrate.

## 11. Word and TXT export (design)

* **Scope**: approved questions only (all subjects or a selection), using the
  approved revision's content.
* **TXT**: exactly the source convention (§7.2) — `<CODE>_questions.txt` with
  `*` before the correct letter, `<CODE>_explanations.txt` with `N. <letter>) …`.
  UTF-8 without BOM, LF by default (CRLF optional). **Round-trip guarantee**:
  every exported pair re-imports through the same parser to identical content
  (automated test).
* **Word (.docx)** via `python-docx` (added as a dependency in that phase):
  heading per subject, numbered questions with options ა)–ე), correct-answer
  modes (answer key at the end / marked inline / questions only), explanations
  section, page numbers, A4. Georgian font set on all `w:rFonts` slots;
  default **Sylfaen** (installed on every Windows), Noto Sans Georgian optional.
  After writing, the document is reopened and its question count verified.
* Files are written atomically (implemented now: `write_utf8_text_atomic`);
  names like `VascQBank_ALI_approved_20261117-2120.docx`; a JSON manifest
  (question uids, revision ids, hashes, database_id) is written beside them
  and logged in `export_runs`.

## 12. Georgian text and fonts (implemented)

* All file I/O passes `encoding="utf-8"` explicitly — never the Windows
  locale default (cp1252). Reading is strict: invalid bytes are an error with
  position, never silently replaced.
* `normalize_text()` removes BOM, unifies line endings, applies NFC.
* Bundled fonts: **Noto Sans Georgian** (Georgian glyphs only — verified by
  inspecting its character map) plus **Noto Sans** (Latin, digits,
  punctuation such as „ “ × ⁹). Qt chooses glyphs per character from the
  family chain `Noto Sans → Noto Sans Georgian → Segoe UI → Sylfaen →
  DejaVu Sans → Arial`, so mixed text such as "EVAR და CTA" renders the same on
  Windows and Linux. Fonts are loaded from bytes via `importlib.resources`,
  which works in source checkouts, wheels and frozen builds.
* UI labels avoid `str.upper()`: for Georgian it produces Mtavruli capitals
  (U+1C90…), which many installed fonts lack.

## 13. Packaging (design)

| | Windows | Linux |
|---|---|---|
| Tool | PyInstaller, one-folder mode, `--windowed` | PyInstaller one-folder → AppDir → `appimagetool` |
| Built on | Windows (no cross-compiling) | Ubuntu 22.04 or another glibc ≥ 2.34 base (PySide6 6.12 wheels are `manylinux_2_34`); runs on newer distros such as CachyOS |
| Result | `VascQBank-Desktop\VascQBank-Desktop.exe` (zip) and optionally a per-user Inno Setup installer with Start-menu shortcut | `VascQBank-Desktop-x86_64.AppImage` (needs FUSE 2: `fuse2` on Arch; or `--appimage-extract-and-run`) |
| Extras | `.ico` from `app.svg`, version resource, AppUserModelID; code signing recommended to avoid SmartScreen warnings | `.desktop` file and icon; Wayland and X11 platform plugins included |

* `--collect-data vascqbank_desktop` bundles fonts and icon; unused Qt modules
  are excluded to keep the bundle small (the dependency is already
  `PySide6-Essentials`, not the full `PySide6` with WebEngine).
* One-folder is preferred over one-file: faster start-up and fewer antivirus
  false positives.
* The data folder lives outside the bundle, so replacing the application never
  touches data; migrations (with automatic pre-migration backup) upgrade it.
* CI (GitHub Actions, Windows + Ubuntu runners) builds both artefacts and runs
  `--self-check` and a timed GUI start on each before publishing.

## 14. Testing strategy

| Level | Tooling | Status |
|---|---|---|
| Domain, validation, text I/O | pytest | implemented |
| Database: migrations, rollback, ownership, STRICT, pragmas | pytest with temp databases | implemented |
| Architecture: layer rules, no networking, UTF-8 sources | `ast` scan | implemented |
| Entry point: `--version`, `--self-check`, real GUI start/exit | subprocess | implemented |
| GUI widgets and fonts | pytest-qt, offscreen platform | implemented |
| Parsers (both formats), validation catalogue, discovery | pytest, synthetic Georgian data generated by `tests/synthetic.py` (never real questions) | implemented |
| Import service: dry run, commit, conflicts, rollback, races, triggers | pytest with temp databases | implemented |
| Backups: verification, corruption, retention, pre-migration | pytest | implemented |
| Import dialog | pytest-qt, offscreen | implemented |
| 10,000-record performance | `perf` marker, synthetic data | implemented |
| Revision/approval services, round-trip export | pytest | later phases |
| Packaged builds | CI smoke run of the EXE/AppImage | packaging phase |

Offscreen GUI tests verify widget structure and wiring; they are not a
substitute for a visual check on a real Windows and Linux desktop.
