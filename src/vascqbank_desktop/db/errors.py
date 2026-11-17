"""Database exceptions."""

from __future__ import annotations


class DatabaseError(Exception):
    """Base class for database-layer errors."""


class ForeignDatabaseError(DatabaseError):
    """The file is a SQLite database that belongs to another application."""


class SchemaTooNewError(DatabaseError):
    """The database was written by a newer version of this application."""

    def __init__(self, found: int, supported: int) -> None:
        self.found = found
        self.supported = supported
        super().__init__(
            f"Database schema version {found} is newer than this application "
            f"supports ({supported}). Update the application instead of downgrading the database."
        )


class MigrationError(DatabaseError):
    """A migration failed; the database was rolled back to its previous version."""
