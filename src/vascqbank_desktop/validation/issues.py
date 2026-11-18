"""Validation issues with Georgian messages.

Each issue has a stable code (for tests, logs and manifests), a severity and a
Georgian message shown to the reviewer. Errors exclude the affected question
(or file) from import; warnings never do.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Severity(StrEnum):
    ERROR = "ERROR"
    WARNING = "WARNING"


class IssueCode(StrEnum):
    # --- file level ---------------------------------------------------------
    FILE_NOT_UTF8 = "FILE_NOT_UTF8"
    FILE_UNREADABLE = "FILE_UNREADABLE"
    FILE_EMPTY = "FILE_EMPTY"
    FILE_UNRECOGNIZED = "FILE_UNRECOGNIZED"
    UNKNOWN_SUBJECT = "UNKNOWN_SUBJECT"
    MISSING_EXPLANATIONS_FILE = "MISSING_EXPLANATIONS_FILE"
    MISSING_QUESTIONS_FILE = "MISSING_QUESTIONS_FILE"
    DUPLICATE_FILE = "DUPLICATE_FILE"
    UNEXPECTED_TEXT = "UNEXPECTED_TEXT"
    # --- numbering ----------------------------------------------------------
    INVALID_NUMBER = "INVALID_NUMBER"
    DUPLICATE_NUMBER = "DUPLICATE_NUMBER"
    NUMBER_ABOVE_TARGET = "NUMBER_ABOVE_TARGET"
    NUMBER_ORDER = "NUMBER_ORDER"
    NUMBER_GAP = "NUMBER_GAP"
    DUPLICATE_ID_IN_SELECTION = "DUPLICATE_ID_IN_SELECTION"
    # --- question structure --------------------------------------------------
    EMPTY_STEM = "EMPTY_STEM"
    OPTION_COUNT = "OPTION_COUNT"
    OPTION_LETTERS = "OPTION_LETTERS"
    OPTION_FORMAT = "OPTION_FORMAT"
    EMPTY_OPTION = "EMPTY_OPTION"
    NO_CORRECT_ANSWER = "NO_CORRECT_ANSWER"
    MULTIPLE_CORRECT_ANSWERS = "MULTIPLE_CORRECT_ANSWERS"
    MISPLACED_MARKER = "MISPLACED_MARKER"
    # --- explanations -------------------------------------------------------
    MISSING_EXPLANATION = "MISSING_EXPLANATION"
    ORPHAN_EXPLANATION = "ORPHAN_EXPLANATION"
    DUPLICATE_EXPLANATION = "DUPLICATE_EXPLANATION"
    EXPLANATION_FORMAT = "EXPLANATION_FORMAT"
    EXPLANATION_MARKER = "EXPLANATION_MARKER"
    EXPLANATION_LETTER_MISMATCH = "EXPLANATION_LETTER_MISMATCH"
    EMPTY_EXPLANATION = "EMPTY_EXPLANATION"
    # --- refined master -----------------------------------------------------
    MASTER_FIELD_MISSING = "MASTER_FIELD_MISSING"
    MASTER_FIELD_ORDER = "MASTER_FIELD_ORDER"
    MASTER_UNEXPECTED_LINE = "MASTER_UNEXPECTED_LINE"
    MASTER_NOT_REFINED = "MASTER_NOT_REFINED"
    MASTER_ANSWER_MISMATCH = "MASTER_ANSWER_MISMATCH"
    MASTER_NO_RECORDS = "MASTER_NO_RECORDS"
    # --- field content (Phase 9b: the same limits the editor and the approval apply) -----------
    FIELD_CONTROL_CHARACTERS = "FIELD_CONTROL_CHARACTERS"
    FIELD_TOO_LONG = "FIELD_TOO_LONG"
    # --- warnings -----------------------------------------------------------
    EXPLANATION_SENTENCES = "EXPLANATION_SENTENCES"
    DUPLICATE_STEM = "DUPLICATE_STEM"
    DUPLICATE_OPTIONS = "DUPLICATE_OPTIONS"
    STRAY_ASTERISK = "STRAY_ASTERISK"
    MARKDOWN_REMNANT = "MARKDOWN_REMNANT"
    MISSING_BLANK_LINE = "MISSING_BLANK_LINE"
    # --- database comparison --------------------------------------------------
    CONFLICT_EXISTING = "CONFLICT_EXISTING"


E, W = Severity.ERROR, Severity.WARNING

_CATALOG: dict[IssueCode, tuple[Severity, str]] = {
    IssueCode.FILE_NOT_UTF8: (E, "ფაილი არ არის სწორი UTF-8 კოდირებით (სტრიქონი {line}, სვეტი {column}). შეინახეთ ფაილი UTF-8 კოდირებით."),
    IssueCode.FILE_UNREADABLE: (E, "ფაილის წაკითხვა ვერ მოხერხდა: {error}"),
    IssueCode.FILE_EMPTY: (E, "ფაილი ცარიელია."),
    IssueCode.FILE_UNRECOGNIZED: (W, "ფაილის სახელი არ შეესაბამება ცნობილ ფორმატს (<კოდი>_questions.txt, <კოდი>_explanations.txt ან REFINED_MASTER_QBANK.txt); ფაილი გამოტოვებულია."),
    IssueCode.UNKNOWN_SUBJECT: (E, "უცნობი თემის კოდი „{subject}“; ფაილი არ დაიმპორტდება."),
    IssueCode.MISSING_EXPLANATIONS_FILE: (E, "კითხვების ფაილს არ ახლავს განმარტებების ფაილი ({expected}); ამ თემის კითხვები არ დაიმპორტდება."),
    IssueCode.MISSING_QUESTIONS_FILE: (E, "განმარტებების ფაილს არ ახლავს კითხვების ფაილი ({expected})."),
    IssueCode.DUPLICATE_FILE: (E, "ერთი და იგივე ფაილი რამდენჯერმეა არჩეული ან ერთ თემას ორი ფაილი შეესაბამება ({files})."),
    IssueCode.UNEXPECTED_TEXT: (E, "სტრიქონი {line} ვერ მიეკუთვნა ვერც ერთ კითხვას: „{text}“."),
    IssueCode.INVALID_NUMBER: (E, "კითხვის ნომერი „{number}“ დაუშვებელია; ნომერი დადებითი მთელი რიცხვი უნდა იყოს."),
    IssueCode.DUPLICATE_NUMBER: (E, "ნომერი {number} ფაილში მეორდება (სტრიქონები {lines}); არც ერთი ვერსია არ დაიმპორტდება."),
    IssueCode.NUMBER_ABOVE_TARGET: (E, "ნომერი {number} აღემატება თემის სამიზნე რაოდენობას ({target})."),
    IssueCode.NUMBER_ORDER: (W, "ნომერი {number} მოდის {previous}-ის შემდეგ; ნუმერაცია ზრდადი უნდა იყოს."),
    IssueCode.NUMBER_GAP: (W, "ნუმერაციაში გამოტოვებულია: {missing}."),
    IssueCode.DUPLICATE_ID_IN_SELECTION: (E, "ID {qid} არჩეულ ფაილებში რამდენჯერმე გვხვდება ({where}); არც ერთი ვერსია არ დაიმპორტდება."),
    IssueCode.EMPTY_STEM: (E, "კითხვის ტექსტი ცარიელია."),
    IssueCode.OPTION_COUNT: (E, "კითხვას ზუსტად 5 პასუხი (ა–ე) უნდა ჰქონდეს; ნაპოვნია {count}."),
    IssueCode.OPTION_LETTERS: (E, "პასუხების ასოები უნდა იყოს ა), ბ), გ), დ), ე) ამ თანმიმდევრობით; ნაპოვნია: {letters}."),
    IssueCode.OPTION_FORMAT: (E, "სტრიქონი {line}: პასუხის ფორმატი არასწორია — უნდა იყოს „ა) ტექსტი“ (ასო, მრგვალი ფრჩხილი, ტექსტი)."),
    IssueCode.EMPTY_OPTION: (E, "პასუხი {letter}) ცარიელია."),
    IssueCode.NO_CORRECT_ANSWER: (E, "სწორი პასუხი მონიშნული არ არის: ფიფქი (*) უშუალოდ სწორი პასუხის ასოს წინ უნდა იდგეს, მაგ.: *გ)."),
    IssueCode.MULTIPLE_CORRECT_ANSWERS: (E, "მონიშნულია {count} სწორი პასუხი ({letters}); დასაშვებია ზუსტად ერთი."),
    IssueCode.MISPLACED_MARKER: (E, "სტრიქონი {line}: ფიფქი (*) არასწორ ადგილასაა; ის უშუალოდ ასოს წინ უნდა იდგეს, ჰარის გარეშე (მაგ.: *გ))."),
    IssueCode.MISSING_EXPLANATION: (E, "კითხვა №{number}-ს განმარტებების ფაილში შესაბამისი განმარტება არ აქვს."),
    IssueCode.ORPHAN_EXPLANATION: (E, "განმარტება №{number}-ს კითხვების ფაილში შესაბამისი კითხვა არ აქვს."),
    IssueCode.DUPLICATE_EXPLANATION: (E, "განმარტება №{number} მეორდება (სტრიქონები {lines})."),
    IssueCode.EXPLANATION_FORMAT: (E, "სტრიქონი {line}: განმარტება უნდა იწყებოდეს ასე: „N. ასო) ტექსტი“."),
    IssueCode.EXPLANATION_MARKER: (E, "სტრიქონი {line}: განმარტებების ფაილში ფიფქი (*) არ უნდა იყოს."),
    IssueCode.EXPLANATION_LETTER_MISMATCH: (E, "განმარტებაში მითითებული ასო „{explanation_letter})“ არ ემთხვევა კითხვაში მონიშნულ სწორ პასუხს „{correct})“."),
    IssueCode.EMPTY_EXPLANATION: (E, "განმარტება ცარიელია."),
    IssueCode.MASTER_FIELD_MISSING: (E, "ჩანაწერს აკლია სავალდებულო ველი „{field}“."),
    IssueCode.MASTER_FIELD_ORDER: (E, "ჩანაწერის ველების თანმიმდევრობა არასწორია: მოსალოდნელია „{expected}“, ნაპოვნია „{found}“."),
    IssueCode.MASTER_UNEXPECTED_LINE: (E, "სტრიქონი {line} ჩანაწერის სტრუქტურას არ შეესაბამება: „{text}“."),
    IssueCode.MASTER_NOT_REFINED: (E, "ჩანაწერის სტატუსია „{status}“; იმპორტდება მხოლოდ რაფინირებული (APPROVED) ჩანაწერები."),
    IssueCode.MASTER_ANSWER_MISMATCH: (E, "„CORRECT ANSWER“ („{answer}“) არ ემთხვევა ფიფქით მონიშნულ პასუხს „{starred})“."),
    IssueCode.MASTER_NO_RECORDS: (E, "ფაილში ვერც ერთი ჩანაწერი ვერ მოიძებნა."),
    IssueCode.EXPLANATION_SENTENCES: (W, "განმარტება შეიცავს {count} წინადადებას; მოსალოდნელია ზუსტად ორი."),
    IssueCode.DUPLICATE_STEM: (W, "კითხვის ტექსტი ემთხვევა {other}-ს — შესაძლო დუბლიკატი."),
    IssueCode.DUPLICATE_OPTIONS: (W, "პასუხები {letters} ერთმანეთს ემთხვევა."),
    IssueCode.STRAY_ASTERISK: (W, "სტრიქონი {line}: ტექსტში ფიფქი (*) გვხვდება; შეამოწმეთ, ხომ არ არის შემთხვევითი."),
    IssueCode.FIELD_CONTROL_CHARACTERS: (E, "ველი „{field}“ შეიცავს დაუშვებელ საკონტროლო სიმბოლოს (მაგ. NUL); ასეთ კითხვას ვერც შეინახავდით და ვერც დაამტკიცებდით. გაასწორეთ წყარო ფაილი."),
    IssueCode.FIELD_TOO_LONG: (E, "ველი „{field}“ ძალიან გრძელია ({length} სიმბოლო; დასაშვებია არაუმეტეს {limit}). სავარაუდოდ ფაილში ორი ჩანაწერი შეერთდა ან ზედმეტი ტექსტი მოხვდა."),
    IssueCode.MARKDOWN_REMNANT: (W, "ტექსტი შეიცავს Markdown-ის ნიშნებს ({token})."),
    IssueCode.MISSING_BLANK_LINE: (W, "სტრიქონი {line}: კითხვებს შორის ცარიელი სტრიქონი აკლია."),
    IssueCode.CONFLICT_EXISTING: (W, "ID {qid} უკვე არსებობს ბაზაში და შემომავალი ტექსტი განსხვავდება ორიგინალისგან. არსებული ჩანაწერი და რეცენზენტის ცვლილებები უცვლელი დარჩება; ეს ვერსია არ დაიმპორტდება."),
}


def severity_of(code: IssueCode) -> Severity:
    return _CATALOG[code][0]


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: IssueCode
    severity: Severity
    message: str
    file: str | None = None
    line: int | None = None
    question_id: str | None = None

    @property
    def is_error(self) -> bool:
        return self.severity is Severity.ERROR

    def location(self) -> str:
        if self.file and self.line:
            return f"{self.file}:{self.line}"
        return self.file or ""

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "question_id": self.question_id,
        }


def make_issue(
    code: IssueCode,
    *,
    file: str | None = None,
    line: int | None = None,
    qid: str | None = None,
    **params: object,
) -> ValidationIssue:
    severity, template = _CATALOG[code]
    if "line" not in params and line is not None:
        params["line"] = line
    params.setdefault("qid", qid or "")
    return ValidationIssue(
        code=code,
        severity=severity,
        message=template.format(**params),
        file=file,
        line=line,
        question_id=qid,
    )


def has_errors(issues: list[ValidationIssue] | tuple[ValidationIssue, ...]) -> bool:
    return any(i.is_error for i in issues)
