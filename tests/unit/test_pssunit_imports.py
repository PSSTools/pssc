"""`pssunit_pkg`'s declarations and the runner's implementations of its imports (T1.1).

The package is PSS; the checks and the platform-double calls in it are
imports the runner implements. The two halves are kept in step here: an
import without an implementation would read as 0 on an engine and pass, so
it fails this tier instead.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from pssc import annotations, driver
from pssc.testing import imports
from pssc.testing.imports import (
    IMPORTS, NoDouble, RequireFailed, RunState, declared, missing)
from pssc.testing.sources import pssunit_sources

DOC = Path(__file__).resolve().parents[2] / "docs" / "writing-tests.rst"


@pytest.fixture(scope="module")
def ctx():
    return driver.translate(pssunit_sources())


def _short(qname: str) -> str:
    return qname.split("::", 1)[1]


# --- the package -------------------------------------------------------------

def test_the_package_translates_alone(ctx):
    assert "pssunit_pkg::fixture_c" in ctx.type_map
    assert "pssunit_pkg::access_s" in ctx.type_map


def test_every_declared_import_has_an_implementation(ctx):
    assert declared(ctx), "no pssunit_pkg imports found: is the package read?"
    assert missing(ctx) == []


def test_every_implementation_is_declared(ctx):
    """A stale entry is a name a test can no longer call."""
    assert sorted(set(IMPORTS) - set(declared(ctx))) == []


def test_a_new_import_without_an_implementation_is_reported():
    fn = SimpleNamespace(name="check_new",
                         metadata={"qualified_name": "pssunit_pkg::check_new"})
    other = SimpleNamespace(name="f", metadata={"qualified_name": "p::f"})
    assert missing(SimpleNamespace(import_functions=[fn, other])) == [
        "pssunit_pkg::check_new"]


def test_each_annotation_is_readable(tmp_path):
    src = tmp_path / "t.pss"
    src.write_text("""
import pssunit_pkg::*;
@pssunit_pkg::under_test {.comp = "dut_c"}
@pssunit_pkg::platform {.min = "fake"}
component fx_c : pssunit_pkg::fixture_c {
    @pssunit_pkg::test {.tags = "slow,io"}
    @pssunit_pkg::property {.runs = 7}
    @pssunit_pkg::expect_error {.text = "busy"}
    @pssunit_pkg::tag {.name = "slow"}
    @pssunit_pkg::allow {.what = "inflight"}
    action t_a : pssunit_pkg::test_a { }

    @pssunit_pkg::field_access {.field = "done", .kind = pssunit_pkg::access_kind_e::RC}
    int r;

    @pssunit_pkg::setup
    target function void up() { }
    @pssunit_pkg::teardown
    target function void down() { }
}
""")
    c = driver.translate(pssunit_sources() + [src])
    fx, t = c.type_map["fx_c"], c.type_map["fx_c::t_a"]
    assert annotations.of(fx, "pssunit_pkg::under_test") == {"comp": "dut_c"}
    assert annotations.of(fx, "pssunit_pkg::platform") == {"min": "fake"}
    assert annotations.of(t, "pssunit_pkg::test")["tags"] == "slow,io"
    assert annotations.of(t, "pssunit_pkg::property") == {"runs": 7}
    assert annotations.of(t, "pssunit_pkg::expect_error") == {"text": "busy"}
    assert annotations.of(t, "pssunit_pkg::tag") == {"name": "slow"}
    assert annotations.of(t, "pssunit_pkg::allow") == {"what": "inflight"}

    field = next(f for f in fx.fields if f.name == "r")
    fa = annotations.of(field, "pssunit_pkg::field_access")
    assert (fa["reg"], fa["field"], fa["kind"].item) == ("", "done", "RC")

    fns = {f.name: f for f in fx.functions}
    assert annotations.of(fns["up"], "pssunit_pkg::setup") == {}
    assert annotations.of(fns["down"], "pssunit_pkg::teardown") == {}


def test_property_runs_has_a_default(tmp_path):
    src = tmp_path / "t.pss"
    src.write_text("""
component fx_c : pssunit_pkg::fixture_c {
    @pssunit_pkg::property
    action t_a : pssunit_pkg::test_a { }
}
""")
    c = driver.translate(pssunit_sources() + [src])
    assert annotations.of(c.type_map["fx_c::t_a"], "pssunit_pkg::property") == {"runs": 100}


# --- the implementations -----------------------------------------------------

def _call(st, name, *args):
    return IMPORTS["pssunit_pkg::" + name](st, *args)


@pytest.mark.parametrize("name,a,b,ok", [
    ("check_eq", 3, 3, True), ("check_eq", 3, 4, False),
    ("check_ne", 3, 4, True), ("check_lt", -1, 0, True),
    ("check_le", 1, 0, False), ("check_gt", 1, 0, True),
    ("check_ge", 0, 1, False),
])
def test_comparisons_record_into_the_ledger(name, a, b, ok):
    st = RunState()
    _call(st, name, a, b, "")
    assert (st.ledger.n_pass, st.ledger.n_fail) == ((1, 0) if ok else (0, 1))


def test_a_failed_comparison_reports_both_sides():
    st = RunState()
    _call(st, "check_eq", 3, 4, "")
    assert st.ledger.failures == ["check_eq: 3 == 4"]


def test_check_goes_on_and_require_stops():
    st = RunState()
    _call(st, "check", False, "first")
    with pytest.raises(RequireFailed):
        _call(st, "require", False, "second")
    assert st.ledger.failures == ["first", "second"]


def test_fail_records_and_stops():
    st = RunState()
    with pytest.raises(RequireFailed, match="nope"):
        _call(st, "fail", "nope")
    assert st.ledger.n_fail == 1


def test_a_double_call_without_a_double_fails_by_name():
    with pytest.raises(NoDouble, match="pssunit_pkg::poke_reg"):
        _call(RunState(), "poke_reg", "dma.csr", 1)


class _Double:
    def __init__(self):
        self.regs = {"dma.csr": 0x41}
        self.calls = []

    def peek_reg(self, reg):
        return self.regs[reg]

    def poke_reg(self, reg, v):
        self.calls.append(("poke_reg", reg, v))

    def last_written(self, reg, field):
        return None

    def accessed(self, reg):
        return True


def test_back_door_checks_read_the_double():
    st = RunState(double=_Double())
    _call(st, "check_reg", "dma.csr", 0x41, "")
    _call(st, "check_reg", "dma.csr", 0x40, "")
    _call(st, "check_written", "dma.csr", "en", 1, "")
    _call(st, "check_not_accessed", "dma.csr", "")
    assert st.ledger.n_pass == 1
    assert st.ledger.failures == [
        "check_reg: dma.csr == 0x40 (is 0x41)",
        "check_written: dma.csr.en == 0x1 (never written)",
        "check_not_accessed: dma.csr"]


def test_double_calls_forward():
    st = RunState(double=_Double())
    _call(st, "poke_reg", "dma.csr", 5)
    assert st.double.calls == [("poke_reg", "dma.csr", 5)]


# --- the reference page ------------------------------------------------------

def test_the_page_names_every_import(ctx):
    text = DOC.read_text()
    for q in declared(ctx):
        assert "``%s(" % _short(q) in text or "``%s``" % _short(q) in text, q


def test_the_page_names_every_annotation():
    pkg = pssunit_sources()[0].read_text()
    text = DOC.read_text()
    for name in re.findall(r"^\s*annotation\s+(\w+)", pkg, re.M):
        assert "``%s``" % name in text, name


def test_the_module_is_documented():
    assert imports.__doc__ and "missing" in imports.__doc__
