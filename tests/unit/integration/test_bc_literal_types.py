"""An integer literal has the type its spelling gives it (LRM 4.6.1, Table 21).

``16`` is a signed ``int``; ``0x10`` and ``0b10000`` are an unsigned
``bit[32]``; ``8'hFF`` is a ``bit[8]``; ``4'sb1010`` is an ``int[4]`` whose
value is -6. The type decides the comparisons and arithmetic the literal
takes part in (Table 22): a signed field compared with an unsigned literal is
an unsigned comparison, so ``x < 0x10`` is false for every negative ``x``.

ast2ir used to keep only a literal's value, and every consumer typed it as a
decimal. ast2ir now records the literal's type on its ``ExprConstant``, and
bc reads it in constraints and in procedural code alike.
"""
from typing import List

import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def runs(src: str, seeds=range(300)) -> List[List[int]]:
    model = _lower("import std_pkg::*;\ncomponent pss_top {\n" + src + "\n}\n")
    out = []
    for seed in seeds:
        lines: List[str] = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.append([int(ln) for ln in lines if ln.strip()])
    return out


def _u32(v):
    return v & 0xFFFFFFFF


@pytest.mark.parametrize("ty, cons, legal", [
    # Unsigned literal, signed field: an unsigned comparison.
    ("int[8]", "x < 0x10", lambda x: 0 <= x < 16),
    ("int[8]", "x < 0b10000", lambda x: 0 <= x < 16),
    # The decimal spelling of the same value compares signed.
    ("int[8]", "x < 16", lambda x: x < 16),
    # Octal is decimal's kind: signed.
    ("int[8]", "x < 020", lambda x: x < 16),
    # Interval folding of a disjunction must not read it by value either.
    ("int[8]", "x < 0x10 || x > 0x70", lambda x: _u32(x) < 0x10 or _u32(x) > 0x70),
    ("int[8]", "x in [0x10..0x20] || x == -5", lambda x: 16 <= x <= 32 or x == -5),
    # A negative constant against an unsigned field: -1 is all ones.
    ("bit[8]", "x > -1 || x == 3", lambda x: x == 3),
    # A sized literal is its size: an 8-bit unsigned comparison.
    ("int[8]", "x > 8'h80", lambda x: (x & 0xFF) > 0x80),
    ("int[8]", "x == 8'sh80", lambda x: x == -128),
])
def test_literal_type_in_a_constraint(ty, cons, legal):
    got = runs("""
    action T { rand %s x; constraint { %s; }
      exec body { message(NONE, "%%d", x); } }""" % (ty, cons))
    xs = [r[0] for r in got]
    bad = sorted({x for x in xs if not legal(x)})
    assert not bad, bad


def test_negative_values_are_reached_where_legal():
    # Calibration for the cases above: the decimal spelling does reach them.
    got = runs("""
    action T { rand int[8] x; constraint { x < 16; }
      exec body { message(NONE, "%d", x); } }""", seeds=range(100))
    assert any(r[0] < 0 for r in got)


def test_literal_type_in_procedural_code():
    got = runs("""
    action T {
      exec body {
        int[8] x = -1;
        message(NONE, "%d", (int)(x < 0x10));   // unsigned: 0xFFFFFFFF < 16
        message(NONE, "%d", (int)(x < 16));     // signed
        bit[16] y = 8'hFF + 8'h01;              // carried out at 16 bits
        message(NONE, "%d", y);
        message(NONE, "%d", 4'sb1010);          // int[4]: -6
        message(NONE, "%u", 4'b1010);           // bit[4]: 10
      }
    }""", seeds=range(1))
    assert got == [[0, 1, 0x100, -6, 10]]
