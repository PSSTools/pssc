"""T2.6: the design's reaction-hook mock (design §7.2) on the WB DMA suite's
fixture. The hook fires on the driver's ``ch_en`` write, its
``schedule_after`` runs ``finish`` two ticks on, which sets CSR.done and
raises the interrupt that wakes the waiting driver; a typo in a hook's
register path is a located error."""
import pathlib
import shutil

from .test_wb_dma_suite import EXAMPLE, _run

MOCK_PSS = '''import pssunit_pkg::*;
import wb_dma_types_pkg::*;

component wb_dma_mock_c : pssunit_pkg::mock_c {
    @pssunit_pkg::on_write {.reg = "wb_dma_ch_regs_c.csr", .field = "ch_en"}
    target function void armed(pssunit_pkg::access_s a) {
        pssunit_pkg::schedule_after(2, "done", a.index);
    }
    @pssunit_pkg::on_event {.name = "done"}
    target function void finish(int ch) {
        pssunit_pkg::poke_field_at(ch, "wb_dma_ch_regs_c.csr", "done", 1);
        pssunit_pkg::raise_irq();
    }
}

extend component wb_dma_fx_c {
    wb_dma_mock_c mock;

    @pssunit_pkg::test
    function void t_copy_done_by_mock() {
        wb_dma_ch_cfg_s cfg;
        cfg.src = 0x1000;
        cfg.dst = 0x2000;
        cfg.tot_sz = 16;
        cfg.mode = WB_DMA_SOFTWARE;
        check(dma.ch[2].transfer_single(cfg) == WB_DMA_DONE, "the mock completes the copy");
        check_field("dma.ch[2].regs.csr", "done", 1, "done set by the mock");
    }
}
'''


def _suite(tmp_path: pathlib.Path, mock: str = MOCK_PSS) -> pathlib.Path:
    # the fixture and the device, on a target that waits for an event: the
    # interrupt (``notify_irq``) is what wakes the driver's wait
    shutil.copytree(EXAMPLE / "pss", tmp_path / "pss")
    tests = tmp_path / "tests"
    tests.mkdir()
    for name in ("model.f", "wb_dma_fx_c.pss"):
        shutil.copy(EXAMPLE / "tests" / name, tests / name)
    cfg = (EXAMPLE / "tests" / "target_cfg.pss").read_text()
    old = "HAVE_EVENT_WAIT     = false"
    assert old in cfg
    (tests / "target_cfg.pss").write_text(
        cfg.replace(old, "HAVE_EVENT_WAIT     = true"))
    (tests / "hooks_a.pss").write_text(mock)
    return tests


def test_the_mock_completes_a_copy(tmp_path):
    rc, events = _run(_suite(tmp_path))
    ends = [e for e in events if e["event"] == "test.end"]
    assert [(e["id"], e["status"]) for e in ends] == [
        ("wb_dma_fx_c::t_copy_done_by_mock", "pass")], ends
    assert rc == 0


def test_a_typo_in_the_register_path_is_located(tmp_path):
    bad = MOCK_PSS.replace('"wb_dma_ch_regs_c.csr", .field', '"wb_dma_ch_regs_c.cs", .field')
    rc, events = _run(_suite(tmp_path, bad))
    (end,) = [e for e in events if e["event"] == "test.end"]
    line = bad[:bad.index("@pssunit_pkg::on_write")].count("\n") + 1
    assert end["status"] == "error"
    assert end["message"].startswith(f"hooks_a.pss:{line}:"), end["message"]
    assert "'wb_dma_ch_regs_c.cs' names no register" in end["message"]
    assert rc != 0
