"""Catalog service: pagination, filters, sorting, search (FTS5 and LIKE), lookup, read-only guarantee."""

from __future__ import annotations

import sqlite3

import pytest
from bank import make_question, seed, set_status, simulate_edit, snapshot

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import search_index
from vascqbank_desktop.domain.answers import ANSWER_LETTERS, AnswerLetter
from vascqbank_desktop.domain.catalog import CatalogFilter, SearchMode, SortOrder
from vascqbank_desktop.domain.question import SourceKind
from vascqbank_desktop.domain.question_id import InvalidReference, ReferenceProblem
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import SUBJECTS
from vascqbank_desktop.services.catalog_service import CatalogService, QuestionNotFound

CAROTID_STEM = "პაციენტს აღენიშნება კაროტიდული სტენოზი 70% და ინსულტის ანამნეზი."
OPTIONS_WITH_WILDCARDS = (
    "ა ვარიანტი",
    "ბ ვარიანტი",
    "გ — ორმხრივი ბლოკი _ 50% (მაგალითი)",
    "დ ვარიანტი",
    "ე ვარიანტი",
)


def _bank() -> list:
    questions = [make_question("ALI", n) for n in range(1, 41)]
    questions += [make_question("VENOUS", n) for n in range(1, 26)]
    questions += [make_question("PAD_CLTI", n) for n in range(1, 16)]
    by_uid = {q.uid: i for i, q in enumerate(questions)}

    def replace(code: str, number: int, **kwargs) -> None:
        questions[by_uid[f"{code}-{number:04d}"]] = make_question(code, number, **kwargs)

    replace("ALI", 3, stem=CAROTID_STEM)
    replace("ALI", 10, options=OPTIONS_WITH_WILDCARDS)
    replace("VENOUS", 5, explanation="ეს ასახსნელი EVAR ან CTA გამოკვლევას ეხება.")
    replace("VENOUS", 7, stem="კაროტიდული არტერიის შეფასება ექოსკოპიით")
    replace("PAD_CLTI", 2, stem='ტექსტი AND NOT "ციტატა" * -y OR NEAR(a b)')
    return questions


@pytest.fixture
def bank(app_paths: AppPaths) -> CatalogService:
    seed(app_paths, _bank())
    return CatalogService(app_paths)


def uids(service: CatalogService, flt: CatalogFilter) -> list[str]:
    results = service.query(flt)
    return [r.uid for r in service.rows(results.ids, results.terms)]


# ------------------------------------------------------------------ pagination


def test_listing_resolves_ids_and_rows_load_page_by_page(bank: CatalogService) -> None:
    results = bank.query(CatalogFilter())
    assert len(results) == 80 and results.mode is SearchMode.BROWSE
    pages = [bank.rows(results.ids[i : i + 25], ()) for i in range(0, len(results), 25)]
    assert [len(p) for p in pages] == [25, 25, 25, 5]
    flat = [row for page in pages for row in page]
    # rows come back exactly in id order, without gaps or duplicates
    assert [r.id for r in flat] == list(results.ids)
    assert len({r.uid for r in flat}) == 80


def test_default_order_is_subject_order_then_number(bank: CatalogService) -> None:
    order = [s.code for s in SUBJECTS]
    flat = uids(bank, CatalogFilter())
    codes = [u.rsplit("-", 1)[0] for u in flat]
    assert codes == sorted(codes, key=order.index)  # PAD_CLTI, ALI, VENOUS — the app's subject order
    assert flat[0] == "PAD_CLTI-0001" and flat[15] == "ALI-0001" and flat[-1] == "VENOUS-0025"
    numbers = [int(u.rsplit("-", 1)[1]) for u in flat if u.startswith("ALI-")]
    assert numbers == list(range(1, 41))


def test_rows_of_unknown_ids_are_skipped_and_empty_input_is_empty(bank: CatalogService) -> None:
    assert bank.rows([], ()) == []
    rows = bank.rows([1, 999_999, 2], ())
    assert [r.id for r in rows] == [1, 2]


def test_row_summary_carries_identity_status_and_preview(bank: CatalogService) -> None:
    row = bank.rows(bank.query(CatalogFilter(subject_code="ALI")).ids[:1])[0]
    assert (row.uid, row.subject_code, row.source_number) == ("ALI-0001", "ALI", 1)
    assert row.status is ReviewStatus.PENDING_REVIEW
    assert row.preview.text.startswith("სინთეზური სატესტო კითხვა ALI №1")


# ------------------------------------------------------------------ filters


def test_subject_and_status_filters(bank: CatalogService, app_paths: AppPaths) -> None:
    for uid, status in (
        ("ALI-0001", ReviewStatus.IN_REVIEW),
        ("ALI-0002", ReviewStatus.APPROVED),
        ("ALI-0003", ReviewStatus.APPROVED),
        ("VENOUS-0001", ReviewStatus.REJECTED),
    ):
        set_status(app_paths, uid, status)
    assert bank.count() == 80 and bank.count("ALI") == 40 and bank.count("VENOUS") == 25
    assert bank.count(None, ReviewStatus.APPROVED) == 2
    assert uids(bank, CatalogFilter(subject_code="ALI", status=ReviewStatus.APPROVED)) == [
        "ALI-0002",
        "ALI-0003",
    ]
    assert uids(bank, CatalogFilter(status=ReviewStatus.REJECTED)) == ["VENOUS-0001"]
    assert uids(bank, CatalogFilter(subject_code="VENOUS", status=ReviewStatus.APPROVED)) == []
    assert len(uids(bank, CatalogFilter(subject_code="ALI", status=ReviewStatus.PENDING_REVIEW))) == 37


def test_identity_is_independent_of_sorting_and_filtering(bank: CatalogService, app_paths: AppPaths) -> None:
    set_status(app_paths, "ALI-0004", ReviewStatus.APPROVED)
    simulate_edit(app_paths, "ALI-0006", stem="ცვლილების შემდეგ")
    expected = {r.id: r.uid for r in bank.rows(bank.query(CatalogFilter()).ids)}
    for sort in SortOrder:
        results = bank.query(CatalogFilter(sort=sort))
        assert sorted(results.ids) == sorted(expected)  # same questions, only another order
        for row in bank.rows(results.ids):
            assert expected[row.id] == row.uid
    filtered = bank.query(CatalogFilter(subject_code="ALI", status=ReviewStatus.APPROVED))
    assert [expected[i] for i in filtered.ids] == ["ALI-0004"]
    searched = bank.query(CatalogFilter(text="ცვლილების"))
    assert [expected[i] for i in searched.ids] == ["ALI-0006"]
    assert bank.detail(searched.ids[0]).uid == "ALI-0006"


def test_sorting_by_status(bank: CatalogService, app_paths: AppPaths) -> None:
    set_status(app_paths, "ALI-0001", ReviewStatus.APPROVED)
    set_status(app_paths, "ALI-0002", ReviewStatus.REJECTED)
    simulate_edit(app_paths, "ALI-0003", stem="შეცვლილი")  # pending becomes in review
    ordered = uids(bank, CatalogFilter(subject_code="ALI", sort=SortOrder.STATUS))
    # pending first (still by number), then in review, rejected, approved
    assert ordered[0] == "ALI-0004" and ordered[36] == "ALI-0040"
    assert ordered[-3:] == ["ALI-0003", "ALI-0002", "ALI-0001"]


def test_sorting_by_last_modified_puts_the_newest_first(bank: CatalogService, app_paths: AppPaths) -> None:
    # Timestamps have one-second precision, so set distinct ones explicitly.
    raw = sqlite3.connect(app_paths.database_file)
    for uid, stamp in (
        ("ALI-0007", "2030-01-03T10:00:00+00:00"),
        ("ALI-0002", "2030-01-02T10:00:00+00:00"),
        ("ALI-0009", "2030-01-01T10:00:00+00:00"),
    ):
        raw.execute("UPDATE questions SET modified_at = ? WHERE question_uid = ?", (stamp, uid))
    raw.commit()
    raw.close()
    ordered = uids(bank, CatalogFilter(subject_code="ALI", sort=SortOrder.MODIFIED))
    assert ordered[:3] == ["ALI-0007", "ALI-0002", "ALI-0009"]
    assert ordered[3:6] == ["ALI-0001", "ALI-0003", "ALI-0004"]  # the rest tie on import time: by number


# ------------------------------------------------------------------ Georgian search


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("კაროტიდული", ["ALI-0003", "VENOUS-0007"]),  # whole word, two subjects
        ("კაროტ", ["ALI-0003", "VENOUS-0007"]),  # word fragment
        ("როტიდულ", ["ALI-0003", "VENOUS-0007"]),  # fragment from the middle
        ("სტენოზი 70%", ["ALI-0003"]),  # two terms, both required
        ("კაროტიდული ექოსკოპ", ["VENOUS-0007"]),
        ("evar", ["VENOUS-0005"]),  # Latin, case-insensitive
        ("EVAR cta", ["VENOUS-0005"]),
        ("ALI-0003", ["ALI-0003"]),  # by ID
        ("ali-0003", ["ALI-0003"]),
        ("VENOUS-0005", ["VENOUS-0005"]),
        ("ზედმეტი არარსებული სიტყვა", []),
        ("ინსულტ", ["ALI-0003"]),
    ],
)
@pytest.mark.parametrize("use_fts", [True, False], ids=["fts5", "like"])
def test_georgian_and_latin_search(
    app_paths: AppPaths, text: str, expected: list[str], use_fts: bool
) -> None:
    seed(app_paths, _bank())
    service = CatalogService(app_paths, use_fts=use_fts)
    results = service.query(CatalogFilter(text=text))
    assert [r.uid for r in service.rows(results.ids, results.terms)] == expected
    fts_capable = use_fts and all(len(t) >= 3 for t in results.terms)
    assert results.mode is (SearchMode.FTS if fts_capable else SearchMode.LIKE)


def test_search_covers_stem_options_explanation_and_id(bank: CatalogService) -> None:
    by_field = {
        "stem": ("ექოსკოპიით", "VENOUS-0007"),
        "option": ("ორმხრივი", "ALI-0010"),
        "explanation": ("ასახსნელი", "VENOUS-0005"),
        "id": ("PAD_CLTI-0002", "PAD_CLTI-0002"),
    }
    for field, (text, uid) in by_field.items():
        assert uids(bank, CatalogFilter(text=text)) == [uid], field


def test_search_matches_the_current_revision_not_the_original(
    bank: CatalogService, app_paths: AppPaths
) -> None:
    assert uids(bank, CatalogFilter(text="კაროტიდული ექოსკოპ")) == ["VENOUS-0007"]
    simulate_edit(app_paths, "VENOUS-0007", stem="სრულიად ახალი ფორმულირება სახელმძღვანელოდან")
    assert uids(bank, CatalogFilter(text="ექოსკოპ")) == []  # old wording no longer matches
    assert uids(bank, CatalogFilter(text="ფორმულირება სახელმძღვანელო")) == ["VENOUS-0007"]
    assert uids(bank, CatalogFilter(text="ფორმულირება")) == ["VENOUS-0007"]


def test_search_respects_subject_and_status_filters(bank: CatalogService, app_paths: AppPaths) -> None:
    assert uids(bank, CatalogFilter(text="კაროტიდული", subject_code="VENOUS")) == ["VENOUS-0007"]
    set_status(app_paths, "ALI-0003", ReviewStatus.APPROVED)
    assert uids(bank, CatalogFilter(text="კაროტიდული", status=ReviewStatus.APPROVED)) == ["ALI-0003"]
    assert (
        uids(bank, CatalogFilter(text="კაროტიდული", subject_code="VENOUS", status=ReviewStatus.APPROVED))
        == []
    )


@pytest.mark.parametrize("use_fts", [True, False], ids=["fts5", "like"])
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("50%", ["ALI-0010"]),  # '%' is literal, not a wildcard
        ("%", ["ALI-0003", "ALI-0010"]),
        ("_ 50", ["ALI-0010"]),  # '_' is literal
        ("AND", ["PAD_CLTI-0002"]),  # FTS operators are plain text
        ("NOT", ["PAD_CLTI-0002"]),
        ("NEAR(a", ["PAD_CLTI-0002"]),
        ('"ციტატა"', ["PAD_CLTI-0002"]),
        ("*", ["PAD_CLTI-0002"]),
        ("-y", ["PAD_CLTI-0002"]),
        ("'; DROP TABLE questions; --", []),
    ],
)
def test_special_characters_are_literal(
    app_paths: AppPaths, text: str, expected: list[str], use_fts: bool
) -> None:
    seed(app_paths, _bank())
    service = CatalogService(app_paths, use_fts=use_fts)
    assert sorted(uids(service, CatalogFilter(text=text))) == sorted(expected)
    assert service.count() == 80  # nothing was damaged by the query


def test_fts_and_like_agree_on_many_queries(app_paths: AppPaths) -> None:
    seed(app_paths, _bank())
    fast, slow = CatalogService(app_paths, use_fts=True), CatalogService(app_paths, use_fts=False)
    for text in (
        "სატესტო",
        "ვარიანტი",
        "ტექსტი ALI",
        "№12",
        "ALI-00",
        "0001",
        "VENOUS-0005 გამოკვლევა",
        "ბლოკი _",
        "კითხვა PAD_CLTI",
        "სწორი?",
        "(მაგალითი)",
        "დ ვარიანტი",
        "x",
        "აბ",
        "ბ",
    ):
        for flt in (
            CatalogFilter(text=text),
            CatalogFilter(text=text, subject_code="ALI", sort=SortOrder.STATUS),
        ):
            assert fast.query(flt).ids == slow.query(flt).ids, text


def test_short_terms_use_the_like_scan(bank: CatalogService) -> None:
    results = bank.query(CatalogFilter(text="ბა"))  # two characters: trigram cannot answer
    assert results.mode is SearchMode.LIKE
    mixed = bank.query(CatalogFilter(text="ვარიანტი ბ"))
    assert mixed.mode is SearchMode.LIKE and len(mixed) == 80


# ------------------------------------------------------------------ index lifecycle


def test_first_search_builds_a_small_index_inline_and_uses_it(
    bank: CatalogService, app_paths: AppPaths
) -> None:
    assert not app_paths.search_index_file.exists()
    assert bank.query(CatalogFilter(text="კაროტიდული")).mode is SearchMode.FTS
    assert app_paths.search_index_file.is_file()


def test_large_unindexed_bank_uses_like_until_the_warm_up_builds_the_index(app_paths: AppPaths) -> None:
    seed(app_paths, [make_question("ALI", n) for n in range(1, 401)])
    service = CatalogService(app_paths)
    first = service.query(CatalogFilter(text="სატესტო"))
    assert first.mode is SearchMode.LIKE and len(first) == 400  # answered without waiting for a build
    status = service.ensure_search_index()
    assert status.available and status.rebuilt and status.indexed == 400
    assert service.query(CatalogFilter(text="სატესტო")).mode is SearchMode.FTS
    again = service.ensure_search_index()
    assert again.available and not again.rebuilt and again.indexed == 0  # nothing to do


def test_edits_are_picked_up_incrementally(bank: CatalogService, app_paths: AppPaths) -> None:
    bank.ensure_search_index()
    simulate_edit(app_paths, "ALI-0005", explanation="უნიკალური განმარტება მეტაბოლური აციდოზის შესახებ")
    results = bank.query(CatalogFilter(text="მეტაბოლური აციდოზ"))
    assert results.mode is SearchMode.FTS  # synchronised within the search itself
    assert [r.uid for r in bank.rows(results.ids)] == ["ALI-0005"]
    refresh = bank.ensure_search_index()
    assert refresh.available and not refresh.rebuilt and refresh.indexed == 0


def test_index_of_another_database_is_rebuilt(bank: CatalogService, app_paths: AppPaths) -> None:
    bank.ensure_search_index()
    raw = sqlite3.connect(app_paths.search_index_file)
    raw.execute("UPDATE meta SET value = 'some-other-database' WHERE key = 'database_id'")
    raw.commit()
    raw.close()
    results = bank.query(CatalogFilter(text="კაროტიდული"))
    assert results.mode is SearchMode.FTS and len(results) == 2


def test_damaged_index_file_falls_back_to_like_and_is_rebuilt(
    bank: CatalogService, app_paths: AppPaths
) -> None:
    bank.ensure_search_index()
    app_paths.search_index_file.write_bytes(b"this is not a sqlite database" * 100)
    for suffix in ("-wal", "-shm"):
        (app_paths.search_index_file.parent / f"{app_paths.search_index_file.name}{suffix}").unlink(
            missing_ok=True
        )
    results = bank.query(CatalogFilter(text="კაროტიდული"))
    assert [r.uid for r in bank.rows(results.ids)] == ["ALI-0003", "VENOUS-0007"]  # still correct
    assert bank.ensure_search_index().available
    assert bank.query(CatalogFilter(text="კაროტიდული")).mode is SearchMode.FTS


def test_without_fts5_support_search_works_with_like_and_no_index_file(
    bank: CatalogService, app_paths: AppPaths, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(search_index, "fts5_trigram_supported", lambda: False)
    assert not bank.fts_available
    results = bank.query(CatalogFilter(text="კაროტიდული"))
    assert results.mode is SearchMode.LIKE and len(results) == 2
    status = bank.ensure_search_index()
    assert not status.available and "LIKE" in status.message
    assert not app_paths.search_index_file.exists()


def test_fts_failure_at_query_time_falls_back_to_like(
    bank: CatalogService, monkeypatch: pytest.MonkeyPatch
) -> None:
    bank.ensure_search_index()

    def broken(*_args, **_kwargs):
        raise sqlite3.OperationalError("fts5: simulated failure")

    monkeypatch.setattr("vascqbank_desktop.db.catalog_repository.search_ids_fts", broken)
    results = bank.query(CatalogFilter(text="კაროტიდული"))
    assert results.mode is SearchMode.LIKE and len(results) == 2


# ------------------------------------------------------------------ opening a question


def test_detail_has_every_field_the_panel_shows(bank: CatalogService) -> None:
    ali_3 = bank.locate("ALI-0003")
    assert ali_3 is not None
    detail = bank.detail(ali_3.id)
    assert detail is not None
    assert detail.uid == "ALI-0003" and detail.subject_code == "ALI"
    assert detail.subject_name_ka == "კიდურის მწვავე იშემია"
    assert detail.status is ReviewStatus.PENDING_REVIEW
    assert detail.content.stem == CAROTID_STEM
    assert len(detail.content.options) == 5 and detail.content.correct in set(AnswerLetter)
    assert detail.content.correct_text == detail.content.options[detail.content.correct.index]
    assert detail.revision_no == 1 and detail.lock_version == 0
    assert (detail.source_kind, detail.source_file, detail.source_line) == (
        SourceKind.PAIRED_TXT,
        "ALI_questions.txt",
        21,
    )
    assert detail.import_batch_id == 1
    assert detail.created_at == detail.modified_at and detail.approved_at is None
    assert bank.detail_by_uid("ALI-0003") == detail
    assert bank.detail(999_999) is None and bank.detail_by_uid("ALI-9999") is None


def test_detail_follows_edits_and_approval(bank: CatalogService, app_paths: AppPaths) -> None:
    simulate_edit(app_paths, "ALI-0003", stem="ახალი ტექსტი")
    set_status(app_paths, "ALI-0003", ReviewStatus.APPROVED)
    detail = bank.detail_by_uid("ALI-0003")
    assert detail is not None
    assert detail.content.stem == "ახალი ტექსტი" and detail.revision_no == 2 and detail.lock_version == 1
    assert detail.status is ReviewStatus.APPROVED and detail.approved_at is not None
    assert detail.modified_at >= detail.created_at


def test_resolve_reference(bank: CatalogService) -> None:
    assert bank.resolve_reference("ali-3").uid == "ALI-0003"
    assert bank.resolve_reference("7", default_subject="VENOUS").uid == "VENOUS-0007"
    with pytest.raises(QuestionNotFound) as missing:
        bank.resolve_reference("ALI-0999")
    assert missing.value.uid == "ALI-0999"
    with pytest.raises(InvalidReference) as invalid:
        bank.resolve_reference("12")
    assert invalid.value.problem is ReferenceProblem.NEEDS_SUBJECT


def test_nearest_id_prefers_the_following_question(bank: CatalogService) -> None:
    ali_10 = bank.locate("ALI-0010")
    assert ali_10 is not None
    approved = bank.nearest_id("ALI-0010", "ALI", ReviewStatus.APPROVED)
    assert approved is None  # nothing approved anywhere in ALI
    next_one = bank.nearest_id("ALI-0010", "ALI", None)
    assert next_one is not None and bank.locate_id(next_one).uid == "ALI-0011"
    last = bank.nearest_id("ALI-0040", "ALI", None)  # nothing follows: the closest preceding one
    assert last is not None and bank.locate_id(last).uid == "ALI-0039"
    across = bank.nearest_id("ALI-0040", None, None)  # next subject in the app's order
    assert across is not None and bank.locate_id(across).uid == "VENOUS-0001"
    assert bank.nearest_id("ALI-9999", None, None) is None


# ------------------------------------------------------------------ read-only guarantee


def test_browsing_and_searching_never_change_the_database(bank: CatalogService, app_paths: AppPaths) -> None:
    before = snapshot(app_paths)
    for flt in (
        CatalogFilter(),
        CatalogFilter(subject_code="ALI"),
        CatalogFilter(status=ReviewStatus.PENDING_REVIEW),
        CatalogFilter(text="კაროტიდული"),
        CatalogFilter(text="ბ"),
        CatalogFilter(text="ALI-0003"),
    ):
        results = bank.query(flt)
        bank.rows(results.ids[:50], results.terms)
        for question_id in results.ids[:20]:
            assert bank.detail(question_id) is not None
    bank.ensure_search_index()
    bank.nearest_id("ALI-0001", None, None)
    assert snapshot(app_paths) == before  # statuses, timestamps, lock versions, revisions, events


def test_service_connections_refuse_writes(bank: CatalogService) -> None:
    with bank._reader() as conn, pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("UPDATE questions SET status = 'APPROVED'")


def test_the_catalog_repository_contains_no_write_statements() -> None:
    from pathlib import Path

    import vascqbank_desktop.db.catalog_repository as module

    text = " ".join(Path(module.__file__).read_text(encoding="utf-8").upper().split())
    for keyword in (
        "INSERT INTO",
        "UPDATE QUESTIONS",
        "DELETE FROM",
        "DROP TABLE",
        "ALTER TABLE",
        "CREATE TABLE",
    ):
        assert keyword not in text, keyword


def test_answer_letters_cover_all_five_options() -> None:
    assert ANSWER_LETTERS == ("ა", "ბ", "გ", "დ", "ე")
