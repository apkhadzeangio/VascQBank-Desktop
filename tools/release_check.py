"""Release-readiness check: collects evidence and writes a PASS / FAIL / NOT TESTED / BLOCKED report.

    QT_QPA_PLATFORM=offscreen python tools/release_check.py \\
        [--soak soak-report.json] [--skip-tests] [--junit results.xml] [--output-dir docs]

Writes ``release-readiness.json`` and ``RELEASE_READINESS.md`` into ``--output-dir``.

Every row is backed by something this script ran or read on THIS machine:

* the test suite (run here with ``--junitxml``, or read from ``--junit``), grouped by area;
* ``ruff check``; version consistency; imports of the source tree (no networking modules);
* ``git ls-files`` (no databases, backups, exports, Word files, question sources or logs tracked);
* the bundled fonts, the Qt platform plugins present, the declared runtime dependencies;
* a CLI ``--self-check`` on a fresh temporary data folder;
* an optional soak-test report (``tools/soak_test.py``).

Platforms, file systems and programs that were not exercised are reported as NOT TESTED — never
inferred. A failing data-safety area makes the overall verdict BLOCKED. Synthetic data only.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import tomllib
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "vascqbank_desktop"

PASS, FAIL, NOT_TESTED, BLOCKED = "PASS", "FAIL", "NOT TESTED", "BLOCKED"

# area -> (test files, is it a data-safety area whose failure blocks the release?)
AREAS: dict[str, tuple[tuple[str, ...], bool]] = {
    "end-to-end acceptance (24 steps)": (("test_phase9_acceptance",), True),
    "SQLite integrity and constraints": (
        ("test_database", "test_migration_v3", "test_approved_contract", "test_workflow_domain"),
        True,
    ),
    "draft / edit / review data safety": (
        ("test_edit_service", "test_review_service", "test_autosaver", "test_crash_recovery"),
        True,
    ),
    "crash and interruption recovery": (
        (
            "test_phase9_crash",
            "test_review_crash_recovery",
            "test_restore_crash",
            "test_gui_crash_recovery",
            "test_gui_review_crash",
            "test_seed_install",
        ),
        True,
    ),
    "export publication and recovery": (("test_export_publication", "test_export_service"), True),
    "backup and restore": (
        (
            "test_backup",
            "test_backup_service",
            "test_restore_service",
            "test_restore_gui",
            "test_phase9_restore_matrix",
        ),
        True,
    ),
    "backup portability and automatic backups": (("test_backup_portability",), True),
    "untrusted journals / manifests (security)": (("test_phase9_security",), True),
    "fuzzing of parsers and readers (Hypothesis)": (("test_fuzz_inputs",), True),
    "import parsing and validation": (
        ("test_parse_paired", "test_parse_master", "test_import_service", "test_issue_catalog"),
        True,
    ),
    "Word/TXT export accuracy": (
        (
            "test_export_roundtrip",
            "test_txt_export",
            "test_docx_export",
            "test_phase9_document_audit",
        ),
        True,
    ),
    "empty installation / first launch": (("test_first_launch", "test_app_startup", "test_paths"), False),
    "seed verification": (("test_seed_builder", "test_seed_compare"), False),
    "partial bank workflows (GUI)": (
        (
            "test_main_window_workflow",
            "test_main_window_editing",
            "test_main_window_phase3",
            "test_reviewer_ux",
        ),
        False,
    ),
    "synthetic 10,000-question bank (perf)": (
        (
            "test_performance",
            "test_phase8_performance",
            "test_browse_performance",
            "test_editing_performance",
            "test_review_performance",
            "test_export_performance",
            "test_seed_performance",
        ),
        False,
    ),
    "Georgian localization and architecture rules": (("test_architecture", "test_text"), False),
    "session persistence and settings": (("test_session_service", "test_settings_service"), False),
}

NETWORK_MODULES = {
    "socket",
    "ssl",
    "http",
    "urllib",
    "ftplib",
    "smtplib",
    "poplib",
    "imaplib",
    "xmlrpc",
    "requests",
    "httpx",
    "aiohttp",
    "websocket",
    "PySide6.QtNetwork",
    "PySide6.QtWebEngineCore",
}
FORBIDDEN_TRACKED = (
    ".sqlite",
    ".sqlite3",
    ".db",
    ".db3",
    ".docx",
    ".doc",
    ".log",
    "-wal",
    "-shm",
    "-journal",
)


def row(area: str, status: str, evidence: str, *, safety: bool = False) -> dict:
    return {"area": area, "status": status, "evidence": evidence, "data_safety": safety}


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:  # noqa: ANN003
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, **kwargs)  # noqa: S603


# ------------------------------------------------------------------------------- the checks
def junit_results(path: Path) -> dict[str, dict[str, int]]:
    per_file: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "failed": 0, "skipped": 0})
    for case in ET.parse(path).getroot().iter("testcase"):
        module = case.get("classname", "").split(".")[-1] or case.get("file", "")
        module = module.removeprefix("tests.")
        if any(child.tag in ("failure", "error") for child in case):
            per_file[module]["failed"] += 1
        elif any(child.tag == "skipped" for child in case):
            per_file[module]["skipped"] += 1
        else:
            per_file[module]["passed"] += 1
    return per_file


def test_rows(per_file: dict[str, dict[str, int]]) -> list[dict]:
    rows = []
    totals = {k: sum(v[k] for v in per_file.values()) for k in ("passed", "failed", "skipped")}
    rows.append(
        row(
            "full automated test suite",
            FAIL if totals["failed"] else (PASS if totals["passed"] else NOT_TESTED),
            f"{totals['passed']} passed, {totals['failed']} failed, {totals['skipped']} skipped",
            safety=True,
        )
    )
    for area, (files, safety) in AREAS.items():
        counts = {
            k: sum(per_file.get(f, {}).get(k, 0) for f in files) for k in ("passed", "failed", "skipped")
        }
        status = FAIL if counts["failed"] else (PASS if counts["passed"] else NOT_TESTED)
        evidence = (
            f"{counts['passed']} passed, {counts['failed']} failed, {counts['skipped']} skipped — "
            + ", ".join(f"{f}.py" for f in files)
        )
        rows.append(row(area, status, evidence, safety=safety))
    return rows


def lint_row() -> dict:
    ruff = shutil.which("ruff") or str(Path(sys.executable).with_name("ruff"))
    if not Path(ruff).exists() and shutil.which("ruff") is None:
        return row("lint (ruff check)", NOT_TESTED, "ruff is not installed")
    result = run([ruff, "check", "src", "tests", "tools"])
    last = (result.stdout.strip().splitlines() or ["(no output)"])[-1]
    return row("lint (ruff check)", PASS if result.returncode == 0 else FAIL, last)


def version_row() -> dict:
    declared = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    namespace: dict[str, object] = {}
    for line in (SRC / "__init__.py").read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            exec(line, namespace)  # noqa: S102 - one literal assignment from our own file
    code = namespace.get("__version__")
    ok = declared == code
    return row("version consistency", PASS if ok else FAIL, f"pyproject {declared}, package {code}")


def network_row() -> dict:
    found = []
    for path in SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for name in names:
                if name in NETWORK_MODULES or name.split(".")[0] in NETWORK_MODULES:
                    found.append(f"{path.relative_to(ROOT)}: {name}")
    return row(
        "no networking code (offline only)",
        FAIL if found else PASS,
        "; ".join(found) or "no networking module imported anywhere in src/",
        safety=True,
    )


def tracked_data_row() -> dict:
    result = run(["git", "ls-files"])
    if result.returncode != 0:
        return row("no question data or local data in git", NOT_TESTED, "git ls-files failed")
    files = result.stdout.splitlines()
    bad = [
        f
        for f in files
        if f.endswith(FORBIDDEN_TRACKED)
        or f.endswith(("_questions.txt", "_explanations.txt"))
        or "REFINED_MASTER" in f
        or f.split("/")[0] in ("backups", "exports", "logs", "data")
        or f.endswith("seed-manifest.json")
    ]
    return row(
        "no question data or local data in git",
        FAIL if bad else PASS,
        "; ".join(bad[:10]) or f"{len(files)} tracked files checked: no databases, backups, exports, logs",
        safety=True,
    )


def fonts_row() -> dict:
    fonts = sorted(p.name for p in (SRC / "resources" / "fonts").glob("*.ttf"))
    needed = {"NotoSansGeorgian-Regular.ttf", "NotoSansGeorgian-Bold.ttf"}
    licensed = (SRC / "resources" / "fonts" / "OFL.txt").is_file()
    ok = needed <= set(fonts) and licensed
    return row(
        "bundled Georgian fonts", PASS if ok else FAIL, ", ".join(fonts) + (" + OFL.txt" if licensed else "")
    )


def qt_rows() -> list[dict]:
    try:
        import PySide6
        from PySide6.QtCore import QLibraryInfo
    except ImportError as exc:
        return [row("Qt platform plugins", FAIL, f"PySide6 not importable: {exc}")]
    plugins = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.PluginsPath)) / "platforms"
    present = (
        sorted(p.stem.removeprefix("libq").removeprefix("q") for p in plugins.glob("*")) if plugins else []
    )
    return [
        row(
            "Qt platform plugins present",
            PASS if present else FAIL,
            f"PySide6 {PySide6.__version__}: " + ", ".join(present),
        )
    ]


def dependencies_row() -> dict:
    deps = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["dependencies"]
    found = []
    try:
        from importlib.metadata import version

        found = [
            f"PySide6-Essentials {version('PySide6-Essentials')}",
            f"python-docx {version('python-docx')}",
        ]
    except Exception as exc:  # noqa: BLE001
        return row("runtime dependencies installable", FAIL, str(exc))
    return row(
        "runtime dependencies installable",
        PASS,
        f"declared: {', '.join(deps)}; installed: {', '.join(found)}",
    )


def self_check_rows() -> list[dict]:
    rows = []
    with tempfile.TemporaryDirectory(prefix="vqb-release-") as tmp:
        data = Path(tmp) / "მონაცემები"
        env = {**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")}
        result = run(
            [sys.executable, "-m", "vascqbank_desktop", "--self-check", "--data-dir", str(data)],
            env=env,
            timeout=120,
        )
        ok = result.returncode == 0 and "integrity_ok:      True" in result.stdout
        rows.append(
            row(
                "CLI self-check on an empty data folder",
                PASS if ok else FAIL,
                f"exit {result.returncode}; " + " | ".join(result.stdout.strip().splitlines()[:1]),
            )
        )
        from vascqbank_desktop.config.paths import AppPaths

        database = AppPaths(data_dir=data).database_file
        if database.is_file():
            conn = sqlite3.connect(database)
            try:
                count = conn.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
            finally:
                conn.close()
            rows.append(
                row(
                    "empty installation needs no question bank",
                    PASS if count == 0 else FAIL,
                    f"{count} questions in the new database; no seed bundled in the source tree",
                )
            )
        else:
            rows.append(row("empty installation needs no question bank", FAIL, "no database was created"))
        again = run(
            [sys.executable, "-m", "vascqbank_desktop", "--self-check", "--data-dir", str(data)],
            env=env,
            timeout=120,
        )
        rows.append(
            row(
                "startup exit codes (second self-check, existing database)",
                PASS if again.returncode == 0 else FAIL,
                f"exit {again.returncode} (0 OK, 1 integrity, 2 storage, 3 recovery needed, 4 seed invalid, "
                "5 restore interrupted, 6 already running — 1–6 are covered by the test suite)",
            )
        )
    return rows


def soak_row(path: Path | None) -> dict:
    if path is None or not path.is_file():
        return row("GUI soak test", NOT_TESTED, "no soak report given (tools/soak_test.py)")
    report = json.loads(path.read_text(encoding="utf-8"))
    res = report.get("resources", {})
    actions = sum(a["count"] for a in report["actions"].values())
    evidence = (
        f"{report.get('duration_s', 0) / 60:.1f} min, {actions} actions, "
        f"{report['parameters']['questions']} synthetic questions; RSS growth after warm-up "
        f"{res.get('rss_growth_after_warmup_kb')} KB, "
        f"handles growth {res.get('open_handles_growth_after_warmup')}, "
        f"heartbeat max lateness {report['heartbeat_gap_ms']['max']} ms; failures {len(report['failures'])}"
    )
    return row("GUI soak test", PASS if report.get("passed") else FAIL, evidence)


def platform_rows() -> list[dict]:
    here = (
        f"{platform.system()} {platform.release()} ({platform.machine()}), Python {platform.python_version()}"
    )
    qpa = os.environ.get("QT_QPA_PLATFORM", "(default)")
    is_linux = platform.system() == "Linux"
    is_windows = platform.system() == "Windows"
    return [
        row("platform: this run", PASS, f"{here}, Qt platform {qpa}, SQLite {sqlite3.sqlite_version}"),
        row(
            "platform: Windows 10/11",
            PASS if is_windows else NOT_TESTED,
            "this run" if is_windows else "not run on Windows in this check",
        ),
        row(
            "platform: Linux X11 / Wayland desktop session",
            NOT_TESTED,
            "offscreen Qt only" if is_linux else "-",
        ),
        row("file systems: FAT32 / exFAT / network drives / USB media", NOT_TESTED, "not available here"),
        row(
            "Microsoft Word rendering", NOT_TESTED, "not available; DOCX checked with python-docx (+ pandoc)"
        ),
        row(
            "LibreOffice rendering",
            NOT_TESTED if shutil.which("soffice") is None else PASS,
            "not installed" if shutil.which("soffice") is None else "soffice present",
        ),
        row("power loss / OS crash", NOT_TESTED, "only process kills were tested; no power-failure claim"),
        row("installer / packaged build", NOT_TESTED, "Phase 10"),
    ]


# ------------------------------------------------------------------------------------ main
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--soak", type=Path)
    parser.add_argument("--junit", type=Path, help="read test results from this JUnit XML instead of running")
    parser.add_argument(
        "--skip-tests", action="store_true", help="do not run the test suite (rows: NOT TESTED)"
    )
    parser.add_argument("--output-dir", type=Path, default=ROOT / "docs")
    args = parser.parse_args(argv)

    rows: list[dict] = []
    if args.junit:
        rows += test_rows(junit_results(args.junit))
    elif not args.skip_tests:
        with tempfile.TemporaryDirectory() as tmp:
            junit = Path(tmp) / "junit.xml"
            env = {**os.environ, "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen")}
            run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", f"--junitxml={junit}"],
                env=env,
            )
            rows += test_rows(junit_results(junit))
    else:
        rows.append(row("full automated test suite", NOT_TESTED, "--skip-tests"))
    rows += [lint_row(), version_row(), network_row(), tracked_data_row(), fonts_row(), *qt_rows()]
    rows += [dependencies_row(), *self_check_rows(), soak_row(args.soak), *platform_rows()]

    failed_safety = [r["area"] for r in rows if r["status"] == FAIL and r["data_safety"]]
    failed_other = [r["area"] for r in rows if r["status"] == FAIL and not r["data_safety"]]
    suite_passed = any(r["area"] == "full automated test suite" and r["status"] == PASS for r in rows)
    verdict = BLOCKED if failed_safety else (FAIL if failed_other else (PASS if suite_passed else NOT_TESTED))
    from vascqbank_desktop import __version__

    report = {
        "application": "VascQBank Review Desktop",
        "version": __version__,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "git_commit_checked": run(["git", "rev-parse", "HEAD"]).stdout.strip() or None,
        "working_tree_dirty": bool(run(["git", "status", "--porcelain"]).stdout.strip()),
        "verdict": verdict,
        "verdict_rule": "BLOCKED if any data-safety row fails; FAIL if any other row fails; NOT TESTED if "
        "the full test suite did not run here; otherwise PASS. NOT TESTED rows are listed as limitations and "
        "never counted as passes",
        "blockers": failed_safety,
        "failures": failed_other,
        "rows": rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "release-readiness.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        f"# Release readiness — VascQBank Review Desktop {__version__}",
        "",
        "Generated by `tools/release_check.py` from evidence collected on the machine that ran it; "
        "see `release-readiness.json` for the same data. NOT TESTED means exactly that.",
        "",
        f"- **Verdict:** {verdict}",
        f"- Generated: {report['generated_at']}",
        f"- Commit checked: `{report['git_commit_checked']}`"
        + (" (with uncommitted changes)" if report["working_tree_dirty"] else ""),
        f"- Blockers: {', '.join(failed_safety) or 'none'}",
        "",
        "| Area | Status | Data safety | Evidence |",
        "|---|---|---|---|",
    ]
    for r in rows:
        evidence = r["evidence"].replace("|", "\\|")
        lines.append(f"| {r['area']} | {r['status']} | {'yes' if r['data_safety'] else ''} | {evidence} |")
    (args.output_dir / "RELEASE_READINESS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"verdict: {verdict}")
    for r in rows:
        print(f"{r['status']:<11} {r['area']}")
    return 0 if verdict == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
