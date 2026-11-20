"""P1.11a: ``pssunit_pkg`` checks capture their operands (T1.11, part a).

``check_rewrite`` turns each check into temporaries, ``check_operand`` calls
and one ``check_capture(id, ok)``, with a table of the check's source text and
operands; so the checks lower to bc, and each operand is evaluated once.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")

import pssc.testing.vm as pv
from pssc.testing import check_rewrite, imports
from pssc.testing.imports import RequireFailed, RunState

SUITE = """\
import pssunit_pkg::*;

component t_f : fixture_c {
    int n;
    function bit[32] two() { return 2; }
    function bit[32] bump() { n = n + 1; return n; }
    @pssunit_pkg::test
    function void t_a() {
        check_eq(two() + 1, 4, "three");
        check(bump() == 1);
        require(n < 5);
        check_reg("dma.csr", n);
    }
}
"""


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    f = tmp_path_factory.mktemp("ck") / "suite.pss"
    f.write_text(SUITE)
    return f


class Recorder(pv.Platform):
    def __init__(self):
        super().__init__()
        self.ops, self.caps = [], []

    def check_operand(self, v):
        self.ops.append(v)

    def check_capture(self, cid, ok):
        self.caps.append((cid, ok, self.ops))
        self.ops = []


def _line(text: str) -> int:
    return SUITE[:SUITE.index(text)].count("\n") + 1


def test_the_table_names_each_check_and_its_operands(suite):
    s = pv.load([suite], root="t_f", platform=Recorder())
    sites = list(s.model.checks.values())
    assert [c.kind for c in sites] == ["check_eq", "check", "require", "check_reg"]
    eq, chk, req, reg = sites
    assert eq.location == "%s:%d:9" % (suite, _line("check_eq("))
    assert eq.text == 'check_eq(two() + 1, 4, "three")'
    assert (eq.op, eq.what) == ("==", "three")
    assert [(o.text, eq.text[o.offset:].startswith(o.text)) for o in eq.operands] \
        == [("two() + 1", True), ("4", True)]
    assert chk.op == "==" and [o.text for o in chk.operands] == ["bump()", "1"]
    assert chk.text[chk.operands[1].offset:] == "1)"
    assert req.stops and not chk.stops and req.op == "<"
    assert reg.strings == ["dma.csr"] and [o.text for o in reg.operands] == ["n"]


def test_a_second_fixture_from_one_context_keeps_the_table(suite):
    # pssc test lowers each fixture from one translated context; the rewrite
    # is in place, so the second lowering must reuse the first's table.
    from pssc import driver
    from pssc.testing import sources as _sources
    ctx = driver.translate(_sources.all_sources([str(suite)]))
    first = pv.from_ctx(ctx, "t_f", platform=Recorder()).model.checks
    second = pv.from_ctx(ctx, "t_f", platform=Recorder()).model.checks
    assert len(first) == 4 and second is first


def test_operands_are_captured_once_and_ok_is_the_models(suite):
    p = Recorder()
    s = pv.load([suite], root="t_f", platform=p)
    s.run_test("t_f::t_a", seed=1)
    assert p.caps == [(0, 0, [3, 4]),    # 2 + 1 != 4
                      (1, 1, [1, 1]),    # bump() ran once
                      (2, 1, [1, 5]),
                      (3, 1, [1])]       # the runner decides check_reg


def test_check_capture_records_on_the_ledger():
    sites = {0: check_rewrite.CheckSite(
                 id=0, kind="check", file="f.pss", line=3, col=5,
                 text="check(a == b)", op="==",
                 operands=[check_rewrite.Operand("a", 6),
                           check_rewrite.Operand("b", 11)]),
             1: check_rewrite.CheckSite(
                 id=1, kind="require", file="f.pss", line=4, col=5,
                 text="require(x < 2)", op="<", stops=True, what="small")}
    st = RunState(checks=sites)
    run = imports.IMPORTS["pssunit_pkg::check_operand"]
    cap = imports.IMPORTS["pssunit_pkg::check_capture"]
    run(st, 3)
    run(st, 4)
    cap(st, 0, False)
    assert st.ledger.failures == ["check: a=3 b=4"] and st.operands == []
    run(st, 7)
    run(st, 2)
    with pytest.raises(RequireFailed, match="small"):
        cap(st, 1, False)
    assert st.ledger.n_fail == 2


def test_a_context_without_pssunit_is_left_alone():
    class Ctx:
        import_functions = []
        type_map = {}
    assert check_rewrite.rewrite(Ctx()) == {}


STOPS = """\
import pssunit_pkg::*;

component t_s : fixture_c {
    int n;
    @pssunit_pkg::test
    function void t_b() {
        check_eq(n + 1, 2, "one");
        check_ne(n, 1);
        require(n > 0, "n is set");
        n = 7;
        check(n == 7);
    }
}
"""


def test_the_runner_keeps_the_ledger_and_require_stops(tmp_path):
    f = tmp_path / "stops.pss"
    f.write_text(STOPS)
    s = pv.load([f], root="t_s", platform=pv.Platform())
    n_pass, n_fail = s.run_test("t_s::t_b", seed=1)[:2]
    led = s.run_state.ledger
    assert (n_pass, n_fail) == (1, 2)
    assert led.failures == ["one", "n is set"]
    assert s.stopped == "n is set"
    assert ("check_capture", [2, 0]) in s.imports.calls
    assert all(a != [3, 1] for q, a in s.imports.calls)    # check(n == 7) never ran


def test_each_run_starts_a_new_ledger(tmp_path):
    f = tmp_path / "stops.pss"
    f.write_text(STOPS)
    s = pv.load([f], root="t_s", platform=pv.Platform())
    first = s.run_test("t_s::t_b", seed=1)
    assert s.run_test("t_s::t_b", seed=1) == first
    assert s.run_state.ledger.failures == ["one", "n is set"]


def test_a_register_check_needs_a_double(suite):
    s = pv.load([suite], root="t_f", platform=pv.Platform())
    with pytest.raises(imports.NoDouble, match="check_reg"):
        s.run_test("t_f::t_a", seed=1)
    assert s.run_state.ledger.failures == ["three"]


def test_a_failure_card_shows_operands_and_repro(tmp_path):
    f = tmp_path / "stops.pss"
    f.write_text(STOPS)
    s = pv.load([f], root="t_s", platform=pv.Platform())
    s.run_test("t_s::t_b", seed=7)
    eq, req = s.failure_cards("t_s::t_b")
    line = STOPS[:STOPS.index("check_eq(")].count("\n") + 1
    assert eq.splitlines() == [
        "FAIL %s:%d:9  t_s::t_b  seed=7" % (f, line),
        "  one",
        '  check_eq(n + 1, 2, "one")',
        "           │      │",
        "           1      2",
        "  repro: pssc test t_s::t_b --seed 7"]
    assert req.splitlines()[2:5] == ['  require(n > 0, "n is set")',
                                     "          │   │",
                                     "          0   0"]


def test_a_card_decodes_registers_and_the_bus(tmp_path):
    from .test_pssunit_fake import BASE, FIELD_ACCESS_PSS
    from pssc.testing.report import failure_card
    src = tmp_path / "t.pss"
    src.write_text(FIELD_ACCESS_PSS)
    fake = pv.RegisterFake()
    s = pv.load([src], "t_pkg::dev_c", platform=fake)
    root = s.construct()
    s.call(root, "initialize", BASE)
    for v in (0b110, 0b010, 0b100, 0b001):  # err W1S: kept once set
        s.call(root, "wr_ctl", v)
    site = check_rewrite.CheckSite(
        id=0, kind="check_reg", file="f.pss", line=3, col=5,
        text='check_reg("regs.ctl", 5)', strings=["regs.ctl"],
        operands=[check_rewrite.Operand("5", 22)])
    card = failure_card(site, [5], test="s::t", seed=1, bus=fake.bus, last=2,
                        registers=[fake.decoded("regs.ctl")]).splitlines()
    assert card[4:6] == ["  regs.ctl = 0x00000003 {done=1, err=1, irq=0, rest=0}",
                         "  last bus activity (2 of 4):"]
    assert card[6].startswith("    #3   W32 0x%04x_%04x  regs.ctl = 0x00000004"
                              % (BASE + 4 >> 16, BASE + 4 & 0xffff))
    i = FIELD_ACCESS_PSS.index("regs.ctl.write_val")
    assert card[7].endswith("%s:%d:%d" % (
        src, FIELD_ACCESS_PSS[:i].count("\n") + 1,
        i - FIELD_ACCESS_PSS.rfind("\n", 0, i)))


DOUBLES = """\
import pssunit_pkg::*;

component t_d : fixture_c {
    t_pkg::dev_c dev;
    @pssunit_pkg::setup
    function void bind_dev() { dev.initialize(base("dev")); }
    @pssunit_pkg::test
    function void t_a() {
        status_sequence("dev.regs.st", "irq", 1);
        poke_field("dev.regs.ctl", "irq", 1);
        check_eq(dev.rd_st(), 4, "scripted read");
        check_eq(dev.rd_st(), 0, "then as stored");
        check_field("dev.regs.ctl", "irq", 1, "poked");
    }
    @pssunit_pkg::test
    function void t_b() {
        poke_field("dev.regs.st", "err", 1);
        complete_after("dev.regs.st", "err", 2);
        check_eq(dev.rd_st(), 2, "first read");
        check_eq(dev.rd_st(), 2, "second read");
        check_eq(dev.rd_st(), 0, "cleared after two");
        dev.wr_ctl(5);
        check_written("dev.regs.ctl", "irq", 1, "written");
    }
}
"""


def test_double_calls_with_strings_run_on_bc(tmp_path, monkeypatch, capsys):
    """Platform-double imports take register names: the rewrite turns each
    into ``double_call(id, v)`` / ``double_handle(id)``, and the fake runs
    them (``base``, ``status_sequence``, ``poke_field``, ``complete_after``)."""
    from pssc.cli import main
    from .test_pssunit_fake import FIELD_ACCESS_PSS
    (tmp_path / "dev.pss").write_text(FIELD_ACCESS_PSS)
    (tmp_path / "t.pss").write_text(DOUBLES)
    monkeypatch.chdir(tmp_path)
    assert main(["test", "dev.pss", "t.pss", "--targets", "none", "--no-ctrf",
                 "--platform", "fake"]) == 0, capsys.readouterr().out
    out = capsys.readouterr().out
    assert "3/3 checks" in out and "4/4 checks" in out and "2 passed" in out
