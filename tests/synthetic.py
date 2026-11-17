"""Synthetic Georgian question data for tests and benchmarks.

Every text here is artificial filler that only imitates the file formats.
No real medical question content is used or committed.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from vascqbank_desktop.domain.answers import ANSWER_LETTERS
from vascqbank_desktop.domain.subjects import SUBJECTS


def correct_letter(number: int) -> str:
    return ANSWER_LETTERS[(number * 7 + 3) % 5]


def question_block(code: str, number: int, *, correct: str | None = None) -> str:
    correct = correct or correct_letter(number)
    lines = [
        f"{number}. სინთეზური სატესტო კითხვა {code} №{number}: "
        "რომელი ვარიანტია სწორი ამ ხელოვნურ მაგალითში?"
    ]
    for letter in ANSWER_LETTERS:
        marker = "*" if letter == correct else ""
        lines.append(f"{marker}{letter}) ვარიანტი {letter} — სატესტო ტექსტი {code}-{number}")
    return "\n".join(lines)


def explanation_block(code: str, number: int, *, letter: str | None = None) -> str:
    letter = letter or correct_letter(number)
    return (
        f"{number}. {letter}) ეს სინთეზური განმარტებაა კითხვისთვის {code} №{number}. "
        f"ის მხოლოდ პროგრამის ტესტირებისთვის არსებობს."
    )


def write_pair(
    folder: Path,
    code: str,
    numbers: Iterable[int],
    *,
    questions_text: str | None = None,
    explanations_text: str | None = None,
) -> tuple[Path, Path]:
    numbers = list(numbers)
    folder.mkdir(parents=True, exist_ok=True)
    q = folder / f"{code}_questions.txt"
    e = folder / f"{code}_explanations.txt"
    q.write_text(
        questions_text if questions_text is not None
        else "\n\n".join(question_block(code, n) for n in numbers) + "\n",
        encoding="utf-8",
    )
    e.write_text(
        explanations_text if explanations_text is not None
        else "\n\n".join(explanation_block(code, n) for n in numbers) + "\n",
        encoding="utf-8",
    )
    return q, e


def write_full_bank(folder: Path) -> int:
    """All 12 subjects up to their target counts: exactly 10,000 questions."""
    total = 0
    for subject in SUBJECTS:
        write_pair(folder, subject.code, range(1, subject.target_count + 1))
        total += subject.target_count
    return total


def master_record(code: str, number: int, *, status: str = "APPROVED",
                  correct: str | None = None, answer_letter: str | None = None,
                  drop_section: str | None = None) -> str:
    correct = correct or correct_letter(number)
    answer_letter = answer_letter or correct
    options = [
        f"{'*' if letter == correct else ''}{letter}) მასტერის ვარიანტი {letter} {code}-{number}"
        for letter in ANSWER_LETTERS
    ]
    sections = {
        "QUESTION": f"რაფინირებული სინთეზური კითხვა {code} №{number}?",
        "OPTIONS": "\n".join(options),
        "CORRECT ANSWER": f"{answer_letter}) მასტერის ვარიანტი {answer_letter} {code}-{number}",
        "ACADEMIC EXPLANATION": f"სინთეზური აკადემიური განმარტება №{number}.",
        "CLINICAL REASONING": "სინთეზური კლინიკური მსჯელობა.",
        "DISTRACTOR ANALYSIS": "სინთეზური დისტრაქტორების ანალიზი.",
        "EVIDENCE": "[SYN2026] სინთეზური წყარო\nhttps://example.invalid/ref\n\n[SYN2027] მეორე წყარო",
        "CLINICAL PEARL": "სინთეზური კლინიკური რჩევა.",
    }
    head = [
        f"TOPIC: {code}",
        f"ORIGINAL ID: {number}",
        f"SOURCE FINGERPRINT: {'ab' * 32}",
        f"SOURCE COMMIT: {'cd' * 20}",
        f"STATUS: {status}",
        "QA SCORE: 90/100",
        "REVIEWED: 2026-11-15T23:27:04+04:00",
    ]
    body = [f"{name}:\n{text}" for name, text in sections.items() if name != drop_section]
    return "\n".join(head) + "\n\n" + "\n\n".join(body) + "\n\nQA GATES: 8/8; სინთეზური\n"


def write_master(folder: Path, records: list[str]) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "REFINED_MASTER_QBANK.txt"
    header = "VascQBank-10K — სინთეზური მასტერი\n\nSource Questions Total: 0\n"
    sep = "-" * 50
    path.write_text(header + "".join(f"\n{sep}\n{r}" for r in records), encoding="utf-8")
    return path
