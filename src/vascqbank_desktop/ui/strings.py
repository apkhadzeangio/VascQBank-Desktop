"""Georgian user-interface strings, kept in one place for consistent wording."""

from __future__ import annotations

from typing import Final

from vascqbank_desktop.config.constants import APP_BRAND, APP_NAME_KA, APP_TAGLINE_KA, APP_TITLE_KA

WINDOW_TITLE: Final = APP_TITLE_KA
BRAND: Final = APP_BRAND
BRAND_SUBTITLE: Final = "კითხვების რეცენზირება"

# Navigation
NAV_SECTION: Final = "ნავიგაცია"
NAV_HOME: Final = "მთავარი"
NAV_CATALOG: Final = "კითხვები"
NAV_BACKUPS: Final = "სარეზერვო ასლები"
NAV_REVIEWER: Final = "რეცენზენტი"
SUBJECTS_SECTION: Final = "თემები"
SUBJECTS_HINT: Final = (
    "აირჩიეთ თემა მისი კითხვების სანახავად. თემა, რომელშიც კითხვები ჯერ არ არის, მიუწვდომელია."
)
SUBJECT_ALL: Final = "ყველა თემა"
SUBJECT_ALL_TOOLTIP: Final = "ყველა თემის კითხვები ერთ სიაში"
SUBJECT_TOOLTIP: Final = (
    "{name}\n"
    "კოდი: {code}\n"
    "სამიზნე რაოდენობა: {target} კითხვა\n"
    "იმპორტირებული კითხვების რაოდენობა ნაჩვენებია სახელის ქვეშ."
)
SUBJECT_COUNT: Final = "{imported} / {target}"
SUBJECTS_TOTAL: Final = "{count} თემა · ბაზაშია {imported} / {total} კითხვა"

# Welcome / empty state
WELCOME_HEADING: Final = APP_NAME_KA
WELCOME_TAGLINE: Final = APP_TAGLINE_KA
EMPTY_TITLE: Final = "კითხვები ჯერ არ არის იმპორტირებული"
EMPTY_BODY: Final = (
    "ადგილობრივი მონაცემთა ბაზა შექმნილია და მზადაა, მაგრამ ჯერ ცარიელია. "
    "დაიწყეთ კითხვებისა და განმარტებების იმპორტით.\n\n"
    "პროგრამა ახალ კითხვებს არ ქმნის — ის მხოლოდ იმპორტირებულ კითხვებს "
    "ამუშავებს: რედაქტირება, რეცენზირება, დამტკიცება და ექსპორტი."
)
IMPORT_BUTTON: Final = "კითხვების იმპორტი…"
BANK_TITLE: Final = "კითხვების ბანკი"
BANK_TOTAL: Final = "ბაზაში სულ {total} კითხვა {subjects} თემიდან"
BANK_STATUS_ROW: Final = "{label}: {count}"
BANK_NOTE: Final = (
    "ახალი კითხვები იწყება სტატუსით „განხილვის მოლოდინში“. დამტკიცება მხოლოდ "
    "რეცენზენტის გადაწყვეტილებაა და ავტომატურად არასდროს ხდება."
)
ROADMAP_TITLE: Final = "მომდევნო ეტაპებზე დაემატება"
ROADMAP_ITEMS: Final = (
    "კითხვის, ხუთი პასუხისა და განმარტების რედაქტირება ავტომატური შენახვით",
    "ცვლილებების ისტორია და წინა ვერსიის აღდგენა",
    "რეცენზირება და დამტკიცება",
    "დამტკიცებული კითხვების ექსპორტი Word (.docx) და TXT ფორმატში",
    "სარეზერვო ასლიდან აღდგენა",
)

# Storage card
STORAGE_TITLE: Final = "ადგილობრივი მონაცემთა ბაზა"
STORAGE_LOCATION: Final = "მდებარეობა"
STORAGE_SCHEMA: Final = "სქემის ვერსია"
STORAGE_INTEGRITY: Final = "მთლიანობის შემოწმება"
STORAGE_INTEGRITY_OK: Final = "წარმატებული"
STORAGE_INTEGRITY_FAILED: Final = "აღმოჩენილია პრობლემა — იხილეთ ჟურნალი"
STORAGE_DATABASE_ID: Final = "ბაზის იდენტიფიკატორი"
STORAGE_CREATED_NOW: Final = "ბაზა შეიქმნა ახლა"
STORAGE_OPENED: Final = "არსებული ბაზა გაიხსნა"

# Status bar
STATUS_OFFLINE: Final = "ოფლაინ რეჟიმი"
STATUS_SCHEMA: Final = "სქემა v{version}"
STATUS_BANK: Final = "ბაზაში: {total} კითხვა"
STATUS_VERSION: Final = "ვერსია {version}"
STATUS_REVIEWER: Final = "რეცენზენტი: {name}"
STATUS_REVIEWER_NONE: Final = "რეცენზენტი: არ არის არჩეული"

# Menus
MENU_FILE: Final = "&ფაილი"
ACTION_IMPORT: Final = "კითხვების იმპორტი…"
ACTION_OPEN_DATA_DIR: Final = "მონაცემთა საქაღალდის გახსნა"
ACTION_QUIT: Final = "გასვლა"
MENU_QUESTIONS: Final = "&კითხვები"
ACTION_SEARCH: Final = "ძიება"
ACTION_GOTO: Final = "გადასვლა ID-ით ან ნომრით"
ACTION_PREVIOUS: Final = "წინა კითხვა"
ACTION_NEXT: Final = "შემდეგი კითხვა"
MENU_HELP: Final = "&დახმარება"
ACTION_ABOUT: Final = "პროგრამის შესახებ"
ABOUT_TITLE: Final = "პროგრამის შესახებ"
ABOUT_TEXT: Final = (
    "<h3>{title}</h3>"
    "<p>ვერსია {version}</p>"
    "<p>სრულად ოფლაინ პროგრამა სისხლძარღვთა ქირურგიის საგამოცდო კითხვების "
    "რეცენზირებისა და დამტკიცებისთვის.</p>"
    "<p><b>მონაცემთა საქაღალდე:</b><br>{data_dir}</p>"
    "<p><b>მონაცემთა ბაზის სქემა:</b> v{schema}<br>"
    "<b>SQLite:</b> {sqlite}</p>"
    "<p>შრიფტები: Noto Sans და Noto Sans Georgian (SIL Open Font License 1.1).</p>"
)

# Startup errors
STARTUP_ERROR_TITLE: Final = "პროგრამის გაშვება ვერ მოხერხდა"
STARTUP_ERROR_TEXT: Final = (
    "მონაცემთა ბაზის გახსნა ან მომზადება ვერ მოხერხდა. მონაცემები არ შეცვლილა."
)

# Import dialog
IMP_TITLE: Final = "კითხვების იმპორტი"
IMP_STEP_SELECT: Final = "1. ფაილების არჩევა"
IMP_STEP_CHECK: Final = "2. შემოწმება"
IMP_STEP_DONE: Final = "3. შედეგი"
IMP_SELECT_INTRO: Final = (
    "აირჩიეთ კითხვებისა და განმარტებების TXT ფაილები (<კოდი>_questions.txt და "
    "<კოდი>_explanations.txt), საქაღალდე, სადაც ისინია, ან REFINED_MASTER_QBANK.txt. "
    "საქაღალდიდან იკითხება მხოლოდ მისი პირდაპირი შიგთავსი. პირველ ეტაპზე ბაზა არ იცვლება."
)
IMP_ADD_FILES: Final = "ფაილების არჩევა…"
IMP_ADD_FOLDER: Final = "საქაღალდის არჩევა…"
IMP_CLEAR: Final = "გასუფთავება"
IMP_FILE_FILTER: Final = "ტექსტური ფაილები (*.txt)"
IMP_COL_SUBJECT: Final = "თემა"
IMP_COL_FORMAT: Final = "ფორმატი"
IMP_COL_FILES: Final = "ფაილები"
IMP_COL_STATE: Final = "მდგომარეობა"
IMP_FORMAT_PAIRED: Final = "TXT წყვილი"
IMP_FORMAT_MASTER: Final = "რაფინირებული მასტერი"
IMP_STATE_READY: Final = "მზადაა შესამოწმებლად"
IMP_STATE_MISSING_EXPL: Final = "აკლია განმარტებების ფაილი"
IMP_STATE_MISSING_Q: Final = "აკლია კითხვების ფაილი"
IMP_STATE_UNKNOWN: Final = "უცნობი თემის კოდი"
IMP_NOTHING_FOUND: Final = "იმპორტისთვის შესაფერისი ფაილი ჯერ არ არის არჩეული."
IMP_SKIPPED_FILES: Final = "გამოტოვებული ფაილები: {count}"
IMP_VALIDATE: Final = "შემოწმება"
IMP_BACK: Final = "უკან"
IMP_CLOSE: Final = "დახურვა"
IMP_CONFIRM_BUTTON: Final = "იმპორტი ({count} კითხვა)"
IMP_WORKING_VALIDATE: Final = "მიმდინარეობს ფაილების წაკითხვა და შემოწმება…"
IMP_WORKING_IMPORT: Final = "მიმდინარეობს სარეზერვო ასლის შექმნა და იმპორტი…"
IMP_SUMMARY_TITLE: Final = "შემოწმების შედეგი"
IMP_SUM_PARSED: Final = "ნაპოვნი კითხვები"
IMP_SUM_VALID: Final = "სტრუქტურულად სწორი"
IMP_SUM_INVALID: Final = "შეცდომით (არ დაიმპორტდება)"
IMP_SUM_DUPLICATES: Final = "განმეორებული ID არჩეულ ფაილებში"
IMP_SUM_IDENTICAL: Final = "უკვე ბაზაშია (იდენტური)"
IMP_SUM_CONFLICTS: Final = "კონფლიქტი ბაზასთან (არ შეიცვლება)"
IMP_SUM_ELIGIBLE: Final = "დაემატება ბაზაში"
IMP_SUM_ERRORS: Final = "შეცდომები"
IMP_SUM_WARNINGS: Final = "გაფრთხილებები"
IMP_ISSUES_TITLE: Final = "შეცდომები და გაფრთხილებები"
IMP_ISSUES_NONE: Final = "შეცდომები და გაფრთხილებები არ არის."
IMP_COL_SEVERITY: Final = "ტიპი"
IMP_COL_ID: Final = "ID"
IMP_COL_LOCATION: Final = "ადგილი"
IMP_COL_MESSAGE: Final = "აღწერა"
IMP_SEV_ERROR: Final = "შეცდომა"
IMP_SEV_WARNING: Final = "გაფრთხილება"
IMP_NOTHING_ELIGIBLE: Final = "დასამატებელი ახალი კითხვა არ არის."
IMP_CONFIRM_TITLE: Final = "იმპორტის დადასტურება"
IMP_CONFIRM_TEXT: Final = (
    "ბაზაში დაემატება {count} ახალი კითხვა სტატუსით „განხილვის მოლოდინში“.\n\n"
    "იმპორტამდე შეიქმნება და შემოწმდება მონაცემთა ბაზის სარეზერვო ასლი. "
    "არსებული კითხვები და რეცენზენტის ცვლილებები არ შეიცვლება.\n\nგავაგრძელოთ?"
)
IMP_RESULT_TITLE: Final = "იმპორტი დასრულდა"
IMP_RES_CREATED: Final = "დაემატა ახალი კითხვა"
IMP_RES_IDENTICAL: Final = "გამოტოვდა — უკვე ბაზაშია"
IMP_RES_CONFLICTS: Final = "გამოტოვდა — კონფლიქტი (არსებული უცვლელია)"
IMP_RES_RACE: Final = "გამოტოვდა — ბაზაში შემოწმების შემდეგ გაჩნდა"
IMP_RES_INVALID: Final = "არ დაიმპორტდა — შეცდომა"
IMP_RES_BATCH: Final = "იმპორტის ნომერი"
IMP_RES_BACKUP: Final = "სარეზერვო ასლი"
IMP_RES_DURATION: Final = "ხანგრძლივობა"
IMP_ERROR_TITLE: Final = "იმპორტი არ შესრულდა"
IMP_ERROR_TEXT: Final = "იმპორტი შეწყდა და მონაცემთა ბაზა არ შეცვლილა."
IMP_ERROR_SOURCES_CHANGED: Final = (
    "შემოწმების შემდეგ ფაილი შეიცვალა. გაიმეორეთ შემოწმება და მხოლოდ შემდეგ დაადასტურეთ იმპორტი."
)


# Home: resume card and progress dashboard
HOME_RESUME_TITLE: Final = "გაგრძელება იქიდან, სადაც შეჩერდით"
HOME_RESUME_BODY: Final = "ბოლოს გახსნილი კითხვა: {uid}"
HOME_RESUME_BUTTON: Final = "გაგრძელება"
PROG_TITLE: Final = "განხილვის პროგრესი"
PROG_BANK: Final = "მთელი ბანკი"
PROG_SUBJECT_NONE: Final = "აირჩიეთ თემა — მისი სტატისტიკა აქ გამოჩნდება"
PROG_TOTAL: Final = "სულ"
PROG_SHARE: Final = "{percent} დამტკიცებულია"
PROG_STRIP_BANK: Final = "ბანკი"
PROG_STRIP_TOTAL: Final = "სულ {count}"
# Short status names for the compact strip (the full labels appear everywhere else).
PROG_SHORT_PENDING: Final = "მოლოდინში"
PROG_SHORT_IN_REVIEW: Final = "პროცესში"
PROG_SHORT_APPROVED: Final = "დამტკიცებული"
PROG_SHORT_REJECTED: Final = "უარყოფილი"

# Catalog
CAT_SEARCH_PLACEHOLDER: Final = "ძიება კითხვაში, პასუხებში, განმარტებაში ან ID-ში…"
CAT_SEARCH_TOOLTIP: Final = (
    "ძიება მუშაობს სიტყვის ნაწილზეც (მაგ.: „კაროტ“). რამდენიმე სიტყვის შემთხვევაში "
    "კითხვა ყველა მათგანს უნდა შეიცავდეს. გასასუფთავებლად დააჭირეთ Esc-ს."
)
CAT_GOTO_PLACEHOLDER: Final = "ID / ნომერი: ALI-0042"
CAT_GOTO_TOOLTIP: Final = (
    "შეიყვანეთ კითხვის სრული ID (მაგ.: ALI-0042) ან მხოლოდ ნომერი არჩეულ თემაში (მაგ.: 42)."
)
CAT_GOTO_BUTTON: Final = "გადასვლა"
CAT_SUBJECT_LABEL: Final = "თემა"
CAT_STATUS_LABEL: Final = "სტატუსი"
CAT_SORT_LABEL: Final = "დალაგება"
CAT_STATUS_ALL: Final = "ყველა სტატუსი"
CAT_SORT_NUMBER: Final = "ნომრის მიხედვით"
CAT_SORT_STATUS: Final = "სტატუსის მიხედვით"
CAT_SORT_MODIFIED: Final = "ბოლოს შეცვლილი — პირველი"
CAT_COUNT: Final = "{count} კითხვა"
CAT_COUNT_FOUND: Final = "ნაპოვნია {count} კითხვა"
CAT_MODE_FTS: Final = "სწრაფი ძიება (FTS5)"
CAT_MODE_LIKE: Final = "ძიება სრული გადასინჯვით"
CAT_SEARCHING: Final = "მიმდინარეობს ძიება…"
CAT_NO_RESULTS: Final = "არაფერი მოიძებნა"
CAT_NO_RESULTS_HINT: Final = "სცადეთ სიტყვის მოკლე ნაწილი ან შეცვალეთ ფილტრები."
CAT_EMPTY_BANK: Final = "ბაზაში კითხვები ჯერ არ არის. დაიწყეთ იმპორტით."
CAT_PREVIOUS: Final = "← წინა"
CAT_NEXT: Final = "შემდეგი →"
CAT_POSITION: Final = "{position} / {total}"
CAT_POSITION_OUTSIDE: Final = "სიის გარეთ · {total}"
CAT_SELECT_PROMPT: Final = "აირჩიეთ კითხვა სიიდან ან გადადით ID-ით"
CAT_SEARCH_ERROR: Final = "ძიება ვერ შესრულდა: {detail}"
PREVIEW_FIELD_STEM: Final = "კითხვაში"
PREVIEW_FIELD_OPTION: Final = "პასუხში {letter}"
PREVIEW_FIELD_EXPLANATION: Final = "განმარტებაში"
PREVIEW_FIELD_ID: Final = "ID-ში"

# Catalog notices
NOTICE_JUMP_FILTER_CLEARED: Final = "{uid} მიმდინარე ფილტრში არ შედიოდა — ფილტრები და ძიება გაუქმდა."
NOTICE_SUBJECT_RESET: Final = "შენახულ თემაში კითხვები აღარ არის — ნაჩვენებია ყველა თემა."
NOTICE_QUESTION_FILTERED: Final = (
    "ბოლოს გახსნილი კითხვა {uid} მიმდინარე ფილტრში აღარ შედის — ნაჩვენებია უახლოესი კითხვა."
)
NOTICE_FILTER_RESET: Final = "მიმდინარე ფილტრს ბოლო კითხვა არ შეესაბამებოდა — სტატუსის ფილტრი გაუქმდა."
NOTICE_QUESTION_MISSING: Final = (
    "ბოლოს გახსნილი კითხვა {uid} ბაზაში ვერ მოიძებნა — ნაჩვენებია სიის პირველი კითხვა."
)
ERR_REF_EMPTY: Final = "შეიყვანეთ კითხვის ID (მაგ.: ALI-0042) ან ნომერი."
ERR_REF_NEEDS_SUBJECT: Final = (
    "მხოლოდ ნომრით გადასასვლელად ჯერ აირჩიეთ თემა; სხვა შემთხვევაში შეიყვანეთ სრული ID, მაგ.: ALI-0042."
)
ERR_REF_UNKNOWN_SUBJECT: Final = "თემის კოდი უცნობია. სწორი ფორმატია, მაგ.: ALI-0042."
ERR_REF_BAD_FORMAT: Final = "ID-ის ფორმატი არასწორია. მაგალითი: ALI-0042, ან მხოლოდ ნომერი: 42."
ERR_REF_NOT_FOUND: Final = "კითხვა {uid} ბაზაში ვერ მოიძებნა."

# Question detail (read-only)
DET_QUESTION: Final = "კითხვა"
DET_OPTIONS: Final = "პასუხის ვარიანტები"
DET_CORRECT_TAG: Final = "✓ სწორი პასუხი"
DET_EXPLANATION: Final = "განმარტება"
DET_SOURCE: Final = "წყარო და ისტორია"
DET_REVISION: Final = "რედაქცია {number}"
DET_ID: Final = "ID"
DET_SUBJECT: Final = "თემა"
DET_STATUS: Final = "სტატუსი"
DET_SOURCE_KIND: Final = "წყაროს ფორმატი"
DET_SOURCE_FILE: Final = "ფაილი და ხაზი"
DET_IMPORT_BATCH: Final = "იმპორტის ნომერი"
DET_IMPORTED_AT: Final = "დაიმპორტდა"
DET_MODIFIED_AT: Final = "ბოლო ცვლილება"
DET_APPROVED_AT: Final = "დამტკიცდა"
DET_REVISION_FIELD: Final = "მიმდინარე რედაქცია"
DET_KIND_PAIRED: Final = "TXT წყვილი (კითხვები + განმარტებები)"
DET_KIND_MASTER: Final = "რაფინირებული მასტერი"
DET_READ_ONLY: Final = "ნახვის რეჟიმი: კითხვის რედაქტირება და დამტკიცება შემდეგ ეტაპზე დაემატება."
DET_COPY: Final = "კითხვის კოპირება"
DET_COPIED: Final = "დაკოპირდა"
DET_COPY_CORRECT: Final = "სწორი პასუხი: {letter}"
DET_COPY_EXPLANATION: Final = "განმარტება:"

# Backups
BK_TITLE: Final = "სარეზერვო ასლები"
BK_INTRO: Final = (
    "სარეზერვო ასლი მონაცემთა ბაზის სრული და შემოწმებული ასლია. ის ავტომატურად იქმნება "
    "იმპორტამდე და ბაზის განახლებამდე; საჭიროების შემთხვევაში აქვე შეგიძლიათ ხელითაც შექმნათ."
)
BK_CREATE: Final = "ახალი სარეზერვო ასლის შექმნა"
BK_VERIFY_ALL: Final = "ყველას შემოწმება"
BK_OPEN_FOLDER: Final = "საქაღალდის გახსნა"
BK_COL_TIME: Final = "შექმნის დრო"
BK_COL_KIND: Final = "ტიპი"
BK_COL_QUESTIONS: Final = "კითხვები"
BK_COL_SIZE: Final = "ზომა"
BK_COL_STATUS: Final = "შემოწმება"
BK_COL_FILE: Final = "ფაილი"
BK_KIND_MANUAL: Final = "ხელით შექმნილი"
BK_KIND_IMPORT: Final = "ავტომატური — იმპორტამდე"
BK_KIND_MIGRATION: Final = "ავტომატური — განახლებამდე"
BK_KIND_OTHER: Final = "სხვა"
BK_STATUS_UNCHECKED: Final = "ჯერ არ შემოწმებულა"
BK_STATUS_CHECKING: Final = "მოწმდება…"
BK_STATUS_VERIFIED: Final = "✓ დადასტურებულია"
BK_STATUS_VERIFIED_NO_MANIFEST: Final = "✓ ბაზა გამართულია (მანიფესტის გარეშე)"
BK_STATUS_FAILED: Final = "✗ დაზიანებულია"
BK_PROBLEM_MISSING_FILE: Final = "ფაილი აღარ არსებობს"
BK_PROBLEM_SIZE_MISMATCH: Final = "ფაილის ზომა მანიფესტს არ ემთხვევა"
BK_PROBLEM_CHECKSUM_MISMATCH: Final = "SHA-256 არ ემთხვევა — ფაილი შეცვლილია"
BK_PROBLEM_UNREADABLE: Final = "ბაზის გახსნა ან მთლიანობის შემოწმება ვერ მოხერხდა"
BK_PROBLEM_COUNTS_MISMATCH: Final = "ჩანაწერების რაოდენობა მანიფესტს არ ემთხვევა"
BK_EMPTY: Final = "სარეზერვო ასლები ჯერ არ არის."
BK_SUMMARY: Final = "ასლების რაოდენობა: {count}"
BK_FOLDER: Final = "საქაღალდე: {path}"
BK_CREATING: Final = "მიმდინარეობს სარეზერვო ასლის შექმნა და შემოწმება…"
BK_VERIFYING: Final = "მიმდინარეობს ასლების შემოწმება: {done} / {total}"
BK_VERIFY_DONE: Final = "შემოწმება დასრულდა: {ok} დადასტურებულია, {bad} დაზიანებულია."
BK_CREATED: Final = "სარეზერვო ასლი შეიქმნა და დადასტურდა: {name}"
BK_CREATE_FAILED: Final = "სარეზერვო ასლი არ შეიქმნა. მონაცემთა ბაზა არ შეცვლილა. {detail}"
BK_RESTORE_NOTE: Final = (
    "ბაზის აღდგენა სარეზერვო ასლიდან ამ ეტაპზე არ არის ხელმისაწვდომი: ის მიმდინარე სამუშაოს "
    "ანაცვლებს და ცალკე, საფუძვლიანად გამოცდილ უსაფრთხო პროცედურას საჭიროებს. ასლები "
    "ინახება მონაცემთა საქაღალდეში და მიმდინარე ბაზას არასდროს გადაწერს."
)

# Reviewer profile
REV_TITLE: Final = "რეცენზენტის პროფილი"
REV_INTRO: Final = (
    "აირჩიეთ ან ჩაწერეთ თქვენი სახელი. პროგრამაში პაროლი, ანგარიში და უფლებები არ არსებობს: "
    "სახელი ინახება მხოლოდ ამ კომპიუტერზე და მომავალში რედაქტირებისა და დამტკიცების "
    "ჩანაწერებში გამოიყენება."
)
REV_CURRENT: Final = "მიმდინარე რეცენზენტი"
REV_NONE: Final = "ჯერ არ არის არჩეული"
REV_NAME_LABEL: Final = "რეცენზენტის სახელი"
REV_SAVE: Final = "შენახვა"
REV_SAVED: Final = "შენახულია: {name}"
REV_HISTORY_NOTE: Final = (
    "სახელის შეცვლა არ ცვლის უკვე არსებულ ჩანაწერებს: ისტორია უცვლელი რჩება და ახალი "
    "სახელი მხოლოდ შემდგომ ცვლილებებს მიეკუთვნება."
)
REV_ERR_EMPTY: Final = "სახელი ცარიელია."
REV_ERR_TOO_LONG: Final = "სახელი ძალიან გრძელია (მაქსიმუმ {max} სიმბოლო)."
REV_ERR_CHARACTERS: Final = "სახელი შეიცავს დაუშვებელ სიმბოლოებს."
REV_ERR_SAVE: Final = "სახელის შენახვა ვერ მოხერხდა: {detail}"
REV_NAME_PLACEHOLDER: Final = "აირჩიეთ სიიდან ან ჩაწერეთ სახელი"
