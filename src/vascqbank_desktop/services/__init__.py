"""Application services: use cases that coordinate the lower layers.

The UI talks only to services and receives plain dataclasses; it never sees a
``sqlite3`` connection.
"""
