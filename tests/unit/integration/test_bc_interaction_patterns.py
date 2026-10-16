"""Interaction patterns that need activities or solve-time execution, on bc
(construct test audit P4: docs/design/construct-test-audit/00-plan.md).

The op-model targets run one target function at a time and never solve, so
a pattern that needs `parallel`, `post_solve` or `pre_solve` is held here,
by the backend that runs activities. Each test names its pattern (IP-n, the
inventory's §8) and the LRM clause its expectation comes from; the
procedural half of a pattern, where there is one, is a matrix case.
"""
import pytest

from zuspec.be.bc.lower.errors import LoweringError

from .test_activity_bc_runs import trace


def test_ip49_a_poll_loop_yields_to_a_body_blocked_on_get():
    """IP-49 (20.8, Example 319): a `while` whose condition reads memory,
    with `yield` in its body, beside a parallel body blocked on `get()`. The
    reader waits until the poller puts the count it reached. (bc has no
    registers; the matrix case "a register polled in a while condition,
    yielding" holds the register half on the op-model targets.)"""
    assert trace("""
import std_pkg::*;
import addr_reg_pkg::*;
import sync_pkg::*;
component pss_top {
  transparent_addr_space_c<> sys_mem;
  addr_handle_t sts;
  channel_c<int, 1> ch;
  exec init_down {
    transparent_addr_region_s<> r;
    r.size = 0x1000;
    r.addr = 0x8000;
    sts = sys_mem.add_region(r);
  }
  action Poll { exec body {
    int n = 0;
    while (read32(comp.sts) == 0) {
      n += 1;
      message(NONE, "poll %d", n);
      if (n == 2) { write32(comp.sts, 1); }
      yield;
    }
    comp.ch.put(n);
  } }
  action Wait { exec body {
    message(NONE, "wait");
    int v = comp.ch.get();
    message(NONE, "got %d", v);
  } }
  action T { activity { parallel { do Wait; do Poll; } } }
}
""") == ["wait", "poll 1", "poll 2", "got 2"]


def test_ip86_a_mutable_field_accumulates_across_traversals():
    """IP-86 (9.1.6, Example 55): a `mutable` component field updated in
    `post_solve` is read by the same and later traversals."""
    assert trace("""
import std_pkg::*;
component pss_top {
  mutable int total;
  action A {
    rand int in [1..3] s;
    constraint s == 2;
    exec post_solve { comp.total = comp.total + s; }
    exec body { message(NONE, "body %d", comp.total); }
  }
  action T { activity { do A; do A; do A; } }
}
""") == ["body 2", "body 4", "body 6"]


def test_ip87_exec_blocks_of_one_kind_then_an_extensions():
    """IP-87 (20.1.1 d, 20.1.4.3): two `pre_solve` blocks run in source
    order, then the one an `extend action` contributes."""
    assert trace("""
import std_pkg::*;
component pss_top {
  action A {
    int x;
    exec pre_solve { x = 1; }
    exec pre_solve { x = x * 10 + 2; }
    exec body { message(NONE, "x %d", x); }
  }
  action T { activity { do A; } }
}
extend action pss_top::A { exec pre_solve { x = x * 10 + 3; } }
""") == ["x 123"]


def test_ip90_yield_in_a_function_two_parallel_bodies_call():
    """IP-90 (20.7.14, 20.8): a `yield` inside a loop of a target function
    interleaves the two parallel bodies that call it."""
    assert trace("""
import std_pkg::*;
component pss_top {
  target function void step(int who) {
    int i = 0;
    while (i < 2) { message(NONE, "%d.%d", who, i); yield; i += 1; }
  }
  action P1 { exec body { comp.step(1); } }
  action P2 { exec body { comp.step(2); } }
  action T { activity { parallel { do P1; do P2; } } }
}
""") == ["1.0", "2.0", "1.1", "2.1"]


_SOLVE_CHAIN = """
import std_pkg::*;
component pss_top {
  solve function int setup() { return 5; }
  function int mid() { return setup(); }
  action A {
    int v;
    exec post_solve { v = comp.mid(); }
    exec body { message(NONE, "v %d", v); }
  }
  action B { exec body { message(NONE, "b %d", comp.mid()); } }
  action T { activity { do A; } }
  action U { activity { do B; } }
}
"""


def test_ip97_a_solve_function_reached_from_post_solve():
    """IP-97 (20.2.1.3), the legal half: `post_solve` is solve context, so a
    solve function reached through an unqualified function may run."""
    assert trace(_SOLVE_CHAIN) == ["v 5"]


@pytest.mark.xfail(strict=True, reason=(
    "W43: bc does not check call context: a solve function reached from "
    "`exec body` through an unqualified function runs (20.2.1.3); the "
    "op-model gate refuses it (matrix: \"a solve function reached from a "
    "target function through another\")"))
def test_ip97_a_solve_function_reached_from_body_is_an_error():
    """IP-97, the error half: the same chain from `exec body` (target
    context)."""
    with pytest.raises(Exception, match="solve"):
        trace(_SOLVE_CHAIN, root="pss_top::U")


def test_ip60_compile_has_of_a_later_declaration_is_false():
    """IP-60 (19.3): `compile has` sees only what is declared before it."""
    assert trace("""
import std_pkg::*;
component pss_top {
  action T { exec body {
    compile if (compile has(later_t)) { message(NONE, "has"); }
    else { message(NONE, "not"); }
  } }
}
struct later_t { int x; }
""") == ["not"]


@pytest.mark.xfail(strict=True, reason=(
    "W44: bc lets `exec body` write a component field (20.7.3 b, 9.1.6; "
    "ruling R13 refuses it; the op-model gate does). "
    "test_bc_comp_array.py relies on it and moves with the fix"))
def test_ip85_a_body_writing_a_component_field_is_an_error():
    """IP-85 (20.7.3 b; R13): target code does not write component fields."""
    with pytest.raises(Exception, match="component field"):
        trace("""
import std_pkg::*;
component pss_top {
  int n;
  action T { exec body { comp.n = comp.n + 1; message(NONE, "%d", comp.n); } }
}
""")


# --- known-unsupported on bc: refused, not dropped ----------------------------

def test_ip98_executor_is_refused():
    """IP-98 (21.7.2.5): `executor()` has no lowering on bc yet."""
    with pytest.raises(LoweringError, match="executor"):
        trace("""
import std_pkg::*;
import executor_pkg::*;
component tap_c : executor_c<> { }
component pss_top {
  tap_c t;
  exec init_down { set_executor(t); }
  action A {
    exec pre_solve { if (executor() == null) { message(NONE, "pre null"); } }
    exec body { if (executor() != null) { message(NONE, "body set"); } }
  }
  action T { activity { do A; } }
}
""")


def test_ip51_write_struct_and_read_struct_are_refused():
    """IP-51 (21.13.1): `write_struct`/`read_struct` have no lowering on bc
    yet (nor on any op-model target)."""
    with pytest.raises(LoweringError, match="write_struct"):
        trace("""
import std_pkg::*;
import addr_reg_pkg::*;
enum e_e : bit[2] { E0, E1, E2 }
struct p_s : packed_s<> { bool b; e_e e; bit[5] v; }
component pss_top {
  transparent_addr_space_c<> sys_mem;
  addr_handle_t ram;
  exec init_down {
    transparent_addr_region_s<> r;
    r.size = 0x100;
    r.addr = 0x8000;
    ram = sys_mem.add_region(r);
  }
  action T { exec body {
    p_s p;
    p.b = true; p.e = E2; p.v = 3;
    write_struct(comp.ram, p);
    p_s q;
    read_struct(comp.ram, q);
    message(NONE, "%x %n %n %d", read8(comp.ram), q.b, q.e, q.v);
  } }
}
""")
