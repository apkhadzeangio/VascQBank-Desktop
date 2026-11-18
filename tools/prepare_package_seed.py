"""Verify a built seed and copy it into the package data for a distribution build.

    python tools/prepare_package_seed.py SEED_DIR [--target src/vascqbank_desktop/resources/seed]
    python tools/prepare_package_seed.py SEED_DIR --allow-development-seed   # TEST builds only

A DEVELOPMENT seed is refused unless --allow-development-seed is given explicitly; such a build is
for testing and the application shows "სინთეზური სატესტო ბანკი" in its status bar. The seed is
verified with the same strict checks the application runs at first launch, and existing files in the
target are never overwritten. Do not commit the result: the target is excluded by .gitignore.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from vascqbank_desktop.services.seed_service import SeedInvalid, copy_seed, verify_seed

DEFAULT_TARGET = Path(__file__).resolve().parent.parent / "src" / "vascqbank_desktop" / "resources" / "seed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("seed", type=Path)
    parser.add_argument("--target", type=Path, default=DEFAULT_TARGET)
    parser.add_argument(
        "--allow-development-seed",
        action="store_true",
        help="TEST BUILDS ONLY: accept a DEVELOPMENT (synthetic or partial) seed",
    )
    args = parser.parse_args(argv)
    try:
        seed = verify_seed(args.seed, require_production=not args.allow_development_seed)
        copy_seed(seed, args.target)
    except (SeedInvalid, FileExistsError) as exc:
        print(f"SEED NOT PACKAGED: {exc}", file=sys.stderr)
        return 1
    m = seed.manifest
    print(f"packaged {m.kind.value} seed {m.seed_version} ({m.question_count} questions) into {args.target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
