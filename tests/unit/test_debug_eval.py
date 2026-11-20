"""T2.2 (eval): PSS expressions in a stopped frame, compiled to an
``exec_snippet`` program; register reads go through the back door and leave
the fake's bus log unchanged. Runs on the WB DMA of ``examples/op_model``,
stopped as in test_debug_variables.py."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.devenv.debug.breakpoints import BreakpointSet
from pssc.devenv.debug.eval import EvalError, Evaluator, Scope, compile_expr, parse
from pssc.devenv.debug.variables import Variables
from pssc.devenv.debug.watches import WatchSet, resolve
from pssc.testing import bc_session, sources
import pssc.testing.vm as pv

OP_MODEL = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "pss"
BASE = 0x10000000


@pytest.fixture(scope="module")
def model():
    files = [str(OP_MODEL / re.sub(r"^src/pss/", "", ln.strip()))
             for ln in (OP_MODEL / "files.f").read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    ctx = driver.translate([str(x) for x in sources.all_sources(files)])
    assert not ctx.errors
    return bc_session.lower(ctx, "wb_dma_c", functions=["*"], debug=True)


def _cfg(model, **vals):
    return [vals.get(n.split(".")[-1], 0)
            for n, _ in model.entries["wb_dma_ch_c::configure_channel"].params]


@pytest.fixture
def stopped(model):
    """A stub stopped on configure_channel's write of ch[1]'s csr
    (src=0x1234, prio=5), the stopped body, and the fake."""
    from zuspec.be.bc.interp.debug import Stub

    fake = pv.RegisterFake()
    s = pv.Session(model, platform=fake)
    s.call(s.construct(), "initialize", BASE)
    s.call(s.instance("ch[1]"), "probe_status")

    def run():
        s.call(s.instance("ch[1]"), "configure_channel",
               *_cfg(model, src=0x1234, prio=5))

    stub = Stub(s.vm, run=run)
    assert stub.request({"cmd": "attach", "args": {"version": 1}})["ok"]
    ws = WatchSet(model)
    ws.learn("ch[1]", fake.bases["ch[1]"])
    ws.set([{"path": "ch[1].regs.csr", "rw": "w"}])
    ws.apply(stub)
    assert stub.request({"cmd": "continue"})["ok"]
    ev = stub.next_event()
    assert ev["event"] == "stopped" and ev["body"]["reason"] == "watch", ev
    yield stub, ev["body"], fake
    stub.request({"cmd": "terminate"})


def _run(text, **vals):
    """Compile ``text`` over locals ``vals`` (name -> (value, width,
    signed)) and run it on pyvm's snippet runner."""
    from zuspec.be.bc.interp.debug import run_snippet
    from zuspec.be.bc.model import Instr, Op

    names = list(vals)
    scope = Scope({n: (i, (vals[n][1], vals[n][2])) for i, n in enumerate(names)})
    code, nregs, (w, signed) = compile_expr(text, scope)

    class F:
        locals = [vals[n][0] & ((1 << 64) - 1) for n in names]
        obj = base = cobj = comp = cbase = None
        args = ()

    ins = [Instr(Op[c["op"]], tuple(c.get("args", ())), c.get("imm", 0), 0) for c in code]
    v = run_snippet(F, ins, nregs, None, None)
    return v - (1 << 64) if signed and v >> 63 else v


@pytest.mark.parametrize("text,want", [
    ("1 + 2 * 3", 7), ("(1 + 2) * 3", 9), ("10 - 15", -5), ("0x10 | 0b1", 17),
    ("8'h2a", 42), ("1_000", 1000), ("7 / 2", 3), ("-7 / 2", -3), ("-7 % 2", -1),
    ("7 % -2", 1), ("-8 >> 1", -4), ("1 << 4", 16), ("3 < 5 && 5 <= 5", 1),
    ("!(2 == 2) || 0", 0), ("-1 < 0", 1), ("~0 == -1", 1), ("1 ? 10 : 20", 10),
    ("false ? 10 : 20", 20), ("true", 1), ("5 != 4", 1), ("6 ^ 3", 5),
])
def test_operators(text, want):
    assert _run(text) == want


def test_widths_and_signedness():
    assert _run("x", x=(0xFF, 8, True)) == -1
    assert _run("x + 1", x=(0xFF, 8, True)) == 0
    assert _run("x + 1", x=(0xFF, 8, False)) == 256
    assert _run("x < 0", x=(0x80, 8, True)) == 1
    assert _run("x < 0", x=(0x80, 8, False)) == 0
    assert _run("x > y", x=(5, 32, False), y=(3, 32, False)) == 1


@pytest.mark.parametrize("text,col", [("1 +", 4), ("a.", 3), ("(1", 3),
                                      ("1 $ 2", 3), ("a[b]", 3)])
def test_parse_errors(text, col):
    with pytest.raises(EvalError) as e:
        parse(text)
    assert e.value.col == col


def test_unknown_name():
    with pytest.raises(EvalError, match="unknown name 'nope'"):
        compile_expr("1 + nope", Scope())


def test_locals_and_component_fields(model, stopped):
    stub, body, fake = stopped
    ev = Evaluator.for_session(stub, model, fake)
    e = ev.at_stop(body)
    assert e("cfg.src") == 0x1234
    assert e("cfg.prio + 1") == 6
    assert e("cfg.prio == 5 && cfg.src > 0x1000") == 1
    assert e("chan") == 1                       # this = ch[1]
    assert e("caps.ars + caps.ed") == 2
    assert e("ch[0].chan") == 0                 # another instance: read_comp
    with pytest.raises(EvalError, match="unknown name"):
        e("cfg.nope")


def test_registers_through_the_back_door(model, stopped):
    stub, body, fake = stopped
    ev = Evaluator.for_session(stub, model, fake)
    v = Variables.for_session(stub, model, fake)
    rid, _, _ = resolve(model.registers, "ch[1].regs.sz")
    bus = list(fake.bus)
    e = ev.at_stop(body)
    assert e("regs.sz") == v.peek(rid)
    assert e("ch[1].regs.sz") == v.peek(rid)
    reg = model.registers[rid]
    f = reg["fields"][0]
    want = (v.peek(rid) >> f["lsb"]) & ((1 << f["width"]) - 1)
    assert e(f"regs.sz.{f['name']}") == want
    assert e("regs.adr0 == cfg.src") == 1       # written before the csr
    assert list(fake.bus) == bus                # no bus access


def test_conditional_breakpoint_uses_eval(model, stopped):
    stub, body, fake = stopped
    e = Evaluator.for_session(stub, model, fake).at_stop(body)
    bps = BreakpointSet(model)
    file = next(iter(bps.index.starts))
    line = min(bps.index.starts[file])
    a, b, c = bps.set_file(file, [
        {"line": line, "condition": "cfg.prio == 5"},
        {"line": line, "condition": "cfg.prio == 4"},
        {"line": line, "log_message": "prio={cfg.prio} src={cfg.src}"}])
    stop, logs = bps.on_stop([a.id, b.id, c.id], e)
    assert stop == [a.id] and logs == ["prio=5 src=4660"]
