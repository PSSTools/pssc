"""P4.3d2: ``Session(engine="c")`` runs a test function on the C engine
(zuspec-rt-eng ``pssvm``) with the same Python mocks as the Python VM, and
gives the same ledger and the same host calls."""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")
pytest.importorskip("zuspec.rt.eng.host")

from zuspec.be.bc.interp.extern import ImportProvider, Memory

from pssc import driver
from pssc.testing import bc_session, native, sources

SUITE = """\
import pssunit_pkg::*;
import function int host_add(int x);
component t_f : fixture_c {
    rt_c rt;
    int cnt;
    @pssunit_pkg::test
    function void t_mock() {
        cnt = host_add(5);
        rt.check_eq_int(cnt, 1005, "mocked");
        rt.check_eq_int(host_add(cnt), 2005, "again");
    }
    @pssunit_pkg::test
    function void t_bad() {
        rt.check_eq_int(host_add(1), 7, "bad");
    }
    @pssunit_pkg::test
    function void t_branch() {
        if (host_add(1) > 1000) {
            cnt = 1;
        } else {
            cnt = 2;
        }
        rt.check_eq_int(cnt, 1, "then");
    }
    @pssunit_pkg::test
    function void t_refused() {
        cnt = host_add(-1);
    }
}
"""


class _Mock(ImportProvider):
    """Adds 1000; refuses a negative argument. Logs every call."""

    def __init__(self):
        self.log = []

    def call(self, fn_id, args):
        self.log.append((fn_id, tuple(args)))
        if (args[0] & 0xFFFFFFFF) >= 0x80000000:
            raise ValueError("mock refused")
        return args[0] + 1000


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    p = tmp_path_factory.mktemp("native") / "suite.pss"
    p.write_text(SUITE)
    ctx = driver.translate([str(s) for s in sources.all_sources([str(p)])])
    assert not ctx.errors
    return bc_session.lower(ctx, "t_f", functions=["*"])


def _both(model, symbol):
    out = []
    for engine in ("py", "c"):
        mock = _Mock()
        s = bc_session.Session(model, seed=1, memory=Memory(), imports=mock,
                               engine=engine)
        try:
            res = s.run_test(symbol)
        except Exception as e:      # noqa: BLE001 - compared by type
            res = type(e)
        out.append((res, mock.log))
    return out


@pytest.mark.parametrize("symbol, counts", [("t_f::t_mock", (2, 0, 0)),
                                            ("t_f::t_bad", (0, 1, 0))])
def test_a_test_gives_the_same_ledger_on_both_engines(model, symbol, counts):
    py, c = _both(model, symbol)
    assert py == c
    assert c[0] == counts and c[1]


def test_a_failing_mock_fails_the_test_on_both(model):
    py, c = _both(model, "t_f::t_refused")
    assert py[0] is ValueError and c[0] is ValueError
    assert py[1] == c[1]


def test_the_engine_comes_from_the_environment(model, monkeypatch):
    monkeypatch.setenv("PSSC_ENGINE", "c")
    assert bc_session.Session(model).engine == "c"
    monkeypatch.setenv("PSSC_ENGINE", "fast")
    with pytest.raises(ValueError, match="unknown engine"):
        bc_session.Session(model)


def test_a_binding_of_a_function_never_called_is_refused_on_c(model):
    """As on the Python VM (``VM.bind``): only a called function binds."""
    s = bc_session.Session(model, engine="c", imports=_Mock())
    with pytest.raises(KeyError, match="never called"):
        s.run_test("t_f::t_mock", bindings={"t_f::t_mock": lambda *a: None})


def test_the_solver_is_linked_only_when_the_image_solves(model):
    assert not native.needs_solver(model)
    assert native.engine_lib(False).endswith("libpssvm.so")


def _traced(model, engine, symbol="t_f::t_mock"):
    import io
    import json
    from pssc.testing.trace import TraceWriter
    buf = io.StringIO()
    w = TraceWriter(buf, model, watch=native.ENGINE_C_EVENTS)
    s = bc_session.Session(model, seed=1, memory=Memory(), imports=_Mock(),
                           engine=engine, events=w, watch=w.watch)
    s.run_test(symbol)
    rows = [json.loads(ln) for ln in buf.getvalue().splitlines()]
    return rows[0], rows[1:]


def test_a_traced_test_writes_the_same_events_on_both_engines(model):
    """A pssc-compiled test traced on each engine: the same events, the
    same source lines, the same stmt ``path``/``scope``."""
    def canon(rows):
        return [dict(d, seq=i) for i, d in enumerate(rows)]
    (hp, py), (hc, c) = _traced(model, "py"), _traced(model, "c")
    assert hp == hc
    assert [d["kind"] for d in c] == ["import", "import"]
    assert all(d.get("file") and d.get("line") for d in c)
    assert canon(c) == canon(py)


@pytest.fixture(scope="module")
def debug_model(tmp_path_factory):
    p = tmp_path_factory.mktemp("native_dbg") / "suite.pss"
    p.write_text(SUITE)
    ctx = driver.translate([str(s) for s in sources.all_sources([str(p)])])
    return bc_session.lower(ctx, "t_f", functions=["*"], debug=True)


def test_coverage_is_the_same_on_both_engines(debug_model):
    """P4.7: the C engine's stmt and branch events give the same coverage."""
    from pssc.testing.coverage import Coverage
    model = debug_model
    reports = []
    for engine in ("py", "c"):
        cov = Coverage(model)
        s = bc_session.Session(model, engine=engine, imports=_Mock(),
                               events=cov, watch=Coverage.WATCH)
        assert s.run_test("t_f::t_branch") == (1, 0, 0)
        reports.append(cov.report())
    py, c = reports
    assert c == py
    arms = {p["arm"]: p["hits"] for p in py["arms"].values()
            if p["scope"] == "t_f::t_branch"}
    assert arms == {"then": 1, "else": 0}
    assert any(p["hits"] for p in c["statements"].values())


CHECKS = """\
import std_pkg::*;
import addr_reg_pkg::*;
import pssunit_pkg::*;
import function int host_add(int x);
component t_v : fixture_c {
    int cnt;
    function void set_up() { cnt = 1; }
    @pssunit_pkg::test
    function void t_checks() {
        cnt = host_add(cnt);
        check_eq(cnt, 1001, "captured");
        check(cnt > 5000);
        write32(make_handle_from_handle(0, 0x100), cnt);
        check_eq(read32(make_handle_from_handle(0, 0x100)), 1001, "bus");
    }
    function int scale(int x) { return x * 2; }
    @pssunit_pkg::test
    function void t_mocked() {
        check_eq(scale(4), 40, "mocked");
        check_eq(scale(1), 10, "again");
    }
    @pssunit_pkg::test
    function void t_require() {
        require(cnt == 2, "stops");
        check(true);
    }
}
"""


class _Plat:
    """A plain platform: memory, and ``host_add`` adding 1000."""

    def __init__(self):
        from pssc.testing.vm import Platform
        self.p = Platform()

    def __getattr__(self, name):
        return getattr(self.p, name)

    def host_add(self, x):
        return x + 1000


@pytest.fixture(scope="module")
def checks_model(tmp_path_factory):
    p = tmp_path_factory.mktemp("native_vm") / "suite.pss"
    p.write_text(CHECKS)
    ctx = driver.translate([str(s) for s in sources.all_sources([str(p)])])
    assert not ctx.errors
    return bc_session.lower(ctx, "t_v", functions=["*"])


@pytest.fixture(scope="module")
def debug_checks_model(tmp_path_factory):
    """``CHECKS`` lowered for debug, as ``vm.load`` does: functions stay
    calls (not inlined), so a mock has a call to replace."""
    p = tmp_path_factory.mktemp("native_vm_dbg") / "suite.pss"
    p.write_text(CHECKS)
    ctx = driver.translate([str(s) for s in sources.all_sources([str(p)])])
    assert not ctx.errors
    return bc_session.lower(ctx, "t_v", functions=["*"], debug=True)


@pytest.mark.parametrize("fn, counts", [("t_checks", (2, 1, 0)),
                                        ("t_require", (0, 1, 0))])
def test_a_python_session_counts_the_same_on_both_engines(checks_model, fn,
                                                          counts):
    """P4.8a: vm.Session on C counts pssunit's rewritten checks, gives the
    same bus log, and runs set-up first (``run_entry``)."""
    from pssc.testing.vm import Session
    from zuspec.be.bc.trace import MemorySink, events as ev
    m = checks_model
    sym = f"t_v::{fn}"
    setup = [m.entries["t_v::set_up"].coro]
    out = []
    for engine in ("py", "c"):
        plat, sink = _Plat(), MemorySink()
        s = Session(m, platform=plat, seed=1, sanitize=False, engine=engine,
                    events=sink, watch=ev.EV_ACCESS)
        got = s.run_entry(m.entries[sym].coro, sym, setup=setup)
        out.append((got, s.stopped, plat.log,
                    [(e.kind, e.detail.get("addr")) for e in sink.events]))
    assert out[0] == out[1]
    assert out[1][0] == counts


def test_a_mock_replaces_a_function_on_both_engines(debug_checks_model):
    """P4.8c2: a session's mock runs in place of a PSS function on C as on
    the Python VM: same checks, same calls, and the trace's ``call`` and
    ``return`` say ``mocked``."""
    from pssc.testing.vm import Session
    from zuspec.be.bc.trace import MemorySink, events as ev
    out = []
    for engine in ("py", "c"):
        seen, sink = [], MemorySink()
        s = Session(debug_checks_model, platform=_Plat(), seed=1,
                    sanitize=False, engine=engine, events=sink,
                    watch=ev.EV_CALL)
        s.mocks["t_v::scale"] = lambda ctx, inst, x: seen.append(x) or x * 10
        got = s.run_test("t_v::t_mocked")
        out.append((got, seen, [(e.kind, e.detail.get("fn"), e.detail.get("mocked"))
                                for e in sink.events]))
    assert out[0] == out[1]
    assert out[1][:2] == ((2, 0, 0), [4, 1])
    # the trace names the lowered coro (``scale$call2``)
    assert [(k, f.split("$")[0], m) for k, f, m in out[1][2]] == \
        [("call", "scale", True), ("return", "scale", True)] * 2


OP_MODEL = __import__("pathlib").Path(__file__).resolve().parents[2] \
    / "examples" / "op_model" / "tests"


@pytest.mark.parametrize("engine", ["py", "c"])
def test_pssc_test_runs_a_suite_on_the_chosen_engine(engine, capsys,
                                                     monkeypatch, tmp_path):
    """P4.8b: ``pssc test --engine c`` runs the tests on the C engine (a
    register-fake test passes with its checks counted, and so does an
    action, P4.8d2)."""
    from pssc.cli import main
    from pssc.testing import cmd
    monkeypatch.chdir(OP_MODEL)
    monkeypatch.setattr(cmd, "LAST_FAILED", str(tmp_path / "lastfailed"))
    rc = main(["test", "@model.f", ".", "wb_dma_fx_c::t_routing_mask",
               "wb_dma_fx_c::sw_copy_a", "--platform", "fake",
               "--engine", engine, "--targets", "none", "--no-ctrf",
               "--seed", "1"])
    out = capsys.readouterr().out
    assert "t_routing_mask" in out and "2/2 checks" in out, out
    assert rc == 0 and "2 passed" in out, out


def test_an_unknown_engine_is_a_usage_error(monkeypatch):
    from pssc.cli import main
    with pytest.raises(SystemExit):
        main(["test", "x.pss", "--engine", "verilog"])


def _suite_on(engine, monkeypatch, tmp_path, capsys, tests=(), where=OP_MODEL,
              suite=("@model.f", ".")):
    """The op_model suite (or ``tests`` of it, or ``suite`` from ``where``)
    under ``pssc test --engine``: each test's report line, and each run's
    register-fake bus log in order."""
    import re
    from pssc.cli import main
    from pssc.testing import cmd, runner
    plats = []
    made = runner._session_for

    def spy(*a, **k):
        s = made(*a, **k)
        plats.append(s.platform)
        return s
    monkeypatch.chdir(where)
    monkeypatch.setattr(cmd, "LAST_FAILED", str(tmp_path / ("lf_" + engine)))
    monkeypatch.setattr(runner, "_session_for", spy)
    main(["test", *suite, *tests, "--platform", "fake", "--engine", engine,
          "--targets", "none", "--no-ctrf", "--seed", "1"])
    out = capsys.readouterr().out
    lines = re.findall(r"^  (\w+) \.+ (PASS|FAIL|ERROR) +[\d.]+s *(.*)$", out, re.M)
    return [(n, v, re.sub(r"\d+ runs?", "", d).strip()) for n, v, d in lines], \
        [list(p.log) for p in plats]


def test_the_example_suite_gives_the_same_verdicts_and_bus_logs_on_both_engines(
        monkeypatch, tmp_path, capsys):
    """P4.8d: every test of examples/op_model the C engine runs passes as on
    the Python VM, with the same checks and the same register accesses in
    the same order, function tests and action tests (P4.8d2) alike."""
    py, py_logs = _suite_on("py", monkeypatch, tmp_path, capsys)
    c, c_logs = _suite_on("c", monkeypatch, tmp_path, capsys)
    assert len(py) == 21 and all(v == "PASS" for _, v, _ in py), py
    assert py == c
    assert len(py_logs) >= 21 and sum(map(len, py_logs)) > 10
    assert py_logs == c_logs


#: pssunit's own example (its repo, not pssc's; PSSUNIT_EXAMPLES points at
#: its examples/ directory, as for tests/pssunit/test_bc_path.py).
PSSUNIT_WB_DMA = __import__("pathlib").Path(
    __import__("os").environ.get("PSSUNIT_EXAMPLES", "/nonexistent")) / "wb_dma"


@pytest.mark.skipif(not PSSUNIT_WB_DMA.is_dir(),
                    reason="pssunit examples/wb_dma not present (set PSSUNIT_EXAMPLES)")
def test_the_pssunit_example_gives_the_same_verdicts_on_both_engines(
        monkeypatch, tmp_path, capsys):
    """P4.8d3: the other example suite pssc runs (pssunit's wb_dma, two
    action tests) passes on both engines alike. It is run from a scratch
    directory: that repository is not written to."""
    suite = (str(PSSUNIT_WB_DMA),)
    py, py_logs = _suite_on("py", monkeypatch, tmp_path, capsys, where=tmp_path,
                            suite=suite)
    c, c_logs = _suite_on("c", monkeypatch, tmp_path, capsys, where=tmp_path,
                          suite=suite)
    assert [n for n, _, _ in py] == ["smoke_1_a", "smoke_a"], py
    assert all(v == "PASS" for _, v, _ in py) and py == c
    assert py_logs == c_logs


def test_an_activity_the_c_engine_cannot_run_yet_is_refused():
    """P4.8d2, P4.10c1: an activity of plain traversals in the root component
    runs on C (its object is the activation's, ``table.names``); one with
    a node in no known instance, a stream pool or a resource pool over 64
    is refused, saying why; a choice of instance (``comp_slot``) runs since
    P4.10d2, state writes since P4.10d3, buffer writes since P4.10d4, claims
    since P4.10e1, a parallel whose branches lock the same pool since
    P4.10e2."""
    from types import SimpleNamespace as NS
    from pssc.testing.native import NativeUnsupported, _action_obj
    node = NS(comp_rel=0, comp_slot=None)
    def table(**kw):
        t = NS(nodes=[node, node], cones=[], pools=[], names=["x", "y", "z"],
               claims=[[], []], state_writes=[[], []], buffer_writes=[[], []],
               nodes_picks=[[], []], par={})
        t.__dict__.update(kw)
        return t
    model = NS(entries={"a": NS(kind="action", coro=3)},
               activations={3: table()}, obj_layouts={3: ["x"]})
    assert _action_obj(model, "a") == 3
    model.activations[3] = table(nodes=[node, NS(comp_rel=None, comp_slot=4)])
    assert _action_obj(model, "a") == 3
    model.activations[3] = table(state_writes=[[], [1]], pools=[NS(kind="state")])
    assert _action_obj(model, "a") == 3
    model.activations[3] = table(buffer_writes=[[], [1]], pools=[NS(kind="buffer")])
    assert _action_obj(model, "a") == 3
    model.activations[3] = table(claims=[[1], []], pools=[NS(kind="resource", size=4)])
    assert _action_obj(model, "a") == 3
    model.activations[3] = table(claims=[[1], []], pools=[NS(kind="resource", size=4)],
                                 par={2: 1})
    assert _action_obj(model, "a") == 3
    for kw in ({"claims": [[1], []], "pools": [NS(kind="resource", size=65)]},
               {"pools": [NS(kind="stream")]},
               {"nodes": [node, NS(comp_rel=None, comp_slot=None)]}):
        model.activations[3] = table(**kw)
        with pytest.raises(NativeUnsupported, match="stream pools, resource pools over 64"):
            _action_obj(model, "a")



ACTIVITIES = """\
import pssunit_pkg::*;
component t_f : fixture_c {
    rt_c rt;
    int cnt;
    int log;
    action A { int k = 1; exec body { comp.cnt += k; comp.log = comp.log * 4 + 1; } }
    action B { exec body { comp.cnt += 10; comp.log = comp.log * 4 + 2; } }
    action AB { activity { do A; do B; } }
    action R { rand bit[4] v; exec body { comp.cnt += v; comp.log = comp.log * 4 + 3; } }
    action t_seq : test_a {
        int n = 2;
        activity {
            repeat (n) { do A; }
            if (comp.cnt > 1) { do B; } else { do A; }
            do AB;
            select { do A; do B; do R; }
            do R;
        }
    }
}
"""


def _act_model(tmp_path_factory, text):
    p = tmp_path_factory.mktemp("native_act") / "acts.pss"
    p.write_text(text)
    ctx = driver.translate([str(s) for s in sources.all_sources([str(p)])])
    assert not ctx.errors
    return bc_session.lower(ctx, "t_f", functions=["*"])


@pytest.fixture(scope="module")
def act_model(tmp_path_factory):
    return _act_model(tmp_path_factory, ACTIVITIES)


def _comp_after(model, symbol, engine, seed):
    s = bc_session.Session(model, seed=seed, memory=Memory(), engine=engine)
    s.run_entry(model.entries[symbol].coro, symbol)
    o = s.comp_obj
    return dict(zip(o.field_names, o.values))


@pytest.mark.parametrize("seed", range(6))
def test_an_activity_without_constraints_runs_alike_on_both_engines(act_model, seed):
    """P4.10c1: a cone-free activity in the root component (repeat, if,
    a nested activity, select, an unconstrained rand field) is plain bytecode
    the C engine runs: the same traversals, in the same order, with the same
    values (``log`` records the order, ``cnt`` the values)."""
    py = _comp_after(act_model, "t_seq", "py", seed)
    assert _comp_after(act_model, "t_seq", "c", seed) == py
    assert py["log"] >> 4 == 0b0101100110       # A A B A B, then select, R


def test_a_model_read_from_its_image_runs_its_activity_on_c(act_model):
    """The activity table comes with the image (``from_bytes`` sets
    ``activations``), so the C engine's guard sees it too."""
    from zuspec.be.bc.model import ZbcModel
    back = ZbcModel.from_bytes(act_model.to_bytes())
    coro = back.entries["t_seq"].coro
    assert native._plain_activity(back.activations[coro])
    assert native._action_obj(back, "t_seq") == len(act_model.activations[coro].names)


CONES = """\
    action t_cone : test_a { activity { do A; do R with { v < 3; }; } }
    action Q { rand bit[4] v; constraint v > comp.cnt; exec body { comp.cnt += v; } }
    action t_pin : test_a { activity { do A; do A; do Q with { v < 6; }; } }
    action t_two : test_a {
        R r1, r2;
        activity { r1; r2; constraint { r1.v + r2.v == 7; r1.v < r2.v; } }
    }
}
"""


@pytest.fixture(scope="module")
def cone_model(tmp_path_factory):
    return _act_model(tmp_path_factory, ACTIVITIES[:ACTIVITIES.rindex("}")] + CONES)


@pytest.mark.parametrize("seed", range(4))
@pytest.mark.parametrize("symbol", ["t_cone", "t_pin", "t_two"])
def test_an_activity_with_constraints_solves_alike_on_both_engines(cone_model, symbol, seed):
    """P4.10d1: a cone (a ``with`` constraint, one reading a component
    attribute, an activity constraint over two traversals) is solved by the C
    engine from the image's blobs to the same values as on the Python VM."""
    assert native._plain_activity(cone_model.activations[cone_model.entries[symbol].coro])
    py = _comp_after(cone_model, symbol, "py", seed)
    assert _comp_after(cone_model, symbol, "c", seed) == py


def test_an_unsatisfiable_activity_constraint_fails_on_both_engines(tmp_path_factory):
    """P4.10d1: a cone with no solution stops the run on both engines (C
    names no constraint: ZBC_ERR_SOLVE_UNSAT, without pyvm's text)."""
    from zuspec.be.bc.interp.activation import ScopeUnsatError
    from zuspec.rt.eng.host import NativeError
    model = _act_model(tmp_path_factory, ACTIVITIES[:ACTIVITIES.rindex("}")]
        + "    action t_un : test_a { activity { do A; do R with { v > 20; }; } }\n}\n")
    with pytest.raises(ScopeUnsatError, match="v > 20"):
        _comp_after(model, "t_un", "py", 0)
    with pytest.raises(NativeError, match="unsatisfiable"):
        _comp_after(model, "t_un", "c", 0)


FLOW = """\
import pssunit_pkg::*;
component t_f : fixture_c {
    rt_c rt;
    int cnt;
    buffer dat_b { int v; }
    pool dat_b dp;
    bind dp *;
    action P { output dat_b o; exec body { o.v = comp.cnt; comp.cnt += 1; } }
    action t_buf : test_a { activity { do P; do P; } }
@MORE@
}
"""


def test_a_buffer_output_no_one_reads_runs_alike_on_both_engines(tmp_path_factory):
    """P4.10c3: a buffer output with no consumer is plain bytecode (no pool,
    no write in the activity table), so the C engine runs it as is."""
    model = _act_model(tmp_path_factory, FLOW.replace("@MORE@", ""))
    t = model.activations[model.entries["t_buf"].coro]
    assert not t.pools and not any(t.buffer_writes) and native._plain_activity(t)
    assert _comp_after(model, "t_buf", "c", 1) == _comp_after(model, "t_buf", "py", 1)


STATE = """\
    state cfg_s { rand bit[8] x; }
    pool cfg_s cp;
    bind cp *;
    action S {
        input cfg_s prev; output cfg_s post;
        constraint post.x == prev.x / 2;
        exec body { comp.cnt = comp.cnt * 256 + post.x; }
    }
    action t_st : test_a { activity { do S; do S; do S; } }"""


@pytest.fixture(scope="module")
def state_model(tmp_path_factory):
    return _act_model(tmp_path_factory, FLOW.replace("@MORE@", STATE))


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 7, 11])
def test_a_state_pool_flows_alike_on_both_engines(state_model, seed):
    """P4.10d3: the root's solve chooses the state pool's initial object,
    each ``S`` reads the current object (``live_from``) and its output
    becomes current when it ends (state writes); C does each as pyvm does,
    so the chain of x values in cnt matches."""
    t = state_model.activations[state_model.entries["t_st"].coro]
    assert t.cones and t.pools and any(t.state_writes) and native._plain_activity(t)
    py = _comp_after(state_model, "t_st", "py", seed)
    cnt = py["cnt"] if "cnt" in py else next(v for k, v in py.items() if k.endswith("cnt"))
    x = [(cnt >> s) & 0xFF for s in (16, 8, 0)]
    assert x[1] == x[0] // 2 and x[2] == x[1] // 2
    assert _comp_after(state_model, "t_st", "c", seed) == py


BUF = """\
    buffer rb_b { rand bit[4] v; }
    pool rb_b rp;
    bind rp *;
    action Q0 { output rb_b o; constraint o.v == 0; }
    action Q5 { output rb_b o; constraint o.v == 5; }
    action D { input rb_b i; constraint i.v > 0; exec body { comp.cnt = comp.cnt * 16 + i.v; } }
    action E { input rb_b i; exec body { comp.cnt = comp.cnt * 16 + i.v; } }
    action Q3 { output rb_b o; constraint o.v == 3; }
    action t_bc : test_a { activity { do Q0; do Q3; do Q5; do E; do E; do E; } }
    action t_bd : test_a { activity { do Q0; do Q5; do Q5; do D; do D; do D; do E; do E; } }"""


@pytest.fixture(scope="module")
def buf_model(tmp_path_factory):
    return _act_model(tmp_path_factory, FLOW.replace("@MORE@", BUF))


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 7, 11])
@pytest.mark.parametrize("symbol", ["t_bc", "t_bd"])
def test_buffer_picks_run_alike_on_both_engines(buf_model, symbol, seed):
    """P4.10d4: each consumer's input, which no bind connects, picks one of
    the objects completed producers output, least-consumed first in an
    order drawn from the seed (pyvm ``_candidates``, random.Random; C
    zbc_rng.c); C picks the same objects, so the digits in cnt match. In
    ``t_bd`` D's constraint rejects v == 0, so the third ``D`` takes an
    object another one consumed; the ``E`` s then take the v == 0 one first."""
    t = buf_model.activations[buf_model.entries[symbol].coro]
    assert t.pools and any(t.buffer_writes) and any(any(p) for p in t.nodes_picks)
    assert native._plain_activity(t)
    py = _comp_after(buf_model, symbol, "py", seed)
    assert _comp_after(buf_model, symbol, "c", seed) == py


SUB = """\
import pssunit_pkg::*;
component sub_c { int cnt; action A { exec body { comp.cnt += 1; } } }
component t_f : fixture_c {
    rt_c rt;
    int cnt;
    sub_c s1;
    action t_one : test_a { activity { do sub_c::A; do sub_c::A; } }
}
"""


def test_an_activity_in_a_sub_component_runs_alike_on_both_engines(tmp_path_factory):
    """P4.10c4: a node in the one instance of a sub-component (no choice of
    instance, so no cone) runs on C in that instance: ``s1.cnt`` counts, the
    root's ``cnt`` does not."""
    model = _act_model(tmp_path_factory, SUB)
    t = model.activations[model.entries["t_one"].coro]
    assert not t.cones and any(n.comp_rel for n in t.nodes) and native._plain_activity(t)
    py = _comp_after(model, "t_one", "py", 1)
    assert sorted(v for k, v in py.items() if k.endswith("cnt")) == [0, 2]
    assert _comp_after(model, "t_one", "c", 1) == py


SUB2 = SUB.replace(
    "action A { exec body { comp.cnt += 1; } } }",
    "action A { exec body { comp.cnt += 1; } }\n    action AA { activity { do A; do A; } } }").replace(
    "    sub_c s1;\n", "    sub_c s1;\n    sub_c s2;\n").replace(
    "    action t_one", "    action t_pick : test_a { activity { do sub_c::A; do sub_c::A; do sub_c::A; } }\n"
    "    action t_nest : test_a { activity { do sub_c::AA; do sub_c::AA; } }\n"
    "    action t_one")


@pytest.fixture(scope="module")
def sub2_model(tmp_path_factory):
    return _act_model(tmp_path_factory, SUB2)


@pytest.mark.parametrize("seed", range(6))
@pytest.mark.parametrize("symbol,total", [("t_pick", 3), ("t_nest", 4)])
def test_a_choice_of_instance_is_made_alike_on_both_engines(sub2_model, symbol, total, seed):
    """P4.10d2: with two sub_c instances each ``do sub_c::A`` (or compound
    ``AA``, whose children run where it does) is solved onto one of them
    (``comp_slot``); C runs it in the instance the solve chose, so the counts
    in s1 and s2 match pyvm's."""
    t = sub2_model.activations[sub2_model.entries[symbol].coro]
    assert t.cones and any(n.comp_slot is not None for n in t.nodes)
    assert native._plain_activity(t)
    py = _comp_after(sub2_model, symbol, "py", seed)
    assert sum(v for k, v in py.items() if k.endswith("cnt")) == total
    assert _comp_after(sub2_model, symbol, "c", seed) == py


RES = """\
    resource ch_r { }
    pool [3] ch_r chp;
    bind chp *;
    action L { lock ch_r c; exec body { comp.cnt = comp.cnt * 4 + c.instance_id; } }
    action S { share ch_r c; exec body { comp.cnt = comp.cnt * 4 + c.instance_id; } }
    action LN { lock ch_r c; constraint c.instance_id != 0;
                exec body { comp.cnt = comp.cnt * 4 + c.instance_id; } }
    action H { lock ch_r c; activity { do L; do S; } }
    action t_rs : test_a { activity { do L; do L; do S; do LN; do H; } }
    action t_rp : test_a { activity { parallel { do L; do S; } do S; parallel { do LN; do S; } } }
    action t_rk : test_a { activity { parallel { do H; sequence { do S; do S; } } do L; } }
    action t_pl : test_a { activity { parallel { do L; do L; } do S; } }
    action t_ph : test_a { activity { do L; parallel { do H; do L; } do LN; } }"""


@pytest.fixture(scope="module")
def res_model(tmp_path_factory):
    return _act_model(tmp_path_factory, FLOW.replace("@MORE@", RES))


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 7, 11])
@pytest.mark.parametrize("symbol", ["t_rs", "t_rp", "t_rk"])
def test_resource_claims_choose_alike_on_both_engines(res_model, symbol, seed):
    """P4.10e1: each lock or share chooses an instance of the pool no
    conflicting claim holds now or took concurrently (pyvm ``_busy``: held
    by a non-ancestor, or released in another branch of a parallel entered
    before it; ``H``'s own lock does not exclude its sub-actions'); C records the claims after the solve,
    releases them at the node's end and pins the busy mask as pyvm does, so
    the instance digits in cnt match. Only one branch of each parallel
    locks, so no footprint is chosen (``table.par`` is empty)."""
    t = res_model.activations[res_model.entries[symbol].coro]
    assert any(any(c) for c in t.claims) and not t.par and native._plain_activity(t)
    py = _comp_after(res_model, symbol, "py", seed)
    assert _comp_after(res_model, symbol, "c", seed) == py


def test_a_claim_no_instance_can_take_fails_on_both_engines(tmp_path_factory):
    """P4.10e1: a share concurrent with a lock of the only instance has no
    solution on either engine (C: ZBC_ERR_SOLVE_UNSAT, without pyvm's text)."""
    from zuspec.be.bc.interp.activation import ScopeUnsatError
    from zuspec.rt.eng.host import NativeError
    model = _act_model(tmp_path_factory, FLOW.replace("@MORE@", RES.replace(
        "pool [3]", "pool [1]")))
    with pytest.raises(ScopeUnsatError):
        _comp_after(model, "t_rp", "py", 0)
    with pytest.raises(NativeError, match="unsatisfiable"):
        _comp_after(model, "t_rp", "c", 0)


@pytest.mark.parametrize("seed", [0, 1, 2, 3, 7, 11])
@pytest.mark.parametrize("symbol", ["t_pl", "t_ph"])
def test_parallel_locks_of_one_pool_split_it_alike_on_both_engines(
        res_model, symbol, seed):
    """P4.10e2: two branches of a parallel lock ``ch_r``, so at the
    parallel's entry each branch is dealt a footprint of the pool (pyvm
    ``_footprints``: a seed drawn there, branches probed in depth order); C
    builds the same table at activation start, deals alike and keeps each
    branch's claims inside its footprint, so the instance digits match."""
    t = res_model.activations[res_model.entries[symbol].coro]
    assert t.par and native._plain_activity(t)
    py = _comp_after(res_model, symbol, "py", seed)
    assert _comp_after(res_model, symbol, "c", seed) == py


def test_parallel_locks_a_pool_cannot_split_fail_on_both_engines(tmp_path_factory):
    """P4.10e2: two parallel locks of a pool of one instance have no
    footprints; both engines say the parallel has no solution."""
    from zuspec.be.bc.interp.activation import ScopeUnsatError
    from zuspec.rt.eng.host import NativeError
    model = _act_model(tmp_path_factory, FLOW.replace("@MORE@", RES.replace(
        "pool [3]", "pool [1]")))
    with pytest.raises(ScopeUnsatError):
        _comp_after(model, "t_pl", "py", 0)
    with pytest.raises(NativeError, match="unsatisfiable"):
        _comp_after(model, "t_pl", "c", 0)
