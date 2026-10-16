"""The matrix's `covers` tags and coverage.yaml name real constructs.

A tag or disposition naming no construct of the inventory fails -- a typo
would otherwise count as coverage of nothing. Every sub-construct and every
interaction pattern is accounted for on every op-model target (plan P4, P5.3):
a matrix case, proved elsewhere, meaningless there, or open with its defect or
ruling. The gap table (what is PROVED, `open` excluded) is printed.
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
            st = entry.get("status")
            need = {"elsewhere": ("test",), "n-a": ("why",),
                    "open": ("defect", "ruling")}.get(st)
            if need is None or not any(entry.get(k) for k in need):
                bad.append((cid, tgt, entry))
    assert not bad, bad


def test_every_elsewhere_names_a_test_that_exists():
    """`test: path::function` must name a function in a file of the repo, so
    a renamed or deleted test cannot leave its coverage behind."""
    root = coverage.AUDIT.parents[2]
    bad = []
    for cid, d in coverage.dispositions().items():
        for tgt, entry in d.items():
            if entry.get("status") != "elsewhere":
                continue
            path, _, fn = str(entry.get("test", "")).partition("::")
            f = root / path
            if not fn or not f.is_file() or \
                    f"def {fn}(" not in f.read_text():
                bad.append((cid, tgt, entry.get("test")))
    assert not bad, bad


def test_every_interaction_pattern_is_mapped_on_every_target():
    """Plan P4's exit: each of the 100 interaction patterns is, on every
    op-model target, a matrix case (executed or refused), proved elsewhere,
    meaningless there, or recorded as open with its defect or ruling."""
    ids = [i for i in coverage.inventory() if i.startswith("IP-")]
    assert len(ids) == 100
    m = coverage.mapped()
    missing = [(i, sorted(set(_matrix.ALL) - m[i])) for i in ids
               if set(_matrix.ALL) - m[i]]
    assert not missing, missing


def test_every_sub_construct_is_mapped_on_every_target():
    """Plan P5.3's exit: each of the inventory's sub-constructs is, on every
    op-model target, a matrix case (executed or refused), proved elsewhere,
    meaningless there, or recorded as open with its defect or ruling. A
    coarse tag (`DT-INT`) maps none of its rows: a case names the rule it
    checks."""
    subs = [i for i, c in coverage.inventory().items()
            if c not in (i, "IP")]
    assert len(subs) == 596
    m = coverage.mapped()
    missing = [(i, sorted(set(_matrix.ALL) - m[i])) for i in subs
               if set(_matrix.ALL) - m[i]]
    assert not missing, f"{len(missing)} unmapped: {missing}"


def test_report(capsys):
    table = coverage.report()
    with capsys.disabled():
        print("\n" + table)
    assert table
