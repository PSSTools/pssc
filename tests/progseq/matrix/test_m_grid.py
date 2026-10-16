"""The expression context grid (00-plan.md §4.3): every integer operator, in
every context, over every operand type pair, covered pairwise.

The rows and their expected values are generated (`grid`); the values come
from `ref8_7`, a model of LRM 8.7 that shares nothing with the targets. One
case per operator. See `_matrix` for what a case is and how it is run.
"""
from __future__ import annotations

from ._matrix import Case, define_tests
from . import grid

#: case name -> {target: defect} for a grid case a target gets wrong.
XFAIL = {}

CASES = {
    name: Case(pss, want, covers=covers, xfail=XFAIL.get(name, {}))
    for name, (pss, want, covers) in grid.cases().items()
}

test_construct = define_tests(CASES)
