"""Build a seed database from a folder of paired-TXT subject files (developer tool).

    python tools/build_seed.py SOURCE_DIR OUTPUT_DIR --version 2027.1 --production
    python tools/build_seed.py SOURCE_DIR OUTPUT_DIR --version dev-1            # DEVELOPMENT seed

Uses the application's own import pipeline (no second parser). A PRODUCTION seed must pass every
acceptance gate: 12 paired-TXT subjects, exact target counts, 10,000 questions, no blocking error,
no duplicate ID, no conflict, a clean integrity and foreign-key check, no reviewer work. Anything
else can only be built as DEVELOPMENT, which the application always labels as test data.

OUTPUT_DIR must not exist (or be empty): a seed is an immutable artefact and is never overwritten.
The real bank is private — never commit its sources or the seed to a public repository.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vascqbank_desktop.services.seed_builder import SeedBuildFailed, build_seed
from vascqbank_desktop.services.seed_service import SeedKind


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--version", required=True, help="seed version, e.g. 2027.1 or dev-3")
    parser.add_argument("--production", action="store_true", help="enforce every production gate")
    args = parser.parse_args(argv)
    kind = SeedKind.PRODUCTION if args.production else SeedKind.DEVELOPMENT
    try:
        result = build_seed(args.source, args.output, kind=kind, seed_version=args.version)
    except SeedBuildFailed as exc:
        print(f"SEED NOT BUILT ({kind.value}):", file=sys.stderr)
        for problem in exc.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    m = result.seed.manifest
    print(f"{m.kind.value} seed {m.seed_version}: {m.question_count} questions, database {m.database_sha256}")
    print(f"  -> {result.directory}  ({', '.join(f'{k} {v:.2f}s' for k, v in result.timings.items())})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
