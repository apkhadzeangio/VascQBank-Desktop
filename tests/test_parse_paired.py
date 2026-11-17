"""Format A — paired TXT parsing and validation (synthetic data only)."""

from __future__ import annotations

from pathlib import Path

import pytest
from synthetic import correct_letter, explanation_block, question_block, write_pair

from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.question import SourceKind
from vascqbank_desktop.importexport.paired_txt import parse_pair
from vascqbank_desktop.validation.issues import IssueCode, Severity


def parse(folder: Path, code: str = "ALI"):
    return parse_pair(code, folder / f"{code}_questions.txt", folder / f"{code}_explanations.txt")


def codes(item) -> set[IssueCode]:
    return {i.code for i in item.issues}


def all_codes(result) -> set[IssueCode]:
    return {i.code for i in result.issues} | {c for it in result.items for c in codes(it)}


def test_valid_pair_preserves_georgian_exactly(tmp_path: Path) -> None:
    write_pair(tmp_path, "ALI", range(1, 6))
    result = parse(tmp_path)
    assert [str(i.qid) for i in result.items] == [f"ALI-000{n}" for n in range(1, 6)]
    assert all(i.is_valid for i in result.items)
    assert not result.issues
    q = result.items[0].question
    assert q is not None
    assert q.content.stem == (
        "სინთეზური სატესტო კითხვა ALI №1: რომელი ვარიანტია სწორი ამ ხელოვნურ მაგალითში?"
    )
    assert q.content.options[1] == "ვარიანტი ბ — სატესტო ტექსტი ALI-1"
    assert q.content.correct is AnswerLetter.from_letter(correct_letter(1))
    assert q.content.explanation.startswith("ეს სინთეზური განმარტებაა")
    assert q.source.kind is SourceKind.PAIRED_TXT
    assert q.source.explanation_file == "ALI_explanations.txt"
    assert {f.name for f in result.files} == {"ALI_questions.txt", "ALI_explanations.txt"}


def test_multiline_stem_options_and_explanation_with_stray_blank_lines(tmp_path: Path) -> None:
    questions = (
        "1. პირველი სტრიქონი კითხვისა\n"
        "მეორე სტრიქონი კითხვისა?\n"
        "\n"  # stray blank line between stem and options
        "ა) პასუხი ა\n"
        "ბ) პასუხი ბ, რომელიც\n"
        "გრძელდება მეორე სტრიქონზე\n"
        "*გ) პასუხი გ\n"
        "\n"
        "დ) პასუხი დ\n"
        "ე) პასუხი ე\n"
    )
    explanations = "1. გ) პირველი წინადადება.\nმეორე წინადადება ახალ სტრიქონზე.\n"
    write_pair(tmp_path, "ALI", [1], questions_text=questions, explanations_text=explanations)
    result = parse(tmp_path)
    assert len(result.items) == 1 and result.items[0].is_valid, result.items[0].issues
    content = result.items[0].question.content
    assert content.stem == "პირველი სტრიქონი კითხვისა\nმეორე სტრიქონი კითხვისა?"
    assert content.options[1] == "პასუხი ბ, რომელიც\nგრძელდება მეორე სტრიქონზე"
    assert content.explanation == "პირველი წინადადება.\nმეორე წინადადება ახალ სტრიქონზე."


def test_crlf_and_bom_are_accepted(tmp_path: Path) -> None:
    q, e = write_pair(tmp_path, "VENOUS", [1, 2])
    for path in (q, e):
        path.write_bytes(b"\xef\xbb\xbf" + path.read_text(encoding="utf-8").replace("\n", "\r\n").encode())
    result = parse(tmp_path, "VENOUS")
    assert [i.is_valid for i in result.items] == [True, True]


def test_missing_explanation_rejects_only_that_question(tmp_path: Path) -> None:
    explanations = "\n\n".join(explanation_block("ALI", n) for n in (1, 3)) + "\n"
    write_pair(tmp_path, "ALI", [1, 2, 3], explanations_text=explanations)
    result = parse(tmp_path)
    assert [i.is_valid for i in result.items] == [True, False, True]
    assert IssueCode.MISSING_EXPLANATION in codes(result.items[1])


def test_orphan_and_duplicate_explanations(tmp_path: Path) -> None:
    explanations = "\n\n".join(explanation_block("ALI", n) for n in (1, 1, 2, 9)) + "\n"
    write_pair(tmp_path, "ALI", [1, 2], explanations_text=explanations)
    result = parse(tmp_path)
    assert IssueCode.DUPLICATE_EXPLANATION in codes(result.items[0])
    assert result.items[1].is_valid
    assert IssueCode.ORPHAN_EXPLANATION in {i.code for i in result.issues}


def test_missing_explanations_file(tmp_path: Path) -> None:
    q, e = write_pair(tmp_path, "ALI", [1])
    e.unlink()
    result = parse_pair("ALI", q, None)
    assert result.items == []
    assert [i.code for i in result.issues] == [IssueCode.MISSING_EXPLANATIONS_FILE]


@pytest.mark.parametrize(
    ("option_lines", "expected"),
    [
        (["ა) ა", "ბ) ბ", "გ) გ", "დ) დ", "ე) ე"], IssueCode.NO_CORRECT_ANSWER),
        (["*ა) ა", "ბ) ბ", "*გ) გ", "დ) დ", "ე) ე"], IssueCode.MULTIPLE_CORRECT_ANSWERS),
        (["ა) ა", "ბ) ბ", "* გ) გ", "დ) დ", "ე) ე"], IssueCode.MISPLACED_MARKER),
        (["ა) ა", "ბ) ბ", "გ)* გ", "დ) დ", "ე) ე"], IssueCode.MISPLACED_MARKER),
        (["ა) ა", "ბ) ბ", "გ*) გ", "დ) დ", "ე) ე"], IssueCode.OPTION_FORMAT),
        (["ა) ა", "ბ) ბ", "*გ. გ", "დ) დ", "ე) ე"], IssueCode.OPTION_FORMAT),
        (["ა) ა", "*ბ) ბ", "გ) გ", "დ) დ"], IssueCode.OPTION_COUNT),
        (["ა) ა", "*ბ) ბ", "დ) დ", "გ) გ", "ე) ე"], IssueCode.OPTION_LETTERS),
        (["ა) ა", "*ბ) ბ", "გ) გ", "დ) დ", "ვ) ვ"], IssueCode.OPTION_LETTERS),
        (["ა) ა", "*ბ) ", "გ) გ", "დ) დ", "ე) ე"], IssueCode.EMPTY_OPTION),
    ],
)
def test_incorrect_answer_markers_and_options(tmp_path: Path, option_lines, expected) -> None:
    questions = "1. კითხვა?\n" + "\n".join(option_lines) + "\n"
    write_pair(tmp_path, "ALI", [1], questions_text=questions)
    result = parse(tmp_path)
    assert len(result.items) == 1
    item = result.items[0]
    assert not item.is_valid
    assert expected in codes(item), [i.message for i in item.issues]
    error = next(i for i in item.issues if i.code is expected)
    assert error.severity is Severity.ERROR
    assert any("ა" <= ch <= "ჿ" for ch in error.message)  # Georgian message


def test_explanation_letter_mismatch_and_marker(tmp_path: Path) -> None:
    questions = question_block("ALI", 1, correct="გ") + "\n\n" + question_block("ALI", 2, correct="ა") + "\n"
    explanations = explanation_block("ALI", 1, letter="დ") + "\n\n2. *ა) განმარტება. მეორე.\n"
    write_pair(tmp_path, "ALI", [1, 2], questions_text=questions, explanations_text=explanations)
    result = parse(tmp_path)
    assert IssueCode.EXPLANATION_LETTER_MISMATCH in codes(result.items[0])
    assert IssueCode.EXPLANATION_MARKER in codes(result.items[1])


def test_duplicate_number_and_target_limit(tmp_path: Path) -> None:
    questions = "\n\n".join(question_block("VGEI", n) for n in (1, 1, 501)) + "\n"
    explanations = "\n\n".join(explanation_block("VGEI", n) for n in (1, 501)) + "\n"
    write_pair(tmp_path, "VGEI", [], questions_text=questions, explanations_text=explanations)
    result = parse(tmp_path, "VGEI")  # VGEI target is 500
    assert IssueCode.DUPLICATE_NUMBER in codes(result.items[0])
    assert IssueCode.DUPLICATE_NUMBER in codes(result.items[1])
    assert IssueCode.NUMBER_ABOVE_TARGET in codes(result.items[2])
    assert not any(i.is_valid for i in result.items)


def test_warnings_never_reject_valid_questions(tmp_path: Path) -> None:
    questions = "\n\n".join(question_block("ALI", n) for n in (1, 3)) + "\n"  # gap at 2
    explanations = (
        explanation_block("ALI", 1)
        + "\n\n3. " + correct_letter(3) + ") ერთი. ორი. სამი.\n"  # three sentences
    )
    write_pair(tmp_path, "ALI", [], questions_text=questions, explanations_text=explanations)
    result = parse(tmp_path)
    assert all(i.is_valid for i in result.items)
    assert IssueCode.EXPLANATION_SENTENCES in codes(result.items[1])
    assert IssueCode.NUMBER_GAP in {i.code for i in result.issues}
    warnings = [i for it in result.items for i in it.issues] + result.issues
    assert all(i.severity is Severity.WARNING for i in warnings)


def test_duplicate_options_warning(tmp_path: Path) -> None:
    questions = "1. კითხვა?\nა) იგივე\n*ბ) სხვა\nგ) იგივე\nდ) დ\nე) ე\n"
    write_pair(tmp_path, "ALI", [1], questions_text=questions,
               explanations_text="1. ბ) ერთი. ორი.\n")
    item = parse(tmp_path).items[0]
    assert item.is_valid
    assert IssueCode.DUPLICATE_OPTIONS in codes(item)


def test_missing_blank_line_between_questions_is_tolerated(tmp_path: Path) -> None:
    questions = question_block("ALI", 1) + "\n" + question_block("ALI", 2) + "\n"
    write_pair(tmp_path, "ALI", [1, 2], questions_text=questions)
    result = parse(tmp_path)
    assert [i.is_valid for i in result.items] == [True, True]
    assert IssueCode.MISSING_BLANK_LINE in codes(result.items[1])


def test_invalid_utf8_reports_line(tmp_path: Path) -> None:
    q, _ = write_pair(tmp_path, "ALI", [1, 2])
    q.write_bytes(q.read_bytes().replace("სინთეზური".encode(), b"\xff\xfe", 1))
    result = parse(tmp_path)
    assert result.items == []
    issue = result.issues[0]
    assert issue.code is IssueCode.FILE_NOT_UTF8 and issue.line == 1


def test_unknown_subject_and_text_before_first_question(tmp_path: Path) -> None:
    q, e = write_pair(tmp_path, "UNKNOWN_CODE", [1])
    assert [i.code for i in parse_pair("UNKNOWN_CODE", q, e).issues] == [IssueCode.UNKNOWN_SUBJECT] * 2

    write_pair(tmp_path, "ALI", [1], questions_text="სათაური\n\n" + question_block("ALI", 1) + "\n")
    result = parse(tmp_path)
    assert result.items[0].is_valid
    assert IssueCode.UNEXPECTED_TEXT in {i.code for i in result.issues}
