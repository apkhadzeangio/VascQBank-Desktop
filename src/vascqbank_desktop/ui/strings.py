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
SUBJECTS_HINT: Final = "თემები გააქტიურდება კითხვების იმპორტის შემდეგ."
SUBJECT_TOOLTIP: Final = (
    "{name}\n"
    "კოდი: {code}\n"
    "სამიზნე რაოდენობა: {target} კითხვა\n"
    "კითხვები ჯერ არ არის იმპორტირებული."
)
SUBJECTS_TOTAL: Final = "სულ: {count} თემა · {total} კითხვა"

# Welcome / empty state
WELCOME_HEADING: Final = APP_NAME_KA
WELCOME_TAGLINE: Final = APP_TAGLINE_KA
EMPTY_TITLE: Final = "კითხვები ჯერ არ არის იმპორტირებული"
EMPTY_BODY: Final = (
    "ადგილობრივი მონაცემთა ბაზა შექმნილია და მზადაა, მაგრამ ჯერ ცარიელია. "
    "კითხვების იმპორტი პროგრამის მომდევნო ეტაპზე დაემატება.\n\n"
    "პროგრამა ახალ კითხვებს არ ქმნის — ის მხოლოდ იმპორტირებულ კითხვებს "
    "ამუშავებს: რედაქტირება, რეცენზირება, დამტკიცება და ექსპორტი."
)
ROADMAP_TITLE: Final = "მომდევნო ეტაპებზე დაემატება"
ROADMAP_ITEMS: Final = (
    "კითხვებისა და განმარტებების იმპორტი TXT ფაილებიდან, ფორმატის შემოწმებით",
    "თემების მიხედვით დათვალიერება, ძიება და ფილტრაცია",
    "კითხვის, ხუთი პასუხისა და განმარტების რედაქტირება",
    "ცვლილებების ისტორია და წინა ვერსიის აღდგენა",
    "რეცენზირება და დამტკიცება",
    "დამტკიცებული კითხვების ექსპორტი Word (.docx) და TXT ფორმატში",
    "სარეზერვო ასლები და აღდგენა",
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
STATUS_VERSION: Final = "ვერსია {version}"

# Menus
MENU_FILE: Final = "&ფაილი"
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
