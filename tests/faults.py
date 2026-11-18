"""Deterministic fault injection at every write point (Phase 9b, campaign B).

:class:`FaultInjector` wraps, for the duration of a ``with`` block,

* the file-system calls the application writes through — ``open``/``io.open``/``os.open`` in a
  writing mode, ``write`` on such files, ``os.write``, ``os.fsync``, ``os.replace``, ``os.rename``,
  ``os.link``, ``os.unlink``/``os.remove``, ``os.mkdir`` and ``os.sendfile`` (``shutil``'s fast
  copy) — and
* every **writing** SQLite statement executed through ``sqlite3.connect(...)`` connections
  (``BEGIN``, ``COMMIT``, ``INSERT``, ``UPDATE``, ``DELETE``, ``REPLACE``, ``CREATE``, ``DROP``,
  setting ``PRAGMA``\\ s) and ``Connection.backup``.

In *recording* mode it only counts these points (in call order). In *failing* mode it raises a
chosen error at exactly the N-th point, *before* the real call runs, and lets every other call
through — so an operation can be failed once at each of its write points in turn.

File errors are real ``OSError``\\ s with an errno (``ENOSPC``, ``EACCES``, ``EIO``, ``EROFS``);
SQLite errors are ``sqlite3.OperationalError`` with SQLite's own messages and error codes
(``SQLITE_FULL``, ``SQLITE_IOERR``, ``SQLITE_BUSY``, ``SQLITE_READONLY``). ``PRAGMA max_page_count``
(see ``bank.limit_database_size``) is used separately for a *real* SQLITE_FULL.
"""

from __future__ import annotations

import builtins
import errno
import io
import os
import re
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field

_WRITE_MODE = re.compile(r"[wax+]")
_SQL_WRITE = re.compile(r"^\s*(BEGIN|COMMIT|END|INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER)\b", re.I)
_PRAGMA_SET = re.compile(r"^\s*PRAGMA\s+[\w.]+\s*=", re.I)


def file_error(code: int) -> OSError:
    return OSError(code, os.strerror(code))


def sqlite_error(kind: str) -> sqlite3.OperationalError:
    messages = {
        "SQLITE_FULL": ("database or disk is full", 13),
        "SQLITE_IOERR": ("disk I/O error", 10),
        "SQLITE_BUSY": ("database is locked", 5),
        "SQLITE_READONLY": ("attempt to write a readonly database", 8),
    }
    message, code = messages[kind]
    exc = sqlite3.OperationalError(message)
    exc.sqlite_errorcode = code
    exc.sqlite_errorname = kind
    return exc


FILE_FAULTS = {"ENOSPC": errno.ENOSPC, "EACCES": errno.EACCES, "EIO": errno.EIO, "EROFS": errno.EROFS}
SQL_FAULTS = ("SQLITE_FULL", "SQLITE_IOERR", "SQLITE_BUSY", "SQLITE_READONLY")


@dataclass
class Point:
    index: int
    kind: str  # "fs" or "sql"
    op: str  # open-w, write, fsync, replace, rename, link, unlink, mkdir, sendfile / BEGIN, INSERT, …
    detail: str


@dataclass
class FaultInjector:
    points: list[Point] = field(default_factory=list)
    fail_at: int | None = None
    error: Callable[[Point], BaseException] | None = None
    fired: Point | None = None
    _active: bool = False

    # -- the decision -----------------------------------------------------------------
    def hit(self, kind: str, op: str, detail: str) -> None:
        if not self._active:
            return
        point = Point(len(self.points), kind, op, detail)
        self.points.append(point)
        if self.fail_at is not None and point.index == self.fail_at and self.fired is None:
            self.fired = point
            assert self.error is not None
            raise self.error(point)

    # -- wrappers --------------------------------------------------------------------
    @contextmanager
    def installed(self):  # noqa: ANN201
        real = {
            "open": builtins.open,
            "io_open": io.open,
            "os_open": os.open,
            "write": os.write,
            "fsync": os.fsync,
            "replace": os.replace,
            "rename": os.rename,
            "link": getattr(os, "link", None),
            "unlink": os.unlink,
            "remove": os.remove,
            "mkdir": os.mkdir,
            "sendfile": getattr(os, "sendfile", None),
            "connect": sqlite3.connect,
        }
        injector = self

        class _File:
            """A writing file object whose writes are fault points; everything else passes through."""

            def __init__(self, inner, name: str) -> None:  # noqa: ANN001
                self._inner = inner
                self._name = name

            def write(self, data):  # noqa: ANN001, ANN202
                injector.hit("fs", "write", self._name)
                return self._inner.write(data)

            def writelines(self, lines):  # noqa: ANN001, ANN202
                injector.hit("fs", "write", self._name)
                return self._inner.writelines(lines)

            def __enter__(self):  # noqa: ANN204
                self._inner.__enter__()
                return self

            def __exit__(self, *exc):  # noqa: ANN002, ANN204
                return self._inner.__exit__(*exc)

            def __iter__(self):  # noqa: ANN204
                return iter(self._inner)

            def __getattr__(self, name: str):  # noqa: ANN204
                return getattr(self._inner, name)

        def wrapped_open(opener):  # noqa: ANN001, ANN202
            def _open(file, mode="r", *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
                if isinstance(file, int) or not _WRITE_MODE.search(mode):
                    return opener(file, mode, *args, **kwargs)
                injector.hit("fs", "open-w", os.fspath(file))
                return _File(opener(file, mode, *args, **kwargs), os.fspath(file))

            return _open

        def os_open(path, flags, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
            if flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC):
                injector.hit("fs", "open-w", os.fspath(path))
            return real["os_open"](path, flags, *args, **kwargs)

        def simple(name: str):  # noqa: ANN202
            target = real[name]

            def _call(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
                injector.hit("fs", name, " ".join(str(a) for a in args[:2]))
                return target(*args, **kwargs)

            return _call

        class FaultyConnection(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
                if _SQL_WRITE.match(sql) or _PRAGMA_SET.match(sql):
                    injector.hit("sql", sql.split()[0].upper(), " ".join(sql.split())[:80])
                return super().execute(sql, *args, **kwargs)

            def executemany(self, sql, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
                if _SQL_WRITE.match(sql):
                    injector.hit("sql", sql.split()[0].upper(), " ".join(sql.split())[:80])
                return super().executemany(sql, *args, **kwargs)

            def commit(self):  # noqa: ANN202
                injector.hit("sql", "COMMIT", "commit()")
                return super().commit()

            def backup(self, target, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
                injector.hit("sql", "BACKUP", "Connection.backup")
                return super().backup(target, *args, **kwargs)

        def connect(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            if args and args[0] == ":memory:":  # the reference schema (db/schema_check): not a write point
                return real["connect"](*args, **kwargs)
            kwargs.setdefault("factory", FaultyConnection)
            return real["connect"](*args, **kwargs)

        builtins.open = wrapped_open(real["open"])
        io.open = wrapped_open(real["io_open"])
        os.open = os_open
        for name in ("write", "fsync", "replace", "rename", "unlink", "remove", "mkdir"):
            setattr(os, name, simple(name))
        if real["link"] is not None:
            os.link = simple("link")
        if real["sendfile"] is not None:
            os.sendfile = simple("sendfile")
        sqlite3.connect = connect
        self._active = True
        try:
            yield self
        finally:
            self._active = False
            builtins.open = real["open"]
            io.open = real["io_open"]
            os.open = real["os_open"]
            for name in ("write", "fsync", "replace", "rename", "unlink", "remove", "mkdir"):
                setattr(os, name, real[name])
            if real["link"] is not None:
                os.link = real["link"]
            if real["sendfile"] is not None:
                os.sendfile = real["sendfile"]
            sqlite3.connect = real["connect"]


def record(operation: Callable[[], object]) -> list[Point]:
    """Run *operation* once without faults; return its write points in order."""
    injector = FaultInjector()
    with injector.installed():
        operation()
    return injector.points


def fail_once(operation: Callable[[], object], index: int, error: Callable[[Point], BaseException]):  # noqa: ANN201
    """Run *operation* failing exactly its *index*-th write point; return (exception or None, point)."""
    injector = FaultInjector(fail_at=index, error=error)
    raised: BaseException | None = None
    with injector.installed():
        try:
            operation()
        except BaseException as exc:  # noqa: BLE001 - the test inspects whatever came out
            raised = exc
    return raised, injector.fired


def error_for(point: Point, file_fault: str, sql_fault: str) -> BaseException:
    return file_error(FILE_FAULTS[file_fault]) if point.kind == "fs" else sqlite_error(sql_fault)
