"""Paired-TXT writing: the exact format, gaps, and texts the format cannot carry unchanged."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.importexport.paired_txt import parse_questions_text
from vascqbank_desktop.importexport.txt_export import (
    TxtProblem,
    explanations_filename,
    questions_filename,
    render_explanations,
    render_questions,
    txt_problems,
)


@dataclass
class Q:
    uid: str
    subject_code: str
    subject_name_ka: str
    source_number: int
    content: QuestionContent


def q(number: int, **changes) -> Q:
    content = QuestionContent(
        changes.get("stem", f"კითხვა {number}"),
        changes.get("options", ("ერთი", "ორი", "სამი", "ოთხი", "ხუთი")),
        changes.get("correct", AnswerLetter.B),
        changes.get("explanation", f"განმარტება {number}."),
    )
    return Q(f"ALI-{number:04d}", "ALI", "კიდურის მწვავე იშემია", number, content)


def test_the_exact_source_convention_with_original_numbers_and_gaps() -> None:
    questions = [q(42, stem="ხაზი ერთი\nხაზი ორი"), q(7, correct=AnswerLetter.E)]
    assert render_questions(questions) == (
        "7. კითხვა 7\nა) ერთი\nბ) ორი\nგ) სამი\nდ) ოთხი\n*ე) ხუთი\n\n"
        "42. ხაზი ერთი\nხაზი ორი\nა) ერთი\n*ბ) ორი\nგ) სამი\nდ) ოთხი\nე) ხუთი\n"
    )
    assert render_explanations(questions) == "7. ე) განმარტება 7.\n\n42. ბ) განმარტება 42.\n"
    assert questions_filename("AAA_EVAR") == "AAA_EVAR_questions.txt"
    assert explanations_filename("ALI") == "ALI_explanations.txt"


def test_exactly_one_marker_directly_before_the_correct_letter() -> None:
    for letter in AnswerLetter:
        text = render_questions([q(1, correct=letter)])
        assert text.count("*") == 1 and f"*{letter.value}) " in text


def test_multiline_options_and_explanations_are_written_as_continuation_lines() -> None:
    item = q(3, options=("ა-ს\nგაგრძელება", "ორი", "სამი", "ოთხი", "ხუთი"), explanation="ერთი\nორი")
    assert "ა) ა-ს\nგაგრძელება\n*ბ) ორი" in render_questions([item])
    assert render_explanations([item]) == "3. ბ) ერთი\nორი\n"
    raw, issues = parse_questions_text(render_questions([item]), "ALI_questions.txt")
    assert not issues and raw[0].options[0].text == "ა-ს\nგაგრძელება"


@pytest.mark.parametrize(
    ("changes", "field", "problem"),
    [
        ({"stem": "აბზაცი\n\nმეორე"}, "stem", TxtProblem.BLANK_LINE),
        ({"stem": "ტექსტი * ვარსკვლავით"}, "stem", TxtProblem.ASTERISK),
        ({"stem": "ტექსტი\nბ) ჰგავს ვარიანტს"}, "stem", TxtProblem.LOOKS_LIKE_OPTION),
        ({"options": ("a*", "ორი", "სამი", "ოთხი", "ხუთი")}, "option_a", TxtProblem.ASTERISK),
        (
            {"options": ("ერთი\n12. ნომერი", "ორი", "სამი", "ოთხი", "ხუთი")},
            "option_a",
            TxtProblem.LOOKS_LIKE_NUMBER,
        ),
        ({"options": ("ერთი\n  შეწეული", "ორი", "სამი", "ოთხი", "ხუთი")}, "option_a", TxtProblem.EDGE_SPACES),
        ({"stem": "ბოლოში ჰარი \nმეორე"}, "stem", TxtProblem.EDGE_SPACES),
        ({"explanation": "*ვარსკვლავით"}, "explanation", TxtProblem.LEADING_MARKER),
        ({"explanation": "ერთი\n\nორი"}, "explanation", TxtProblem.BLANK_LINE),
    ],
)
def test_texts_the_format_cannot_carry_unchanged_are_named(changes, field, problem) -> None:
    issues = txt_problems([q(5, **changes)])
    assert (issues[0].uid, issues[0].field, issues[0].problem) == ("ALI-0005", field, problem)


def test_ordinary_medical_text_is_representable() -> None:
    item = q(
        9,
        stem="EVAR / CTA ≥ 70 % (ESVS); α, β, Δ, 10⁹/ლ\nმეორე ხაზი: 3. ეტაპი",
        options=("EVAR", "CTA 6 თვეში", "ABI = 0.9", "p < 0.05", "„ციტატა“"),
        explanation="ერთი ხაზი\n2. ნომრით დაწყებული ხაზი განმარტებაში",
    )
    assert txt_problems([item]) == []
