"""Phase 9 property-based fuzzing of every parser that reads data from disk.

Deterministic (``derandomize=True``): the same examples run on every machine and in CI, so a
failure is always reproducible. Every parser must either succeed or fail in its *documented*,
controlled way — never with an unexpected exception that could break start-up or an import.

The round-trip property is the strongest one: any question content that the TXT writer accepts
(``txt_problems() == []``) must come back from the importer **exactly** — that is the guarantee
the "export → re-import" workflow relies on. Synthetic text only.
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")

from hypothesis import HealthCheck, assume, example, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.db import backup as backup_db  # noqa: E402
from vascqbank_desktop.domain.answers import AnswerLetter  # noqa: E402
from vascqbank_desktop.domain.question import QuestionContent  # noqa: E402
from vascqbank_desktop.domain.question_id import InvalidReference, parse_reference  # noqa: E402
from vascqbank_desktop.domain.subjects import get_subject  # noqa: E402
from vascqbank_desktop.importexport.paired_txt import (  # noqa: E402
    parse_explanations_text,
    parse_pair,
    parse_questions_text,
)
from vascqbank_desktop.importexport.txt_export import (  # noqa: E402
    render_explanations,
    render_questions,
    txt_problems,
)
from vascqbank_desktop.services import export_publication, restore_service  # noqa: E402
from vascqbank_desktop.services.seed_service import SeedInvalid, parse_manifest  # noqa: E402
from vascqbank_desktop.services.settings_service import (  # noqa: E402
    AppSettings,
    SettingsStore,
    parse_settings,
)

FUZZ = settings(
    derandomize=True,
    deadline=None,
    max_examples=250,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.filter_too_much],
    database=None,  # no .hypothesis folder in the repository
)

GEORGIAN = "აბგდევზთიკლმნოპჟრსტუფქღყშჩცძწჭხჯჰ"
TEXT_ALPHABET = GEORGIAN + "abcxyzABCXYZ0123456789 .,;:!?()[]%/=<>+-–—„“\"'αβΔ≥≤°₂⁹·…"
HOSTILE_ALPHABET = TEXT_ALPHABET + "*\t\r\n\x00\x0b\x0c\u200b\ufeff\u202e\ud7ff\U0001f600ა\u0301"

any_text = st.text(alphabet=st.characters(), max_size=400)
hostile_text = st.text(alphabet=HOSTILE_ALPHABET, max_size=600)

json_leaf = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**30), max_value=10**30),
    st.floats(allow_nan=True, allow_infinity=True),
    st.text(max_size=40),
)
json_values = st.recursive(
    json_leaf,
    lambda children: st.one_of(
        st.lists(children, max_size=5), st.dictionaries(st.text(max_size=20), children, max_size=6)
    ),
    max_leaves=30,
)


# ----------------------------------------------------------------------------- paired TXT parser
@FUZZ
@given(hostile_text)
@example("1. ა\n*ა) x\n*ბ) y\nგ) z\nდ) w\nე) v")  # two markers
@example("\ufeff1. ქ\r\nა) x\r\n")  # BOM and CRLF
@example("99999999999999999999999. ა")  # a number larger than any int column
def test_question_parser_never_raises(text: str) -> None:
    questions, issues = parse_questions_text(text, "ALI_questions.txt")
    assert isinstance(questions, list) and isinstance(issues, list)


@FUZZ
@given(hostile_text)
@example("1. ა) ტექსტი\n1. ბ) დუბლიკატი")
@example("99999999999999999999999. ა) x")
def test_explanation_parser_never_raises(text: str) -> None:
    explanations, issues = parse_explanations_text(text, "ALI_explanations.txt")
    assert isinstance(explanations, list) and isinstance(issues, list)


@FUZZ
@given(st.binary(max_size=800), st.binary(max_size=800))
@example(b"\xff\xfe1. x", b"1. \xc3")  # invalid UTF-8
@example("1. ა\nა) x\nბ) y\nგ) z\nდ) w\n*ე) v".encode(), b"\xef\xbb\xbf1. \xe1\x83\x94) x")
def test_pair_import_never_raises_on_arbitrary_bytes(questions: bytes, explanations: bytes) -> None:
    with tempfile.TemporaryDirectory() as folder:
        q = Path(folder) / "ALI_questions.txt"
        e = Path(folder) / "ALI_explanations.txt"
        q.write_bytes(questions)
        e.write_bytes(explanations)
        result = parse_pair("ALI", q, e)
    for item in result.items:
        if item.is_valid:  # anything accepted must be a well-formed question
            assert item.question is not None and len(item.question.content.options) == 5


# ------------------------------------------------------------------------- export → import round trip
line = st.text(alphabet=TEXT_ALPHABET, min_size=1, max_size=60).map(str.strip).filter(bool)
field_text = st.lists(line, min_size=1, max_size=3).map("\n".join)


@dataclass
class _Q:
    uid: str
    subject_code: str
    subject_name_ka: str
    source_number: int
    content: QuestionContent


@FUZZ
@given(
    number=st.integers(min_value=1, max_value=get_subject("ALI").target_count),  # above it: refused
    stem=field_text,
    options=st.lists(field_text, min_size=5, max_size=5, unique=True),
    correct=st.sampled_from(list(AnswerLetter)),
    explanation=field_text,
)
def test_txt_export_reimports_exactly(
    number: int, stem: str, options: list[str], correct: AnswerLetter, explanation: str
) -> None:
    try:
        content = QuestionContent(stem, tuple(options), correct, explanation)
    except ValueError:
        assume(False)
        return
    item = _Q(f"ALI-{number:04d}", "ALI", "კიდურის მწვავე იშემია", number, content)
    assume(not txt_problems([item]))  # the writer itself refuses anything it cannot carry exactly
    with tempfile.TemporaryDirectory() as folder:
        q = Path(folder) / "ALI_questions.txt"
        e = Path(folder) / "ALI_explanations.txt"
        q.write_text(render_questions([item]), encoding="utf-8")
        e.write_text(render_explanations([item]), encoding="utf-8")
        result = parse_pair("ALI", q, e)
    valid = [i for i in result.items if i.is_valid]
    assert len(valid) == 1, [i.issues for i in result.items] + [result.issues]
    imported = valid[0].question
    assert imported is not None
    assert imported.qid.source_number == number
    assert imported.content == content


# ------------------------------------------------------------------------------------- references
@FUZZ
@given(any_text, st.one_of(st.none(), st.sampled_from(["ALI", "VENOUS", "XYZ", ""])))
@example("ALI-0001", None)
@example("ALI-" + "9" * 400, None)
@example("../../ALI-0001", "ALI")
def test_reference_parser_is_total(text: str, default: str | None) -> None:
    try:
        qid = parse_reference(text, default)
    except InvalidReference:
        return
    assert qid.source_number >= 1


# --------------------------------------------------------------------------------- seed manifest
@FUZZ
@given(st.one_of(st.binary(max_size=600), json_values.map(lambda v: json.dumps(v).encode())))
@example(b'{"manifest_format": 1e999}')
@example(b'{"manifest_format": 1, "question_count": 1e999}')
@example(b'{"manifest_format": ' + b"9" * 5000 + b"}")
@example(b"[" * 50_000)
def test_seed_manifest_parser_is_total(data: bytes) -> None:
    try:
        parse_manifest(data)
    except SeedInvalid:  # the documented, controlled refusal
        return


# -------------------------------------------------------------------------------------- settings
@FUZZ
@given(json_values)
def test_settings_parser_is_total(data: object) -> None:
    assert isinstance(parse_settings(data), AppSettings)


@FUZZ
@given(st.binary(max_size=400))
@example(b"[" * 50_000)
@example(b'{"recent_reviewers": ["' + "ა".encode() * 10_000 + b'"]}')
def test_settings_file_of_any_content_loads_defaults_or_values(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "settings.json"
        path.write_bytes(data)
        assert isinstance(SettingsStore(path).load(), AppSettings)


# ------------------------------------------------------------------------- journals and manifests
@FUZZ
@given(json_values)
def test_restore_journal_validation_is_total(data: object) -> None:
    assert isinstance(restore_service.journal_problems(data), list)


@FUZZ
@given(st.binary(max_size=600))
@example(b"[" * 50_000)
def test_restore_journal_file_of_any_content_is_never_trusted_blindly(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as folder:
        paths = AppPaths(data_dir=Path(folder))
        path = restore_service.journal_path(paths)
        path.parent.mkdir(parents=True)
        path.write_bytes(data)
        parsed = restore_service._read_journal(paths)
        assert parsed is None or not restore_service.journal_problems(parsed)


@FUZZ
@given(json_values)
def test_export_journal_validation_is_total(data: object) -> None:
    journal = Path(tempfile.gettempdir()) / "export-20260101-120000-abcdef.json"
    try:
        export_id, final, staging, _digest, _rows = export_publication.parse_journal(data, journal)
    except export_publication.UntrustedJournal:
        return
    assert final.parent == staging.parent and export_id in final.name


@FUZZ
@given(st.one_of(json_values, st.binary(max_size=300)))
def test_export_manifest_verification_is_total(data: object) -> None:
    with tempfile.TemporaryDirectory() as folder:
        export = Path(folder) / "VascQBank_Export_x"
        export.mkdir()
        manifest = export / export_publication.MANIFEST_NAME
        manifest.write_bytes(data if isinstance(data, bytes) else json.dumps(data).encode())
        digest = export_publication._sha256(manifest)
        problems = export_publication.verify_published(export, digest)
        assert isinstance(problems, list)


@FUZZ
@given(st.one_of(json_values.map(lambda v: json.dumps(v).encode()), st.binary(max_size=300)))
@example(b'{"size": 1e999, "sha256": "' + b"0" * 64 + b'"}')
@example(b'{"size": true, "sha256": "' + b"0" * 64 + b'"}')
def test_backup_manifest_check_is_total(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as folder:
        backup = Path(folder) / "vascqbank-20260101-000000-000000-manual.sqlite3"
        backup.write_bytes(b"SQLite format 3\x00" + b"\x00" * 84)
        backup.with_name(backup.name + ".json").write_bytes(data)
        check = backup_db.check_backup_file(backup)
        assert not check.ok  # never a database of ours: must not verify, must not raise
