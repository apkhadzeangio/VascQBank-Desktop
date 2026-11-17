from __future__ import annotations

import re

import pytest

from vascqbank_desktop.domain.answers import ANSWER_LETTERS, OPTION_COUNT, AnswerLetter
from vascqbank_desktop.domain.question_id import QuestionId, format_question_id
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import (
    SUBJECTS,
    TOTAL_TARGET_QUESTIONS,
    get_subject,
    is_known_subject,
)
from vascqbank_desktop.validation.text import contains_georgian

# --- subjects --------------------------------------------------------------


def test_twelve_unique_subjects_totalling_ten_thousand() -> None:
    codes = [s.code for s in SUBJECTS]
    assert len(SUBJECTS) == 12
    assert len(set(codes)) == 12
    assert TOTAL_TARGET_QUESTIONS == 10_000


@pytest.mark.parametrize("subject", SUBJECTS, ids=lambda s: s.code)
def test_subject_fields(subject) -> None:
    # Codes are the prefixes of <CODE>_questions.txt / <CODE>_explanations.txt.
    assert re.fullmatch(r"[A-Z][A-Z0-9_]+", subject.code)
    assert contains_georgian(subject.name_ka)
    assert subject.target_count > 0


def test_subject_lookup() -> None:
    assert get_subject("ALI").name_ka == "კიდურის მწვავე იშემია"
    assert is_known_subject("VGEI")
    assert not is_known_subject("ali")
    with pytest.raises(KeyError):
        get_subject("UNKNOWN")


# --- answer letters --------------------------------------------------------


def test_answer_letters_order() -> None:
    assert ANSWER_LETTERS == ("ა", "ბ", "გ", "დ", "ე")
    assert OPTION_COUNT == 5
    assert [m.index for m in AnswerLetter] == [0, 1, 2, 3, 4]
    assert str(AnswerLetter.C) == "გ"


def test_answer_letter_parsing() -> None:
    assert AnswerLetter.from_letter(" დ ") is AnswerLetter.D
    for bad in ("a", "ვ", "", "ა)", "*გ"):
        with pytest.raises(ValueError):
            AnswerLetter.from_letter(bad)


# --- review status ---------------------------------------------------------


def test_review_status_codes_match_review_web_app() -> None:
    assert [s.value for s in ReviewStatus] == [
        "PENDING_REVIEW",
        "IN_REVIEW",
        "APPROVED",
        "REJECTED",
    ]
    assert ReviewStatus.APPROVED.label_ka == "დამტკიცებული"
    assert all(contains_georgian(s.label_ka) for s in ReviewStatus)


# --- question identity -----------------------------------------------------


@pytest.mark.parametrize(
    ("code", "number", "expected"),
    [
        ("ALI", 1, "ALI-0001"),
        ("AAA_EVAR", 152, "AAA_EVAR-0152"),
        ("PAD_CLTI", 9999, "PAD_CLTI-9999"),
        ("PAD_CLTI", 10000, "PAD_CLTI-10000"),
    ],
)
def test_question_id_format_and_round_trip(code: str, number: int, expected: str) -> None:
    assert format_question_id(code, number) == expected
    qid = QuestionId(code, number)
    assert str(qid) == expected
    assert QuestionId.parse(expected) == qid


@pytest.mark.parametrize("text", ["ALI-1", "ALI-00001", "ali-0001", "ALI0001", "ALI-0000", ""])
def test_question_id_rejects_invalid_or_non_canonical(text: str) -> None:
    with pytest.raises(ValueError):
        QuestionId.parse(text)


def test_question_id_validation_and_ordering() -> None:
    with pytest.raises(ValueError):
        QuestionId("ALI", 0)
    with pytest.raises(TypeError):
        QuestionId("ALI", True)  # bool is not a valid number
    assert sorted([QuestionId("ALI", 10), QuestionId("ALI", 2)]) == [
        QuestionId("ALI", 2),
        QuestionId("ALI", 10),
    ]
