"""SQLite access layer: connection policy and schema migrations.

No Qt imports are allowed here. Phase 1 creates only the schema-version
foundation; question tables are added as migration 2 in Phase 2.
"""
