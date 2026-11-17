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
SUBJECTS_SECTION: Final = "თემები"
SUBJECTS_HINT: Final = "თემის გახსნა და კითხვების დათვალიერება შემდეგ ეტაპზე დაემატება."
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
    "თემების მიხედვით დათვალიერება, ძიება და ფილტრაცია",
    "კითხვის, ხუთი პასუხისა და განმარტების რედაქტირება",
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

# Menus
MENU_FILE: Final = "&ფაილი"
ACTION_IMPORT: Final = "კითხვების იმპორტი…"
ACTION_OPEN_DATA_DIR: Final = "მონაცემთა საქაღალდის გახსნა"
ACTION_QUIT: Final = "გასვლა"
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
