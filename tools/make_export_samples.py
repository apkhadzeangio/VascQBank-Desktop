"""Produce SAMPLE export files from a small SYNTHETIC bank (artificial text, no real questions).

    python tools/make_export_samples.py OUTPUT_DIR

Builds a throw-away bank in a temporary folder (two subjects with gaps in the numbering, edited,
approved, rejected and pending questions, Georgian multiline text and scientific notation),
runs the real exporter (TXT + review, exam and answer-key Word editions, per subject and
combined) and copies the verified export folder to OUTPUT_DIR.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from exportbank import build  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.importexport.docx_export import DocxEdition  # noqa: E402
from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService  # noqa: E402


def main() -> int:
    output = Path(sys.argv[1])
    with tempfile.TemporaryDirectory(prefix="vqb-samples-") as tmp:
        paths = AppPaths(data_dir=Path(tmp) / "data")
        build(paths)
        result = ExportService(paths).run(
            ExportRequest(
                subjects=("ALI", "VENOUS"),
                formats=frozenset({ExportFormat.DOCX, ExportFormat.TXT}),
                destination=Path(tmp) / "exports",
                editions=tuple(DocxEdition),
                per_subject_docx=False,
                combined_docx=True,
            )
        )
        output.mkdir(parents=True, exist_ok=True)
        for part in ("DOCX", "TXT"):  # replace only what this tool produces
            if (output / part).exists():
                shutil.rmtree(output / part)
        shutil.copytree(result.folder, output, dirs_exist_ok=True)
        print(f"{result.approved} questions, {len(result.files)} files -> {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
