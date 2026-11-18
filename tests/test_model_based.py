"""Phase 9b, campaign E — stateful model-based testing of the reviewer workflow.

A Hypothesis ``RuleBasedStateMachine`` drives the real services on a temporary synthetic bank with
random sequences of: autosave draft, discard draft, save revision, start review, approve,
approve-and-next, reject, reopen, restore an earlier revision, a second reviewer with a stale
``lock_version``, create backup, restore backup, export (TXT, read back) and a simulated crash
(a child process killed inside an approval) followed by a restart. A small independent model keeps
what the reviewer was *told*: every revision ever confirmed, the current and approved revision, the
status and the confirmed draft of every question.

Invariants after EVERY step: the approval is bound to the exact approved revision (and its APPROVE
event); revisions are contiguous and append-only and none ever changes; no confirmed draft or
revision is ever lost; a status only changes through an explicit action (never through a draft);
the counts per subject and status equal a recount; an export contains exactly the approved
questions with their approved text; ``integrity_check`` and ``foreign_key_check`` hold.

CI size by default; ``VASCQBANK_MODEL_EXAMPLES`` / ``VASCQBANK_MODEL_STEPS`` raise it (nightly).
"""

from __future__ import annotations

import collections
import copy
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")

from bank import seed_counts  # noqa: E402
from crash import CHILD_DIE, KILLED_RETURNCODE  # noqa: E402
from hypothesis import HealthCheck, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402
from hypothesis.stateful import (  # noqa: E402
    RuleBasedStateMachine,
    initialize,
    invariant,
    precondition,
    rule,
)

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.answers import ANSWER_LETTERS, AnswerLetter  # noqa: E402
from vascqbank_desktop.domain.editing import normalize_content, same_content  # noqa: E402
from vascqbank_desktop.domain.question import QuestionContent  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.workflow import ReviewAction, is_repeat, next_status  # noqa: E402
from vascqbank_desktop.importexport.paired_txt import parse_pair  # noqa: E402
from vascqbank_desktop.services.backup_service import create_manual_backup  # noqa: E402
from vascqbank_desktop.services.draft_service import DraftService  # noqa: E402
from vascqbank_desktop.services.edit_errors import (  # noqa: E402
    EditConflict,
    InvalidTransition,
    UnresolvedDraft,
)
from vascqbank_desktop.services.edit_service import EditService  # noqa: E402
from vascqbank_desktop.services.export_service import (  # noqa: E402
    ExportEmpty,
    ExportFormat,
    ExportRequest,
    ExportService,
)
from vascqbank_desktop.services.question_stats import bank_counts  # noqa: E402
from vascqbank_desktop.services.restore_service import inspect_backup, restore_backup  # noqa: E402
from vascqbank_desktop.services.review_service import ReviewService  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.startup import Outcome, prepare_storage  # noqa: E402

REVIEWER = "მოდელის რეცენზენტი"
UIDS = ("ALI-0001", "ALI-0002", "ALI-0003", "VENOUS-0001", "VENOUS-0002")
# Georgian (Mkhedruli + Mtavruli), Latin, digits, combining mark, ZWJ/ZWNJ, NBSP, RTL mark, emoji
ALPHABET = "აბგდევზთიკლმნოჟრსტუფქღყშჩცძწჭხჯჰᲐᲑᲒ abcXYZ0123456789.,;:()%≥±–—„“́‍‌ ‏😀\t"
field_text = st.text(alphabet=ALPHABET, min_size=1, max_size=40).filter(lambda t: t.strip() != "")
CALLS: collections.Counter[str] = collections.Counter()  # rules that ran to completion (reported)
EXAMPLES = int(os.environ.get("VASCQBANK_MODEL_EXAMPLES", "60"))
STEPS = int(os.environ.get("VASCQBANK_MODEL_STEPS", "25"))


@dataclass
class QModel:
    status: ReviewStatus = ReviewStatus.PENDING_REVIEW
    revisions: list[QuestionContent] = field(default_factory=list)  # index = revision_no - 1
    approved_no: int | None = None
    draft: QuestionContent | None = None


def _content(stem: str, explanation: str, letter: int) -> QuestionContent:
    options = tuple(f"{stem[:10]} — ვარიანტი {x}" for x in ANSWER_LETTERS)
    return QuestionContent(stem, options, AnswerLetter(ANSWER_LETTERS[letter % 5]), explanation)


@pytest.fixture(scope="module")
def template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("model-template") / "მონაცემები"
    paths = AppPaths(data_dir=folder)
    seed_counts(paths, {"ALI": 3, "VENOUS": 2})
    ReviewerProfile(SettingsStore(paths.settings_file)).set_name(REVIEWER)
    return folder


def counted(fn):  # noqa: ANN001, ANN201
    def wrapper(self, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        result = fn(self, *args, **kwargs)
        CALLS[fn.__name__] += 1
        return result

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class ReviewWorkflow(RuleBasedStateMachine):
    template: Path  # set by the test below

    # ------------------------------------------------------------------ set-up
    @initialize()
    def start(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="vqb-model-"))
        self.paths = AppPaths(data_dir=self.tmp / "მონაცემები")
        shutil.copytree(self.template, self.paths.data_dir)
        self.profile = ReviewerProfile(SettingsStore(self.paths.settings_file))
        self.model: dict[str, QModel] = {}
        self.ids: dict[str, int] = {}
        conn = sqlite3.connect(self.paths.database_file)
        try:
            for uid in UIDS:
                qid, rev = conn.execute(
                    "SELECT id, current_revision_id FROM questions WHERE question_uid = ?", (uid,)
                ).fetchone()
                self.ids[uid] = int(qid)
                self.model[uid] = QModel(revisions=[self._revision_content(conn, rev)])
        finally:
            conn.close()
        self.seen_revisions: dict[int, tuple] = {}  # revision id -> row: append-only, never changes
        self.stale_locks: dict[str, int] = {uid: 0 for uid in UIDS}
        self.backups: list[tuple[Path, dict[str, QModel]]] = []

    def teardown(self) -> None:
        shutil.rmtree(getattr(self, "tmp", ""), ignore_errors=True)

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _revision_content(conn: sqlite3.Connection, revision_id: int) -> QuestionContent:
        r = conn.execute(
            "SELECT stem, option_a, option_b, option_c, option_d, option_e, correct_letter, explanation "
            "FROM question_revisions WHERE id = ?",
            (revision_id,),
        ).fetchone()
        return QuestionContent(r[0], tuple(r[1:6]), AnswerLetter(r[6]), r[7])

    def _lock(self, uid: str) -> int:
        conn = sqlite3.connect(self.paths.database_file)
        try:
            return int(
                conn.execute("SELECT lock_version FROM questions WHERE id = ?", (self.ids[uid],)).fetchone()[
                    0
                ]
            )
        finally:
            conn.close()

    def _current(self, uid: str) -> QuestionContent:
        return self.model[uid].revisions[-1]

    def _review(self) -> ReviewService:
        return ReviewService(self.paths, self.profile)

    def _apply_decision(self, uid: str, action: ReviewAction, call) -> None:  # noqa: ANN001
        q = self.model[uid]
        allowed = next_status(q.status, action) is not None or is_repeat(q.status, action)
        pending = q.draft is not None and not same_content(q.draft, self._current(uid))
        try:
            result = call(self.ids[uid], self._lock(uid))
        except InvalidTransition:
            assert not allowed, (uid, q.status, action)
            return
        except UnresolvedDraft:
            assert action is ReviewAction.APPROVE and pending, (uid, q.status)
            return
        assert allowed, (uid, q.status, action)
        if action is ReviewAction.APPROVE:
            assert not pending, "approved over an unsaved, different draft"
            if q.draft is not None:  # an identical draft carries nothing and goes with the approval
                q.draft = None
        if result.changed:
            q.status = result.status_after
            q.approved_no = len(q.revisions) if q.status is ReviewStatus.APPROVED else None
        assert q.status is result.status_after

    # ------------------------------------------------------------------ rules
    @rule(uid=st.sampled_from(UIDS), stem=field_text, explanation=field_text, letter=st.integers(0, 4))
    @counted
    def autosave_draft(self, uid: str, stem: str, explanation: str, letter: int) -> None:
        content = _content(stem, explanation, letter)
        rev_no = len(self.model[uid].revisions)
        conn = sqlite3.connect(self.paths.database_file)
        try:
            rev_id = conn.execute(
                "SELECT id FROM question_revisions WHERE question_id = ? AND revision_no = ?",
                (self.ids[uid], rev_no),
            ).fetchone()[0]
        finally:
            conn.close()
        status_before = self.model[uid].status
        DraftService(self.paths).save_draft(self.ids[uid], rev_id, self._lock(uid), content, REVIEWER)
        self.model[uid].draft = normalize_content(content)
        assert self.model[uid].status is status_before  # a draft never changes a status

    @rule(uid=st.sampled_from(UIDS))
    @counted
    def discard_draft(self, uid: str) -> None:
        removed = DraftService(self.paths).discard_draft(self.ids[uid], REVIEWER)
        assert removed == (self.model[uid].draft is not None)
        self.model[uid].draft = None

    @rule(uid=st.sampled_from(UIDS), stem=field_text, explanation=field_text, letter=st.integers(0, 4),
          from_draft=st.booleans())  # fmt: skip
    @counted
    def save_revision(self, uid: str, stem: str, explanation: str, letter: int, from_draft: bool) -> None:
        q = self.model[uid]
        content = (
            q.draft
            if from_draft and q.draft is not None
            else normalize_content(_content(stem, explanation, letter))
        )
        self.stale_locks[uid] = self._lock(uid)  # what a second reviewer saw before this save
        result = EditService(self.paths, self.profile).save(self.ids[uid], self._lock(uid), content)
        changed = not same_content(content, self._current(uid))
        assert result.created == changed
        if changed:
            q.revisions.append(content)
            assert result.revision_no == len(q.revisions)
        assert result.draft_cleared == (q.draft is not None and same_content(q.draft, content))
        if result.draft_cleared:
            q.draft = None
        q.status = result.status_after
        if q.status is not ReviewStatus.APPROVED:
            q.approved_no = None

    @rule(uid=st.sampled_from(UIDS))
    @counted
    def start_review(self, uid: str) -> None:
        self._apply_decision(uid, ReviewAction.START_REVIEW, self._review().start_review)

    @rule(uid=st.sampled_from(UIDS), note=st.sampled_from([None, "კარგია", "დამტკიცება და შემდეგი"]))
    @counted
    def approve(self, uid: str, note: str | None) -> None:
        self._apply_decision(
            uid, ReviewAction.APPROVE, lambda qid, lock: self._review().approve(qid, lock, note)
        )

    @rule(uid=st.sampled_from(UIDS))
    @counted
    def approve_and_next(self, uid: str) -> None:
        """What Ctrl+Enter does at the service level: approve, then the next question is opened."""
        self.approve(uid, None)
        nxt = UIDS[(UIDS.index(uid) + 1) % len(UIDS)]
        self._lock(nxt)  # opening the next question reads only

    @rule(uid=st.sampled_from(UIDS))
    @counted
    def reject(self, uid: str) -> None:
        self._apply_decision(
            uid, ReviewAction.REJECT, lambda qid, lock: self._review().reject(qid, lock, "სინთეზური მიზეზი")
        )

    @rule(uid=st.sampled_from(UIDS))
    @counted
    def reopen(self, uid: str) -> None:
        self._apply_decision(uid, ReviewAction.REOPEN, self._review().reopen)

    @rule(uid=st.sampled_from(UIDS), which=st.integers(0, 10), keep_draft=st.booleans())
    @counted
    def restore_revision(self, uid: str, which: int, keep_draft: bool) -> None:
        q = self.model[uid]
        number = which % len(q.revisions) + 1
        conn = sqlite3.connect(self.paths.database_file)
        try:
            source = conn.execute(
                "SELECT id FROM question_revisions WHERE question_id = ? AND revision_no = ?",
                (self.ids[uid], number),
            ).fetchone()[0]
        finally:
            conn.close()
        target = q.revisions[number - 1]
        pending = q.draft is not None and not same_content(q.draft, target)
        try:
            result = self._review().restore_revision(
                self.ids[uid], self._lock(uid), source, keep_draft=keep_draft
            )
        except UnresolvedDraft:
            assert pending and not keep_draft
            return
        changed = not same_content(target, self._current(uid))
        assert result.created_revision == changed
        if changed:
            q.revisions.append(target)
        if result.draft_cleared:
            assert q.draft is not None and same_content(q.draft, target)
            q.draft = None
        q.status = result.status_after
        if q.status is not ReviewStatus.APPROVED:
            q.approved_no = None

    @rule(uid=st.sampled_from(UIDS))
    @counted
    def stale_second_reviewer(self, uid: str) -> None:
        """Another reviewer acts on the text as it was before the last save: refused, nothing changes."""
        stale = self.stale_locks[uid]
        if stale == self._lock(uid):
            return
        before = self._snapshot()
        with pytest.raises(EditConflict):
            self._review().approve(self.ids[uid], stale)
        with pytest.raises(EditConflict):
            EditService(self.paths, self.profile).save(self.ids[uid], stale, _content("მოძველებული", "x", 1))
        assert self._snapshot() == before

    @rule()
    @counted
    def create_backup(self) -> None:
        entry = create_manual_backup(self.paths)
        self.backups.append((entry.path, copy.deepcopy(self.model)))

    @precondition(lambda self: bool(self.backups))
    @rule(which=st.integers(0, 5))
    @counted
    def restore_backup(self, which: int) -> None:
        path, model = self.backups[which % len(self.backups)]
        restore_backup(self.paths, path, expected_sha256=inspect_backup(path).sha256)
        self.model = copy.deepcopy(model)
        self.seen_revisions = {}  # later revisions were rolled back on purpose (kept in displaced/)
        self.stale_locks = {uid: 0 for uid in UIDS}

    @rule()
    @counted
    def export(self) -> None:
        approved = {uid for uid, q in self.model.items() if q.status is ReviewStatus.APPROVED}
        destination = self.tmp / "exports"
        try:
            result = ExportService(self.paths).run(
                ExportRequest(
                    subjects=("ALI", "VENOUS"), formats=frozenset({ExportFormat.TXT}), destination=destination
                )
            )
        except ExportEmpty:
            assert not approved
            return
        exported: dict[str, QuestionContent] = {}
        for questions in sorted((result.folder / "TXT").glob("*_questions.txt")):
            explanations = questions.with_name(questions.name.replace("_questions", "_explanations"))
            parsed = parse_pair(questions.name.split("_")[0], questions, explanations)
            for item in parsed.items:
                assert item.is_valid, [i.message for i in item.issues]
                exported[str(item.qid)] = item.question.content
        assert set(exported) == approved
        for uid in approved:
            q = self.model[uid]
            assert same_content(exported[uid], q.revisions[q.approved_no - 1]), uid

    @rule(
        uid=st.sampled_from(UIDS),
        at=st.sampled_from(["UPDATE questions", "INSERT INTO review_events", "COMMIT"]),
    )
    @counted
    def crash_during_approval_and_restart(self, uid: str, at: str) -> None:
        before = self._snapshot()
        child = subprocess.run(
            [sys.executable, "-c", CRASH_CHILD, str(self.paths.data_dir), str(self.ids[uid]), at],
            capture_output=True, text=True, timeout=120,
        )  # fmt: skip
        if "NOT-ATTEMPTED" in child.stdout:  # the approval would be refused anyway: nothing to kill
            return
        assert child.returncode == KILLED_RETURNCODE, child.stderr[-2000:]
        result = prepare_storage(self.paths, None)  # the restart
        assert result.outcome is Outcome.READY and result.status is not None and result.status.integrity_ok
        assert self._snapshot() == before  # killed inside the transaction: as if it never started

    # ------------------------------------------------------------------ invariants
    def _snapshot(self) -> tuple:
        conn = sqlite3.connect(self.paths.database_file)
        try:
            return tuple(
                tuple(tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1"))
                for t in ("questions", "question_revisions", "review_events", "drafts")
            )
        finally:
            conn.close()

    @invariant()
    def database_matches_the_model(self) -> None:
        if not hasattr(self, "paths"):
            return
        conn = sqlite3.connect(self.paths.database_file)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
            for uid, q in self.model.items():
                qid = self.ids[uid]
                status, current, approved, approved_at = conn.execute(
                    "SELECT status, current_revision_id, approved_revision_id, approved_at FROM questions"
                    " WHERE id = ?",
                    (qid,),
                ).fetchone()
                assert ReviewStatus(status) is q.status, uid  # only explicit actions change a status
                rows = conn.execute(
                    "SELECT id, revision_no, stem, option_a, option_b, option_c, option_d, option_e,"
                    " correct_letter, explanation FROM question_revisions WHERE question_id = ?"
                    " ORDER BY revision_no",
                    (qid,),
                ).fetchall()
                assert [r[1] for r in rows] == list(range(1, len(q.revisions) + 1)), uid  # contiguous
                for row, expected in zip(rows, q.revisions, strict=True):  # no confirmed revision lost
                    content = QuestionContent(row[2], tuple(row[3:8]), AnswerLetter(row[8]), row[9])
                    assert same_content(content, expected), (uid, row[1])
                    seen = self.seen_revisions.setdefault(int(row[0]), tuple(row))
                    assert seen == tuple(row), "a revision changed after it was written"
                assert current == rows[-1][0]
                if q.status is ReviewStatus.APPROVED:  # bound to the exact approved revision
                    assert approved == rows[q.approved_no - 1][0] and approved_at is not None
                    last = conn.execute(
                        "SELECT revision_id FROM review_events WHERE question_id = ? AND action = 'APPROVE' "
                        "ORDER BY id DESC LIMIT 1",
                        (qid,),
                    ).fetchone()
                    assert last is not None and last[0] == approved
                else:
                    assert approved is None and approved_at is None
                draft = conn.execute(
                    "SELECT stem, option_a, option_b, option_c, option_d, option_e, correct_letter,"
                    " explanation FROM drafts WHERE question_id = ?",
                    (qid,),
                ).fetchone()
                if q.draft is None:
                    assert draft is None, uid
                else:  # no confirmed draft is ever lost or changed
                    assert draft is not None, uid
                    stored = QuestionContent(draft[0], tuple(draft[1:6]), AnswerLetter(draft[6]), draft[7])
                    assert same_content(stored, q.draft), uid
            recount = dict(
                ((s, st_), n)
                for s, st_, n in conn.execute(
                    "SELECT subject_code, status, count(*) FROM questions GROUP BY 1, 2"
                )
            )
        finally:
            conn.close()
        counts = bank_counts(self.paths)
        for subject in ("ALI", "VENOUS"):
            for status in ReviewStatus:
                assert counts.subjects[subject].count(status) == recount.get((subject, status.value), 0)
            assert counts.subjects[subject].drafts == sum(
                1 for uid, q in self.model.items() if uid.startswith(subject) and q.draft is not None
            )


RULES = (
    "autosave_draft", "discard_draft", "save_revision", "start_review", "approve", "approve_and_next",
    "reject", "reopen", "restore_revision", "stale_second_reviewer", "create_backup", "restore_backup",
    "export", "crash_during_approval_and_restart",
)  # fmt: skip

CRASH_CHILD = CHILD_DIE + textwrap.dedent(
    """
    import sqlite3, sys
    from pathlib import Path
    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.db import connection
    from vascqbank_desktop.services import review_service
    from vascqbank_desktop.services.edit_errors import EditError
    from vascqbank_desktop.services.review_service import ReviewService
    from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

    paths = AppPaths(data_dir=Path(sys.argv[1]))
    qid, kill_at = int(sys.argv[2]), sys.argv[3]
    real_open = connection.open_database

    def dying_open(path):
        conn = real_open(path)
        conn.set_trace_callback(lambda statement: die() if kill_at in statement else None)
        return conn

    review_service.open_database = dying_open
    check = sqlite3.connect(paths.database_file)
    lock = check.execute("SELECT lock_version FROM questions WHERE id = ?", (qid,)).fetchone()[0]
    check.close()
    try:
        result = ReviewService(paths, ReviewerProfile(SettingsStore(paths.settings_file))).approve(qid, lock)
    except EditError:
        print("NOT-ATTEMPTED")  # refused before writing (status, draft): nothing to kill
        sys.exit(0)
    # a repeat approval writes nothing, so nothing was killed; anything else must not get here
    print("NOT-ATTEMPTED" if not result.changed else "SURVIVED")
    """
)


def test_the_review_workflow_keeps_every_invariant(template: Path) -> None:
    ReviewWorkflow.template = template
    machine = ReviewWorkflow.TestCase
    machine.settings = settings(
        max_examples=EXAMPLES,
        stateful_step_count=STEPS,
        deadline=None,
        derandomize=True,
        database=None,
        suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    )
    CALLS.clear()
    machine().runTest()
    print("\n[model] rule calls:", dict(sorted(CALLS.items())))
    assert all(CALLS[name] > 0 for name in RULES), CALLS
