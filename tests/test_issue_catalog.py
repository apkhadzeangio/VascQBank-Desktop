"""Every validation message must render and be Georgian."""

from __future__ import annotations

import string

import pytest

from vascqbank_desktop.validation.issues import _CATALOG, IssueCode, make_issue


@pytest.mark.parametrize("code", list(IssueCode), ids=lambda c: c.value)
def test_every_message_template_renders(code: IssueCode) -> None:
    _severity, template = _CATALOG[code]
    names = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    reserved = {"code", "file"}  # make_issue's own keyword arguments
    assert not names & reserved, f"{code}: placeholder clashes with make_issue arguments"
    params = {n: "x" for n in names if n not in ("line", "qid")}
    issue = make_issue(code, file="f.txt", line=3, qid="ALI-0001", **params)
    assert "{" not in issue.message
    assert any("ა" <= ch <= "ჿ" for ch in issue.message)
