# VascQBank Review Desktop — Architecture

This document describes the architecture implemented in Phases 1–4 and the design
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
| `domain` | subjects, answer letters ა–ე, review statuses and the edit transitions, question IDs, edit validation, word-level text diff | Qt, sqlite3, db, services, ui, app, importexport |
| `validation` | text normalisation; later structural question checks | Qt, sqlite3, upper layers |
| `importexport` | strict UTF-8 I/O; later TXT/master parsers, TXT/DOCX writers | Qt, ui, app |
| `db` | connection policy, ownership check, migrations, repositories (questions, catalog, drafts, edits) | Qt, services, ui, app |
| `services` | use cases (storage init, import, catalog/search, settings, session, backups, **drafts, edits**; later approve, export) | Qt, ui, app |
| `ui` | widgets, Georgian strings, fonts, theme | sqlite3, db, app |
| `app` | entry point and composition root | — |

`tests/test_architecture.py` parses every module's imports with `ast` and fails
the build if a rule above is broken. It also fails on any networking import
(`socket`, `urllib`, `http`, `QtNetwork`, …), which keeps the application
provably offline.

The UI never receives a `sqlite3` connection: services return immutable
dataclasses (e.g. `StorageStatus`, `QuestionDetail`). This keeps widgets testable
without a database and keeps SQL out of the UI. A plain enum that is part of a
service result (e.g. `BackupProblem`) is re-exported by the service module, so
the UI still never imports the `db` package — `tests/test_architecture.py`
enforces it, and caught exactly such a slip while Phase 3 was written.

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
6. The main window then (Phase 3) restores its geometry if it is still reachable on
   a connected screen, reads the real counts, restores the working session
   (§15.5, read-only) and starts building/refreshing the derived search index on a
   background thread (§15.2).

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
schema (§5). Migration 3 (Phase 4): `drafts` and `draft_discards` (§5.4, §17).
Existing databases are backed up and then migrated; version 3 only *adds*
tables, so no existing row is touched.

## 5. Data model (implemented — migration 2 `db/schema_v2.py`, migration 3 `db/schema_v3.py`)

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

**Full-text search is not part of this schema.** The Phase 1 plan was an FTS5
table added by a migration. Phase 3 deliberately keeps it *out of* the
authoritative database: a derived, disposable index file lives beside it (§15.2).
Reason: a bundled seed database, a backup and every write would otherwise depend
on an optional SQLite extension (triggers calling FTS5 make *all* writes fail on a
build without it, and backup verification counts every table). The existing
indexes already give index-ordered plans for browsing (§15.1). Phase 3 therefore
needed no migration; **migration 3 is the Phase 4 draft schema** (§5.4).

### 5.3 Text and time

* All imported text passes `normalize_text()` (no BOM, LF, NFC). NFC never
  alters Georgian letters; Georgian text is stored byte-identical (tested).
* Timestamps: UTC ISO-8601 with offset (`2026-11-17T17:20:00+00:00`), shown in
  the computer's local time zone.

### 5.4 Drafts (migration 3, `db/schema_v3.py`)

| Table | Purpose |
|---|---|
| `drafts` | At most one row per question (`question_id UNIQUE`): the text being typed (stem, options A–E, correct letter, explanation, optional note), `base_revision_id` and `base_lock_version` it was started from, `reviewer_name`, `write_count`, `created_at`, `saved_at`. |
| `draft_discards` | Append-only archive (update/delete trigger-protected) of drafts a reviewer *chose* to throw away, with who, when and why (`DISCARDED` / `REVERTED`). |

* Drafts are **working copies**. Nothing in the draft code path writes to `questions`,
  `question_revisions` or `review_events`; a draft cannot change a status, a timestamp, a
  lock version or any saved text. Revisions stay append-only and the original imported
  content immutable (§5.2).
* Unlike a revision, draft text may be incomplete (an answer still empty while typing), so
  its columns are not required to be non-empty; the CHECKs that remain are the valid
  correct letter and a non-blank reviewer name.
* Triggers force `base_revision_id` to belong to the same question; every foreign-key
  column is indexed (tested for every table).

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

Dry runs and imports run on a background worker-pool thread (`ui/workers.py`,
§15.3) with their own SQLite connection, so the window stays responsive.

### Phase 3 — browsing and search (measured)

`tests/test_browse_performance.py`, same 10,000 synthetic questions (all 12 subjects at
their target counts; a spread of statuses), Linux, Python 3.13, SQLite 3.53, offscreen Qt.
Medians of repeated runs; two complete runs agreed within ≈15 %.

| Operation | Measured | Test budget |
|---|---:|---:|
| Catalog load: all 10,000 ordered ids + first page of 100 rows | 4 ms | < 250 ms |
| Whole catalog view ready (widgets, ids, first page, counts) | 59 ms | < 2 s |
| Subject switch (ids + first page; median / worst of 12) | 1.7 / 2.0 ms | < 250 ms |
| Subject switch in the GUI (list reloaded) | 1.7 ms | < 300 ms |
| Status filter · sort by status (10,000 ids) | 1.5 · 9.5 ms | < 250 · 400 ms |
| Open a question (service read) · in the GUI incl. detail rebuild | 0.4 · 1.4 ms | < 20 · 100 ms |
| Jump to a far question by ID (GUI) · "next question" (GUI) | 6.6 · 3.2 ms | < 500 · 100 ms |
| FTS5 search: selective term · by ID · all 10,000 hits | 1.8 · 1.4 · 11.8 ms | < 100 · 100 · 500 ms |
| FTS5 search + subject + status filters | 5.4 ms | < 250 ms |
| LIKE fallback: full scan · short (< 3 characters) term | 29 · 21 ms | < 1 s |
| Preview rows for one page of search results (100) | 2.2 ms | < 250 ms |
| Search in the GUI until the list is updated (background thread) | 27 ms | < 2 s |
| Derived index: first build (10,000) · refresh with nothing to do | 410 · 8.7 ms | < 8 s |
| Python memory while paging through all 10,000 rows (bounded cache) | 0.96 MB | < 20 MB |
| Process RSS: window + catalog + 10,000 ids | 102 → 115 MB | +< 250 MB |
| Database file · derived search index | 33.7 MB · 19.9 MB | — |

Query-plan tests assert index-ordered listing without a temporary sort, primary-key-only
lookups for a page and for a detail, and that a page of 100 rows costs exactly one
`SELECT` and reads no revision history (no N+1 pattern).

### Phase 4 — editing in the 10,000-question catalog (measured)

`tests/test_editing_performance.py`, the same synthetic 10,000 questions, offscreen Qt,
`synchronous=FULL` on the editing connections (tmpfs-class disk, so `fsync` is cheap here —
a spinning or network disk will be slower).

| Operation | Measured | Test budget |
|---|---:|---:|
| Open the editor on a question deep in the catalog (detail read + draft lookup + widgets) | 4–7 ms | < 500 ms |
| One draft write (own connection, one transaction) | 2.4–2.8 ms | < 200 ms |
| Explicit save incl. list refresh and detail reload (GUI) | 55–64 ms | < 500 ms |
| 300 drafts: list all · ids only · refresh markers/banner · open the list dialog | 1.9 · 0.5 · 1.2 · 27 ms | < 1 s · 100 · 500 · 2000 ms |
| Worst event-loop gap while typing 60 changes of a 4,000-character stem with autosave running | 30 ms | < 400 ms |

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

## 8. Editorial revision history (implemented — Phase 4; restore deferred)

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
* History view (implemented, `ui/revision_history_view.py`): read-only list of revisions
  (number, editor, time, origin, note, status change) with the full text of the selected
  one and a word-level comparison with the current version (`domain/textdiff.py`). Restoring
  an old revision stays deferred to a later phase; the format above is what it will use.
* The save itself is §17.4.

## 9. Approval states (edit transitions implemented — Phase 4; approve/reject/reopen design)

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

**Implemented in Phase 4** (`domain.review_status.status_after_edit`): saving an edit moves
`PENDING_REVIEW → IN_REVIEW`, keeps `IN_REVIEW`, keeps `REJECTED` (leaving it is the explicit
*reopen* of a later phase) and moves `APPROVED → IN_REVIEW`. The last one **revokes the
approval**: the `questions` CHECK requires `approved_revision_id` and `approved_at` to be NULL
unless the status is `APPROVED`, so both are cleared — the approved revision itself and its
`APPROVE` event stay in the history, and the revoked revision number is written into the
`EDITED` event's note. The editor warns before this happens and asks for confirmation.
*Typing never changes a status*: only the committed save does. Approve, reject and reopen
are not implemented yet.

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

Implemented in Phase 3 (§15.6): manual "create backup now" (reason `manual`, never
pruned), and the backup list with creation time, type, size and a re-verification
(size, SHA-256, `integrity_check`, row counts against the manifest).

Design (later phase): "save backup as…", start-up backup if the last one is older
than 24 h, and **restore**: validate
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
| Catalog, search (FTS5 + LIKE parity), session, settings, backups services | pytest, synthetic data (`tests/bank.py` seeds through the real repository) | implemented |
| Catalog page, detail rendering, main-window integration, session restore | pytest-qt, offscreen | implemented |
| 10,000-question browse/search benchmark, query plans, memory | `perf` marker | implemented |
| Edit service: every field, Unicode, status rules, conflicts, rollback, disk-full, locks, threads | pytest with temp databases | implemented |
| Crash recovery: SIGKILL at exact SQL statements; whole GUI killed mid-edit | isolated child processes, temp databases only | implemented |
| Autosaver (single flight, stale answers, flush, retry, close) · editor, history, drafts, main window | pytest-qt, offscreen | implemented |
| Editing in a 10,000-question catalog | `perf` marker | implemented |
| Approval services, round-trip export | pytest | later phases |
| Packaged builds | CI smoke run of the EXE/AppImage | packaging phase |

Offscreen GUI tests verify widget structure and wiring; they are not a
substitute for a visual check on a real Windows and Linux desktop.

## 15. Browsing, search, reviewer profile and session (implemented — Phase 3)

### 15.1 Catalog and detail

* **Ids first, text later.** A listing resolves only the ordered question ids
  (`db/catalog_repository.py::list_ids`, ≈2 ms for all 10,000; index-ordered, no
  sort, because `UNIQUE (subject_code, source_number)` already orders each subject).
  `ui/question_list_model.py` is a random-access `QAbstractListModel` over those
  ids; a page of 100 rows is fetched with **one** `IN (…)` query the first time a row
  is needed (primary-key lookups into `questions` and the current revision) and at
  most 16 pages are cached. Jumping to row 9,000 loads one page. No query reads
  revision history; `questions.current_revision_id` is followed directly.
* **Identity is independent of ordering.** Filters (subject, status, search text)
  and sort orders (number, status, last modified) only choose *which* ids are listed
  and in what order; rows, the detail panel and navigation work on the immutable
  `question_uid` / database id. A test opens the same question under every sort and
  filter.
* **Read-only by construction.** All catalog reads use `PRAGMA query_only = ON`
  connections and `db/catalog_repository.py` contains no write statement (tested);
  a test snapshots every authoritative table before and after browsing, searching,
  jumping, restoring a session and changing the reviewer name.
* **Detail panel** (`ui/question_detail_view.py`): all clinical text is HTML-escaped
  and shown with `white-space: pre-wrap`, so `PaO2 < 60 mmHg & <b>` displays
  literally and runs of spaces and line breaks are kept (tested by round-tripping
  through `QTextDocument`). The correct option is marked by colour *and* the words
  „სწორი პასუხი“. Labels are selectable/copyable; a button copies the whole question.
  No edit or approval control exists.

### 15.2 Search

* **Definition** (`domain/search.py`): the text is split on whitespace into terms; a
  question matches if **every** term occurs (substring, so word fragments work) in
  its *current* revision text — ID, stem, options ა–ე, explanation — joined by newlines.
  FTS5 operators and LIKE wildcards in the input are literal (every FTS term is
  quoted, every LIKE term escaped).
* **FTS5 + trigram** (`db/search_index.py`): a separate derived file
  `database/search-index.sqlite3` holds one row per question (`rowid = questions.id`,
  `tokenize='trigram'`) plus `doc_state(question_id, revision_id)` and `meta`. It is
  `ATTACH`ed to the read-only connection, so one SQL statement joins index hits with
  the subject/status filter and the catalog ordering. Builds take ≈0.3 s per 10,000
  questions; the file is ≈2.3× the text size (≈20 MB for the synthetic bank).
* **Freshness is cheap and exact.** `(database_id, count, sum(current_revision_id))`
  is compared on every search; revisions are append-only and the current revision only
  moves forward, so any edit raises the sum. A stale index is reconciled per question
  (`doc_state`): ≤300 differences (an edit or two) are synchronised *inside* the
  search that notices them; more (first build, a big import) are left to the
  background warm-up and that search uses LIKE. A different `database_id` (database
  replaced) triggers a rebuild. Phase 4 therefore needs no index hook, but may call
  `CatalogService.ensure_search_index()` in the background after saving to avoid the
  first-search wait.
* **LIKE fallback** (`catalog_repository.search_ids_like`): used when FTS5 trigram is
  unavailable, when any term has fewer than three characters, when the index is
  missing, stale beyond the inline limit, damaged or locked, and when an FTS query
  raises. ≈20–28 ms for 10,000 questions. A test compares both back ends on many
  queries (identical id lists) and simulates missing FTS5 support, a damaged index
  file, another database's index and a failing FTS query.
* **Never on the UI thread.** The catalog view debounces typing (250 ms), submits the
  search to a `LatestOnlyRunner` (one call at a time, only the newest waiting request is
  kept) and discards any result whose generation is no longer current — including a
  late result that arrives after the reviewer cleared the box (a test fails if that
  guard is removed). Clearing the box restores the previous catalog at once.
* **Previews**: single-line excerpts around the first match (field named: question /
  answer ა–ე / explanation / ID), match ranges highlighted by the list delegate.

### 15.3 Threading model

`ui/workers.py`: `BackgroundJob` runs one call on Qt's `QThreadPool` through a
`QRunnable`; results return as queued signals of a UI-thread `QObject`. `LatestOnlyRunner`
serialises calls and keeps only the newest waiting request. Services open their own
short-lived SQLite connection per call (0.3 ms), so any thread may call them.

*History.* An earlier version of this module used a `QThread` per job with a task object
moved into it. Under pytest-qt it caused rare heap corruption (glibc `corrupted
double-linked list`, about one run in nine of the catalog GUI tests). The root cause inside
that thread/object lifecycle was not pinned to a single line, but garbage collection, FTS5,
list painting and the worker classes in isolation were each ruled out by experiment, and
running the search on the UI thread never crashed. After switching to `QThreadPool` there
were no crashes in 8 complete suite runs and 80 runs of the GUI test files.

### 15.4 Reviewer profile and settings

`services/settings_service.py`: `settings.json` in the data directory, written
atomically (temporary file in the same directory → `fsync` → `os.replace`, retried on
Windows sharing violations). Loading never fails: an unreadable or corrupt file is moved
to `settings.json.corrupt-<time>` and defaults are used; every value is validated
individually (known subject and status, canonical question id, bounded geometry);
unknown keys written by a newer version survive a save. The reviewer name
(`domain/reviewer.py`: NFC, collapsed whitespace, ≤80 characters, no control/format
characters) is available to future services through `ReviewerProfile.require_name()`
(raises `ReviewerNotSetError` when unset). It is never written to the database in
this phase, so history cannot change when the name changes (tested).

### 15.5 Session restore

Saved: subject, status filter, last opened question uid, window geometry (and the
reviewer name). Writes are debounced (500 ms) and flushed on close, so a crash loses at
most the last half second. `services/session_service.py::restore_context` decides what to
show, without writing anything:

1. unknown or now-empty subject → all subjects;
2. the remembered question still matches the filter → open it;
3. it exists but no longer matches (e.g. approved meanwhile) → the **closest following**
   question that matches, else the closest preceding one; the reviewer is told why;
4. the filter matches nothing → the status filter is cleared and the question shown;
5. the question no longer exists → the first question of the filter, with a notice.

Window geometry is restored only if enough of it overlaps a connected screen
(`ui/window_state.py`), otherwise the default size is used.

### 15.6 Backups UI

`services/backup_service.py` + `ui/backup_view.py`: create a verified backup on a worker
thread, list backups newest first, and verify them in the background (manifest size and
SHA-256, read-only open, `integrity_check`, per-table counts). A damaged backup is shown as
damaged with the reason. There is **no restore action** (§10).

## 16. Seed database distribution (design — documentation only in Phase 3)

The finished bank will ship *inside* the application as a validated SQLite database.

**Building the seed (developer side, packaging phase).**
1. Source of truth: the 12 subject-specific paired TXT files. The refined master stays a
   separately supported import source; it is not the seed's source.
2. A build tool (`tools/build_seed.py`, later) runs the **same import pipeline**
   (`dry_run` + `commit_import`) into a fresh database — there is no second code path, so
   strict UTF-8, NFC, structural validation, fingerprints and every constraint apply.
3. Acceptance gates: the expected count per subject, every question `PENDING_REVIEW`, no
   validation errors or duplicate IDs, `PRAGMA integrity_check` ok, our `application_id`,
   the current schema version. A `seed-manifest.json` records the seed version, per-subject
   counts, SHA-256 of every source file and of the seed database, and the build date.
4. Seed and manifest are bundled as read-only package data.

**First launch.** If no database exists in the user-data directory: verify the bundled
seed (hash and counts against the manifest, `integrity_check`), copy it to a `.partial`
file in the **writable user-data directory**, verify the copy, rename atomically, give it a
fresh `database_id` (the seed's id is kept as `seed_database_id`, with `seed_version` and
`seeded_at`, in `app_metadata`) and run the normal migrations (with their automatic
backup). If the database is missing but backups exist the application does **not** silently
re-seed; it offers the newest verified backup or an explicit fresh start. All edits affect
the writable copy only; the bundled seed is never opened read-write.

**Updates.** An update replaces application files (and its bundled seed) only; the data
directory is outside the application folder, so reviewer work is never overwritten. A newer
seed is **never merged automatically**: a "compare with bundled seed" action would reuse the
import dry-run — new IDs are offered as pending, identical ones skipped, and a differing
fingerprint is a *conflict* that is listed and never applied (§7.4). Schema changes ship as
forward-only migrations on the user's database, not by re-copying the seed.

**Invariants.** The application never generates or invents questions; the seed holds only
what the 12 source files hold; `original_content_json` keeps the seed's content immutable.

## 17. Editor, autosave and crash recovery (implemented — Phase 4)

### 17.1 The editor

The detail area of the catalog page is a `QStackedWidget` with three modes owned by
`ui/catalog_editing.py::EditingController`: the read-only detail view (the default, unchanged),
the **editor** (`ui/question_editor_view.py`) and the read-only **history** (§8). Opening a
question never starts editing and never writes.

* Fields: stem, answers ა–ე, the correct answer (one radio per answer; the chosen answer's card
  is highlighted and the radios are exclusive — exactly one is always selected), explanation
  and an optional short note that is stored with the review event.
* Text fields are plain text (`ui/text_edit.py`): no rich-text paste, Enter makes a new line,
  Tab moves to the next field, height grows with the content. Georgian text is stored exactly
  as typed after the same normalisation as imported text (NFC, LF line ends); the outer
  whitespace of a saved field is trimmed.
* Keyboard: **Ctrl+E** edit · **Ctrl+S** save · **Esc** cancel · **Ctrl+1 … Ctrl+5** correct
  answer ა … ე · **Ctrl+H** history · **Tab** next field.
* The footer always shows one state, in Georgian: *no change · changing — draft will be saved
  soon · draft saving · draft saved at HH:MM:SS · saving · saved as revision N · error (the
  text is kept) · conflict*. "Saved" is shown only after the SQLite transaction has committed.
* Editing an `APPROVED` question shows a warning at the top and asks for confirmation when the
  reviewer saves (§9). Without a chosen reviewer name the editor does not open and the
  reviewer page is shown instead (`ReviewerProfile.require_name()`).
* **Cancel** with unsaved changes asks: keep the draft and leave · discard the changes ·
  continue editing. **ტექსტის დაბრუნება** (revert) puts the saved text back after
  confirmation, still in edit mode.

### 17.2 Draft lifecycle

```
 typing ──(≈1 s pause)──▶ drafts row (UPSERT, one short transaction)        "draft saved"
   │                           │
   │  navigate / filter / subject / page / leave edit mode / close
   │  ───────────▶ synchronous flush of the newest text first
   │
 Ctrl+S ─▶ validate ─▶ ONE transaction: new revision + questions row + review event
                       + delete the draft only if it holds exactly the saved text
 Cancel ─▶ keep (draft stays) │ discard (draft → draft_discards, then deleted) │ continue
 leave with text equal to the saved text ─▶ the pointless draft is removed (drop_if_identical)
```

A draft is removed in exactly three ways, never implicitly: (a) the same transaction that
commits a revision of **identical** text; (b) an explicit, confirmed discard, which first
archives the text in the append-only `draft_discards`; (c) leaving the editor with text equal
to the saved text (nothing is lost, there is nothing to keep). Filtering a question out of the
list, switching subject, closing or restarting the program never deletes a draft. One draft
exists per question and it stores the revision and `lock_version` it was started from, which is
how a draft written on an older revision is recognised (§17.5).

### 17.3 Autosave: guarantees and limits (`ui/autosave.py`, `services/draft_service.py`)

Guarantees — each one has a test:

* **"Saved" means committed.** The state changes to *draft saved* only after
  `DraftService.save_draft` returned, i.e. after `COMMIT`. A pending or failed write keeps the
  state at *changing* or *error*.
* **No revision per keystroke.** Autosave only writes `drafts`; revisions are created by an
  explicit save.
* **One write at a time, newest text wins.** A single background write is in flight; edits made
  meanwhile are coalesced into one follow-up write of the newest text. All draft writes also pass
  through one lock in `DraftService`, and **every call opens its own SQLite connection** — no
  connection is shared between threads.
* **Immediate persistence when the context changes.** Navigating to another question, the
  previous/next buttons, jumping to an ID, changing subject/status/sort/search, leaving the
  page, leaving edit mode and closing the window all call a *synchronous flush*: it waits for
  the write in flight, then writes the newest pending text on the calling thread and reports
  whether it is confirmed. A termination request from the operating system (`SIGTERM`,
  `SIGINT`, `SIGHUP`: `kill`, a systemd stop, a closing terminal) closes the window through the
  same path (`app/main.py::install_signal_shutdown`); `SIGKILL` and power loss cannot be caught.
* **Failure never discards text.** If a write fails (disk full, database locked or read-only,
  I/O error) the text stays in the editor, the footer shows a Georgian error explaining the
  cause and that the text is kept, and the write is retried every few seconds. A flush that
  cannot be confirmed makes the application ask before navigating away or closing; the default
  answer keeps the text, and the window stays open.
* **Stale answers are ignored.** Every snapshot has a session id and a sequence number; the
  outcome of a write that belongs to an earlier editing session, or to text that has since been
  replaced, cannot change the state of the current one.
* **Durable commits.** The editing connections use `synchronous=FULL` (§4.1) so a committed
  draft or revision also survives a power cut, not only a crash of the program.

Limits — stated plainly:

* Text typed during the last pause (≈1 s) and the write itself exists only in memory. If the
  program is killed, or the computer loses power, in that window **those last keystrokes are
  lost**; what is on disk is then exactly the previous confirmed draft (tested by killing a
  child process in the middle of a write). Only the whole editing window being closed
  normally, or a context change, flushes immediately.
* If the writer cannot ever succeed (disk full, read-only disk) the text stays only in memory
  until space is available; the application says so and refuses to close silently.
* Nothing protects against physical disk failure, file-system corruption or deleting the data
  folder; backups (§10) are the protection for that and are the reviewer's responsibility.
  The application does not promise zero data loss under hardware failure.

### 17.4 The authoritative save (`services/edit_service.py`, `db/edit_repository.py`)

`EditService.save(question_id, expected_lock_version, content, note)`:

1. validate (`domain/editing.validate_content`: no empty field, no control characters, ≤ 20,000
   characters per field, note ≤ 500) and require the reviewer name — nothing is opened or
   written if either fails;
2. one `BEGIN IMMEDIATE` transaction:
   1. read the question; **if `lock_version` differs from the one the editor loaded → rollback and
      `EditConflict`** (no write happened);
   2. if the normalised text equals the current revision → no revision, no event; an identical
      draft is deleted in the same transaction;
   3. insert the new `question_revisions` row (`origin = EDIT`, contiguous number, content hash);
   4. update `questions`: `current_revision_id`, status per §9, `lock_version + 1`, `modified_at`
      (guarded by `AND lock_version = ?`);
   5. append the `EDITED` `review_events` row (actor = reviewer; the note, the revoked approval
      and a conscious rebase are recorded in it);
   6. delete the draft **only if** it holds the text just saved;
   7. `COMMIT`; any exception → `ROLLBACK`, so either all of 3–6 happened or none.
3. errors become `EditConflict`, `ValidationFailed`, `ReviewerRequired`, `EditTargetMissing` or
   `StorageFailure` (disk full / locked / read-only / I/O), each with a Georgian message in the UI.

On a conflict (the question was saved from another window or instance after the editor loaded
it) the editor keeps the typed text and offers: *compare with the new version* · *keep as a
draft and load the new version* · *save my text as a new revision on top* (the other revision
stays in the history and the event records `rebased_from`). Nothing is overwritten silently.
The same check protects a draft recovered after the question changed.

### 17.5 Crash recovery procedure

There is no special "recovery mode": a draft is ordinary committed data, so after any
interruption — crash, kill, power loss, a failed update — the next start simply finds it.

1. Startup runs the migrations (an automatic backup is taken first when one is applied) and
   restores the previous subject, filter, **last opened question** and the reviewer name
   (§15.5). Nothing is edited or restored automatically.
2. If any drafts exist, the catalog shows a banner *დაუსრულებელი მონახაზი: N* with *მონახაზების
   ნახვა*, the status bar and the home page show the count, and each draft's row in the list is
   marked *✎ მონახაზი*.
3. When the question that was open is shown, a bar above it says that it has an unfinished draft
   (who, when) and offers **აღდგენა რედაქტორში** (restore into the editor), **შედარება**
   (compare with the saved text) and **გაუქმება** (discard, with confirmation; the text is
   archived). A draft based on an older revision says so (*დაფუძნებულია ძველ რედაქციაზე
   (1 → 2)*); restoring it never overwrites the newer saved text — saving it hits the conflict
   path of §17.4.
4. The draft list (menu or banner) shows every draft — question, reviewer, time, outdated
   flag — and can open, compare or discard each. A draft is never deleted because its
   question is filtered out, because another question was opened, or because the program was
   restarted.

Verified by tests that kill (`SIGKILL`) *isolated child processes working on temporary
databases* — at an exact SQL statement of a draft write, of a save and of a discard, and the
entire GUI while a draft is confirmed, while typing is unconfirmed and in the middle of a
draft write — and then reopen the database: it is sound (`integrity_check`, `foreign_key_check`),
holds exactly the last committed state and offers the draft. No test or tool terminates a
process that holds real data.

### 17.6 Threading review (`ui/workers.py`, `ui/autosave.py`)

Reviewed again after the heap corruption of Phase 3 (§15.3):

* A `BackgroundJob`/`QRunnable` is kept in a module-level set until its run has finished, so
  Qt's pool never holds a pointer to a collected object; results return as queued signals of
  a UI-thread `QObject`.
* A job whose creator did not connect `failed` still logs the original traceback
  (`log.exception`) — threading errors are not suppressed.
* The autosaver is a child of the editor; late results for a closed or destroyed autosaver
  are ignored, and the synchronous flush reads the outcome from a per-write ticket instead of
  from a queued signal (so shutdown never depends on the event loop).
* Services open a connection per call; the draft write lock serialises draft writes; the
  catalog (search) runs on its own latest-only runner, unchanged.
* Test coverage: a page deleted while a draft write is still inside the database call, a
  window closed while a slow write runs, stale outcomes after switching questions, failing
  writes, and repeated runs of the whole GUI test set (results in the Phase 4 report).

### 17.7 Data-loss risks and protections

| Risk | Protection | Remaining exposure |
|---|---|---|
| Program killed / crashes while typing | autosave ≈1 s; flush on every context change | last ≈1 s of keystrokes |
| Power cut | `synchronous=FULL` on draft and edit commits; WAL recovery on next open | last ≈1 s of keystrokes |
| Save fails half-way | single `BEGIN IMMEDIATE` transaction, rollback; draft kept until commit | none (tested with injected failures and a real disk-full) |
| Two windows/instances edit the same question | optimistic `lock_version`; conflict dialog | none silently; the reviewer decides |
| Draft written for an older revision | stores base revision and lock; flagged *outdated*; saving it conflicts | none silently |
| Mistaken discard | confirmation; text archived in append-only `draft_discards` | recoverable by hand from the database |
| Mistaken edit | every revision is kept; history view shows and compares them | restoring is by copying text until the restore feature exists |
| Disk full / read-only / locked | Georgian error, text kept, retry, refuse to close silently | text only in memory while writing is impossible |
| Disk failure, corruption, deleted folder | verified backups (§10) | the application cannot prevent this |

## 18. Extension points for Phase 5 (approval, rejection, reopening, restore)

* **Approval** is a new `services/review_service.py` using the same transaction pattern as
  §17.4: check `lock_version`, set status and `approved_revision_id`/`approved_at`, append the
  `APPROVE`/`REJECT`/`REOPEN` event, bump `lock_version`, commit. `tests/bank.py::approve` and
  `set_status` show the exact writes and the invariants the triggers enforce. Content must pass
  `validate_content` before approval; unresolved drafts of the question should be shown first.
* **Restore of an old revision** creates a new revision with `origin = RESTORE` (§8) through the
  same `edit_repository.append_edit`; the history view already lists and compares revisions.
* **Export** reads `approved_revision_id` only (§9); drafts and `IN_REVIEW` text never reach it.
* **Search** needs no change: the derived index detects new revisions (§15.2).
* **Invariants to keep**: catalog code paths stay `query_only`; the UI never imports `db`;
  Georgian text is preserved exactly; every write is a single transaction; "saved" is shown only
  after the commit; drafts are never deleted implicitly; the unchanged-database snapshot tests.
