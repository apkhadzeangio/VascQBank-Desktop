"""Strict UTF-8 text file I/O.

* Reading never guesses an encoding: invalid UTF-8 raises
  :class:`TextDecodingError` with the line and column of the first bad byte,
  so the user can fix the source file instead of importing mojibake.
* Writing is atomic: data goes to a temporary file in the same directory,
  is flushed to disk, then replaces the target in a single rename. A crash or
  power cut leaves either the old file or the new file, never half of one.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from vascqbank_desktop.validation.text import normalize_text


class TextDecodingError(ValueError):
    """A file is not valid UTF-8."""

    def __init__(self, path: Path, line: int, column: int, byte_offset: int) -> None:
        self.path = path
        self.line = line
        self.column = column
        self.byte_offset = byte_offset
        super().__init__(
            f"{path}: invalid UTF-8 at line {line}, column {column} (byte offset {byte_offset})"
        )


def read_utf8_text(path: str | os.PathLike[str]) -> str:
    """Read *path* as strict UTF-8 (BOM tolerated) and return normalised text."""
    file_path = Path(path)
    data = file_path.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        prefix = data[: exc.start]
        line = prefix.count(b"\n") + 1
        line_start = prefix.rfind(b"\n") + 1
        # Column counted in characters of the valid prefix on that line.
        column = len(prefix[line_start:].decode("utf-8", errors="replace")) + 1
        raise TextDecodingError(file_path, line, column, exc.start) from exc
    return normalize_text(text)


def write_utf8_text_atomic(
    path: str | os.PathLike[str],
    text: str,
    *,
    newline: str = "\n",
) -> Path:
    """Atomically write *text* as UTF-8 (no BOM) to *path*.

    ``newline`` controls the line terminator written to disk (``"\\n"`` or
    ``"\\r\\n"``); the input is normalised to LF first.
    """
    if newline not in ("\n", "\r\n"):
        raise ValueError("newline must be '\\n' or '\\r\\n'")
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = normalize_text(text)
    if newline != "\n":
        payload = payload.replace("\n", newline)

    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return target
