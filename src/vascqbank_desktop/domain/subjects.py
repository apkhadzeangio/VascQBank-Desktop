"""The 12 VascQBank subjects.

Codes, Georgian names and target counts mirror the table in the VascQBank-10K
repository's AGENTS.md (section 2). Codes are also the prefixes of the source
files, e.g. ``ALI_questions.txt`` / ``ALI_explanations.txt``.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Subject:
    code: str
    name_ka: str
    target_count: int


SUBJECTS: tuple[Subject, ...] = (
    Subject(
        "PAD_CLTI",
        "პერიფერიული არტერიული დაავადება და კიდურისთვის საფრთხის შემცველი "
        "ქრონიკული იშემია (CLTI)",
        1500,
    ),
    Subject("ALI", "კიდურის მწვავე იშემია", 900),
    Subject("AAA_EVAR", "მუცლის აორტის ანევრიზმა და EVAR", 1200),
    Subject("THORACIC_AORTA", "გულმკერდის აორტა და დისექცია", 700),
    Subject("CAROTID_VERTEBRAL", "საძილე და ხერხემლის არტერიების პათოლოგიები", 900),
    Subject("VENOUS", "ვენური დაავადებები", 900),
    Subject("DIALYSIS_ACCESS", "ჰემოდიალიზის სისხლძარღვოვანი მიდგომა", 650),
    Subject("MESENTERIC_RENAL", "მეზენტერიული და თირკმლის არტერიები", 600),
    Subject("VASCULAR_TRAUMA", "სისხლძარღვთა ტრავმა", 650),
    Subject(
        "VGEI",
        "სისხლძარღვოვანი პროთეზებისა და ენდოპროთეზების ინფექციები",
        500,
    ),
    Subject("ANATOMY_IMAGING", "სისხლძარღვთა ანატომია და ვიზუალიზაცია", 900),
    Subject("PERIOPERATIVE", "პერიოპერაციული მართვა და გართულებები", 600),
)

TOTAL_TARGET_QUESTIONS: int = sum(s.target_count for s in SUBJECTS)

_BY_CODE: dict[str, Subject] = {s.code: s for s in SUBJECTS}


def get_subject(code: str) -> Subject:
    """Return the subject for *code*; raise ``KeyError`` for unknown codes."""
    try:
        return _BY_CODE[code]
    except KeyError:
        raise KeyError(f"Unknown subject code: {code!r}") from None


def is_known_subject(code: str) -> bool:
    return code in _BY_CODE
