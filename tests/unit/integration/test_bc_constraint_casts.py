"""A cast in a constraint (LRM 7.12) is solved, sized as the LRM sizes it.

A cast is an assignment-like context (8.7.2): a cast wider than its operand
widens the operand's evaluation, and the value is then truncated or extended
to the cast's type and read at its signedness. bc lowers it to the solver's
cast, which follows the same rules; it used to refuse every cast.

Each case runs 200 seeds and checks every printed value against the LRM's
meaning of the constraint, and is chosen so that dropping the cast, or
sizing it wrongly, gives a different set of values.
"""
from typing import List

import pytest

from zuspec.be.bc.interp import NativeBlobBackend, run_model

from .test_activity_bc_runs import _lower

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

SEEDS = range(200)

_HDR = """\
import std_pkg::*;
enum op_e { OP_READ, OP_WRITE, OP_FLUSH }
component pss_top {
"""


def runs(src: str, seeds=SEEDS) -> List[List[int]]:
    model = _lower(_HDR + src + "\n}\n")
    out = []
    for seed in seeds:
        lines: List[str] = []
        run_model(model, seed=seed, solve_backend=NativeBlobBackend(),
                  out=lines.append)
        out.append([int(ln) for ln in lines if ln.strip()])
    return out


def test_widening_cast_widens_the_operand():
    # The high byte of an 8x8 product: the 16-bit cast makes `a * b` a 16-bit
    # product. Without it, `a * b` is computed at 8 bits and `>> 8` is 0.
    got = runs("""
    action T {
      rand bit[8] a; rand bit[8] b; rand bit[8] c;
      constraint { c == (bit[8])(((bit[16])(a * b)) >> 8); c > 10; }
      exec body { message(NONE, "%u", a); message(NONE, "%u", b); message(NONE, "%u", c); }
    }""")
    for a, b, c in got:
        assert c == (a * b) >> 8 and c > 10, (a, b, c)
    assert len({c for _, _, c in got}) > 10


def test_widening_cast_of_a_wide_product():
    # The comparison's context is 64 bits already (p), so this one holds
    # with or without the casts; it is the bench's spelling.
    got = runs("""
    action T {
      rand bit[32] x; rand bit[32] y; rand bit[64] p;
      constraint { (bit[64])x * (bit[64])y == p; p > 0x100000000; }
      exec body { message(NONE, "%u", x); message(NONE, "%u", y); message(NONE, "%u", p); }
    }""")
    for x, y, p in got:
        assert x * y == p > 0x100000000, (x, y, p)


def test_narrowing_cast_keeps_the_low_bits():
    got = runs("""
    action T {
      rand bit[16] x;
      constraint { (bit[4])x == 5; x > 100; }
      exec body { message(NONE, "%u", x); }
    }""")
    xs = [r[0] for r in got]
    assert all(x & 0xF == 5 and x > 100 for x in xs)
    assert len(set(xs)) > 100


def test_cast_to_signed_reinterprets():
    got = runs("""
    action T {
      rand bit[8] x;
      constraint { (int[8])x < 0; }
      exec body { message(NONE, "%u", x); }
    }""")
    xs = [r[0] for r in got]
    assert all(128 <= x <= 255 for x in xs)
    assert len(set(xs)) > 50


def test_cast_from_and_to_bool():
    got = runs("""
    action T {
      rand bit[8] x; rand bool b; rand bit[8] y;
      constraint { (bit[8])b + x == 7; (bool)y == b; }
      exec body { message(NONE, "%u", x); message(NONE, "%d", (int)b); message(NONE, "%u", y); }
    }""")
    for x, b, y in got:
        assert x + b == 7 and (y != 0) == bool(b), (x, b, y)
    assert {b for _, b, _ in got} == {0, 1}
    # Read as `y == b`, y would only ever be 0 or 1.
    assert any(y > 1 for _, _, y in got)


def test_cast_of_an_enum_reads_its_value():
    # The field's domain keeps it a member; the cast only reads the value.
    got = runs("""
    action T {
      rand op_e op;
      constraint { (bit[8])op != 1; }
      exec body { message(NONE, "%d", (int)op); }
    }""")
    assert {r[0] for r in got} == {0, 2}
