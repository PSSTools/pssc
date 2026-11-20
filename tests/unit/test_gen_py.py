"""P5.4b: a pssunit test runs as generated Python (op-model-py) against the
run's register fake and the runner's checks (testing/gen_py.py).
P5.4c (T5.4): ``--engine gen-py`` fails a test whose generated code's bus
traffic is not pyvm's (runner.py ``_run_gen``)."""

import types
from pathlib import Path

import pytest

from pssc import driver
from pssc.testing import gen_py, runner
from pssc.testing import sources as _sources
from pssc.testing.discover import TestItem
from pssc.testing.runner import Kind, Status

TESTS = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "tests"
COMP = "wb_dma_fx_c"

# A test whose last check fails: the channel is left enabled.
BAD = """\
import pssunit_pkg::*;

extend component wb_dma_fx_c {
    action bad_copy_a : test_a {
        exec body {
            comp.sw_copy();
            check_field("dma.ch[0].regs.csr", "ch_en", 0, "channel left disabled");
        }
    }
}
"""


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gen_py")
    (tmp / "bad_copy_a.pss").write_text(BAD)
    paths = [str(p) for p in _sources.all_sources(
        ["@%s" % (TESTS / "model.f"), str(TESTS / "wb_dma_fx_c.pss"),
         str(TESTS / "sw_copy_a.pss"), str(tmp / "bad_copy_a.pss")])]
    ctx = driver.translate(paths)
    image = runner._build_image(ctx, COMP)
    assert image.error is None, image.error
    built = gen_py.build(paths, COMP, ["%s::sw_copy_a" % COMP,
                                       "%s::bad_copy_a" % COMP,
                                       "%s::no_such_a" % COMP],
                         str(tmp / "gen"))
    return types.SimpleNamespace(image=image, built=built, ctx=ctx)


def _session(image, out=None):
    s = runner._session_for(image, 1,
                            types.SimpleNamespace(fake=True, doubles=[]))
    s.out = (out if out is not None else []).append
    return s


def _pyvm(image, name):
    s = _session(image)
    counts = s.run_entry(image.entries[name], name, seed=1,
                         setup=image.setup, teardown=image.teardown)
    return s, counts


def _bus(session):
    # not the register's path: generated code names no register, so an
    # address two aliases share decodes to the one placed first
    return [(e["op"], e["width"], e["addr"], e["data"])
            for e in session.platform.bus]


def test_refused_test_says_why(fx):
    assert set(fx.built.methods) == {"%s::sw_copy_a" % COMP,
                                     "%s::bad_copy_a" % COMP}
    assert "no_such_a" in fx.built.refused["%s::no_such_a" % COMP]
    assert fx.built.setup == ["bind_dma"]


def test_passing_test_matches_pyvm(fx):
    ref, counts = _pyvm(fx.image, "sw_copy_a")
    assert counts[:2] == (2, 0)
    s = _session(fx.image)
    passed, failed, steps = gen_py.run(fx.built, "%s::sw_copy_a" % COMP, s,
                                       bases=ref.platform.bases)
    assert (passed, failed) == (2, 0)
    # the same accesses
    assert _bus(s) == _bus(ref)
    assert [(x["rw"], x["addr"], x["data"]) for x in steps] == \
        [(op[0], a, d) for op, _w, a, d in _bus(ref)]


def test_failing_check_gives_the_card(fx):
    ref, counts = _pyvm(fx.image, "bad_copy_a")
    assert counts[:2] == (2, 1)
    s = _session(fx.image)
    passed, failed, _ = gen_py.run(fx.built, "%s::bad_copy_a" % COMP, s,
                                   bases=ref.platform.bases)
    assert (passed, failed) == (2, 1)
    card = "\n".join(s.failure_cards("bad_copy_a", repro=""))
    # the bus lines differ only in their source locations (pyvm's)
    assert card.splitlines()[:2] == \
        "\n".join(ref.failure_cards("bad_copy_a", repro="")).splitlines()[:2]
    assert "channel left disabled" in card and "ch_en" in card


def _gen(fx, monkeypatch, name, **kw):
    monkeypatch.setitem(runner._GEN, "cache",
                        types.SimpleNamespace(get=lambda comp: fx.built))
    item = TestItem(qname="%s::%s" % (COMP, name), name=name, comp=COMP,
                    **kw)
    return runner._run_gen(fx.image, fx.ctx, item, 1, False, None, None,
                           None)


def test_gen_engine_passes_and_fails_like_pyvm(fx, monkeypatch):
    r = _gen(fx, monkeypatch, "sw_copy_a")
    assert r.status is Status.PASS, r.detail
    assert (r.checks_passed, r.checks_failed) == (2, 0)
    r = _gen(fx, monkeypatch, "bad_copy_a")
    assert (r.status, r.kind) == (Status.FAIL, Kind.CHECK)
    assert "channel left disabled" in r.detail


def test_gen_engine_fails_a_divergent_access(fx, monkeypatch):
    # calibration: generated code that writes one word off is caught
    orig = gen_py.Adapter._writer

    def shifted(self, width):
        wr = orig(self, width)
        return lambda addr, data: wr(addr + 4, data)
    monkeypatch.setattr(gen_py.Adapter, "_writer", shifted)
    r = _gen(fx, monkeypatch, "sw_copy_a")
    assert (r.status, r.kind) == (Status.FAIL, Kind.DIVERGED), r.detail
    assert r.detail.startswith("diverges from pyvm at bus access ")
    assert "expected write32 0x" in r.detail.splitlines()[0]


def test_gen_engine_skips_what_it_does_not_export(fx, monkeypatch):
    r = _gen(fx, monkeypatch, "no_such_a")
    assert r.status is Status.SKIP and "no_such_a" in r.detail
    r = _gen(fx, monkeypatch, "fn", kind="function")
    assert r.status is Status.SKIP
    assert r.detail == ("gen-py: a test function is not exported to "
                        "generated code")
    # a property test's skip is not worded as a failed run
    r = runner._run_image(fx.image, fx.ctx, TestItem(
        qname="%s::no_such_a" % COMP, name="no_such_a", comp=COMP,
        property_runs=8), 1, False, None, None, None, None, None,
        engine="gen-py")
    assert r.status is Status.SKIP and r.detail.startswith("gen-py: ")


def test_diverges():
    a = {"op": "write", "width": 32, "addr": 0x10, "data": 1, "reg": "r"}
    b = dict(a, data=2)
    assert gen_py.diverges([a], [dict(a)]) is None
    assert gen_py.diverges([a], [b]) == (
        "diverges from pyvm at bus access 1: expected write32 0x10 = 0x1 "
        "(r), got write32 0x10 = 0x2 (r)")
    assert gen_py.diverges([a, a], [a]).endswith(
        "at bus access 2: expected write32 0x10 = 0x1 (r), "
        "got nothing (the run ended)")
