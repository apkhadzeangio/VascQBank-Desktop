# VascQBank Review Desktop — Architecture

This document describes the architecture implemented in Phases 1–8 and the design
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
| `domain` | subjects, answer letters ა–ე, review statuses, the editorial workflow state machine, question IDs, edit validation, word-level text diff | Qt, sqlite3, db, services, ui, app, importexport |
| `validation` | text normalisation; later structural question checks | Qt, sqlite3, upper layers |
| `importexport` | strict UTF-8 I/O; TXT/master parsers; paired-TXT and Word (.docx) writers and the .docx verifier | Qt, ui, app |
| `db` | connection policy, ownership check, migrations, repositories (questions, catalog, drafts, edits, review decisions, approved text) | Qt, services, ui, app |
| `services` | use cases (storage init, import, catalog/search, settings, session, backups, drafts, edits, review decisions, approved-text contract, **export**) | Qt, ui, app |
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

### Phase 6 — export of 10,000 approved questions (measured)

`tests/test_export_performance.py`: the synthetic 10,000-question bank, every question approved by
test set-up; Linux, Python 3.13, python-docx 1.2, tmpfs-class disk.

| Operation | Measured | Test budget |
|---|---:|---:|
| TXT export of all 10,000 (snapshot 0.18 · write 0.06 · parse-back verification 0.41 · manifest 0.03 s) | 0.75 s | < 60 s |
| One Word document with all 10,000 questions, review edition: build · save (ZIP) | 9.5 · 0.1 s | < 120 · 60 s |
| Complete export: three Word editions of 10,000 + all TXT, including verification of every file | 23 s (Word 18.5, verification 4.2) | < 600 s |
| Process peak memory: before → one 10,000-question document → complete export | 119 → 258 → 376 MB | — |
| File sizes: TXT · manifest · review / exam / answer-key .docx | 9.5 · 3.0 · 0.4 / 0.3 / 0.2 MB | — |
| Window responsiveness during a 10,000-question Word export (event-loop gap p95 / worst) | 64 / 217 ms | worst < 1.5 s |

The synthetic text is highly repetitive, so the .docx files compress far better than real questions
will (expect several MB for a full bank); the timings do not depend on that. python-docx's own
`add_paragraph` and style setter made a 2,000-question document take 21 s (quadratic); the writer
inserts paragraphs before the remembered `w:sectPr` and writes style ids directly, which made it
linear (≈ 1 ms per question). The worker runs in the background (`QThreadPool`); the window stays
usable, with occasional pauses of up to a fifth of a second while Python code competes for the GIL.

### Phase 5 — review decisions on 10,000 questions (measured)

`tests/test_review_performance.py`, same synthetic bank with 2,021 questions approved by test
set-up, `synchronous=FULL`, offscreen Qt, tmpfs-class disk (a real disk adds the `fsync` time).

| Operation | Measured | Test budget |
|---|---:|---:|
| Approve one question · reopen it (service, one transaction) | 3.2 · 3.1 ms | < 100 ms |
| *Approve and next* in the GUI inside a status filter (decision + list refresh + navigation; median / worst of 20) | 31 / 42 ms | < 250 / 600 ms |
| Decision until the filtered list has settled | 18–21 ms | < 1000 ms |
| Restore an earlier revision · read a 12-revision history | 3.5 · 0.8 ms | < 150 · 50 ms |
| Progress statistics for the whole bank | 1.1 ms | < 100 ms |
| Approved-question ids · count · one subject's snapshot (≈ 2,000 approved) | 3.2 · 2.4 · 2.8 ms | < 100 · 100 · 500 ms |
| Full approved snapshot of 2,021 questions · export-contract check | 24.6 · 9.8 ms | < 1500 · 500 ms |

Ordinary review work (decisions and navigation) reads **no revision history**: a test counts the
calls of the history queries and requires zero.

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

## 8. Editorial revision history (implemented — Phases 4 and 5)

* Revisions are append-only; `questions.current_revision_id` points to the
  latest. Saving content identical to the current revision creates nothing.
* **Restore** (implemented in Phase 5, §18.5) never rewrites history: it creates a new revision
  (`RESTORE`) copying the chosen one and recording `restored_from_revision_id`.
* **Optimistic concurrency**: a save carries the `lock_version` the editor
  loaded; `UPDATE … WHERE id=? AND lock_version=?` affecting 0 rows means
  the question changed meanwhile (second window or instance) and the user is
  asked to reload — no silent lost update.
* Every revision stores `author` (the reviewer name set for this installation —
  there is no login) and an optional note.
* History view (implemented, `ui/revision_history_view.py`): read-only list of revisions
  (number, editor, time, origin, note, status change) with the full text of the selected
  one and a word-level comparison with the current version (`domain/textdiff.py`). The history
  has a **წინა რედაქციის დაბრუნება** button for the selected earlier revision (§18.5); restored
  revisions are labelled *აღდგენა (რედაქცია N-დან)*.
* The save itself is §17.4.

## 9. Approval states (implemented — Phases 4 and 5)

Status codes and Georgian labels match the VascQBank-Review web application
(implemented in `domain.review_status`).

| From | Action | To | Rule |
|---|---|---|---|
| `PENDING_REVIEW` | first edit saved / `START_REVIEW` | `IN_REVIEW` | |
| `PENDING_REVIEW`, `IN_REVIEW` | `APPROVE` | `APPROVED` | content must pass validation, no unresolved draft; `approved_revision_id = current_revision_id` |
| `PENDING_REVIEW`, `IN_REVIEW`, `APPROVED` | `REJECT` (reason required) | `REJECTED` | question stays in the database; approval pointers cleared |
| `APPROVED` | content edited or restored | `IN_REVIEW` | approval is bound to an exact revision, so any content change revokes it |
| `APPROVED`, `REJECTED` | `REOPEN` | `IN_REVIEW` | approval pointers cleared; history kept |
| `REJECTED` | content edited or restored | `REJECTED` | only an explicit reopen leaves `REJECTED` |

The whole table is `domain/workflow.py` (pure rules); §18 describes how it is applied.

Every transition writes a `review_events` row in the same transaction. Exports
read `approved_revision_id`, so only the exact approved text can ever be
exported.

**Implemented in Phase 4** (`domain.review_status.status_after_edit`): saving an edit moves
`PENDING_REVIEW → IN_REVIEW`, keeps `IN_REVIEW`, keeps `REJECTED` (leaving it is the explicit
*reopen* of Phase 5, §18.4) and moves `APPROVED → IN_REVIEW`. The last one **revokes the
approval**: the `questions` CHECK requires `approved_revision_id` and `approved_at` to be NULL
unless the status is `APPROVED`, so both are cleared — the approved revision itself and its
`APPROVE` event stay in the history, and the revoked revision number is written into the
`EDITED` event's note. The editor warns before this happens and asks for confirmation.
*Typing never changes a status*: only a committed save or decision does.

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

**Restore is implemented in Phase 8 (§28)**: verify the chosen backup → verified safety backup of
the current database → close connections → atomic replace through a journal → stale
`-wal`/`-shm` set aside → reopen and migrate. Still design: "save backup as…" and a start-up
backup if the last one is older than 24 h.

## 11. Word and TXT export (implemented — Phase 6, details in §22)

* **Scope**: approved questions only (all subjects or a selection), using the
  approved revision's content.
* **TXT**: exactly the source convention (§7.2) — `<CODE>_questions.txt` with
  `*` before the correct letter, `<CODE>_explanations.txt` with `N. <letter>) …`.
  UTF-8 without BOM, LF by default (CRLF optional). **Round-trip guarantee**:
  every exported pair re-imports through the same parser to identical content
  (automated test).
* **Word (.docx)** via `python-docx`: heading per subject, numbered questions with options
  ა)–ე), three editions (review: answers marked inline + explanations; exam: questions only;
  answer key), page numbers, A4. One font on all four `w:rFonts` slots; default **Sylfaen**
  (installed on every Windows), DejaVu Sans as the alternative. *Noto Sans Georgian was tried and
  rejected*: it has no Latin letters or digits, so mixed text (EVAR, mmHg, 70 %) rendered badly.
  After writing, every document is reopened and compared with the snapshot question by question.
* Files are staged, verified, then published as one folder `VascQBank_Export_<time>-<id>/` with
  `DOCX/<CODE>_<edition>.docx`, `DOCX/VascQBank_All_Approved_<edition>.docx`, `TXT/<CODE>_questions.txt`,
  `TXT/<CODE>_explanations.txt` and `export-manifest.json` (question uids, approved revision ids,
  content hashes, snapshot fingerprint, database id, every file's SHA-256); each successful export is
  recorded in `export_runs`.

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
| Review decisions: every rule, conflicts, drafts, rollback, original data, restore | pytest with temp databases | implemented |
| Review decisions interrupted by SIGKILL (service level and whole GUI) | isolated child processes, temp databases only | implemented |
| Workflow UI: bar, approve-and-next, filtered navigation, dialogs, shortcuts, statistics, restart | pytest-qt, offscreen | implemented |
| Export contract (approved revision only, ordering, fingerprint, corruption detection) | pytest | implemented |
| 10,000-question review benchmark | `perf` marker | implemented |
| TXT writer, representability rules; Word writer, fonts, editions; .docx verifier against tampering | pytest | implemented |
| Export service: plan, snapshot consistency under concurrent edits, manifest, atomic publication, disk-full, permissions, cancellation, rename/commit failures, stale staging | pytest with temp folders | implemented |
| TXT round trip through the real importer (fixed and randomised banks, gaps, partial selections) | pytest | implemented |
| Export page: preview, background export, progress, cancel, errors, closing the window | pytest-qt, offscreen | implemented |
| 10,000-question export: time, size, memory, verification, GUI responsiveness | `perf` marker | implemented |
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
damaged with the reason. Phase 8 added the restore action (§28.6): the page only *requests* the
restore dialog, which verifies, explains and needs an explicit confirmation.

## 16. Seed database distribution (design of Phase 3 — implemented in Phase 7, see §24)

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
re-seed; it stops and explains (§24.5) — restoring a backup in-app is not implemented yet, the
user restores by copying a backup file, or explicitly starts a new database. All edits affect
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
* Test-harness note: `QTest.qWait` (and therefore `qtbot.wait`) keeps the GIL while it spins, which
  starves background threads — a worker doing 40 short SQLite commits took 2.2 s under `qWait`
  and 0.07 s inside a real `QEventLoop` (the application's own `app.exec()` behaves like the
  latter). A repeated GUI run exposed this as a ≈12 % flake in a test that waited a fixed 400 ms;
  it was a test artefact, not a lost write (the worker always resumed within 20 ms), and the test
  now waits in a real event loop for the condition. Tests that wait for a worker use
  `qtbot.waitUntil(<condition>)` with a generous timeout, never a fixed `qtbot.wait`.
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
| Mistaken edit | every revision is kept; history view shows and compares them; **restore** appends the old text as a new revision (§18.5) | none for content; restore is explicit and confirmed |
| Disk full / read-only / locked | Georgian error, text kept, retry, refuse to close silently | text only in memory while writing is impossible |
| Disk failure, corruption, deleted folder | verified backups (§10) | the application cannot prevent this |

## 18. Approval, rejection, reopening and restoration (implemented — Phase 5)

No migration was needed: schema v2 already has the `APPROVE`/`REJECT`/`REOPEN`/`START_REVIEW`/
`RESTORE` event codes, `question_revisions.restored_from_revision_id` (with the CHECK
`origin = 'RESTORE' ⇔ restored_from_revision_id IS NOT NULL`) and the `APPROVED ⇔ approval
pointers` CHECK. The schema stays at version 3.

### 18.1 Layers

```
ui/catalog_workflow.py  WorkflowController     buttons, dialogs, shortcuts, navigation, messages
ui/workflow_widgets.py  WorkflowBar, ReasonDialog
        │  (never touches SQLite)
services/review_service.py   ReviewService      reviewer name, locking of the write, error mapping
        │
db/review_repository.py      apply_action()     ONE BEGIN IMMEDIATE transaction per decision
db/edit_repository.py        append_edit()      restore = a revision with origin RESTORE
domain/workflow.py                              the state machine (pure)
db/approved_repository.py + services/approved_service.py   read-only export contract (§19)
```

### 18.2 Transaction boundaries

Every decision is **one** `BEGIN IMMEDIATE … COMMIT` on its own `synchronous=FULL` connection;
a success is reported to the user only after `COMMIT`, and any exception rolls everything back.

`ReviewService.approve / reject / reopen / start_review` → `apply_action`:

1. read the question and its current revision; wrong `lock_version` → `LockConflict`
   (reported as `EditConflict`), nothing written;
2. *repeat* (approve an already approved revision, start a review that is running) → commit
   nothing: **no duplicate event, no lock bump**, `changed = False`;
3. not allowed from this status (`domain.workflow`) → `InvalidTransition`;
4. `APPROVE` only: the **authoritative saved text** must pass `validate_content`
   (`ValidationFailed`) and the question must have **no unresolved draft** (`UnresolvedDraft`;
   a draft that merely repeats the saved text is deleted with the approval);
5. `UPDATE questions SET status, approved_revision_id, approved_at, lock_version = lock_version + 1,
   modified_at WHERE id = ? AND lock_version = ?` — approval sets `approved_revision_id =
   current_revision_id` and `approved_at`; reject and reopen clear both;
6. `INSERT review_events` (`action`, `from_status`, `to_status`, `revision_id = current`, `actor`,
   `note`), `COMMIT`.

Content, revisions, the imported original and identity are not touched by these actions (the
triggers would refuse it). Autosaving a draft never calls any of this: **a draft can never
approve a question**.

### 18.3 Approval and rejection rules

* A reviewer name is required (`ReviewerProfile.require_name()`); without one nothing happens.
* Approval is of the **revision the reviewer is looking at**: the UI passes the `lock_version` of
  the displayed question, so a question changed elsewhere gives a Georgian conflict message and
  reloads the screen instead of approving text that was never seen.
* An unsaved draft blocks the approval (the dialog offers to open the draft in the editor);
  approving while *editing* first asks to save the editor text as a new revision, saves it
  explicitly (own validation, conflict and failure handling), and approves **that** revision. If
  the save or the draft flush fails the approval does not run and the screen does not move.
* Rejection needs a written reason (≤ 500 characters, one line), stored in the event; the
  question and all revisions stay; an approved question loses its approval (the revoked revision
  number is shown); a rejected question cannot be approved until it is reopened.
* `START_REVIEW` (button *განხილვის დაწყება*) moves a pending question to review without
  changing the text.

### 18.4 Reopening

`APPROVED`/`REJECTED` → `IN_REVIEW`, optional note, confirmation dialog; the approval pointers are
cleared (the schema allows them only on an approved question) while every earlier `APPROVE` event
and approved revision stays in the append-only history, so "what was approved when, by whom" is
always answerable. Reopening a pending or in-review question is refused.

### 18.5 Restoring an earlier revision

`ReviewService.restore_revision(question_id, lock_version, source_revision_id, note, keep_draft)`:

* the source must be a revision of the same question (`RestoreSourceInvalid` otherwise) and its
  text must validate;
* `append_edit(origin="RESTORE", restored_from_revision_id=source, event_action="RESTORE")` —
  the same single transaction as a save: lock check, contiguous revision number, status rule
  (§9: an approved question loses its approval; rejected stays rejected), `lock_version + 1`,
  `RESTORE` event ("restored from revision N"), commit;
* text identical to the **current** revision creates nothing (`created_revision = False`); the
  history disables the button for the current revision and for revisions with the current text;
* old revisions are never changed; restoring *previously approved* text produces an unapproved
  new revision — approving again is an explicit decision;
* an unsaved draft is **never deleted** by a restore: unless it equals the restored text the call
  is refused (`UnresolvedDraft`) and the dialog makes the reviewer choose *keep the draft* (it
  stays, flagged outdated, and saving it later goes through the normal conflict path) or
  *discard it* (archived in `draft_discards`) or cancel.

### 18.6 Conflict resolution

All decisions use optimistic concurrency. On `EditConflict` nothing was written; the screen is
reloaded so the reviewer sees the new text and status, a red notice names the revision now
current, and the decision can be repeated deliberately. A second program window or instance
behaves identically. A draft started on a revision that is still current is **not** treated as a
conflict when only a status decision (approve/reject/reopen) moved the lock version since.

### 18.7 The reviewer's interface

| Control | Shortcut | Notes |
|---|---|---|
| დამტკიცება და შემდეგი → | Ctrl+Enter | approve, then move on only after the commit |
| დამტკიცება | Ctrl+Shift+Enter | approve and stay |
| უარყოფა | Ctrl+Shift+R | dialog, reason required |
| ხელახლა გახსნა | Ctrl+Shift+O | confirmation, optional note |
| განხილვის დაწყება | — | pending → in review |
| წინა რედაქციის დაბრუნება | Ctrl+Shift+B | in the history, on the selected revision |

Only the actions the displayed status allows are shown. After every committed decision the
detail view, the list row and the progress counters (strip, home page, sidebar) refresh and a
green success notice names the question; failures show a red notice and change nothing.

**Approve and next**: the "next" question is taken from the list **before** the approval, so when
the approved question leaves an active status filter the reviewer continues with the question
that followed it (not the list's first); at the end of the list the screen stays and says so.
It never navigates when the approval, the explicit save or the draft flush failed.

### 18.8 Testing

Domain table; 44 service tests (every rule, conflict, draft, rollback by sabotage trigger,
original data intact); 17 SIGKILL tests on isolated child processes and 5 on the whole GUI
(a killed approval/restore leaves the database exactly as it was and can be repeated); 47 + 6
GUI tests (bar, approve-and-next, filtered navigation, editor flows, reject/reopen/restore
dialogs, conflicts, Georgian messages, shortcuts, statistics, restart); the export contract;
10,000-question benchmark (§6). Several tests were run against deliberately broken code to
prove they can fail.

## 19. The export contract (read side implemented in Phase 5, used by the exporter of Phase 6)

The Phase 6 exporter gets its data **only** from `services/approved_service.ApprovedQuestionsService`
(`db/approved_repository.py`, read-only SELECTs — tested to contain no write keyword):

* `snapshot(subject_code=None)` — every approved question (or one subject's) in export order
  (subject order, then source number), read in one consistent database snapshot, plus a
  `fingerprint` (SHA-256 over `uid|revision_id|content_hash`) the export run can record in
  `export_runs` and the manifest;
* each `ApprovedQuestion` carries the immutable question ID (`uid`, `subject_code`,
  `source_number`), the **approved revision** (`revision_id` = `questions.approved_revision_id`,
  `revision_no`, `approved_at`, `approved_by`), the exact text, correct answer and explanation;
* `get(uid)`, `approved_ids(subject)`, `count(subject)`, `counts_by_subject()`;
* `verify()` — run before exporting: reports a state that must stop the export (an approved
  question without an approved revision, an approved revision that is not the current one, an
  unapproved question with approval pointers, no `APPROVE` event bound to the approved revision).

**Eligibility rule** (`domain.workflow.is_exportable`): status `APPROVED` and an approved revision,
always *that* revision. By construction of the queries the following can never be exported:
unsaved drafts (the `drafts` table is never read), pending, in-review and rejected questions, and
superseded revisions (an edit or restore revokes the approval first, so a revision that is not
current can never be approved). Phase 6 must not add another path to question text.

## 20. Remaining risks (Phase 5)

* **Approval is a human decision on what is displayed.** The software guarantees that exactly
  the displayed saved revision is approved (or a conflict), not that the text is medically
  correct.
* **No undo for a decision** other than the explicit reopen/restore — both leave history.
* **Two programs on one database** are protected by `lock_version`, but only the single-window
  behaviour is exercised in GUI tests.
* **Rejection reasons are one line** (500 characters); long discussions belong in a later
  comment feature (`COMMENT` events exist in the schema but are not used yet).
* **The exporter is not written**: until Phase 6, approved text exists only in the database and
  backups; take a backup before long sessions (§10).
* The physical-disk and power-loss caveats of §17.3 apply unchanged: committed decisions are
  durable (`synchronous=FULL`); nothing protects against disk failure.

## 21. Remaining risks (Phase 6)

* **Rendering is not verified pixel by pixel.** The verifier proves the OOXML structure and every
  character; how Word paginates depends on the installed fonts and Word version. A sample rendered
  with LibreOffice (`docs/samples/phase6/rendered/`) looked as intended; nobody has checked it in
  Microsoft Word in this environment.
* **Fonts are named, not embedded.** A computer without Sylfaen (or DejaVu Sans) substitutes another
  face; Georgian stays correct only if the substitute covers Georgian.
* **TXT cannot carry every text**: blank lines inside a field, `*` in a question or option, lines that
  look like options/numbers, or edge spaces are refused with the question named (§22.5) — the text must
  be corrected and re-approved. Word has no such limit.
* Large exports hold the whole document in memory (≈ 140 MB per 10,000-question document).
* Exports are files the reviewer then copies or shares; the application cannot protect them afterwards.

## 22. The export engine (implemented — Phase 6)

### 22.1 Layers

```
ui/export_view.py           ExportView            choices, preview, progress, cancel, messages
        │ (BackgroundJob / LatestOnlyRunner — the QThreadPool workers of §15.3)
services/export_service.py  ExportService         plan → snapshot → write → verify → manifest → publish
        │                          │
importexport/txt_export.py   docx_export.py   docx_verify.py   paired_txt.py (the importer, for read-back)
services/approved_service.py  (the ONLY source of question text, §19)
db/export_repository.py       record_and_publish(): export_runs rows + folder rename in one transaction
```

### 22.2 Workflow and guarantees

1. **Snapshot**: `ApprovedQuestionsService.snapshot()` (one read transaction) and `verify()`; a
   violation blocks the export. Only the questions of the selected subjects are kept; the fingerprint
   is recomputed for exactly that selection. Edits committed while the export runs cannot reach it
   (tested by committing an edit from inside the writer).
2. **Plan** (also the page's preview): per subject total/approved/excluded counts, the files that will
   be written, TXT representability problems. Nothing approved → `ExportEmpty` (no empty document).
3. **Staging**: `<destination>/.vqb-export-<id>.partial/` carrying a marker file. An abandoned staging
   folder from a crashed export is removed on the next export **only if it carries the marker** —
   user files and look-alike folders are never touched.
4. **Writing**: TXT per subject; Word per edition, per subject and/or one combined document.
5. **Verification** of every file (§22.4); any problem → `ExportVerificationFailed`, nothing published.
6. **Manifest** `export-manifest.json`: export id, app version, time, database id, snapshot time and
   fingerprint, selection (subjects, formats, editions, layout, font), counts (exported / excluded per
   subject), every question's uid, subject, source number, approved revision id and number, content
   hash and approval time, and every file's path, kind, size and SHA-256.
7. **Publication** (corrected in Phase 7, §25): a durable intent journal is written; one
   `BEGIN IMMEDIATE` inserts the `export_runs` rows (one per format); the staging folder is renamed to
   `VascQBank_Export_<yyyymmdd-HHMMSS>-<random>/`; the transaction commits; the journal is removed. An
   ordinary failure before the commit renames the folder back and rolls the rows back. **A rename and a
   commit are not jointly atomic**: a crash between them is completed (verified, then recorded once) or
   undone by the reconciliation of §25. An existing folder is never overwritten.
8. Any failure or cancellation before step 7 leaves no export folder and no row; the staging folder is
   removed; earlier exports are untouched (tested with disk-full, permission, verification, rename and
   commit failures). Nothing in the database other than `export_runs` is written.

### 22.3 Output modes and numbering

| Edition | Content |
|---|---|
| Review (default) | ID, question, five options with the correct one in its own style **and** „✓ სწორი პასუხი“, explanation |
| Exam | ID, question, five options in one identical style — no marker, no explanation, no answer anywhere in the package |
| Answer key | `12. ALI-0042 — ბ) <correct option>` and the explanation, numbered exactly like the exam edition |

* **Numbering**: Word shows a continuous display number (1 … N in export order) *followed by the
  immutable question ID* — `12. ALI-0042`; exam and answer key of one export share the numbers. TXT keeps
  the **original source number** (`42.`) and subject code; a partial export has gaps, which the importer
  accepts. No question is ever renumbered in its identity.
* **Layout**: A4, 2 / 2.2 cm margins, header with the document title, footer `გვერდი PAGE / NUMPAGES`
  (real fields), reusable paragraph styles (`VQB Title`, `VQB Meta`, `VQB Subject` based on Heading 1,
  `VQB Question ID`, `VQB Stem`, `VQB Option`, `VQB Option Correct`, `VQB Explanation Label`,
  `VQB Explanation`, `VQB Key Entry`) and one character style (`VQB Correct Marker`). Each subject starts
  on a new page; questions never get page breaks of their own, but ID, question and options are kept
  together (`keep with next`, `keep lines together`). Line breaks inside a field are Word line breaks.
* **Fonts**: one family on `ascii`, `hAnsi`, `cs` and `eastAsia` of the document defaults and every style,
  theme fonts removed, language `ka-GE`. See §11 for the font choice.
* The front matter states that the document was produced from approved revisions and is **not an
  officially accredited or certified examination**.

### 22.4 Verification

* **Word**: reopened with python-docx; A4 and the page-number field checked; every VascQBank style must
  name the chosen font in all four slots; the body paragraphs are walked **by style** and every question
  is rebuilt (display number, uid, subject, stem, five options in ა…ე order, which option carries the
  correct style and marker, explanation or key entry) and compared character for character with the
  snapshot — missing, extra, duplicated or reordered questions are reported. The exam edition must have
  no correct style, no marker run, no explanation, no key entry, no direct run formatting on any option,
  and the marker text must appear nowhere in the package. The tests tamper with real files (an option, an
  ID, an explanation, a symbol, a style, the key, a duplicate) and the verifier catches each.
* **TXT**: no BOM, LF only; parsed back with the **real importer** (`parse_pair`); no error allowed;
  exactly the expected IDs; every question equal to the snapshot field by field.

### 22.5 The TXT format and what it cannot carry

The paired-TXT convention (§7.2) has no escape mechanism. Before writing, `txt_problems()` names texts
that would not read back identically — a blank line inside a field (it ends the question), `*` in a
question or option (the answer marker), a continuation line that looks like `ბ) …` (an option) or, in an
option, like `12. …` (a new question), spaces at a line edge that the reader trims, an explanation that
starts with `*`. A TXT export containing such a text is refused with the question IDs and fields; the
text is **never altered to make the format fit**. The read-back verification is the final proof.

### 22.6 Round-trip guarantee

`tests/test_export_roundtrip.py`: synthetic bank → edits (multi-line, Georgian, EVAR/CTA/≥/α/10⁹) →
approval through `ReviewService` → TXT export → import into a fresh database with the normal import
service → every approved question identical (ID, question, five options, correct letter, explanation),
nothing unapproved present, gaps preserved; fixed bank, single-subject partial export and three
randomised banks with gaps in three subjects; the answer key of the same export agrees with the TXT.

### 22.7 The export page

Subjects (all / none / any combination), formats, editions, per-subject and/or combined Word files,
font, destination folder (default `<data>/exports`). The preview shows the selected subjects, how many
questions will be exported and how many are excluded because they are not approved, the editions, the
destination and every file name; empty or blocked selections disable the button and say why. The export
runs on a worker with phase, progress and count; **Cancel** stops before publication; success is shown
only after verification and publication. Closing the window during an export asks whether to stop it
(the worker removes its staging folder before the window closes) or to keep working.

## 23. Phase 7 handoff (done — see §24 and §25)

The Phase 6 handoff items were implemented in Phase 7: seeds built through the same import pipeline
and checked to contain no reviewer work (empty `export_runs`, `drafts`, `draft_discards`), seed
provenance in `app_metadata` and in export manifests, the seed folder in the package data, and the
font note for Linux (§11). Packaging itself stays in Phase 10.

## 24. Bundled seed and first launch (implemented — Phase 7)

### 24.1 Layers

```
tools/build_seed.py              developer CLI → services/seed_builder.py (uses import_service unchanged)
tools/prepare_package_seed.py    production gate → copies a verified seed into resources/seed/
services/seed_service.py         manifest, strict verification, no-overwrite installation, locating
services/startup.py              prepare_storage(): open / install / stop — the start-up decision
app/main.py                      Georgian dialogs (preparing, recovery, invalid seed), --seed-dir
```

### 24.2 Building a seed (developer side)

`python tools/build_seed.py SOURCE OUTPUT --version 2027.1 --production`:

1. discover + **dry run** with `import_service.dry_run` against a fresh empty database (the same parser,
   validation and conflict rules as a manual import — no second parser);
2. **gates for every seed**: no blocking validation error, no invalid item, no duplicate ID, no
   conflict, at least one question; **production gates**: only paired-TXT sources, exactly the 12
   subjects, each at its target count, 10,000 in total;
3. **import** with `import_service.commit_import` — every question `PENDING_REVIEW`, one `IMPORT`
   revision, immutable `original_content_json`;
4. **compact** with `VACUUM INTO` into one self-contained file in rollback-journal mode, made read-only;
5. **inspect read-only** (§24.4) and write `seed-manifest.json`; then run the very `verify_seed()` the
   application runs at first launch;
6. **publish** the output folder by renaming it into place — an existing artefact is never overwritten.

Anything that fails a production gate can only be built as **DEVELOPMENT**.

### 24.3 The manifest (`seed-manifest.json`, format 1)

```json
{
  "manifest_format": 1,
  "kind": "DEVELOPMENT",
  "seed_version": "dev-1",
  "seed_database_id": "57cbed67-735d-4da4-979f-3b53fb7d0495",
  "built_at": "2026-11-18T14:45:46+00:00",
  "app_version": "0.7.0",
  "schema_version": 3,
  "question_count": 10000,
  "subjects": {"PAD_CLTI": 1500, "ALI": 900, "…": "…"},
  "source_files": [{"name": "ALI_questions.txt", "sha256": "1c8c…", "size": 581543}, "…"],
  "database": {"file": "vascqbank-seed.sqlite3", "sha256": "…", "size": 34938880},
  "verification": {"integrity_check": "ok", "foreign_key_check": "ok", "all_pending_review": true,
                   "one_import_revision_per_question": true, "approvals": 0, "drafts": 0,
                   "export_runs": 0, "warnings": 0},
  "production_gates": {"passed": true, "failures": []}
}
```

Semantics: `kind` is `PRODUCTION` only if every production gate passed at build time and passes again
at verification; `seed_database_id` is the seed database's own `database_id`; `subjects` sum to
`question_count`; `database.sha256`/`size` are of the file named in `database.file` (a plain name in the
same folder); `source_files` are the TXT files the seed was built from; `verification` and
`production_gates` record what the builder found. Any other `manifest_format` is refused. A new format
must get a new number.

### 24.4 Verification (never trusting the manifest merely because it exists)

`verify_seed()` parses the manifest strictly, checks the database file's **size and SHA-256**, then opens
it with `mode=ro&immutable=1` (not one byte — not even the header — is written) and checks: our
`application_id`; `user_version` equal to the manifest and supported (3 ≤ v ≤ latest); `integrity_check`
and `foreign_key_check`; `database_id` equal to `seed_database_id`; per-subject counts equal to the
manifest; **no reviewer work** (every question pending with `lock_version` 0, no approval pointers, exactly
one `IMPORT` revision per question, only `IMPORTED` events, no drafts, discarded drafts or export runs,
no installation metadata); and, for a PRODUCTION manifest, the production gates. `require_production`
refuses DEVELOPMENT seeds (packaging). Tests tamper with every one of these and each is caught,
including edits whose checksum was re-sealed so that only the content checks can see them.

### 24.5 First launch and every later launch (`prepare_storage`)

1. Temporary files of an interrupted installation (`database/.seed-install-*.partial`) are removed.
2. **A working database exists → it is opened and migrated as always.** The seed is ignored. An
   application update never resets, replaces or merges a database; existing work continues through
   the normal versioned migrations (with their pre-migration backup).
3. **No database but traces of one** — a backup (`backups/vascqbank-*.sqlite3`), a `-wal`/`-shm`/`-journal`
   file of the database, the derived search index, a `VascQBank_Export_*` folder in the default export
   folder, or `settings.json` → **nothing is created** and the dialog *სამუშაო ბაზა ვერ მოიძებნა* explains
   what was found and how to restore by hand (in-app restore does not exist yet). Choices: open the data
   folder and exit (default: exit), or — after a second confirmation — create a new database; backups and
   exports are never touched. `--self-check` exits with code 3.
4. **No database, a seed** → `verify_seed`, then `install_seed`: stream-copy to
   `database/.seed-install-<random>.partial` with fsync, compare the copy's SHA-256, give it a **fresh
   `database_id`**, record provenance (`seed_database_id`, `seed_version`, `seed_kind`,
   `seed_installed_at`, `seed_manifest_hash`, `installed_by_app_version`) in one transaction, `quick_check`,
   fsync, then publish with `os.link` (fails if the target exists — never overwrites; a rename that also
   refuses to replace on Windows is the fallback) and sync the directory. The derived search index is
   deleted so that it is rebuilt for this database. Then `initialize_storage` switches it to WAL.
   A seed that fails verification → *ჩაშენებული ბანკი დაზიანებულია*, **no database is created** (exit
   code 4), so a corrected application can still install.
5. **No database, no seed** → an empty database, the manual TXT import, exactly as before.

A small "preparing" window is shown only while a seed is installed; a successful start shows no dialog.
A DEVELOPMENT seed is always labelled — „⚠ სინთეზური სატესტო ბანკი“ in the status bar and its version and
kind on the home page — so it can never be mistaken for the real bank.

**Interruption.** SIGKILL tests at `copied`, `provenance`, `before-publish` and `published` show that the
real database name only ever holds a complete, verified, provenance-carrying database; the next start
cleans the temporary file and installs (or, after `published`, simply opens) it.

### 24.6 Locating the seed

`--seed-dir PATH` beats the environment variable `VASCQBANK_SEED_DIR` beats the package data
`vascqbank_desktop/resources/seed/` (declared in `pyproject.toml` package data). The public repository
contains only `resources/seed/README.md`; `.gitignore` excludes `seed-manifest.json` and
`vascqbank-seed.sqlite3` everywhere, so a private seed cannot be committed by accident (tested).

### 24.7 Packaging with a private production seed (for Phase 10)

1. Obtain the 12 approved source TXT pairs privately (never from or into this repository).
2. `python tools/build_seed.py /private/source /private/seed-2027.1 --version 2027.1 --production`
3. `python tools/prepare_package_seed.py /private/seed-2027.1` (refuses DEVELOPMENT seeds unless
   `--allow-development-seed` is given for a *test* build; never overwrites).
4. Build the package from that working tree; do **not** commit; delete the copied seed afterwards.

### 24.8 Measured (10,000 synthetic questions, tmpfs-class disk)

| Operation | Measured | Budget |
|---|---:|---:|
| Seed build (dry run 0.45 · import 0.85 · compact 0.10 · checks 0.21 s) | 1.7 s | < 60 s |
| Seed verification (SHA-256 + read-only checks) | 0.10 s | < 10 s |
| First launch: verify + copy + provenance + publish + open | 0.32 s (install 0.14) | < 30 s |
| Every later start-up (storage only; no reinstall) | 69 ms | < 2 s |
| Main window with 10,000 questions | 0.20 s | < 10 s |
| Search index build after installation | 0.36 s | — |
| Seed and working database size | 34.9 MB | — |

## 25. Export publication recovery (implemented — Phase 7)

**The problem (audit).** Phase 6 renamed the export folder inside an open SQLite transaction and
committed afterwards, and documented that as atomic. A filesystem rename and a SQLite `COMMIT` are two
independent systems: a process killed after the rename and before the commit left a published folder
whose rows SQLite rolled back — an export without a record. A test also exposed an ordinary error after
the rename that did not move the folder back.

**The protocol** (`services/export_publication.py`, `db/export_repository.py`):

1. the verified staging folder and its manifest exist;
2. a durable **intent** `<data>/journal/export-<id>.json` is written (temp file, fsync, rename, directory
   fsync): export id, staging and final paths, the manifest's SHA-256, and the rows without the manifest
   body — no question text;
3. `BEGIN IMMEDIATE`, insert the rows; rename staging → final (never over an existing folder), sync the
   directory; `COMMIT`. Any exception before the commit renames the folder back and rolls back;
4. the intent is removed.

**Reconciliation** (`reconcile()`, at start-up in the main window and before every export) per intent:
rows for the export id exist → remove the intent; the final folder exists → verify the manifest hash and
**every file's size and SHA-256**, then insert the rows once (`record_once`, keyed by the export id) and
remove the intent — or, if anything differs, record nothing, keep the folder untouched and move the
intent to `journal/unverified/`; only the staging folder exists → remove it (publication never
happened); neither → remove the intent.

**Guarantee, stated precisely.** After any interruption *and the next reconciliation*: a published folder
whose files match its manifest has exactly one set of `export_runs` rows; a folder that was never
published is removed; a published folder is never deleted or overwritten; nothing is recorded twice;
`export_runs` stays append-only. Between the crash and the next start-up a published folder can exist
without its record. SIGKILL tests cover `journal-written`, `inserted`, `renamed`, `committed`,
`journal-removed` and a crash inside the reconciliation itself.

## 26. Remaining risks (Phase 7)

* The production gates are tested with the complete **synthetic** bank only; the real 10,000 questions do
  not exist yet, and their source files may reveal validation findings that need editorial decisions.
* Backups cannot be restored from inside the application; the recovery dialog explains the manual copy.
* A seed's integrity is checked against its own manifest; the manifest is not cryptographically signed.
  Someone able to replace both files inside an installed application could ship a different (but
  structurally valid) bank. Signing belongs to the packaging phase if required.
* The recovery evidence is limited to the data folder and the default export folder; exports written
  elsewhere are not considered.
* Fsync is best-effort on Windows directories (not supported there); NTFS journalling covers renames.

## 27. Phase 8 handoff (done — see §28–§31)

* **In-app backup restore** can now build on §24.5: the recovery dialog is the natural entry point;
  restore into `database/.restore-*.partial`, verify (`verify_backup`), publish with the same
  no-overwrite link as the seed install, and keep the replaced database as a backup.
* **Comparing with a newer seed** (§9 of the brief, never automatic): run `import_service.dry_run` on the
  seed's source TXT (or read the seed read-only) against the working database; new IDs → offer import,
  identical → skip, differing → list as conflicts (§7.4). Never replace reviewer work.
* Keep `prepare_storage` the single place that decides about creating a database, and keep every path
  that publishes a file a no-overwrite publish.

## 28. Restoring a backup (implemented — Phase 8)

### 28.1 Layers

```
ui/backup_view.py        "ამ ასლიდან აღდგენა…" → only *requests* the dialog (restoreRequested)
ui/restore_dialog.py     choose · verify in background · details · confirmation box · restore on a worker
ui/main_window.py        prepare_for_restore() (quiesce) · apply_restore() / reload_database()
app/main.py              missing database: recovery message → "სარეზერვო ასლიდან აღდგენა…" → same dialog
services/restore_service.py   inspect_backup · restore_backup · recover_interrupted_restore
services/startup.py      prepare_storage() resolves an interrupted restore FIRST (step 0)
```

Two situations: **A** — the working database is missing (start-up recovery message, §24.5);
**B** — it exists and the reviewer explicitly wants an older backup (backup page or *File* menu).
Nothing happens without the dialog's confirmation box ticked and the destructive button pressed;
*Cancel* is the default button.

### 28.2 Verification before anything is touched (`inspect_backup`)

The backup must have its manifest (`….sqlite3.json`) — a backup without one is refused because its
SHA-256 cannot be checked. Then: size and **SHA-256 against the manifest**; the file is opened
read-only and `immutable`; **application id**; `PRAGMA integrity_check`; **`PRAGMA
foreign_key_check`**; schema version `1 … LATEST` (newer → refused, older → accepted and migrated after
the restore behind its own pre-migration backup); row counts equal to the manifest. The dialog shows
the backup's date, type, per-status question counts, drafts, revisions, schema and size, and warns if
the backup belongs to another database (different `database_id`). The confirmed SHA-256 is passed to
the restore, so a file changed after the check is refused (`CHANGED_SINCE_CHECK`).

### 28.3 The restore protocol (`restore_backup`)

| Step | What | Checkpoint |
|---|---|---|
| 0 | refuse if `journal/restore.json` exists; delete stray `database/.restore-*.partial` | — |
| 1 | `inspect_backup` again; compare with the confirmed SHA-256 | `verified` |
| 2 | (B) `wal_checkpoint(TRUNCATE)` and close; if `-wal`/`-shm` remain, **another connection has the database open** → `DATABASE_BUSY`, nothing done. Safety backup `pre-restore` via the ordinary verified backup code (never pruned); quiesce again; SHA-256 of the live file | `safety-backup` |
| 3 | backup API copy (read-only source) → `database/.restore-<uuid>.partial`; provenance (`restored_from_backup`, `restored_from_sha256`, `restored_at`) into `app_metadata`; `journal_mode=DELETE`; fsync; re-verify (integrity, FKs, app id, counts, `database_id`); SHA-256 | `temp-created` |
| 4 | durable journal (fsync file + directory): backup, hashes, safety backup, temp name, stamp | `journal-written` |
| 5 | (B) live file unchanged since step 2? else `DATABASE_BUSY`. **Hard link** the live file into `displaced/vascqbank-<stamp>-displaced.sqlite3` (copy + verify where links are impossible); stale `-wal`/`-shm`/`-journal` are **moved** there too (a stale WAL would otherwise be replayed into the restored file). Journal stage `displaced` | `displaced` |
| 6 | (B) `os.replace(temp, live)` — one atomic rename; the old inode stays reachable in `displaced/`. (A) no-overwrite `os.link` publish — a database that appeared meanwhile is never overwritten | `replaced` |
| 7 | journal stage `published` | `published` |
| 8 | delete the derived search index (+ side files) | `index-removed` |
| 9 | reopen through `initialize_storage` (migrates older schemas behind a pre-migration backup); `quick_check` must pass; remove the journal | `completed` |

Only after step 9 is success reported. Errors: before step 7 an exception runs the same recovery
in-process (rollback, §28.4); if reopening fails (step 8–9), the previous database is put back
(copied from `displaced/` through a temp file + atomic rename) and the restored file is kept as
`…-restored-unusable.sqlite3` → `REOPEN_FAILED`. **No path deletes the current database.**

### 28.4 Crash recovery (`recover_interrupted_restore`, run by `prepare_storage` before anything opens a database)

| Journal | Live file | Decision |
|---|---|---|
| none | — | delete stray `.restore-*.partial` (never published, never referenced) → `NONE` |
| unreadable | — | `AMBIGUOUS` |
| `prepared` | present (B) / absent (A) | **roll back**: delete the temp copy and the journal; the live database was never touched |
| `prepared` | absent though it existed | `AMBIGUOUS` |
| `displaced` | SHA-256 = original | **roll back** (the duplicate link in `displaced/` is removed after its hash is checked) |
| `displaced` | SHA-256 = restored copy | the replace happened → **complete** (steps 8–9) |
| `displaced` (A) | a foreign file appeared | **roll back** (only our temp copy is removed; the other file is untouched) |
| `displaced` | anything else / missing | `AMBIGUOUS` |
| `published` | present | **complete** (idempotent: index removal and reopen can run again) |
| `published` | missing | `AMBIGUOUS` |

`AMBIGUOUS` means: **nothing is changed**, the journal stays, start-up stops with
`Outcome.RESTORE_INTERRUPTED` (exit code 5) and a Georgian message lists every candidate file (the
live file, temp copies, `displaced/`, the safety backup). An older database is never chosen silently
when a newer one may exist: before publication the newer live database always wins (roll back). A
finished or undone restore is announced once at the next start. Recovery is idempotent and itself
crash-safe (a crash during recovery is recovered again — tested).

### 28.5 Coordination inside the program

Services open short-lived connections per operation, so the hand-over is: `prepare_for_restore()`
refuses while an export or a backup *creation* runs; writes the editor's text as a draft and leaves
edit/history mode (if the draft cannot be written and the reviewer keeps the text, the restore does
not start); flushes the session; waits for a running search, export preview and index warm-up
(their late results are discarded by generation counters). The restore itself runs on a worker
thread; the dialog cannot be closed meanwhile. Afterwards `reload_database()` refreshes the home
page (database facts incl. "restored from"), status bar, counts, the catalog (re-resolving the saved
session in the restored data), drafts, export preview and rebuilds the search index.

**Second program instance.** A `QLockFile` (`<data>/vascqbank.lock`) now allows one running program
per data folder (`EXIT_ALREADY_RUNNING` = 6); stale locks of a dead process are taken over. Even
without it, the restore detects other open connections (step 2) and a changed live file (step 5).

### 28.6 Backup page

Create now · verify all · open folder · **restore from the selected backup**; table with time, type
(manual / before import / before migration / **before restore**), question count (from the manifest
until verified), size, verification status (in words, colour only as a supplement), file. The selected
backup is named under the table; double-click or the button opens the dialog. The note says that
backups on the same disk do not protect against disk failure and recommends copying `backups/` to
another device. Automatic retention is unchanged (20 automatic; manual and pre-restore never pruned).

### 28.7 Tests

`test_restore_service.py` (31): verified restore A and B, safety backup content, displaced file
byte-identical, provenance, index removal, repeated restores and restoring the safety backup, older
schema + migration, and refusals — no manifest, SHA-256, size, foreign keys, schema too new, foreign
application, damaged pages, counts, changed since check, database in use — each proving the live file
unchanged and nothing left behind; injected errors at every checkpoint; ambiguous journals.
`test_restore_crash.py` (11): **SIGKILL in a child process at all nine checkpoints**, then start-up
recovery, idempotence, a crash during recovery. `test_restore_gui.py` (12): dialog, confirmation,
window refresh, editor text saved first, export blocks, damaged backup, no cancel while running,
refusal message, start-up restore, interrupted-restore stop, single instance.

## 29. Reviewer workspace (implemented — Phase 8)

### 29.1 Workflow

Open → read → (edit → save) → approve / reject → continue. **Quick filters** replace the status
drop-down: `ყველა · მოლოდინში · პროცესში · დამტკიცებული · უარყოფილი · მონახაზით` (Alt+1…6); the
last one is a new filter `CatalogFilter.drafts_only` (`EXISTS` on the unique `drafts.question_id`).
Sorting moved into the list header. Filtering never writes. When the open question leaves the list
after a decision (e.g. approved while "pending" is active), next/previous continue from its **old
neighbours** instead of jumping to the top (`CatalogView._remember_neighbours`). Approve-and-next
keeps subject and filter. Unresolved conflicts and undeletable drafts still block navigation (Phase 4).
A draft never changes a status; the first *saved revision* moves a pending question to "in review"
(domain rule, §18), and only an explicit approval approves.

### 29.2 Keyboard

| Keys | Action |
|---|---|
| Ctrl+F · Ctrl+G · **Ctrl+L** | search · go to ID/number · focus the question list (↑/↓ open questions) |
| Alt+← / Alt+→ | previous / next |
| **Alt+1 … Alt+6** | quick filters |
| Ctrl+Enter · Ctrl+Shift+Enter | approve and next · approve |
| Ctrl+Shift+R · Ctrl+Shift+O · **Ctrl+Shift+I** | reject · reopen · start review |
| Ctrl+E · Ctrl+S · Ctrl+1…5 · Esc · Ctrl+H · Ctrl+Shift+B | edit · save · correct answer · leave editor · history · restore revision |
| Ctrl+I · Ctrl+Shift+E · **F1** · Ctrl+Q | import · export · shortcut list · quit |

Visible hints: a one-line hint under the catalog, shortcuts in tooltips and menus, and F1.
**Two keyboard defects were found and fixed:** (1) with Qt's Georgian catalogue loaded,
`QKeySequence("Ctrl+Enter")` parses as Ctrl+Return — the two approve shortcuts became one ambiguous
sequence that Qt never triggers; every shortcut is now built with `ui/keys.py::key()` (portable
notation). (2) After choosing the catalog in the sidebar, focus stayed in the sidebar so the catalog's
shortcuts did nothing; showing the catalog now puts focus into it (`focus_content`). Both have
regression tests. A test also asserts that no two shortcuts collide and that every documented shortcut
exists.

### 29.3 Counts

All numbers come from SQLite (`question_stats.bank_counts`): per subject and globally — imported,
the four statuses and **unfinished drafts**. The home page has a per-subject table with a separate
"target" column (never shown as imported); the catalog strip and the home dashboard show drafts.
Draft counts refresh when a draft appears or disappears.

### 29.4 Screen sizes

Checked at 1280×720, 1366×768, 1440×900 and 1920×1080 and after user resizing (`test_reviewer_ux.py`:
every visible button/combo of every page is inside the window or inside a scroll area, and no button is
narrower than its text needs). Long questions scroll in the detail panel; the export page's two columns
sit in a scroll area. Looking at the actual 1280×720 render found the edit button clipped by a few
pixels (the 4:6 list/detail split left the detail column too narrow once the position read
"1 500 / 9 996"); the split is now 3:7 and the size test uses a full-bank position text and the
buttons' full size hint (incl. style-sheet padding). High-DPI: Qt 6 scales automatically; a 150 % render is in the screenshots. All of
this is **offscreen rendering on Linux**; it is not a test on real Windows or CachyOS desktops.

### 29.5 Georgian localization

Application text lives in `ui/strings.py`. Qt's own texts ("Show Details...", OK/Cancel/Yes/No,
context menus) come from Qt's catalogue `qtbase_ka.qm` (shipped with PySide6; a copy in
`resources/translations/` would take precedence — Phase 10 should bundle one), loaded by
`ui/localization.py`, with a small override layer for renderings that read wrongly ("OK" is „დიახ"
— "yes" — in Qt's catalogue; here „კარგი") and as a fallback if the catalogue is missing. A test walks
every page and the new dialogs and fails on Latin-script words other than technical identifiers (SQLite,
DOCX, TXT, EVAR, IDs, file names, font names). Question content is never translated.

### 29.6 Accessibility

Accessible names on the list, search, go-to, subject/sort selectors, quick filters and tables; a
visible blue focus ring (not colour alone — a 2 px outline) on buttons, combos, check boxes, tables and
the list; statuses always written in words; medical text selectable and copyable; disabled actions are
disabled, not hidden. The review cycle is tested end to end with the keyboard only.

## 30. Comparing with a newer source (implemented read-only — Phase 8)

`services/seed_compare.py`: `compare_sources(paths, folders)` reuses the importer's dry run (parsing,
validation, source fingerprints); `compare_seed(paths, seed_dir)` verifies the seed like a first launch
and reads it read-only. Result per question ID: **NEW**, **IDENTICAL** (same source fingerprint),
**CONFLICT** (same ID, different source; with whether the working question is already edited or
decided) and **MISSING** (only in the working database); invalid and duplicated proposed items are
counted. *File → ახალ ვერსიასთან შედარება…* shows the counts and ID lists. **Nothing is merged**: there is
no merge action; adding new questions remains the ordinary explicit import, which skips identical and
conflicting questions and never replaces edited or approved ones.

## 31. Measured (Phase 8, 10,000 synthetic questions)

Offscreen Linux container, tmpfs-class disk; `tests/test_phase8_performance.py` (run with `-s` to
print). Budgets in the tests are several times these values.

| Operation | Measured |
|---|---|
| Next question (median of 50) | 6.1 ms |
| Quick filter switch (all six) | 0–25 ms |
| Search incl. 250 ms debounce | 277 ms |
| Counts incl. drafts per subject | 2.1 ms |
| Open editor · save revision | 24 ms · 128 ms |
| Approve and next (median of 10) | 46 ms |
| Create a verified backup (34 MB) | 166 ms |
| List backups · verify one backup | 0.2 ms · 64 ms |
| Restore pre-check (`inspect_backup`) | 83 ms |
| **Restore** (verify 82 · safety backup 178 · copy 165 · publish 28 · reopen 98) | **552 ms** |
| Window refresh after a restore | 34 ms |
| Export page with preview | 213 ms |

## 32. Remaining risks (Phase 8)

* **All backups are on the same disk** as the database; a disk failure loses both. The program
  recommends copying `backups/` elsewhere but cannot enforce it.
* **Restore across processes**: another program (not this application) writing the file between the
  final hash check and the atomic rename would write into the displaced inode — kept, not lost, but not
  in the restored database. The instance lock covers this application.
* **Windows**: `os.replace` over a file another process has open fails (sharing violation) — the
  restore then reports `PUBLISH_FAILED` and rolls back; hard links on FAT/exFAT or network drives fall
  back to a verified copy. Not tested on real Windows.
* `displaced/` grows with every restore (one database copy each); nothing deletes it automatically.
* The interrupted-restore `AMBIGUOUS` case needs a person to choose a file; there is no in-app chooser.
* Restoring a backup brings back its `export_runs`; exports published after the backup are still on
  disk but no longer listed in the database.
* Qt's Georgian catalogue may be missing in a stripped package → only the override essentials are
  Georgian (Phase 10: bundle `qtbase_ka.qm`).
* The visual checks are offscreen renders; real-platform DPI/scaling and fonts may differ.

## 33. Phase 9 handoff — reliability, data integrity and release readiness

* Re-run every crash suite (`test_edit_crash`, `test_review_crash`, `test_export_publication`,
  `test_restore_crash`, seed install) on **real Windows 10/11 and CachyOS**, including antivirus
  interference and a USB/exFAT data folder (no hard links).
* Power-loss style tests (fsync behaviour) are beyond SIGKILL; consider a VM snapshot harness.
* Long-session soak test (hours of navigation/editing with autosave) watching memory and handle counts.
* Fuzz the TXT parser and the manifest/journal readers; property-test round trips.
* End-to-end acceptance script: empty start → import partial bank → review → export → backup → restore
  → export again; compare outputs.
* Review all exit codes and Georgian messages with a native speaker; screen-reader check (NVDA/Orca).
* Decide "save backup as…" (copy to another device) and a start-up backup when the last is old.
