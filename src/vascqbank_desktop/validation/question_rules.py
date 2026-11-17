"""Source-independent content rules applied to every parsed question.

These checks are structural and editorial only. They never judge medical
correctness — that remains the reviewer's decision.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from vascqbank_desktop.domain.answers import ANSWER_LETTERS
from vascqbank_desktop.validation.issues import IssueCode, ValidationIssue, make_issue

_MARKDOWN = re.compile(r"(\*\*|__|`|^#{1,6}\s|^\s*[-+]\s{1,3}\S)", re.M)
# A sentence ends at . ! ? … followed by whitespace or end of text, but not in a
# decimal number written with a point (2.5) — Georgian decimals use a comma.
_SENTENCE_END = re.compile(r"(?<!\d\.\d)[.!?…]+(?=\s|$)")


def count_sentences(text: str) -> int:
    stripped = text.strip()
    if not stripped:
        return 0
    ends = len(_SENTENCE_END.findall(stripped))
    # Text without final punctuation still counts its last sentence.
    if not re.search(r"[.!?…][\"'»“”)]*\s*$", stripped):
        ends += 1
    return ends


def normalized_key(text: str) -> str:
    """Comparison key for duplicate detection (case, spacing, final punctuation)."""
    collapsed = " ".join(text.casefold().split())
    return collapsed.rstrip(" .?!…:;")


def content_warnings(
    *,
    qid: str,
    file: str | None,
    line: int | None,
    stem: str,
    options: Sequence[str],
    explanation: str,
    check_sentence_count: bool,
) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []

    keys = [normalized_key(o) for o in options]
    seen: dict[str, list[str]] = {}
    for letter, key in zip(ANSWER_LETTERS, keys, strict=False):
        if key:
            seen.setdefault(key, []).append(letter)
    for letters in seen.values():
        if len(letters) > 1:
            issues.append(
                make_issue(
                    IssueCode.DUPLICATE_OPTIONS, file=file, line=line, qid=qid,
                    letters=", ".join(f"{x})" for x in letters),
                )
            )

    if check_sentence_count:
        count = count_sentences(explanation)
        if count != 2:
            issues.append(
                make_issue(IssueCode.EXPLANATION_SENTENCES, file=file, line=line, qid=qid, count=count)
            )

    for text in (stem, *options, explanation):
        match = _MARKDOWN.search(text)
        if match:
            issues.append(
                make_issue(
                    IssueCode.MARKDOWN_REMNANT, file=file, line=line, qid=qid,
                    token=match.group(0).strip(),
                )
            )
            break
    return issues


def numbering_warnings(
    numbers_in_file_order: Iterable[int], *, file: str, qid_prefix: str
) -> list[ValidationIssue]:
    """Order and gap warnings for one source file (numbers already unique-checked)."""
    issues: list[ValidationIssue] = []
    previous: int | None = None
    ordered: list[int] = []
    for number in numbers_in_file_order:
        if previous is not None and number <= previous:
            issues.append(
                make_issue(IssueCode.NUMBER_ORDER, file=file, number=number, previous=previous)
            )
        previous = number
        ordered.append(number)
    if ordered:
        present = set(ordered)
        missing = [n for n in range(1, max(present) + 1) if n not in present]
        if missing:
            issues.append(
                make_issue(IssueCode.NUMBER_GAP, file=file, missing=_compress_ranges(missing))
            )
    return issues


def _compress_ranges(numbers: list[int], limit: int = 12) -> str:
    ranges: list[str] = []
    start = prev = numbers[0]
    for n in numbers[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append(f"{start}" if start == prev else f"{start}–{prev}")
        start = prev = n
    ranges.append(f"{start}" if start == prev else f"{start}–{prev}")
    if len(ranges) > limit:
        return ", ".join(ranges[:limit]) + f" … (სულ {len(numbers)})"
    return ", ".join(ranges)
