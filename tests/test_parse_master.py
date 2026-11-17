"""Format B — refined master parsing (synthetic records in the verified format)."""

from __future__ import annotations

from pathlib import Path

from synthetic import master_record, write_master

from vascqbank_desktop.domain.question import SourceKind
from vascqbank_desktop.importexport.discovery import discover
from vascqbank_desktop.importexport.refined_master import parse_master
from vascqbank_desktop.validation.issues import IssueCode


def codes(item) -> set[IssueCode]:
    return {i.code for i in item.issues}


def test_valid_records_keep_every_section(tmp_path: Path) -> None:
    path = write_master(tmp_path, [master_record("AAA_EVAR", 1), master_record("AAA_EVAR", 4),
                                   master_record("VENOUS", 2)])
    result = parse_master(path)
    assert [str(i.qid) for i in result.items] == ["AAA_EVAR-0001", "AAA_EVAR-0004", "VENOUS-0002"]
    assert all(i.is_valid for i in result.items), [i.issues for i in result.items]
    q = result.items[0].question
    assert q.source.kind is SourceKind.REFINED_MASTER
    assert q.content.stem == "რაფინირებული სინთეზური კითხვა AAA_EVAR №1?"
    assert q.content.explanation == "სინთეზური აკადემიური განმარტება №1."
    extras = dict(q.extras)
    assert extras["STATUS"] == "APPROVED"
    assert extras["QA GATES"] == "8/8; სინთეზური"
    # EVIDENCE keeps its internal blank line
    assert extras["EVIDENCE"] == (
        "[SYN2026] სინთეზური წყარო\nhttps://example.invalid/ref\n\n[SYN2027] მეორე წყარო"
    )
    assert list(extras) == [
        "SOURCE FINGERPRINT", "SOURCE COMMIT", "STATUS", "QA SCORE", "REVIEWED",
        "CLINICAL REASONING", "DISTRACTOR ANALYSIS", "EVIDENCE", "CLINICAL PEARL", "QA GATES",
    ]
    # gaps in ORIGINAL ID are normal for the refined master: no gap warning
    assert not result.issues


def test_record_errors(tmp_path: Path) -> None:
    records = [
        master_record("ALI", 1, status="PENDING"),
        master_record("ALI", 2, correct="ბ", answer_letter="გ"),
        master_record("ALI", 3, drop_section="CLINICAL PEARL"),
        master_record("ALI", 4),
        master_record("ALI", 4),
        master_record("NOT_A_TOPIC", 5),
    ]
    result = parse_master(write_master(tmp_path, records))
    items = result.items
    assert IssueCode.MASTER_NOT_REFINED in codes(items[0])
    assert IssueCode.MASTER_ANSWER_MISMATCH in codes(items[1])
    assert IssueCode.MASTER_FIELD_MISSING in codes(items[2])
    assert IssueCode.DUPLICATE_NUMBER in codes(items[3]) and IssueCode.DUPLICATE_NUMBER in codes(items[4])
    assert IssueCode.UNKNOWN_SUBJECT in codes(items[5])
    assert not any(i.is_valid for i in items)


def test_empty_master(tmp_path: Path) -> None:
    path = tmp_path / "REFINED_MASTER_QBANK.txt"
    path.write_text("მხოლოდ სათაური\n", encoding="utf-8")
    assert [i.code for i in parse_master(path).issues] == [IssueCode.MASTER_NO_RECORDS]


def test_discovery_is_non_recursive_and_reports_unknown_files(tmp_path: Path) -> None:
    from synthetic import write_pair

    write_pair(tmp_path, "ALI", [1])
    write_pair(tmp_path, "VENOUS", [1])
    write_master(tmp_path / "ACADEMIC_REVIEW", [master_record("ALI", 1)])
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")

    found = discover([tmp_path])
    assert [s.label for s in found.sources] == ["ALI", "VENOUS"]
    assert [i.code for i in found.issues] == [IssueCode.FILE_UNRECOGNIZED]

    both = discover([tmp_path, tmp_path / "ACADEMIC_REVIEW" / "REFINED_MASTER_QBANK.txt"])
    assert [s.kind for s in both.sources] == [SourceKind.PAIRED_TXT, SourceKind.PAIRED_TXT,
                                              SourceKind.REFINED_MASTER]
