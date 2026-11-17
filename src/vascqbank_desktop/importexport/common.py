"""Types and helpers shared by the import adapters."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from vascqbank_desktop.domain.question import ImportedQuestion, SourceKind
from vascqbank_desktop.domain.question_id import QuestionId
from vascqbank_desktop.importexport.text_io import TextDecodingError
from vascqbank_desktop.validation.issues import (
    IssueCode,
    ValidationIssue,
    has_errors,
    make_issue,
)
from vascqbank_desktop.validation.text import normalize_text


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: Path
    sha256: str
    size: int

    @property
    def name(self) -> str:
        return self.path.name


@dataclass(slots=True)
class ParsedItem:
    """One question-shaped unit found in a source, valid or not."""

    qid: QuestionId | None
    location: str
    question: ImportedQuestion | None = None
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.question is not None and not has_errors(self.issues)


@dataclass(slots=True)
class ParseResult:
    kind: SourceKind
    label: str
    files: list[SourceFile] = field(default_factory=list)
    items: list[ParsedItem] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)  # not tied to one question


def read_source_file(path: Path) -> tuple[str | None, SourceFile | None, list[ValidationIssue]]:
    """Read *path* as strict UTF-8; never raises for bad input files."""
    name = path.name
    try:
        data = path.read_bytes()
    except OSError as exc:
        return None, None, [make_issue(IssueCode.FILE_UNREADABLE, file=name, error=exc.strerror or exc)]
    source = SourceFile(path=path, sha256=hashlib.sha256(data).hexdigest(), size=len(data))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        prefix = data[: exc.start]
        line = prefix.count(b"\n") + 1
        column = len(prefix[prefix.rfind(b"\n") + 1 :].decode("utf-8", errors="replace")) + 1
        err = TextDecodingError(path, line, column, exc.start)
        return None, source, [
            make_issue(IssueCode.FILE_NOT_UTF8, file=name, line=err.line, column=err.column)
        ]
    text = normalize_text(text)
    if not text.strip():
        return None, source, [make_issue(IssueCode.FILE_EMPTY, file=name)]
    return text, source, []


# --- answer option lines --------------------------------------------------
# Captures everything before the letter so marker mistakes can be diagnosed:
#   "*გ) text"  -> correct        "* გ) text" / " *გ)" -> misplaced marker
#   "გ)* text"  -> misplaced      "გ. text" / "გ: text" -> wrong option format
_OPTION = re.compile(r"^(?P<pre>[\s*]*)(?P<letter>[ა-ჰ])\)(?P<text>.*)$")
_OPTION_WRONG_DELIMITER = re.compile(r"^[\s*]*[აბგდე](?:[.:]\s+\S|\*\))")


@dataclass(slots=True)
class OptionLine:
    letter: str
    starred: bool
    text: str
    line: int
    marker_ok: bool  # False when '*' is present but not exactly "*<letter>)"


def parse_option_line(text: str, line_no: int) -> OptionLine | None:
    match = _OPTION.match(text)
    if match is None:
        return None
    pre = match.group("pre")
    body = match.group("text")
    starred = "*" in pre or body.lstrip().startswith("*")
    marker_ok = pre in ("", "*") and not body.lstrip().startswith("*")
    if body.lstrip().startswith("*"):
        body = body.lstrip()[1:]
    return OptionLine(
        letter=match.group("letter"),
        starred=starred,
        text=body.strip(),
        line=line_no,
        marker_ok=marker_ok,
    )


def looks_like_wrong_option_format(text: str) -> bool:
    return _OPTION_WRONG_DELIMITER.match(text) is not None


def join_lines(lines: list[str]) -> str:
    """Join multiline text, trimming line ends and outer blank lines."""
    trimmed = [ln.rstrip() for ln in lines]
    while trimmed and not trimmed[0].strip():
        trimmed.pop(0)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return "\n".join(ln.strip() if i == 0 else ln for i, ln in enumerate(trimmed)).strip()
