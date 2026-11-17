"""Editing rules: normalisation, validation, the status consequence of an edit, word diff."""

from __future__ import annotations

import pytest

from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.editing import (
    MAX_FIELD_LENGTH,
    MAX_NOTE_LENGTH,
    EditField,
    ProblemKind,
    changed_fields,
    normalize_content,
    normalize_field,
    normalize_note,
    same_content,
    validate_content,
)
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import (
    ReviewStatus,
    edit_revokes_approval,
    status_after_edit,
)
from vascqbank_desktop.domain.textdiff import DiffKind, compare_contents, diff_words


def content(**changes) -> QuestionContent:
    base = {
        "stem": "კითხვის ტექსტი",
        "options": ("ა1", "ბ2", "გ3", "დ4", "ე5"),
        "correct": AnswerLetter.C,
        "explanation": "განმარტება",
    }
    base.update(changes)
    return QuestionContent(**base)


# ------------------------------------------------------------------ normalisation


def test_normalisation_matches_the_import_rules() -> None:
    assert normalize_field("﻿  ტექსტი\r\nმეორე ხაზი\r\n\n ") == "ტექსტი\nმეორე ხაზი"
    assert normalize_field("José") == "José"  # NFC
    assert normalize_field("   ") == ""


def test_georgian_letters_are_never_altered() -> None:
    text = "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ — „ციტატა“ ≥ 5,5 cm · ΔP × 10⁹/ლ"
    assert normalize_field(text) == text


def test_normalize_content_keeps_the_correct_letter() -> None:
    result = normalize_content(
        content(stem="  ა  ", options=(" 1", "2 ", "3", "4", "5"), correct=AnswerLetter.E)
    )
    assert (
        result.stem == "ა"
        and result.options == ("1", "2", "3", "4", "5")
        and result.correct is AnswerLetter.E
    )


def test_note_is_one_line_or_none() -> None:
    assert normalize_note(None) is None and normalize_note("  \n ") is None
    assert normalize_note(" შესწორდა\n  ორთოგრაფია ") == "შესწორდა ორთოგრაფია"


# ------------------------------------------------------------------ change detection


def test_identical_content_is_not_a_change() -> None:
    assert same_content(content(), content())
    assert same_content(content(), content(stem="  კითხვის ტექსტი \r\n"))  # only whitespace differs
    assert changed_fields(content(), content()) == ()


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"stem": "სხვა"}, EditField.STEM),
        ({"options": ("X", "ბ2", "გ3", "დ4", "ე5")}, EditField.OPTION_A),
        ({"options": ("ა1", "X", "გ3", "დ4", "ე5")}, EditField.OPTION_B),
        ({"options": ("ა1", "ბ2", "X", "დ4", "ე5")}, EditField.OPTION_C),
        ({"options": ("ა1", "ბ2", "გ3", "X", "ე5")}, EditField.OPTION_D),
        ({"options": ("ა1", "ბ2", "გ3", "დ4", "X")}, EditField.OPTION_E),
        ({"correct": AnswerLetter.A}, EditField.CORRECT),
        ({"explanation": "სხვა განმარტება"}, EditField.EXPLANATION),
    ],
)
def test_each_field_change_is_detected_on_its_own(changes: dict, field: EditField) -> None:
    assert changed_fields(content(), content(**changes)) == (field,)


# ------------------------------------------------------------------ validation


def test_valid_content_has_no_problems() -> None:
    assert validate_content(content(), "შენიშვნა") == []


def test_every_text_field_is_required() -> None:
    assert [(p.field, p.kind) for p in validate_content(content(stem="  \n "))] == [
        (EditField.STEM, ProblemKind.EMPTY)
    ]
    assert [p.field for p in validate_content(content(explanation=""))] == [EditField.EXPLANATION]
    for index, field in enumerate(
        (EditField.OPTION_A, EditField.OPTION_B, EditField.OPTION_C, EditField.OPTION_D, EditField.OPTION_E)
    ):
        options = list(content().options)
        options[index] = " "
        assert [p.field for p in validate_content(content(options=tuple(options)))] == [field]


def test_several_problems_are_all_reported() -> None:
    problems = validate_content(content(stem="", options=("", "", "გ3", "დ4", "ე5"), explanation=""))
    assert {p.field for p in problems} == {
        EditField.STEM,
        EditField.OPTION_A,
        EditField.OPTION_B,
        EditField.EXPLANATION,
    }


def test_control_characters_and_oversized_text_are_rejected() -> None:
    assert [p.kind for p in validate_content(content(stem="ა\x00ბ"))] == [ProblemKind.CONTROL_CHARACTERS]
    assert [p.kind for p in validate_content(content(explanation="ა\x1bბ"))] == [
        ProblemKind.CONTROL_CHARACTERS
    ]
    assert validate_content(content(stem="ა\tბ\nგ")) == []  # tabs and line breaks are ordinary text
    assert [p.kind for p in validate_content(content(stem="ა" * (MAX_FIELD_LENGTH + 1)))] == [
        ProblemKind.TOO_LONG
    ]
    assert validate_content(content(stem="ა" * MAX_FIELD_LENGTH)) == []


def test_over_long_note_is_reported() -> None:
    problems = validate_content(content(), "ა" * (MAX_NOTE_LENGTH + 1))
    assert [(p.field, p.kind) for p in problems] == [(None, ProblemKind.NOTE_TOO_LONG)]


def test_there_is_exactly_one_correct_answer_by_construction() -> None:
    with pytest.raises(TypeError):
        QuestionContent("s", ("1", "2", "3", "4", "5"), "გ", "e")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        QuestionContent("s", ("1", "2", "3", "4"), AnswerLetter.A, "e")  # four options


# ------------------------------------------------------------------ the status rule (ARCHITECTURE §9)


@pytest.mark.parametrize(
    ("before", "after", "revokes"),
    [
        (ReviewStatus.PENDING_REVIEW, ReviewStatus.IN_REVIEW, False),
        (ReviewStatus.IN_REVIEW, ReviewStatus.IN_REVIEW, False),
        (ReviewStatus.APPROVED, ReviewStatus.IN_REVIEW, True),  # an edit revokes the approval
        (ReviewStatus.REJECTED, ReviewStatus.REJECTED, False),  # only an explicit reopen leaves REJECTED
    ],
)
def test_status_after_a_saved_edit(before: ReviewStatus, after: ReviewStatus, revokes: bool) -> None:
    assert status_after_edit(before) is after
    assert edit_revokes_approval(before) is revokes


# ------------------------------------------------------------------ word diff


def test_diff_words_reconstructs_both_texts() -> None:
    old, new = "პაციენტს აქვს მწვავე იშემია და ტკივილი", "პაციენტს აქვს ქრონიკული იშემია"
    segments = diff_words(old, new)
    assert "".join(s.text for s in segments if s.kind is not DiffKind.ADDED) == old
    assert "".join(s.text for s in segments if s.kind is not DiffKind.REMOVED) == new
    assert any(s.kind is DiffKind.REMOVED and "მწვავე" in s.text for s in segments)
    assert any(s.kind is DiffKind.ADDED and "ქრონიკული" in s.text for s in segments)


def test_identical_text_is_one_unchanged_segment_and_empty_diffs_are_empty() -> None:
    assert [(s.kind, s.text) for s in diff_words("ა ბ  გ", "ა ბ  გ")] == [(DiffKind.SAME, "ა ბ  გ")]
    assert diff_words("", "") == ()


def test_compare_contents_marks_only_changed_fields() -> None:
    diffs = compare_contents(
        content(), content(options=("ა1", "ბ2", "ახალი", "დ4", "ე5"), correct=AnswerLetter.B)
    )
    changed = {d.field for d in diffs if d.changed}
    assert changed == {EditField.OPTION_C, EditField.CORRECT}
    correct = next(d for d in diffs if d.field is EditField.CORRECT)
    assert (correct.old, correct.new) == ("გ", "ბ")
