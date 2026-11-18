"""A small synthetic bank for the export tests: gaps in the numbering, two subjects, edited,
approved, rejected and pending questions, Georgian multiline text and scientific notation."""

from __future__ import annotations

from bank import make_question, seed

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.edit_service import EditService
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore

REVIEWER = "რამაზ აფხაძე"
ALI_NUMBERS = (1, 2, 3, 5, 8, 13, 21, 34)
VENOUS_NUMBERS = (4, 7, 10)
SCIENTIFIC = (
    "EVAR-ის შემდეგ CTA აჩვენებს ენდოლიკს (ტიპი II); ABI = 0.45, წნევა 70 mmHg,\n"
    "სტენოზი ≥ 70 % — ESVS 2024; α-ბლოკატორი, β₂, Δp ≈ 15 mmHg, 10⁹/ლ, CLTI, ±2 σ."
)


class Bank:
    def __init__(self, paths: AppPaths) -> None:
        self.paths = paths
        self.profile = ReviewerProfile(SettingsStore(paths.settings_file))
        self.profile.set_name(REVIEWER)
        self.catalog = CatalogService(paths)
        self.edits = EditService(paths, self.profile)
        self.review = ReviewService(paths, self.profile)

    def detail(self, uid: str):
        detail = self.catalog.detail_by_uid(uid)
        assert detail is not None
        return detail

    def edit(self, uid: str, **changes) -> None:
        d = self.detail(uid)
        c = d.content
        content = QuestionContent(
            changes.get("stem", c.stem),
            changes.get("options", c.options),
            changes.get("correct", c.correct),
            changes.get("explanation", c.explanation),
        )
        self.edits.save(d.id, d.lock_version, content)

    def approve(self, *uids: str) -> None:
        for uid in uids:
            d = self.detail(uid)
            self.review.approve(d.id, d.lock_version)

    def reject(self, uid: str) -> None:
        d = self.detail(uid)
        self.review.reject(d.id, d.lock_version, "სინთეზური მიზეზი")


def build(paths: AppPaths) -> Bank:
    seed(
        paths,
        [make_question("ALI", n) for n in ALI_NUMBERS]
        + [make_question("VENOUS", n, correct=AnswerLetter.E) for n in VENOUS_NUMBERS],
    )
    bank = Bank(paths)
    bank.edit(
        "ALI-0003",
        stem=f"სინთეზური შემთხვევა, ორ ხაზად:\n{SCIENTIFIC}",
        options=(
            "ენდოვასკულური მკურნალობა (EVAR)",
            "ღია რეკონსტრუქცია\nდისტალური ანასტომოზით",
            "„დაკვირვება“ — CTA 6 თვეში",
            "ანტიკოაგულაცია (INR 2–3)",
            "ამპუტაცია",
        ),
        correct=AnswerLetter.B,
        explanation="სინთეზური განმარტება.\nმეორე ხაზი: ხსნადობა < 5 % და p < 0.05.",
    )
    bank.approve("ALI-0001", "ALI-0003", "ALI-0008", "ALI-0021", "VENOUS-0007", "VENOUS-0010")
    bank.edit("ALI-0005", stem="რედაქტირებული, მაგრამ დაუმტკიცებელი ტექსტი")  # in review
    bank.reject("ALI-0013")
    return bank


APPROVED = ("ALI-0001", "ALI-0003", "ALI-0008", "ALI-0021", "VENOUS-0007", "VENOUS-0010")
NOT_APPROVED = ("ALI-0002", "ALI-0005", "ALI-0013", "ALI-0034", "VENOUS-0004")
