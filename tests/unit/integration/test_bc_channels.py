"""Channels on bc (LRM 21.9.1; bc procedural gaps G3, B-D3/B-D4).

A ``channel_c<T, D>`` is ``2 + D`` slots of the component instance declaring
it: a count, a head and a ring of D elements. ``try_put``/``try_get`` never
block; ``put``/``get`` wait, for now by spinning on a SPIN yield, so a
deadlock is a run-time error rather than a hang, and a run that ends with its
entry blocked is never a success.
"""
import pytest

from zuspec.be.bc.interp.vm import VMError
from zuspec.be.bc.lower.errors import PssSemanticError

from .test_activity_bc_runs import trace

_SRC = """\
import std_pkg::*;
import sync_pkg::*;
component pss_top {
    channel_c<%s, %d> c;
    action P { exec body { %s } }
    action Q { exec body { %s } }
    action T { %s }
}
"""


def _run(body: str, elem: str = "int", depth: int = 2, p: str = "", q: str = ""):
    return trace(_SRC % (elem, depth, p, q, body))


def _body(stmts: str) -> str:
    return "exec body { %s }" % stmts


def test_fifo_order_and_capacity():
    assert _run(_body(
        'bool a = comp.c.try_put(1); bool b = comp.c.try_put(2); bool f = comp.c.try_put(3); '
        'int x; int y; int z = 9; bool g1 = comp.c.try_get(x); bool g2 = comp.c.try_get(y); '
        'bool g3 = comp.c.try_get(z); '
        'message(NONE, "%n %n %n %n %n %n %d %d %d", a, b, f, g1, g2, g3, x, y, z);')) \
        == ["true true false true true false 1 2 9"]


def test_the_ring_wraps():
    """Five rounds through a depth-2 ring: head and tail wrap twice."""
    assert _run(_body(
        'int v; repeat (i : 5) { comp.c.put(i); comp.c.try_put(i + 10); '
        'v = comp.c.get(); message(NONE, "%d", v); comp.c.try_get(v); '
        'message(NONE, "%d", v); }'), depth=2) \
        == [str(x) for i in range(5) for x in (i, i + 10)]


@pytest.mark.parametrize("elem,put,want", [
    ("bit[4]", "0x1f", "15"), ("int", "-3", "-3"), ("bit[64]", "-1", "18446744073709551615")])
def test_an_element_takes_the_element_type(elem, put, want):
    conv = "%d" if elem == "int" else "%u"
    assert _run(_body(f'comp.c.put({put}); message(NONE, "{conv}", comp.c.get());'),
                elem=elem) == [want]


def test_a_blocking_get_waits_for_the_producer():
    """Q gets before P puts; the get waits (spinning) until P has put."""
    got = _run("activity { parallel { do Q; do P; } }", depth=1,
               p='message(NONE, "put 1"); comp.c.put(1); '
                 'message(NONE, "put 2"); comp.c.put(2); message(NONE, "P done");',
               q='int a = comp.c.get(); message(NONE, "got %d", a); '
                 'int b = comp.c.get(); message(NONE, "got %d", b);')
    assert got.index("put 1") < got.index("got 1") < got.index("got 2")
    assert sorted(got) == sorted(["put 1", "put 2", "P done", "got 1", "got 2"])


def test_a_channel_per_instance():
    src = """\
import std_pkg::*;
import sync_pkg::*;
component box_c { channel_c<int, 1> c;
    function bool give(int v) { return c.try_put(v); } }
component pss_top {
    box_c a, b;
    action T { exec body { int x; bool r1 = comp.a.give(1); bool r2 = comp.b.give(2);
        bool r3 = comp.a.give(3); comp.b.c.try_get(x);
        message(NONE, "%n %n %n %d", r1, r2, r3, x); } }
}
"""
    assert trace(src) == ["true true false 2"]


def test_a_get_nothing_will_satisfy_is_a_deadlock():
    with pytest.raises(VMError, match="deadlock"):
        _run(_body("int v = comp.c.get();"))


def test_a_put_to_a_full_channel_nobody_reads_is_a_deadlock():
    with pytest.raises(VMError, match="deadlock"):
        _run("activity { parallel { do P; do Q; } }", depth=1,
             p="comp.c.put(1); comp.c.put(2);", q="int v = 0;")


def test_try_gets_argument_must_be_assignable():
    with pytest.raises(PssSemanticError, match="must be assignable"):
        _run(_body("comp.c.try_get(1);"))
