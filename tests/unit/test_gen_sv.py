"""P5.4d1: a pssunit test runs as generated SystemVerilog (op-model-sv)
under Verilator, offline: reads and import outputs served from the pyvm
reference run, every access and import call logged (testing/gen_sv.py)."""

import types
from pathlib import Path

import pytest

from pssc import driver
from pssc.testing import gen_py, gen_sv, runner
from pssc.testing import sources as _sources
from pssc.testing.discover import TestItem
from pssc.testing.runner import Kind, Status

pytestmark = pytest.mark.skipif(gen_sv.verilator() is None,
                                reason="needs verilator on PATH")

TESTS = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "tests"
COMP = "wb_dma_fx_c"
TEST = "%s::sw_copy_a" % COMP


def _key(e):
    return (e["op"], e["width"], e["addr"], e["data"])


@pytest.fixture(scope="module")
def fx(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("gen_sv")
    paths = [str(p) for p in _sources.all_sources(
        ["@%s" % (TESTS / "model.f"), str(TESTS / "wb_dma_fx_c.pss"),
         str(TESTS / "sw_copy_a.pss")])]
    image = runner._build_image(driver.translate(paths), COMP)
    assert image.error is None, image.error

    def session():
        s = runner._session_for(image, 1,
                                types.SimpleNamespace(fake=True, doubles=[]))
        s.out = [].append
        return s
    ref = session()
    assert ref.run_entry(image.entries["sw_copy_a"], "sw_copy_a", seed=1,
                         setup=image.setup,
                         teardown=image.teardown)[:2] == (2, 0)
    # the import outputs the SV run is served: what the same imports
    # returned to the generated Python
    s = session()
    api = gen_py.Adapter(s)
    gen_py.run(gen_py.build(paths, COMP, [TEST], str(tmp / "py")), TEST, s,
               bases=ref.platform.bases, api=api)
    built = gen_sv.build(paths, COMP, [TEST, "%s::no_such_a" % COMP],
                         str(tmp / "sv"))
    reads = [e["data"] for e in ref.platform.bus if e["op"] == "read"]
    return types.SimpleNamespace(ref=ref, built=built, reads=reads,
                                 returns=api.returns)


def test_refused_test_says_why(fx):
    assert set(fx.built.methods) == {TEST}
    assert "no_such_a" in fx.built.refused["%s::no_such_a" % COMP]
    assert fx.built.setup == ["bind_dma"]
    # the pssunit imports are the root's import API, with their outputs
    assert [a.output for a in fx.built.imports["double_call"]] == \
        [True, False, False]


def test_sv_accesses_match_pyvm(fx):
    log = gen_sv.run(fx.built, TEST, reads=fx.reads, returns=fx.returns)
    assert log.finished, log.error
    assert [_key(e) for e in log.bus()] == \
        [_key(e) for e in fx.ref.platform.bus]
    assert gen_py.diverges(fx.ref.platform.bus, log.bus()) is None
    # import calls logged with their arguments and what they were served
    calls = [x for x in log.steps if x["kind"] == "import"]
    assert calls[0]["name"] == "double_handle"
    assert calls[0]["ret"] == fx.returns["double_handle"][:1]
    assert any(x["name"] == "check_capture" for x in calls)


def test_read_past_reference_stops_the_run(fx):
    # one read fewer than the reference made: the poll would spin on
    # stale data; the run stops at the extra read, a divergence
    log = gen_sv.run(fx.built, TEST, reads=fx.reads[:-1],
                     returns=fx.returns)
    assert not log.finished
    assert "past the reference" in log.error
    assert gen_py.diverges(fx.ref.platform.bus, log.bus()) is not None


# P5.4d2: `pssc test --engine gen-sv` (runner._run_gen)

@pytest.fixture(scope="module")
def rx(tmp_path_factory):
    from tests.unit.test_gen_py import BAD
    tmp = tmp_path_factory.mktemp("gen_sv_run")
    (tmp / "bad_copy_a.pss").write_text(BAD)
    paths = [str(p) for p in _sources.all_sources(
        ["@%s" % (TESTS / "model.f"), str(TESTS / "wb_dma_fx_c.pss"),
         str(TESTS / "sw_copy_a.pss"), str(tmp / "bad_copy_a.pss")])]
    ctx = driver.translate(paths)
    image = runner._build_image(ctx, COMP)
    assert image.error is None, image.error
    items = [TestItem(qname="%s::%s" % (COMP, n), name=n, comp=COMP)
             for n in ("sw_copy_a", "bad_copy_a", "no_such_a")]
    cache = gen_sv.Cache(paths, str(tmp / "out"), items)
    return types.SimpleNamespace(image=image, ctx=ctx, cache=cache)


def _gen(rx, monkeypatch, name, **kw):
    monkeypatch.setitem(runner._GEN, "cache", rx.cache)
    item = TestItem(qname="%s::%s" % (COMP, name), name=name, comp=COMP,
                    **kw)
    return runner._run_gen(rx.image, rx.ctx, item, 1, False, None, None,
                           None, "gen-sv")


def test_gen_sv_engine_passes_and_fails_like_pyvm(rx, monkeypatch):
    r = _gen(rx, monkeypatch, "sw_copy_a")
    assert r.status is Status.PASS, r.detail
    assert (r.checks_passed, r.checks_failed) == (2, 0)
    r = _gen(rx, monkeypatch, "bad_copy_a")
    assert (r.status, r.kind) == (Status.FAIL, Kind.CHECK), r.detail
    assert "channel left disabled" in r.detail
    r = _gen(rx, monkeypatch, "no_such_a")
    assert r.status is Status.SKIP and r.detail.startswith("gen-sv: ")


def test_gen_sv_engine_fails_a_divergent_access(rx, monkeypatch):
    # calibration: an SV run that is served one read fewer than the
    # reference made stops at the extra read: DIVERGED, not ERROR
    orig = gen_sv.run

    def short(built, test, reads=(), **kw):
        return orig(built, test, reads=list(reads)[:-1], **kw)
    monkeypatch.setattr(gen_sv, "run", short)
    r = _gen(rx, monkeypatch, "sw_copy_a")
    assert (r.status, r.kind) == (Status.FAIL, Kind.DIVERGED), r.detail
    assert r.detail.startswith("diverges from pyvm at bus access ")
