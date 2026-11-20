"""Register path on every access (P1.7, T1.7).

Debug lowering records which register each register-access ``IMPORT`` is
for; the VM's ``access`` event carries it as ``reg_id`` (the register's
component instance), and ``bc_session.decode_access`` names the register and
its fields from the same ``reg_layout`` walk the manifest's ``registers``
come from. T1.7: in a WB DMA run (``examples/op_model``), every register
access decodes to the register the manifest places at its address.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.targets.manifest import _registers as manifest_registers
from pssc.testing import bc_session, sources

OP_MODEL = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "pss"
BASE = 0x10000000
CH_FNS = ["configure_channel", "transfer_single_start", "probe_status",
          "stop_channel_start", "check_completion", "set_auto_restart",
          "set_software_pointer"]
ROOT_FNS = ["configure_interrupt_routing", "pause_engine", "write_descriptor",
            "read_descriptor_residual", "notify_irq"]


@pytest.fixture(scope="module")
def wb_dma():
    files = [str(OP_MODEL / re.sub(r"^src/pss/", "", ln.strip()))
             for ln in (OP_MODEL / "files.f").read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    ctx = driver.translate([str(x) for x in sources.all_sources(files)])
    assert not ctx.errors
    return ctx, bc_session.lower(ctx, "wb_dma_c", functions=["*"], debug=True)


def _run(model):
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev

    sink = MemorySink()
    s = bc_session.Session(model, events=sink, watch=ev.EV_ACCESS)
    root = s.construct()
    s.call(root, "initialize", BASE)
    for fn in ROOT_FNS:
        s.call(root, fn, *[1] * len(model.entries["wb_dma_c::" + fn].params))
    for i in range(4):
        ch = s.instance(f"ch[{i}]")
        for fn in CH_FNS:
            s.call(ch, fn, *[1] * len(model.entries["wb_dma_ch_c::" + fn].params))
    return [e.detail for e in sink.events if e.kind == "access"]


def _addr(regs, path: str, base: int) -> int:
    """``base`` + the manifest's offset of register ``path`` (indices folded
    with its strides)."""
    key = [re.sub(r"\[\d+\]$", "", s) for s in path.split(".")]
    idx = [int(k) for k in re.findall(r"\[(\d+)\]", path)]
    r = next(r for r in regs if r["path"] == key)
    return base + r["offset"] + sum(k * s for k, s in zip(idx, r["strides"]))


def test_every_register_access_decodes_to_the_manifest_register(wb_dma):
    ctx, model = wb_dma
    root_regs = manifest_registers(ctx.type_map["wb_dma_c"])
    ch_regs = manifest_registers(ctx.type_map["wb_dma_ch_c"])
    acc = _run(model)
    decoded = [bc_session.decode_access(model, d) for d in acc if "reg_id" in d]
    assert len(decoded) > 50
    seen = set()
    for d in decoded:
        reg = model.registers[d["reg_id"]]
        m = re.fullmatch(r"ch\[(\d+)\]", reg["comp"])
        if reg["comp"] == "":
            want = _addr(root_regs, reg["reg"], BASE)
        else:
            # a channel's group is bound to the device's bank[i]
            bank = _addr(root_regs, f"regs.bank[{m.group(1)}].csr", BASE)
            want = _addr(ch_regs, reg["reg"], bank - _addr(ch_regs, "regs.csr", 0))
        assert d["addr"] == want, d
        assert d["width"] == reg["width"]
        seen.add(reg["comp"].split("[")[0])
    assert seen == {"", "ch"}


def test_plain_memory_accesses_carry_no_register(wb_dma):
    _, model = wb_dma
    plain = [d for d in _run(model) if "reg_id" not in d]
    assert plain                       # write_descriptor's write32()s
    assert all(bc_session.decode_access(model, d) == d for d in plain)


def test_fields_decode_from_the_data(wb_dma):
    _, model = wb_dma
    d = next(bc_session.decode_access(model, d) for d in _run(model)
             if d.get("reg_id") is not None and d["rw"] == "w"
             and model.registers[d["reg_id"]]["path"] == "regs.csr")
    assert d["reg"] == "regs.csr" and d["fields"]["pause"] == d["data"] & 1


def test_release_lowering_records_no_registers(wb_dma):
    ctx, _ = wb_dma
    m = bc_session.lower(ctx, "wb_dma_c", functions=["*"])
    assert not hasattr(m, "registers")
    assert not any(c.regs for c in m.coros)


def test_trace_file_names_registers_and_statement_dataflow(wb_dma):
    # P5.3a: the NDJSON trace carries `reg`/`fields` on register accesses
    # and `defs`/`uses` on statements, for `pssc trace why`.
    import io
    import json

    from pssc.testing.trace import TraceWriter

    _, model = wb_dma
    out = io.StringIO()
    w = TraceWriter(out, model, test="t", seed=1)
    s = bc_session.Session(model, events=w, watch=w.watch)
    root = s.construct()
    s.call(root, "initialize", BASE)
    s.call(s.instance("ch[1]"), "configure_channel",
           *[1] * len(model.entries["wb_dma_ch_c::configure_channel"].params))
    rows = [json.loads(x) for x in out.getvalue().splitlines()[1:]]
    acc = [r["detail"] for r in rows if r["kind"] == "access" and "reg_id" in r["detail"]]
    assert acc and all("reg" in d for d in acc)
    assert any(d["reg"].startswith("ch[1].") and "fields" in d for d in acc)
    stmts = [r["detail"] for r in rows if r["kind"] == "stmt"]
    assert any(d.get("uses") for d in stmts) and any(d.get("defs") for d in stmts)
    # `csr.mode = cfg.mode;` (a struct parameter's fields are named locals)
    assert any(d.get("defs") == ["l:csr.mode"] and d.get("uses") == ["l:cfg.mode"]
               for d in stmts)
