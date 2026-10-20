"""Long `if` / `else if` chains: one flat IR node, on every target.

pssparser keeps a chain's links in a list, and the IR used to nest them
again, an `ir.StmtIf` in each `else`: N links were N levels deep. Every
recursive walk after ast2ir then ran out of stack (the first op-model pass
at about 500 links), every target printed one indentation level per link
(Python refuses a module 100 levels deep), and the parser's own rule was
recursive too (quadratic, and a crash at about 10000 links).

A chain is now `ir.StmtIfChain`, printed `else if` / `elif`, and a register
offset function written as one is indexed by name as a `match` is. These
tests hold the addresses and the branch taken on every target, a hoisted
call in a later clause (it must run only if that clause is reached), the
`else` binding to the nearest `if`, and the scale.
"""
from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

import zuspec.ir.core as ir

from pssc.targets.progseq_model import _name_index

from . import trace_harness as th
from .test_large_offset_match import _all_targets, _top, _want


def _bank(n, *, last_by_else=False):
    """``n`` 32-bit registers 4 bytes apart, placed by one `else if` chain;
    with ``last_by_else`` the last is placed by the final `else`."""
    named = n - 1 if last_by_else else n
    fields = "\n".join(f"  reg_c<word_s> WORD_{i};" for i in range(n))
    # Written both ways round: the index takes `name == "x"` and `"x" == name`.
    links = [(f'name == "WORD_{i}"' if i % 2 else f'"WORD_{i}" == name')
             for i in range(named)]
    chain = "\n    else ".join(f"if ({t}) {{ return {i * 4}; }}"
                               for i, t in enumerate(links))
    last = (n - 1) * 4 if last_by_else else -1
    return f"""
struct word_s : packed_s<> {{ bit[32] value; }}
pure component bank_c : reg_group_c {{
{fields}
  function bit[64] get_offset_of_instance(string name) {{
    {chain}
    else {{ return {last}; }}
  }}
}}
"""


def _bank_run(n, **kw):
    mid = n // 2
    return _bank(n, **kw) + _top(n, f"""
    regs.WORD_0.write_val(1);
    regs.WORD_{n - 1}.write_val(2);
    bit[32] v = regs.WORD_{mid}.read_val();
    message(LOW, "v=%d", v);""")


# --- addresses, on every target ----------------------------------------------

def test_first_middle_and_last_link(tmp_path):
    traces = _all_targets(tmp_path, _bank_run(500),
                          mem={0x1000 + 250 * 4: 7})
    assert traces
    for t, trace in traces.items():
        assert trace == _want(500, 7), t


def test_the_final_else_places_a_register(tmp_path):
    traces = _all_targets(tmp_path, _bank_run(500, last_by_else=True))
    for t, trace in traces.items():
        assert trace == _want(500), t


# --- an operation body -------------------------------------------------------

def _pick(n):
    """`pick(x)` is 3*x for x < n through an n-link chain (link 7 written
    twice: the first wins), else -1."""
    links = [f"if (x == {i}) {{ y = {i * 3}; }}" for i in range(n)]
    links.insert(8, "if (x == 7) { y = 0; }")
    chain = "\n    else ".join(links)
    return f"""
component pss_top {{
  target function int pick(int x) {{
    int y = -1;
    {chain}
    return y;
  }}
  target function void run() {{
    message(LOW, "%d %d %d %d %d", pick(0), pick(7), pick({n // 2}),
            pick({n - 1}), pick({n}));
  }}
}}
"""


def test_a_long_chain_in_an_operation_body(tmp_path):
    """200 links: twice the indentation Python accepts, had each link been
    an `if` in an `else`."""
    traces = _all_targets(tmp_path, _pick(200), args=())
    assert "py" in traces
    for t, trace in traces.items():
        assert trace == ["0 21 300 597 -1"], t


_HOISTED = """
struct word_s : packed_s<> { bit[32] value; }
pure component bank_c : reg_group_c {
  reg_c<word_s> STATUS;
  function bit[64] get_offset_of_instance(string name) {
    if (name == "STATUS") { return 0x10; } else { return -1; }
  }
}
component pss_top {
  bank_c regs;
  solve function void initialize(addr_handle_t base) {
    regs.set_handle(base);
  }
  target function int pick(int x) {
    if (x == 0) { return 100; }
    else if (regs.STATUS.read_val() == 5) { return 200; }
    else if (x == 2) { return 300; }
    else { return 400; }
  }
  target function void run() {
    message(LOW, "a=%d", pick(0));
    message(LOW, "b=%d", pick(1));
  }
}
"""


def test_a_later_clause_reads_only_when_reached(tmp_path):
    """A target that hoists a call out of a condition (SV: a task) cannot
    put the second clause's read in front of the chain: `pick(0)` takes the
    first clause and reads nothing."""
    traces = _all_targets(tmp_path, _HOISTED, mem={0x1010: 5})
    assert "py" in traces
    for t, trace in traces.items():
        assert trace == ["a=100", "read 32 0x1010 0x5", "b=200"], t


def test_a_later_clause_awaits_only_when_reached(tmp_path):
    """Async Python hoists every awaited read to a statement (an `await` is
    never a subexpression): the second clause's read goes under `else:`,
    not in front of the chain."""
    import argparse
    import asyncio
    import importlib

    from pssc import driver

    src = tmp_path / "m.pss"
    src.write_text(th._PRELUDE + _HOISTED)
    out = tmp_path / "out"
    driver.compile([str(src)], target="op-model-py", opts=argparse.Namespace(
        progseq_root="pss_top", output_dir=str(out), py_await="async"))
    assert "elif" in (out / "pss_top.py").read_text()   # the third clause
    sys.path.insert(0, str(out))
    try:
        for name in ("pss_top", "pssc_rt", "pssc_rt_async"):
            sys.modules.pop(name, None)
        mod = importlib.import_module("pss_top")
        rt = importlib.import_module("pssc_rt_async")
    finally:
        sys.path.remove(str(out))
    bus = rt.AsyncMemoryBus()
    dut = mod.PssTop(bus, 0x1000)
    bus.mem[0x1010] = 0
    got = [asyncio.run(dut.pick(x)) for x in (0, 2, 1)]
    assert got == [100, 300, 400]
    assert [k for k, *_ in bus.log] == ["read", "read"]


_DANGLING = """
component pss_top {
  target function int f(int a, int b) {
    int r = 0;
    if (a == 1) if (b == 1) r = 1; else if (b == 2) r = 3; else r = 2;
    return r;
  }
  target function void run() {
    message(LOW, "%d %d %d %d", f(1, 1), f(1, 2), f(1, 9), f(0, 2));
  }
}
"""


def test_an_else_belongs_to_the_nearest_if(tmp_path):
    traces = _all_targets(tmp_path, _DANGLING, args=())
    assert "py" in traces
    for t, trace in traces.items():
        assert trace == ["1 3 2 0"], t


# --- the name index ----------------------------------------------------------

def _name():
    return ir.ExprRefLocal(name="name")


def _is_name(e):
    return isinstance(e, ir.ExprRefLocal) and e.name == "name"


def _eq(a, b):
    return ir.ExprBin(lhs=a, op=ir.BinOp.Eq, rhs=b)


def _or(a, b):
    return ir.ExprBin(lhs=a, op=ir.BinOp.Or, rhs=b)


def _s(v):
    return ir.ExprConstant(value=v)


def _chain(*tests):
    return ir.StmtIfChain(clauses=[ir.StmtIfClause(test=t, body=[])
                                   for t in tests])


def test_the_index_picks_the_first_clause_holding_a_name():
    st = _chain(_eq(_name(), _s("A")),
                _or(_eq(_s("B"), _name()), _eq(_name(), _s("A"))),
                _eq(_name(), _s("C")))
    assert _name_index(st, _is_name) == {"A": 0, "B": 1, "C": 2}


@pytest.mark.parametrize("test", [
    _eq(_name(), ir.ExprRefLocal(name="other")),        # not a literal
    _eq(ir.ExprRefLocal(name="other"), _s("A")),        # not the name
    ir.ExprBin(lhs=_name(), op=ir.BinOp.NotEq, rhs=_s("A")),
    ir.ExprBin(lhs=_eq(_name(), _s("A")), op=ir.BinOp.And,
               rhs=_eq(_name(), _s("B"))),
])
def test_a_chain_the_index_cannot_answer_is_walked(test):
    assert _name_index(_chain(_eq(_name(), _s("Z")), test), _is_name) is None


# --- scale --------------------------------------------------------------------

@pytest.mark.parametrize("target", ["c", "sv"])
def test_a_5000_link_bank_generates_at_the_default_recursion_limit(
        tmp_path, target):
    assert sys.getrecursionlimit() <= 1000 or pytest.skip(
        "the recursion limit was raised by something else in this process")
    out = th.compile_model(tmp_path, _bank_run(5000), target)
    text = "".join(p.read_text() for p in out.iterdir() if p.is_file())
    assert "WORD_4999" in text


def test_a_20000_link_chain_parses_and_translates(tmp_path):
    """In a child process: the parser's recursive rule crashed the process
    (a native stack overflow) at about 10000 links, which no assertion here
    would survive."""
    src = tmp_path / "m.pss"
    src.write_text(th._PRELUDE + _pick(20000))
    code = textwrap.dedent("""
        import sys
        import zuspec.ir.core as ir
        from pssc.driver import translate
        ctx = translate(sys.argv[1])
        (fn,) = [f for f in ctx.type_map["pss_top"].functions
                 if f.name == "pick"]
        (chain,) = [s for s in fn.body if isinstance(s, ir.StmtIfChain)]
        print(len(chain.clauses))
    """)
    r = subprocess.run([sys.executable, "-c", code, str(src)],
                       capture_output=True, text=True, timeout=600)
    assert r.returncode == 0, r.stderr[-2000:]
    assert r.stdout.split() == ["20001"]
