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
STARTUP_ERROR_TEXT: Final = "მონაცემთა ბაზის გახსნა ან მომზადება ვერ მოხერხდა. მონაცემები არ შეცვლილა."

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
DET_READ_ONLY: Final = (
    "ნახვის რეჟიმი. შესასწორებლად დააჭირეთ „რედაქტირება“-ს (Ctrl+E); "
    "დამტკიცების ფუნქცია შემდეგ ეტაპზე დაემატება."
)
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


# Editing (Phase 4)
ACTION_EDIT: Final = "რედაქტირება"
ACTION_HISTORY: Final = "ცვლილებების ისტორია"
ACTION_DRAFTS: Final = "მონახაზები…"
EDIT_BUTTON: Final = "✎ რედაქტირება"
EDIT_BUTTON_TOOLTIP: Final = "კითხვის რედაქტირება (Ctrl+E). ცვლილებები ავტომატურად ინახება მონახაზად."
HISTORY_BUTTON: Final = "ისტორია"
HISTORY_BUTTON_TOOLTIP: Final = "ცვლილებების ისტორია (Ctrl+H) — ხელმისაწვდომია რედაქტირების გარეთ"
STATUS_DRAFTS: Final = "მონახაზები: {count}"
ED_NEED_REVIEWER: Final = "რედაქტირებამდე აირჩიეთ რეცენზენტი („რეცენზენტი“ გვერდზე)."
ED_NOT_AVAILABLE: Final = "რედაქტირება ამ ფანჯარაში ხელმისაწვდომი არ არის."

ED_SECTION_STEM: Final = "კითხვა"
ED_SECTION_OPTIONS: Final = "პასუხის ვარიანტები — აირჩიეთ სწორი პასუხი (Ctrl+1 … Ctrl+5)"
ED_SECTION_EXPLANATION: Final = "განმარტება"
ED_SECTION_NOTE: Final = "შენიშვნა ცვლილებაზე (არასავალდებულო)"
ED_NOTE_PLACEHOLDER: Final = "მაგ.: შესწორდა ტერმინოლოგია"
ED_SAVE: Final = "შენახვა (Ctrl+S)"
ED_CANCEL: Final = "გაუქმება (Esc)"
ED_REVERT: Final = "ტექსტის დაბრუნება"
ED_REVERT_TOOLTIP: Final = "ველებს დაუბრუნებს კითხვის შენახულ ვერსიას"
ED_FIELD_STEM: Final = "კითხვა"
ED_FIELD_OPTION: Final = "პასუხი {letter}"
ED_FIELD_EXPLANATION: Final = "განმარტება"
ED_FIELD_NOTE: Final = "შენიშვნა"

ED_STATE_CLEAN: Final = "ცვლილება არ არის"
ED_STATE_UNSAVED: Final = "მიმდინარეობს ცვლილება — მონახაზი მალე შეინახება…"
ED_STATE_SAVING_DRAFT: Final = "მონახაზი ინახება…"
ED_STATE_DRAFT_SAVED: Final = "მონახაზი შენახულია {time} · რედაქციად ჯერ არ არის შენახული"
ED_STATE_SAVING: Final = "ინახება…"
ED_STATE_SAVED: Final = "✓ შენახულია — რედაქცია {number}"
ED_STATE_UNCHANGED: Final = "ცვლილება არ არის — ახალი რედაქცია არ შექმნილა"
ED_STATE_ERROR: Final = "✗ ვერ შეინახა: {detail}"
ED_STATE_CONFLICT: Final = "✗ კითხვა სხვაგან შეიცვალა — აირჩიეთ, როგორ გააგრძელოთ"

ED_APPROVED_WARNING: Final = (
    "ეს კითხვა დამტკიცებულია. ახალი რედაქციის შენახვა გააუქმებს დამტკიცებას და სტატუსს "
    "შეცვლის „განხილვის პროცესში“-ზე. მონახაზის წერა სტატუსს არ ცვლის."
)
ED_CONFIRM_REVOKE_TITLE: Final = "დამტკიცების გაუქმება"
ED_CONFIRM_REVOKE_TEXT: Final = (
    "კითხვა დამტკიცებულია (რედაქცია {number}). ცვლილების შენახვა შექმნის ახალ რედაქციას, "
    "გააუქმებს დამტკიცებას და სტატუსს შეცვლის „განხილვის პროცესში“-ზე. გავაგრძელოთ?"
)
ED_DRAFT_RESTORED: Final = (
    "აღდგენილია დაუსრულებელი მონახაზი ({reviewer}, {time}). ის კითხვაში ჩაწერილი არ არის, "
    "სანამ რედაქციად არ შეინახავთ."
)
ED_DRAFT_OUTDATED: Final = (
    "ეს მონახაზი დაფუძნებულია რედაქცია {base}-ზე, კითხვის მიმდინარე რედაქცია კი {current}-ია. "
    "შენახვისას კონფლიქტი გამოჩნდება — არაფერი გადაიწერება ჩუმად."
)
ED_CONFLICT_TEXT: Final = (
    "კითხვა სხვა ადგილას შეიცვალა: მისი მიმდინარე რედაქცია ახლა {current}-ია. თქვენი ტექსტი "
    "არ დაკარგულა — ის მონახაზად ინახება."
)
ED_CONFLICT_MINE: Final = "ჩემი ტექსტის შენახვა ახალ რედაქციად"
ED_CONFLICT_MINE_TOOLTIP: Final = (
    "არსებული ახალი რედაქცია ისტორიაში დარჩება; თქვენი ტექსტი მის შემდეგ ახალ რედაქციად ჩაიწერება"
)
ED_CONFLICT_COMPARE: Final = "ახალ ვერსიასთან შედარება"
ED_CONFLICT_KEEP: Final = "მონახაზად დატოვება და ახალი ვერსიის ჩატვირთვა"
ED_VALIDATION_INTRO: Final = "შენახვამდე შეასწორეთ:"
ED_PROBLEM_EMPTY: Final = "{field} — ველი ცარიელია"
ED_PROBLEM_CONTROL: Final = "{field} — შეიცავს დაუშვებელ სიმბოლოებს"
ED_PROBLEM_TOO_LONG: Final = "{field} — ძალიან გრძელია (მაქსიმუმ {max} სიმბოლო)"
ED_PROBLEM_NOTE_TOO_LONG: Final = "შენიშვნა ძალიან გრძელია (მაქსიმუმ {max} სიმბოლო)"
ED_STORAGE_DISK_FULL: Final = "დისკზე ადგილი არ არის"
ED_STORAGE_LOCKED: Final = "მონაცემთა ბაზა სხვა პროგრამას ან ფანჯარას უკავია"
ED_STORAGE_READ_ONLY: Final = "მონაცემთა ბაზა მხოლოდ საკითხავია"
ED_STORAGE_IO: Final = "დისკის წაკითხვის/ჩაწერის შეცდომა"
ED_STORAGE_OTHER: Final = "ჩაწერა ვერ მოხერხდა"
ED_STORAGE_RETAINED: Final = " ტექსტი რედაქტორშია დარჩენილი და ჩაწერა ავტომატურად განმეორდება."
ED_TARGET_MISSING: Final = "კითხვა ბაზაში აღარ მოიძებნა."
ED_CANCEL_TITLE: Final = "რედაქტირების გაუქმება"
ED_CANCEL_TEXT: Final = "კითხვაში შეუნახავი ცვლილებებია. რა გავაკეთოთ?"
ED_CANCEL_KEEP: Final = "მონახაზის დატოვება და გასვლა"
ED_CANCEL_DISCARD: Final = "ცვლილებების გადაგდება"
ED_CANCEL_CONTINUE: Final = "რედაქტირების გაგრძელება"
ED_REVERT_TITLE: Final = "ტექსტის დაბრუნება"
ED_REVERT_TEXT: Final = (
    "ველებს დაუბრუნდება კითხვის შენახული ვერსია, ჩაწერილი ცვლილებები გაქრება. გავაგრძელოთ?"
)
ED_FLUSH_FAILED_TITLE: Final = "მონახაზი ვერ შეინახა"
ED_FLUSH_FAILED_TEXT: Final = (
    "თქვენი ბოლო ცვლილებები ვერ ჩაიწერა ({detail}). თუ გააგრძელებთ, ეს ტექსტი დაიკარგება. გავაგრძელოთ მაინც?"
)
ED_CLOSE_FAILED_TEXT: Final = (
    "თქვენი ბოლო ცვლილებები ვერ ჩაიწერა ({detail}). თუ პროგრამას ახლა დახურავთ, ეს ტექსტი "
    "დაიკარგება. დავხუროთ მაინც?"
)

# Drafts and recovery
DR_BAR_TEXT: Final = "ამ კითხვას აქვს დაუსრულებელი მონახაზი — {reviewer}, {time}."
DR_BAR_OUTDATED: Final = " დაფუძნებულია ძველ რედაქციაზე ({base} → {current})."
DR_RESTORE: Final = "აღდგენა რედაქტორში"
DR_COMPARE: Final = "შედარება"
DR_DISCARD: Final = "მონახაზის გაუქმება"
DR_DISCARD_TITLE: Final = "მონახაზის გაუქმება"
DR_DISCARD_TEXT: Final = (
    "მონახაზი ({uid}) წაიშლება. მისი ტექსტი მაინც შეინახება ბაზის დაცულ არქივში. გავაგრძელოთ?"
)
DR_BANNER: Final = "დაუსრულებელი მონახაზი: {count}"
DR_BANNER_BUTTON: Final = "მონახაზების ნახვა"
DR_LIST_TITLE: Final = "დაუსრულებელი მონახაზები"
DR_LIST_INTRO: Final = (
    "აქ ჩანს ყველა შეუნახავი მონახაზი, მათ შორის წინა სესიებისა. მონახაზი არასდროს იშლება "
    "თავისით — მხოლოდ თქვენი არჩევით."
)
DR_LIST_EMPTY: Final = "დაუსრულებელი მონახაზები არ არის."
DR_COL_QUESTION: Final = "კითხვა"
DR_COL_SUBJECT: Final = "თემა"
DR_COL_REVIEWER: Final = "რეცენზენტი"
DR_COL_SAVED: Final = "ბოლო ჩაწერა"
DR_COL_STATE: Final = "მდგომარეობა"
DR_STATE_CURRENT: Final = "მიმდინარე რედაქციაზე"
DR_STATE_OUTDATED: Final = "ძველ რედაქციაზე ({base} → {current})"
DR_OPEN: Final = "გახსნა რედაქტორში"
DR_CLOSE: Final = "დახურვა"

# History
HI_TITLE: Final = "ცვლილებების ისტორია"
HI_BACK: Final = "← კითხვაზე დაბრუნება"
HI_ROW: Final = "რედაქცია {number} · {author} · {time}"
HI_CURRENT: Final = "მიმდინარე"
HI_ORIGIN_IMPORT: Final = "იმპორტი"
HI_ORIGIN_EDIT: Final = "რედაქტირება"
HI_ORIGIN_RESTORE: Final = "აღდგენა"
HI_APPROVED_MARK: Final = "დამტკიცებული იყო"
HI_META_AUTHOR: Final = "რედაქტორი"
HI_META_TIME: Final = "დრო"
HI_META_NOTE: Final = "შენიშვნა"
HI_META_STATUS: Final = "სტატუსი"
HI_META_ORIGIN: Final = "სახეობა"
HI_NO_NOTE: Final = "—"
HI_STATUS_CHANGE: Final = "{before} → {after}"
HI_SHOW_DIFF: Final = "შედარება მიმდინარე ვერსიასთან"
HI_SAME_AS_CURRENT: Final = "ეს მიმდინარე რედაქციაა"
HI_NO_DIFFERENCES: Final = "ტექსტი მიმდინარე ვერსიას ემთხვევა"
HI_READ_ONLY: Final = (
    "ისტორია არასდროს იცვლება: ძველი რედაქციის დაბრუნება ქმნის ახალ რედაქციას, "
    "არსებულებს კი არ ცვლის და არ შლის."
)
HI_RESTORE: Final = "წინა რედაქციის დაბრუნება"
HI_RESTORE_TOOLTIP: Final = "ამ რედაქციის ტექსტი ახალ რედაქციად შეინახება (Ctrl+Shift+B)"
HI_RESTORE_IS_CURRENT: Final = "ეს მიმდინარე რედაქციაა — დასაბრუნებელი არაფერია"
HI_RESTORE_SAME_TEXT: Final = "ტექსტი მიმდინარე რედაქციის იდენტურია — ახალი რედაქცია არ შეიქმნება"
HI_ORIGIN_RESTORE_FROM: Final = "აღდგენა (რედაქცია {number}-დან)"
DIFF_LEGEND: Final = "წაშლილი ტექსტი · დამატებული ტექსტი"
DIFF_UNCHANGED_FIELDS: Final = "უცვლელი ველები: {fields}"
DIFF_OLD_TITLE: Final = "{title} (ძველი)"
DIFF_COMPARE_TITLE: Final = "ვერსიების შედარება"
DIFF_FIELD_CORRECT: Final = "სწორი პასუხი"
DR_MARKER: Final = "მონახაზი"


# Editorial workflow (Phase 5): approve, reject, reopen, restore
WF_APPROVE: Final = "დამტკიცება"
WF_APPROVE_NEXT: Final = "დამტკიცება და შემდეგი →"
WF_REJECT: Final = "უარყოფა"
WF_REOPEN: Final = "ხელახლა გახსნა"
WF_START: Final = "განხილვის დაწყება"
WF_APPROVE_TOOLTIP: Final = "ამტკიცებს ახლა ნაჩვენებ შენახულ რედაქციას (Ctrl+Shift+Enter)"
WF_APPROVE_NEXT_TOOLTIP: Final = (
    "ამტკიცებს ახლა ნაჩვენებ შენახულ რედაქციას და წარმატებული ჩაწერის შემდეგ გადადის "
    "შემდეგ კითხვაზე (Ctrl+Enter)"
)
WF_REJECT_TOOLTIP: Final = "კითხვა უარყოფილი გახდება; მიზეზი სავალდებულოა (Ctrl+Shift+R)"
WF_REOPEN_TOOLTIP: Final = "დამტკიცებულ ან უარყოფილ კითხვას აბრუნებს განხილვაში (Ctrl+Shift+O)"
WF_START_TOOLTIP: Final = "კითხვის სტატუსს „განხილვის პროცესში“ ხდის ტექსტის შეცვლის გარეშე"

WF_OK_APPROVED: Final = "✓ დამტკიცებულია — რედაქცია {number}"
WF_OK_ALREADY_APPROVED: Final = "ეს რედაქცია უკვე დამტკიცებულია — არაფერი შეცვლილა"
WF_OK_REJECTED: Final = "✓ კითხვა უარყოფილია"
WF_OK_REJECTED_REVOKED: Final = "✓ კითხვა უარყოფილია; რედაქცია {number}-ის დამტკიცება გაუქმდა"
WF_OK_REOPENED: Final = "✓ კითხვა ხელახლა გაიხსნა — სტატუსი: განხილვის პროცესში"
WF_OK_STARTED: Final = "✓ განხილვა დაიწყო"
WF_OK_ALREADY_STARTED: Final = "განხილვა უკვე დაწყებულია"
WF_OK_RESTORED: Final = "✓ რედაქცია {source}-ის ტექსტი დაბრუნდა — ახალი რედაქცია {number}"
WF_OK_RESTORED_REVOKED: Final = (
    "✓ რედაქცია {source}-ის ტექსტი დაბრუნდა — ახალი რედაქცია {number}; დამტკიცება გაუქმდა, "
    "კითხვა ისევ განხილვაშია"
)
WF_OK_RESTORE_SAME: Final = "ეს ტექსტი უკვე მიმდინარე რედაქციაა — ახალი რედაქცია არ შექმნილა"
WF_LAST_QUESTION: Final = "ეს სიის ბოლო კითხვა იყო — შემდეგი კითხვა აღარ არის"
WF_NEXT_NOT_POSSIBLE: Final = "შემდეგ კითხვაზე გადასვლა ვერ მოხერხდა — ტექსტი რედაქტორშია დარჩენილი"

WF_ERR_PREFIX: Final = "✗ "
WF_ERR_INVALID: Final = "ეს მოქმედება სტატუსზე „{status}“ შეუძლებელია"
WF_ERR_REASON: Final = "უარყოფისთვის მიზეზის მითითება სავალდებულოა"
WF_ERR_CONFLICT: Final = (
    "კითხვა სხვაგან შეიცვალა (ახლა რედაქცია {current}, სტატუსი „{status}“). ეკრანი განახლდა — "
    "გადახედეთ ახალ ტექსტს და გაიმეორეთ მოქმედება. არაფერი ჩაწერილა."
)
WF_ERR_VALIDATION: Final = "შენახული ტექსტი ვერ გაიარა შემოწმება, ამიტომ არ დამტკიცდა: {details}"
WF_ERR_MISSING: Final = "კითხვა ბაზაში აღარ მოიძებნა"
WF_ERR_NO_SOURCE: Final = "დასაბრუნებელი რედაქცია ამ კითხვას არ ეკუთვნის ან აღარ მოიძებნა"
WF_ERR_STORAGE: Final = "ჩაწერა ვერ მოხერხდა — {detail}. არაფერი შეცვლილა."

WF_DRAFT_TITLE: Final = "დაუსრულებელი მონახაზი"
WF_DRAFT_TEXT: Final = (
    "ამ კითხვას აქვს მონახაზი ({reviewer}, {time}), რომელიც შენახული რედაქცია არ არის. "
    "დამტკიცება მხოლოდ შენახულ ტექსტს ეხება, ამიტომ ჯერ გახსენით მონახაზი რედაქტორში და "
    "შეინახეთ ან გააუქმეთ — წინააღმდეგ შემთხვევაში შეიძლება არასწორი ტექსტი დამტკიცდეს."
)
WF_DRAFT_OPEN: Final = "მონახაზის გახსნა რედაქტორში"
WF_DRAFT_CANCEL: Final = "გაუქმება"

WF_SAVE_APPROVE_TITLE: Final = "შენახვა და დამტკიცება"
WF_SAVE_APPROVE_TEXT: Final = (
    "რედაქტორში შეუნახავი ცვლილებებია. ჯერ ისინი ახალ რედაქციად შეინახება, შემდეგ "
    "სწორედ ეს რედაქცია დამტკიცდება."
)
WF_SAVE_APPROVE_BUTTON: Final = "შენახვა და დამტკიცება"

WF_REJECT_TITLE: Final = "კითხვის უარყოფა"
WF_REJECT_INTRO: Final = "მიუთითეთ უარყოფის მიზეზი (სავალდებულოა). კითხვა და მისი ისტორია ბაზაში დარჩება."
WF_REJECT_APPROVED_WARNING: Final = "კითხვა დამტკიცებულია — უარყოფისას დამტკიცება გაუქმდება."
WF_REJECT_CONFIRM: Final = "უარყოფა"
WF_REASON_PLACEHOLDER: Final = "მიზეზი…"
WF_REASON_REQUIRED: Final = "მიზეზი სავალდებულოა"
WF_REOPEN_TITLE: Final = "კითხვის ხელახლა გახსნა"
WF_REOPEN_INTRO_APPROVED: Final = (
    "კითხვა დამტკიცებულია. ხელახლა გახსნისას დამტკიცება გაუქმდება და სტატუსი გახდება "
    "„განხილვის პროცესში“; ისტორია შენარჩუნდება. შენიშვნა (არასავალდებულო):"
)
WF_REOPEN_INTRO_REJECTED: Final = (
    "კითხვა უარყოფილია. ხელახლა გახსნისას სტატუსი გახდება „განხილვის პროცესში“. შენიშვნა (არასავალდებულო):"
)
WF_REOPEN_CONFIRM: Final = "ხელახლა გახსნა"
WF_CANCEL: Final = "გაუქმება"

WF_RESTORE_TITLE: Final = "წინა რედაქციის დაბრუნება"
WF_RESTORE_TEXT: Final = (
    "რედაქცია {source}-ის ტექსტი შეინახება როგორც ახალი რედაქცია (№{new}). არსებული რედაქციები "
    "არ შეიცვლება და არ წაიშლება."
)
WF_RESTORE_APPROVED_WARNING: Final = "კითხვა დამტკიცებულია — ტექსტის შეცვლისას დამტკიცება გაუქმდება."
WF_RESTORE_DRAFT: Final = "ამ კითხვას აქვს დაუსრულებელი მონახაზი ({reviewer}, {time}). არჩიეთ, რა ვუყოთ მას."
WF_RESTORE_KEEP_DRAFT: Final = "დაბრუნება, მონახაზის დატოვებით"
WF_RESTORE_DISCARD_DRAFT: Final = "მონახაზის გაუქმება და დაბრუნება"
WF_RESTORE_CONFIRM: Final = "დაბრუნება"
