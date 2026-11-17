"""Pure-domain tests: reviewer names, question references, search terms and previews."""

from __future__ import annotations

import pytest

from vascqbank_desktop.domain.catalog import ResultSet, SearchMode
from vascqbank_desktop.domain.question_id import (
    InvalidReference,
    QuestionId,
    ReferenceProblem,
    parse_reference,
)
from vascqbank_desktop.domain.reviewer import (
    MAX_RECENT_REVIEWERS,
    REVIEWER_PRESETS,
    InvalidReviewerName,
    ReviewerNameProblem,
    normalize_reviewer_name,
    remember_reviewer,
)
from vascqbank_desktop.domain.search import (
    MAX_QUERY_LENGTH,
    MAX_TERMS,
    PreviewField,
    browse_preview,
    document_text,
    fts_match_expression,
    fts_supports,
    like_pattern,
    match_preview,
    normalize_query,
    split_terms,
)

# ---------------------------------------------------------------- reviewer names


def test_presets_are_the_two_named_reviewers_and_are_valid() -> None:
    assert REVIEWER_PRESETS == ("რამაზი აფხაძე", "დავით ფარღალავა")
    assert [normalize_reviewer_name(n) for n in REVIEWER_PRESETS] == list(REVIEWER_PRESETS)


def test_reviewer_name_is_trimmed_collapsed_and_nfc() -> None:
    assert normalize_reviewer_name("  დავით \t ფარღალავა \n") == "დავით ფარღალავა"
    # "é" typed as e + combining acute becomes the single NFC code point; Georgian is untouched
    assert normalize_reviewer_name("José ა") == "José ა"


def test_custom_names_are_allowed() -> None:
    assert normalize_reviewer_name("Dr. Nino Beridze-Kapanadze") == "Dr. Nino Beridze-Kapanadze"


@pytest.mark.parametrize(
    ("raw", "problem"),
    [
        ("", ReviewerNameProblem.EMPTY),
        ("   \t\n", ReviewerNameProblem.EMPTY),
        ("ა" * 81, ReviewerNameProblem.TOO_LONG),
        ("Name\x00Null", ReviewerNameProblem.INVALID_CHARACTERS),
        ("Zero​Width", ReviewerNameProblem.INVALID_CHARACTERS),
        ("Bidi‮Mark", ReviewerNameProblem.INVALID_CHARACTERS),
    ],
)
def test_invalid_reviewer_names(raw: str, problem: ReviewerNameProblem) -> None:
    with pytest.raises(InvalidReviewerName) as info:
        normalize_reviewer_name(raw)
    assert info.value.problem is problem


def test_recent_reviewers_are_most_recent_first_unique_and_bounded() -> None:
    recent: tuple[str, ...] = ()
    for name in ("ა", "ბ", "ა", "გ"):
        recent = remember_reviewer(recent, name)
    assert recent == ("გ", "ა", "ბ")
    for i in range(30):
        recent = remember_reviewer(recent, f"n{i}")
    assert len(recent) == MAX_RECENT_REVIEWERS and recent[0] == "n29"


# ---------------------------------------------------------------- question references


@pytest.mark.parametrize(
    ("text", "default", "expected"),
    [
        ("ALI-0042", None, QuestionId("ALI", 42)),
        ("ali-0042", None, QuestionId("ALI", 42)),
        ("  Ali 42  ", None, QuestionId("ALI", 42)),
        ("ALI42", None, QuestionId("ALI", 42)),
        ("ALI_42", None, QuestionId("ALI", 42)),
        ("PAD_CLTI-0001", None, QuestionId("PAD_CLTI", 1)),
        ("pad_clti 1", None, QuestionId("PAD_CLTI", 1)),
        ("THORACIC_AORTA–0700", None, QuestionId("THORACIC_AORTA", 700)),  # en dash
        ("42", "ALI", QuestionId("ALI", 42)),
        ("0042", "VENOUS", QuestionId("VENOUS", 42)),
    ],
)
def test_parse_reference(text: str, default: str | None, expected: QuestionId) -> None:
    assert parse_reference(text, default) == expected


@pytest.mark.parametrize(
    ("text", "default", "problem"),
    [
        ("", None, ReferenceProblem.EMPTY),
        ("   ", "ALI", ReferenceProblem.EMPTY),
        ("42", None, ReferenceProblem.NEEDS_SUBJECT),
        ("XYZ-0001", None, ReferenceProblem.UNKNOWN_SUBJECT),
        ("ALI-0", None, ReferenceProblem.BAD_FORMAT),
        ("0", "ALI", ReferenceProblem.BAD_FORMAT),
        ("ALI-", None, ReferenceProblem.BAD_FORMAT),
        ("კითხვა 5", "ALI", ReferenceProblem.BAD_FORMAT),
        ("ALI-12345678", None, ReferenceProblem.BAD_FORMAT),
        ("A" * 80, None, ReferenceProblem.BAD_FORMAT),
        ("ALI-1; DROP TABLE questions", None, ReferenceProblem.BAD_FORMAT),
    ],
)
def test_parse_reference_rejects(text: str, default: str | None, problem: ReferenceProblem) -> None:
    with pytest.raises(InvalidReference) as info:
        parse_reference(text, default)
    assert info.value.problem is problem


# ---------------------------------------------------------------- search terms


def test_query_normalisation_and_terms() -> None:
    assert normalize_query("  კაროტიდული \n  სტენოზი  ") == "კაროტიდული სტენოზი"
    assert split_terms("EVAR evar  Evar ბლოკი") == ("EVAR", "ბლოკი")  # case-insensitive dedupe
    assert split_terms("") == () and split_terms("   ") == ()
    assert len(split_terms(" ".join(f"term{i}" for i in range(20)))) == MAX_TERMS
    assert len(normalize_query("x" * 1000)) == MAX_QUERY_LENGTH


def test_fts_expression_quotes_every_term() -> None:
    assert fts_match_expression(("abc", "დე ფ")) == '"abc" AND "დე ფ"'
    assert fts_match_expression(('a"b',)) == '"a""b"'
    # operators and punctuation stay literal because each term is quoted
    assert fts_match_expression(("NOT", "OR*", "-x")) == '"NOT" AND "OR*" AND "-x"'


def test_fts_supports_only_terms_of_three_characters_or_more() -> None:
    assert fts_supports(("ვარიანტი", "EVAR"))
    assert not fts_supports(("ბა",))
    assert not fts_supports(("ვარიანტი", "ბ"))
    assert not fts_supports(())


def test_like_pattern_escapes_wildcards() -> None:
    assert like_pattern("50%") == "%50\\%%"
    assert like_pattern("a_b") == "%a\\_b%"
    assert like_pattern("back\\slash") == "%back\\\\slash%"


def test_document_text_puts_the_id_first() -> None:
    assert (
        document_text("ALI-0001", "stem", ("a", "b", "c", "d", "e"), "expl")
        == "ALI-0001\nstem\na\nb\nc\nd\ne\nexpl"
    )


# ---------------------------------------------------------------- previews


def _slice(preview) -> list[str]:
    return [preview.text[a:b] for a, b in preview.spans]


def test_preview_highlights_the_match_inside_a_long_stem() -> None:
    stem = "პაციენტს აღენიშნება " * 20 + "კაროტიდული სტენოზი" + " დამატებითი ტექსტი" * 20
    preview = match_preview("ALI-0001", stem, ("ა", "ბ", "გ", "დ", "ე"), "განმარტება", ("სტენოზი",))
    assert preview.field is PreviewField.STEM
    assert len(preview.text) <= 160
    assert preview.text.startswith("…") and preview.text.endswith("…")
    assert _slice(preview) == ["სტენოზი"]


def test_preview_reports_the_field_that_matched() -> None:
    opts = ("ა1", "ბ1", "გ — ორმხრივი ბლოკი", "დ1", "ე1")
    preview = match_preview("ALI-0001", "stem", opts, "expl", ("ბლოკი",))
    assert (preview.field, preview.option_letter) == (PreviewField.OPTION, "გ")
    explanation = match_preview("ALI-0001", "stem", ("a",) * 5, "ახსნა: EVAR გამოიყენება", ("evar",))
    assert explanation.field is PreviewField.EXPLANATION and _slice(explanation) == ["EVAR"]
    by_id = match_preview("ALI-0042", "stem", ("a",) * 5, "expl", ("0042",))
    assert by_id.field is PreviewField.ID and _slice(by_id) == ["0042"]


def test_preview_with_terms_spread_over_fields_falls_back_to_stem_start() -> None:
    preview = match_preview("ALI-0001", "პირველი ველი", ("x",) * 5, "მეორე ველი", ("პირველი", "მეორე"))
    assert preview.field is PreviewField.STEM and _slice(preview) == ["პირველი"]


def test_preview_flattens_whitespace_and_merges_overlapping_hits() -> None:
    preview = match_preview("ALI-0001", "ერთი\n\n   ორი  სამი", ("x",) * 5, "e", ("ორი", "ორი სამი", "სამ"))
    assert "\n" not in preview.text and "  " not in preview.text
    assert _slice(preview) == ["ორი სამი"]


def test_browse_preview_truncates_and_flattens() -> None:
    assert browse_preview("მოკლე\nტექსტი").text == "მოკლე ტექსტი"
    long = browse_preview("ა" * 500)
    assert long.text.endswith("…") and len(long.text) <= 221


# ---------------------------------------------------------------- result sets / navigation


def test_result_set_navigation() -> None:
    results = ResultSet((10, 20, 30), (), SearchMode.BROWSE)
    assert len(results) == 3 and 20 in results and 99 not in results
    assert results.position_of(20) == 1 and results.position_of(99) is None
    assert results.next_id(10) == 20 and results.next_id(30) is None
    assert results.previous_id(30) == 20 and results.previous_id(10) is None


def test_navigation_when_the_current_question_is_not_listed() -> None:
    results = ResultSet((10, 20, 30))
    assert results.next_id(None) == 10 and results.next_id(99) == 10
    assert results.previous_id(None) == 30 and results.previous_id(99) == 30


def test_navigation_in_an_empty_result() -> None:
    empty = ResultSet()
    assert empty.next_id(1) is None and empty.previous_id(1) is None and len(empty) == 0
