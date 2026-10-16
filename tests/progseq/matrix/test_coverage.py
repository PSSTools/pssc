"""The matrix's `covers` tags and coverage.yaml name real constructs.

Report mode (plan P0.3): a tag or disposition naming no construct of the
inventory fails -- a typo would otherwise count as coverage of nothing. The
gap table is printed, not enforced; it becomes enforcing at the end of P5.
"""
from __future__ import annotations

from . import _matrix
from . import coverage


def test_every_covers_tag_names_a_construct():
    ids = coverage.inventory()
    bad = [(mod, name, tag) for mod, name, case in coverage.cases()
           for tag in case.covers if tag not in ids]
    assert not bad, bad


def test_every_case_covers_something():
    bare = [(mod, name) for mod, name, case in coverage.cases()
            if not case.covers]
    assert not bare, bare


def test_every_disposition_names_a_construct_and_a_target():
    ids = coverage.inventory()
    bad = []
    for cid, d in coverage.dispositions().items():
        if cid not in ids:
            bad.append(cid)
        for tgt, entry in d.items():
            if tgt != "all" and tgt not in _matrix.ALL:
                bad.append((cid, tgt))
            if entry.get("status") not in ("elsewhere", "n-a"):
                bad.append((cid, tgt, entry))
    assert not bad, bad


def test_report(capsys):
    table = coverage.report()
    with capsys.disabled():
        print("\n" + table)
    assert table
