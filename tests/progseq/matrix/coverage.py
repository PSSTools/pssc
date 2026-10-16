"""Construct coverage accounting: which LRM constructs the matrix proves.

The constructs are those of
docs/design/construct-test-audit/01-lrm-inventory.md:
a coarse ID per heading (``DT-INT``), a sub-construct per table row
(``DT-INT.3``), and the interaction patterns of its §8 (``IP-6``). The
inventory is read directly, so it is the one list.

A matrix case names what it exercises in ``covers``. A construct is covered
on a target by a case that is not an xfail there: either executed with its
trace checked, or refused with its diagnostic checked. What no case can say
-- a construct proved elsewhere (bc, the corpus), or meaningless on a target
-- is a disposition in ``coverage.yaml`` beside the inventory.

``python -m progseq.matrix.coverage`` (from ``tests/``) prints the table.
"""
from __future__ import annotations

import importlib
import pkgutil
import re
from collections import defaultdict
from pathlib import Path

import yaml

from . import _matrix

AUDIT = (Path(__file__).resolve().parents[3] / "docs" / "design"
         / "construct-test-audit")
INVENTORY = AUDIT / "01-lrm-inventory.md"
DISPOSITIONS = AUDIT / "coverage.yaml"

_ROW = re.compile(r"^\| ((?:[A-Z]+-[A-Z]+)\.\d+[a-z]?|IP-\d+) \|")
_HEAD = re.compile(r"^### ([A-Z]+-[A-Z]+)\b")


def inventory():
    """{id: coarse id} for every construct and pattern in the inventory; a
    coarse ID maps to itself, an IP to ``IP``."""
    ids = {}
    for ln in INVENTORY.read_text().splitlines():
        m = _HEAD.match(ln)
        if m:
            ids[m.group(1)] = m.group(1)
            continue
        m = _ROW.match(ln)
        if m:
            i = m.group(1)
            ids[i] = "IP" if i.startswith("IP-") else i.split(".")[0]
    return ids


def matrix_modules():
    for info in pkgutil.iter_modules([str(Path(__file__).parent)]):
        if info.name.startswith("test_m_"):
            yield importlib.import_module(f"{__package__}.{info.name}")


def cases():
    """[(module name, case name, Case)] over every matrix module."""
    out = []
    for mod in matrix_modules():
        for name, case in mod.CASES.items():
            out.append((mod.__name__.rsplit(".", 1)[1], name, case))
    return out


def dispositions():
    if not DISPOSITIONS.exists():
        return {}
    return (yaml.safe_load(DISPOSITIONS.read_text()) or {}).get(
        "dispositions", {}) or {}


def covered():
    """{id: {target: [how, ...]}} -- ``how`` is a case name, or a
    disposition's status."""
    cov = defaultdict(lambda: defaultdict(list))
    for _mod, name, case in cases():
        for tag in case.covers:
            for tgt in _matrix.ALL:
                if tgt not in case.xfail:
                    cov[tag][tgt].append(name)
    for cid, d in dispositions().items():
        for tgt, entry in d.items():
            for t in (_matrix.ALL if tgt == "all" else (tgt,)):
                cov[cid][t].append(entry.get("status", "?"))
    return cov


def report():
    """The gap table: per coarse ID, how many of its sub-constructs each
    target covers, and whether the coarse ID itself is tagged."""
    ids = inventory()
    cov = covered()
    subs = defaultdict(list)
    for i, coarse in ids.items():
        if i != coarse:
            subs[coarse].append(i)
    lines = ["| ID | subs | " + " | ".join(_matrix.ALL) + " | coarse-tagged |",
             "|---|---|" + "---|" * len(_matrix.ALL) + "---|"]
    for coarse in sorted(subs):
        row = [coarse, str(len(subs[coarse]))]
        for t in _matrix.ALL:
            row.append(str(sum(1 for s in subs[coarse] if cov[s][t])))
        row.append("yes" if any(cov[coarse][t] for t in _matrix.ALL) else "-")
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines)


if __name__ == "__main__":
    print(report())
