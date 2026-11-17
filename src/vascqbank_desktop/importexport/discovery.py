"""Find import sources among user-selected files and folders.

* ``<CODE>_questions.txt`` + ``<CODE>_explanations.txt`` → one paired source per subject;
* ``REFINED_MASTER_QBANK.txt`` → one refined-master source;
* anything else → reported and skipped.

Folders are scanned non-recursively, so choosing the VascQBank-10K root picks
up the 24 paired files but not ``ACADEMIC_REVIEW/``; the master must be
chosen explicitly. This avoids importing the same questions from two formats
by accident (if both are chosen, overlapping IDs are reported as duplicates).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from vascqbank_desktop.domain.question import SourceKind
from vascqbank_desktop.domain.subjects import SUBJECTS, is_known_subject
from vascqbank_desktop.importexport import paired_txt, refined_master
from vascqbank_desktop.importexport.common import ParseResult
from vascqbank_desktop.validation.issues import IssueCode, ValidationIssue, make_issue

_SUBJECT_ORDER = {s.code: i for i, s in enumerate(SUBJECTS)}


@dataclass(frozen=True, slots=True)
class ImportSource:
    kind: SourceKind
    label: str
    subject_code: str | None = None
    questions_path: Path | None = None
    explanations_path: Path | None = None
    master_path: Path | None = None

    @property
    def files(self) -> tuple[Path, ...]:
        return tuple(p for p in (self.questions_path, self.explanations_path, self.master_path) if p)

    @property
    def is_complete(self) -> bool:
        if self.kind is SourceKind.REFINED_MASTER:
            return True
        return bool(self.questions_path and self.explanations_path) and is_known_subject(
            self.subject_code or ""
        )

    def parse(self) -> ParseResult:
        if self.kind is SourceKind.REFINED_MASTER:
            assert self.master_path is not None
            return refined_master.parse_master(self.master_path)
        return paired_txt.parse_pair(
            self.subject_code or "", self.questions_path, self.explanations_path
        )


@dataclass(slots=True)
class Discovery:
    sources: list[ImportSource] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)


def _expand(paths: Iterable[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(p for p in path.iterdir() if p.is_file() and p.suffix.lower() == ".txt"))
        elif path.is_file():
            files.append(path)
    unique: dict[Path, Path] = {}
    for f in files:
        unique.setdefault(f.resolve(), f)
    return list(unique.values())


def discover(paths: Iterable[Path]) -> Discovery:
    result = Discovery()
    pairs: dict[str, dict[str, list[Path]]] = {}
    masters: list[Path] = []
    for path in _expand(paths):
        if path.name == refined_master.FILENAME:
            masters.append(path)
            continue
        match = paired_txt.FILENAME.match(path.name)
        if match is None:
            result.issues.append(make_issue(IssueCode.FILE_UNRECOGNIZED, file=path.name))
            continue
        pairs.setdefault(match.group("code"), {}).setdefault(match.group("kind"), []).append(path)

    for code in sorted(pairs, key=lambda c: (_SUBJECT_ORDER.get(c, 99), c)):
        kinds = pairs[code]
        duplicated = [p for group in kinds.values() if len(group) > 1 for p in group]
        if duplicated:
            result.issues.append(
                make_issue(IssueCode.DUPLICATE_FILE, file=duplicated[0].name,
                           files=", ".join(str(p) for p in duplicated))
            )
            continue
        result.sources.append(
            ImportSource(
                kind=SourceKind.PAIRED_TXT,
                label=code,
                subject_code=code,
                questions_path=kinds.get("questions", [None])[0],
                explanations_path=kinds.get("explanations", [None])[0],
            )
        )
    if len(masters) > 1:
        result.issues.append(
            make_issue(IssueCode.DUPLICATE_FILE, file=masters[0].name,
                       files=", ".join(str(p) for p in masters))
        )
    elif masters:
        result.sources.append(
            ImportSource(kind=SourceKind.REFINED_MASTER, label=masters[0].name, master_path=masters[0])
        )
    return result
