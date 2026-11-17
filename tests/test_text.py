from __future__ import annotations

import os
from pathlib import Path

import pytest

from vascqbank_desktop.importexport import text_io
from vascqbank_desktop.importexport.text_io import (
    TextDecodingError,
    read_utf8_text,
    write_utf8_text_atomic,
)
from vascqbank_desktop.validation.text import (
    contains_georgian,
    is_georgian_char,
    normalize_text,
)

SAMPLE = (
    "1. 67 წლის მამაკაცს აღენიშნება ქვედა კიდურის მწვავე იშემია. რომელია ტაქტიკა?\n"
    "ა) დინამიკური დაკვირვება\n"
    "*გ) დაუყოვნებელი რევასკულარიზაცია — „ტესტი“ ×10⁹/ლ, 0,9 mmHg\n"
)


def test_normalize_strips_bom_and_unifies_newlines() -> None:
    assert normalize_text("﻿ა\r\nბ\rგ\n") == "ა\nბ\nგ\n"


def test_normalize_applies_nfc_without_touching_georgian() -> None:
    assert normalize_text("é") == "é"
    assert normalize_text(SAMPLE) == SAMPLE


def test_georgian_detection() -> None:
    assert contains_georgian("EVAR და CTA")
    assert not contains_georgian("EVAR, CTA, 0.9")
    assert is_georgian_char("ა")
    assert is_georgian_char("Ა")  # Mtavruli capital
    assert not is_georgian_char("A")
    with pytest.raises(ValueError):
        is_georgian_char("აბ")


def test_read_accepts_bom_and_crlf(tmp_path: Path) -> None:
    path = tmp_path / "კითხვები.txt"
    path.write_bytes(b"\xef\xbb\xbf" + SAMPLE.replace("\n", "\r\n").encode("utf-8"))
    assert read_utf8_text(path) == SAMPLE


def test_read_reports_position_of_invalid_utf8(tmp_path: Path) -> None:
    path = tmp_path / "bad.txt"
    path.write_bytes("ა\nბგ".encode() + b"\xff" + "დ\n".encode())
    with pytest.raises(TextDecodingError) as info:
        read_utf8_text(path)
    assert info.value.line == 2
    assert info.value.column == 3
    assert "line 2" in str(info.value)


def test_atomic_write_round_trip_lf(tmp_path: Path) -> None:
    target = tmp_path / "ქვესაქაღალდე" / "out.txt"
    write_utf8_text_atomic(target, SAMPLE.replace("\n", "\r\n"))
    raw = target.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert b"\r\n" not in raw
    assert raw.decode("utf-8") == SAMPLE
    assert [p.name for p in target.parent.iterdir()] == ["out.txt"]  # no temp files left


def test_atomic_write_crlf_option(tmp_path: Path) -> None:
    target = tmp_path / "out.txt"
    write_utf8_text_atomic(target, SAMPLE, newline="\r\n")
    assert target.read_bytes() == SAMPLE.replace("\n", "\r\n").encode("utf-8")
    with pytest.raises(ValueError):
        write_utf8_text_atomic(target, SAMPLE, newline="\r")


def test_atomic_write_failure_keeps_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "out.txt"
    target.write_text("ორიგინალი", encoding="utf-8")

    def broken_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(text_io.os, "replace", broken_replace)
    with pytest.raises(OSError):
        write_utf8_text_atomic(target, "ახალი")
    assert target.read_text(encoding="utf-8") == "ორიგინალი"
    assert [p.name for p in tmp_path.iterdir()] == ["out.txt"]
