"""P5.8: a test can expect a sequence of bus accesses (T5.8).

``expect_write``/``expect_read`` queue the accesses a test expects;
``check_seq`` matches them in order against the bus log, with other accesses
allowed between, and ``check_seq_strict`` allows none. Opt-in: a test that
calls neither is not checked, but expectations no ``check_seq`` checks fail.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")

from pssc.testing import imports
from pssc.testing.imports import RunState


SEQ = """\
import pssunit_pkg::*;

component t_seq : fixture_c {
    t_pkg::dev_c dev;
    @pssunit_pkg::setup
    function void bind_dev() { dev.initialize(base("dev")); }

    @pssunit_pkg::test
    function void t_in_order() {
        expect_write("dev.regs.ctl", "irq", 1);
        expect_read("dev.regs.st");
        dev.wr_ctl(4);
        dev.wr_sts(0);
        dev.rd_st();
        check_seq("start, then poll");
    }
"""

SEQ_BAD = """\
    @pssunit_pkg::test
    function void t_strict_extra() {
        expect_write("dev.regs.ctl", "", 4);
        dev.wr_ctl(4);
        dev.rd_st();
        check_seq_strict();
    }
    @pssunit_pkg::test
    function void t_out_of_order() {
        expect_read("dev.regs.st");
        expect_write("dev.regs.ctl", "irq", 1);
        dev.wr_ctl(4);
        dev.rd_st();
        check_seq();
    }
    @pssunit_pkg::test
    function void t_never_checked() {
        expect_read("dev.regs.st");
    }
"""


def _run(tmp_path, monkeypatch, src: str, *extra: str):
    from pssc.cli import main
    from .test_pssunit_fake import FIELD_ACCESS_PSS
    (tmp_path / "dev.pss").write_text(FIELD_ACCESS_PSS)
    (tmp_path / "t.pss").write_text(src)
    monkeypatch.chdir(tmp_path)
    return main(["test", "dev.pss", "t.pss", "--targets", "none", "--no-ctrf",
                 "--platform", "fake", *extra])


def test_expected_accesses_in_order_pass(tmp_path, monkeypatch, capsys):
    assert _run(tmp_path, monkeypatch, SEQ + "}\n") == 0, capsys.readouterr().out
    assert "1 passed" in capsys.readouterr().out


@pytest.mark.parametrize("engine", ["py", "c"])
def test_extra_missing_and_unchecked_accesses_fail(tmp_path, monkeypatch, capsys,
                                                    engine):
    """Each failure says why, on its card; the same on the C engine."""
    if engine == "c":
        pytest.importorskip("zuspec.rt.eng.host")
    assert _run(tmp_path, monkeypatch, SEQ + SEQ_BAD + "}\n",
                "--engine", engine) != 0
    out = capsys.readouterr().out
    assert "1 passed" in out and "3 failed" in out, out
    assert "check_seq_strict: unexpected access 2 (read dev.regs.st)" in out
    assert "check_seq: expected access 2 (write dev.regs.ctl.irq == 0x1) " \
           "did not happen" in out
    assert "1 expected access(es) never checked, from read dev.regs.st" in out


class _Bus:
    """The bus log and names of a ``RegisterFake``, for the imports alone."""

    def __init__(self):
        self.bus = []

    def _rid(self, reg):
        return 0

    def _field(self, reg, fld):
        return {"lsb": 4, "width": 4}


def test_check_seq_matches_from_the_last_check():
    st = RunState(double=_Bus())
    call = lambda n, *a: imports.IMPORTS["pssunit_pkg::" + n](st, *a)
    st.double.bus += [{"op": "write", "reg": "r", "data": 0x30, "addr": 0}]
    call("expect_write", "r", "f", 3)
    call("check_seq", "")
    call("expect_write", "r", "f", 3)          # the write above is used up
    call("check_seq", "again")
    assert (st.ledger.n_pass, st.ledger.failures) == (1, ["again"])
