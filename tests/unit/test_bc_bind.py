"""T1.8: host-bound functions.

``VM.bind`` (``Session.run_test(..., bindings=...)``, ``bc_session.run(...,
bindings=...)``) runs a host callable in place of a PSS function's body. The
CALL's ``call``/``return`` events carry ``mocked``; a binding lasts one run.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources

SUITE = """\
import pssunit_pkg::*;
component base_f : fixture_c {
    rt_c rt;
    @pssunit_pkg::test
    function void t_scale() {
        rt.check_eq_int(scale(2), 5, "scale");
    }
    function int scale(int x) { return x * 2; }
    function int twice_scale(int x) { return scale(x) + scale(x); }
}
"""


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    p = tmp_path_factory.mktemp("bind") / "suite.pss"
    p.write_text(SUITE)
    c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
    assert not c.errors
    return c


def _model(ctx, debug=True):
    return bc_session.lower(ctx, "base_f", functions=["*"], debug=debug)


def test_a_bound_function_replaces_the_body(ctx):
    s = bc_session.Session(_model(ctx))
    assert s.run_test("base_f::t_scale", seed=1) == (0, 1, 0)
    seen = []
    def scale(x):
        seen.append(x)
        return 5
    assert s.run_test("base_f::t_scale", seed=1,
                      bindings={"base_f::scale": scale}) == (1, 0, 0)
    assert seen == [2]


def test_the_binding_does_not_leak_into_the_next_test(ctx):
    s = bc_session.Session(_model(ctx))
    assert s.run_test("base_f::t_scale", seed=1,
                      bindings={"base_f::scale": lambda x: 5}) == (1, 0, 0)
    assert s.run_test("base_f::t_scale", seed=1) == (0, 1, 0)


def test_a_bound_call_is_a_mocked_call_event(ctx):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev

    sink = MemorySink()
    s = bc_session.Session(_model(ctx), events=sink, watch=ev.EV_CALL)
    s.run_test("base_f::t_scale", seed=1, bindings={"base_f::scale": lambda x: 7})
    mine = [e for e in sink.events if e.detail.get("fn", "").startswith("scale$")]
    assert [(e.kind, e.detail["mocked"]) for e in mine] == [("call", True),
                                                            ("return", True)]
    assert mine[0].detail["args"] == [2]
    assert mine[1].detail["retval"] == 7


def test_run_binds_for_one_run(ctx):
    m = _model(ctx)
    entry = m.entries["base_f::twice_scale"].coro
    r, _ = bc_session.run(m, entry, seed=1, args=(3,),
                          bindings={"base_f::scale": lambda x: x + 100})
    assert r.retval == 206
    r, _ = bc_session.run(m, entry, seed=1, args=(3,))
    assert r.retval == 12


def test_release_has_nothing_to_bind(ctx):
    s = bc_session.Session(_model(ctx, debug=False))
    with pytest.raises(KeyError, match="debug lowering"):
        s.run_test("base_f::t_scale", bindings={"base_f::scale": lambda x: 5})
