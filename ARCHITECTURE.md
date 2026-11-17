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

Phase 1 schema (migration 1): `schema_migrations`, `app_metadata`. Question
tables arrive as migration 2.

## 5. Data model (design — Phase 2, migration 2)

### 5.1 Identity

* `question_uid` = `<SUBJECT>-<NNNN>` (e.g. `ALI-0001`), from subject code and
  original source number. Immutable, unique, identical to the VascQBank-Review
  web application's `external_id`, so the same ID means the same question in
  both tools. Implemented now as `domain.question_id.QuestionId`.
* `source_fingerprint` (SHA-256 of normalised imported content) detects
  upstream changes; it is **never** used as identity.

### 5.2 Tables

```sql
subjects        (code PK, name_ka, target_count, sort_order UNIQUE)

import_batches  (id PK, source_kind CHECK IN ('PAIRED_TXT','REFINED_MASTER'),
                 source_label, source_sha256, started_at, finished_at,
                 added_count, unchanged_count, conflict_count, report_json)

questions       (id PK,
                 question_uid UNIQUE NOT NULL,
                 subject_code NOT NULL → subjects,
                 source_number NOT NULL CHECK (> 0),
                 status NOT NULL CHECK IN ('PENDING_REVIEW','IN_REVIEW','APPROVED','REJECTED'),
                 current_revision_id  → question_revisions (deferred FK),
                 approved_revision_id → question_revisions (deferred FK),
                 import_batch_id NOT NULL → import_batches,
                 source_fingerprint NOT NULL,
                 lock_version NOT NULL DEFAULT 0,
                 created_at, modified_at, approved_at,
                 UNIQUE (subject_code, source_number),
                 CHECK (question_uid = subject_code || '-' || printf('%04d', source_number)),
                 CHECK ((status = 'APPROVED') = (approved_revision_id IS NOT NULL)))

question_revisions (id PK, question_id → questions, revision_no,
                 stem, option_a … option_e        -- ა ბ გ დ ე
                 correct_letter CHECK IN ('ა','ბ','გ','დ','ე'),
                 explanation,
                 origin CHECK IN ('IMPORT','EDIT','RESTORE'),
                 restored_from_revision_id → question_revisions,
                 author, note, content_hash, created_at,
                 UNIQUE (question_id, revision_no))

review_events   (id PK, question_id → questions,
                 action CHECK IN ('IMPORTED','EDITED','START_REVIEW','APPROVE',
                                  'REJECT','REOPEN','RESTORE','COMMENT'),
                 from_status, to_status, revision_id, actor, note, created_at)

export_runs     (id PK, format CHECK IN ('DOCX','TXT'), scope_json,
                 question_count, files_json, manifest_sha256, created_at)

question_search  -- FTS5 virtual table (stem, options, explanation), trigram tokenizer
```

Enforcement in the database, not only in Python:

* triggers make `question_revisions` and `review_events` append-only
  (`RAISE(ABORT)` on UPDATE/DELETE);
* a trigger forbids `DELETE` on `questions` (rejection is a status, not a deletion);
* `questions.import_batch_id NOT NULL` means every question traces back to an
  import; the import service is the only code that inserts questions, and an
  architecture test will forbid `INSERT INTO questions` anywhere else.

Indexes: `questions(subject_code, status)`, `questions(status)`,
`question_revisions(question_id, revision_no DESC)`, `review_events(question_id, created_at)`.

### 5.3 Text and time

* All text is stored after `normalize_text()` (no BOM, LF, NFC). Edits are
  normalised the same way, so hashes and comparisons are stable.
* Timestamps: UTC ISO-8601 with offset (`2026-11-17T17:20:00+00:00`), shown in
  the computer's local time zone (Asia/Tbilisi for the primary user).

## 6. Performance at 10,000 records (design)

Size estimate: Georgian letters take 3 bytes in UTF-8; a question with five
options and an explanation is ≈1.5–2.5 KB. 10,000 current revisions ≈ 25 MB;
with several revisions each, the database stays well under 200 MB. This fits
comfortably in SQLite and in memory.

| Operation | Approach | Target |
|---|---|---|
| Start-up | migrations + `quick_check`; no question data loaded until needed | < 2 s |
| Subject list / counts | one `GROUP BY subject_code, status` on indexed columns | < 50 ms |
| Question list (one subject, ≤1,500 rows) | summary rows only (uid, number, status, 160-char preview) in a `QAbstractTableModel` | < 100 ms |
| All 10,000 rows | same summary model with `fetchMore` batches of 500 | < 300 ms |
| Full-text search | FTS5 `trigram` index — substring matching suits Georgian inflection (`აორტ` finds `აორტის`, `აორტაში`); queries shorter than 3 characters fall back to `LIKE` | < 150 ms |
| Save an edit | one transaction: revision + question update + event + FTS row | < 50 ms |
| Import 10,000 | parse and validate in memory, then one transaction | < 10 s |
| DOCX export 10,000 | background worker with progress | < 60 s |

Threading: a `sqlite3` connection is never shared between threads. Long jobs
(import, export, backup, integrity check) run in a `QThreadPool` worker with
its own connection and report progress through Qt signals; WAL lets the UI keep
reading while they write. A synthetic 10,000-question dataset (generated in
tests, never real content) will back a `perf`-marked benchmark suite.

FTS availability: Phase 1's `--self-check` already reports whether the SQLite
build supports FTS5 trigram (`fts5_trigram: True` in the test environment). If
a build lacks it, search falls back to `LIKE` over the summary data.

## 7. Question import and validation (design)

### 7.1 Sources

Two adapters, one internal record format (`ImportedQuestion`: subject, number,
stem, five options, correct letter, explanation, provenance):

1. **Paired TXT** — `<CODE>_questions.txt` + `<CODE>_explanations.txt`, the
   VascQBank-10K convention (AGENTS.md §6–8), required by this project.
2. **Refined master** — `ACADEMIC_REVIEW/REFINED_MASTER_QBANK.txt`, which the
   VascQBank-Review web application uses as its sole source. Supporting it is
   optional; **which source(s) the desktop app accepts is a Phase 2 decision**.

Its `STATUS: APPROVED` field reflects the automated editorial process and
**never** sets the reviewer's approval: every imported question starts as
`PENDING_REVIEW`.

### 7.2 Paired TXT grammar

```
questions:     N. <stem, may continue on following lines>
               ა) <option>
               ბ) <option>
               *გ) <option>          ← ASCII '*' immediately before the letter, exactly once
               დ) <option>
               ე) <option>
               <blank line>
explanations:  N. გ) <text, may continue on following lines>
               <blank line>
```

### 7.3 Validation report

Every import is a **dry run first**: the user sees a report, then confirms.

Errors (the affected subject is not imported):

| Code | Rule |
|---|---|
| E01 | file is not valid UTF-8 (line and column reported — implemented now in `read_utf8_text`) |
| E02 | file name does not match `<CODE>_questions.txt` / `<CODE>_explanations.txt` for one of the 12 codes |
| E03 | questions file without its explanations file, or vice versa |
| E04 | numbering does not start at 1, has gaps, duplicates or is out of order |
| E05 | not exactly five options, or letters not ა ბ გ დ ე in order |
| E06 | zero or several `*`, or `*` not directly before the letter |
| E07 | empty stem, option or explanation |
| E08 | question without explanation, or explanation without question |
| E09 | explanation letter differs from the starred option |
| E10 | `*` inside the explanations file |
| E11 | number exceeds the subject's target count |

Warnings (imported, flagged for the reviewer): explanation not exactly two
sentences (AGENTS.md rule); identical options within a question; duplicate
stems; Markdown/emoji remnants; Latin or Cyrillic look-alike letters inside
Georgian words; trailing whitespace normalised.

### 7.4 Re-import policy (insert-only)

| Situation | Result |
|---|---|
| `question_uid` not in database | inserted: revision 1 (`IMPORT`), status `PENDING_REVIEW` |
| present, same fingerprint | skipped as unchanged |
| present, different fingerprint | **not overwritten**; listed as "changed upstream" so reviewer edits are never lost; adopting the new text is an explicit, later reviewer action |

Each import is one transaction recorded in `import_batches` with its full
report; the database is backed up first (§10).

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

## 10. Local backup and recovery (design)

* **Mechanism**: SQLite's online backup API (`Connection.backup`), which
  produces a consistent snapshot even with WAL and open connections. The live
  database file is never copied directly.
* **When**: automatically at start-up if the last backup is older than 24 h,
  before every migration, before every import, before every restore; manually
  from the menu.
* **Where**: `<data>/backups/vascqbank-YYYYMMDD-HHMMSS-<reason>.sqlite3` plus a
  JSON manifest (database_id, schema version, counts per status, SHA-256).
  "Save backup as…" copies one to a chosen location (e.g. USB drive).
* **Verification**: each backup is reopened read-only and checked
  (application_id, schema version, `PRAGMA integrity_check`, counts).
* **Retention**: manual backups kept forever; the 20 most recent automatic ones kept.
* **Restore**: validate the chosen backup (ours, supported schema, integrity ok)
  → safety backup of the current database → close connections → copy to a temp
  file and atomically replace → remove stale `-wal`/`-shm` → reopen and migrate
  if the backup is older.
* Single-question recovery uses revision history (§8); whole-database recovery
  uses backups. On exit the WAL is checkpointed.

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
| Parsers, import report, round-trip export | pytest with synthetic Georgian fixtures in `tests/fixtures/` (never real questions) | Phase 2+ |
| Revision/approval rules, triggers | pytest | Phase 2+ |
| 10,000-record performance | `perf` marker, synthetic data | Phase 2–3 |
| Packaged builds | CI smoke run of the EXE/AppImage | packaging phase |

Offscreen GUI tests verify widget structure and wiring; they are not a
substitute for a visual check on a real Windows and Linux desktop.
