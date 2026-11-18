# Bundled seed database (package data)

This folder is where a **distribution build** places the verified seed:

* `seed-manifest.json`
* `vascqbank-seed.sqlite3`

Both are produced by `tools/build_seed.py` and copied here by `tools/prepare_package_seed.py`,
which refuses DEVELOPMENT seeds unless `--allow-development-seed` is given for a test build.

**Never commit a seed here.** The real question bank is private; `.gitignore` excludes these files.
In the public repository this folder holds only this note, so a source checkout starts with an empty
database and the manual TXT import, exactly as before Phase 7.
