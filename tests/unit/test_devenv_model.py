"""`pssc model api|regs|explain|tests` (P1.17, T1.17).

T1.17: `explain` of every WB DMA register address (``examples/op_model``)
names the register the manifest places there, and a channel register's
address -- inferred from the layout, the binding being a run-time one --
is the address the VM really accesses for it.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("pssparser")

from pssc import cli
from pssc.devenv import model_cmd

OP_MODEL = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "pss"
FILES = [str(OP_MODEL / re.sub(r"^src/pss/", "", ln.strip()))
         for ln in (OP_MODEL / "files.f").read_text().splitlines()
         if ln.strip() and not ln.startswith("#")]


@pytest.fixture(scope="module")
def doc():
    return model_cmd.load(FILES, "wb_dma_c")[2]


def _manifest_addrs(doc):
    """(path, address) of every root register element, from the manifest
    alone: offset + sum(index * stride)."""
    root = next(c for c in doc["components"] if c["is_root"])
    out = []
    for r in root["registers"]:
        n = r["counts"][0] if r["counts"] else 1
        for i in range(n):
            segs = list(r["path"])
            if r["counts"]:
                segs[r["array_at"][0]] += f"[{i}]"
            out.append((".".join(segs),
                        r["offset"] + (i * r["strides"][0] if r["strides"] else 0)))
    return out


def test_explain_every_address_matches_the_manifest(doc):
    addrs = _manifest_addrs(doc)
    assert len(addrs) == 5 + 4 * 8
    for path, addr in addrs:
        for byte in (addr, addr + 3):
            hits = model_cmd.explain(doc, hex(byte))
            assert [h["path"] for h in hits] == [path]
            assert hits[0]["addr"] == addr


def test_explain_0x88_gives_fields_aliases_and_operations(doc):
    (h,) = model_cmd.explain(doc, "0x88")
    assert h["path"] == "regs.bank[3].adr0"
    assert h["aliases"] == ["ch[3].regs.adr0"]
    assert [f["name"] for f in h["fields"]] == ["addr"]
    ops = {o["op"]: (o["reads"], o["writes"]) for o in h["operations"]}
    assert ops["ch[3].configure_channel"] == (False, True)
    assert all(op.startswith("ch[3].") for op in ops)
    (csr,) = model_cmd.explain(doc, "regs.csr")
    assert {o["op"] for o in csr["operations"]} == {"pause_engine"}


def test_explain_by_path(doc):
    (h,) = model_cmd.explain(doc, "ch[1].regs.csr", base=0x1000)
    assert h["addr"] == 0x1040 and h["via"] == "layout"
    assert len(model_cmd.explain(doc, "ch.regs.csr")) == 4
    assert model_cmd.explain(doc, "0x5000") == []


def test_channel_addresses_are_the_ones_the_vm_accesses(doc):
    pytest.importorskip("zuspec.be.bc")
    from zuspec.be.bc.trace import MemorySink
    from zuspec.be.bc.trace import events as ev

    from pssc import driver
    from pssc.testing import bc_session, sources

    ctx = driver.translate([str(x) for x in sources.all_sources(FILES)])
    model = bc_session.lower(ctx, "wb_dma_c", functions=["*"], debug=True)
    sink = MemorySink()
    s = bc_session.Session(model, events=sink, watch=ev.EV_ACCESS)
    root = s.construct()
    s.call(root, "initialize", 0x10000000)
    for i in range(4):
        ch = s.instance(f"ch[{i}]")
        s.call(ch, "configure_channel",
               *[1] * len(model.entries["wb_dma_ch_c::configure_channel"].params))
    by_path = {i["path"]: i for i in model_cmd.instances(doc, 0x10000000)}
    seen = 0
    for e in sink.events:
        if e.kind != "access" or "reg_id" not in e.detail:
            continue
        d = bc_session.decode_access(model, e.detail)
        reg = model.registers[d["reg_id"]]
        assert by_path[reg["path"]]["addr"] == d["addr"], reg["path"]
        seen += reg["comp"].startswith("ch[")
    assert seen >= 4 * 6


def test_cli_json_documents(doc, capsys):
    assert cli.main(["model", "explain", "0x88", *FILES, "--root", "wb_dma_c",
                     "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == "pssc.model/1"
    assert out["registers"][0]["path"] == "regs.bank[3].adr0"
    assert cli.main(["model", "regs", *FILES, "--root", "wb_dma_c",
                     "--json"]) == 0
    regs = json.loads(capsys.readouterr().out)["registers"]
    assert len(regs) == 5 + 4 * 8 + 4 * 8
    assert cli.main(["model", "api", *FILES, "--root", "wb_dma_c",
                     "--json"]) == 0
    api = json.loads(capsys.readouterr().out)
    ch = next(c for c in api["components"] if c["name"] == "wb_dma_ch_c")
    op = next(o for o in ch["operations"] if o["name"] == "set_software_pointer")
    assert op["writes"] == ["regs.swptr"]
    assert cli.main(["model", "explain", "0x5000", *FILES,
                     "--root", "wb_dma_c"]) == 1
    assert cli.main(["model", "regs", "nothing_here.pss",
                     "--root", "wb_dma_c"]) == 2


@pytest.mark.parametrize("cmd", [["api"], ["regs"], ["explain", "0x88"],
                                 ["explain", "regs.bank[3].adr0"]])
def test_json_validates_against_schema(cmd, capsys):
    """T-G5: real `pssc model ... --json` output against pssc.model/1."""
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((OP_MODEL.parents[2] / "docs/schemas/pssc.model-1.json")
                        .read_text())
    jsonschema.Draft202012Validator.check_schema(schema)
    assert cli.main(["model", *cmd, *FILES, "--root", "wb_dma_c", "--json"]) == 0
    jsonschema.validate(json.loads(capsys.readouterr().out), schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"schema": "pssc.model/1", "command": "regs",
                             "root": "x", "base": 0, "registers": [{}]}, schema)
