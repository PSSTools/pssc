"""`pssc trace why` (P5.3b, T5.3): where a register value came from.

A WB DMA run (``examples/op_model``, debug image) writes a full trace;
``why`` follows ``ch[1].regs.csr.mode`` back to ``cfg.mode``, the
operation's input, and ``adr0``'s base address to ``initialize``'s
``bank`` argument, across frames.
"""
from __future__ import annotations

import io
import json
import pathlib

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.cli import main
from pssc.testing import bc_session

from .test_bc_reg_access import BASE, wb_dma  # noqa: F401 -- fixture

REPO = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def trace(wb_dma, tmp_path_factory):  # noqa: F811
    from pssc.testing.trace import TraceWriter
    _, model = wb_dma
    out = io.StringIO()
    w = TraceWriter(out, model, test="t", seed=1)
    s = bc_session.Session(model, events=w, watch=w.watch)
    s.call(s.construct(), "initialize", BASE)
    n = len(model.entries["wb_dma_ch_c::configure_channel"].params)
    s.call(s.instance("ch[1]"), "configure_channel", *range(1, n + 1))
    f = tmp_path_factory.mktemp("why") / "t.ndjson"
    f.write_text(out.getvalue())
    return f


def _why(capsys, *args):
    capsys.readouterr()
    rc = main(["trace", "why", *map(str, args)])
    return rc, capsys.readouterr()


def test_field_follows_back_to_the_operation_input(trace, capsys):
    rc, out = _why(capsys, trace, "ch[1].regs.csr.mode", "--json")
    assert rc == 0, out.err
    doc = json.loads(out.out)
    assert doc["schema"] == "pssc.trace-why/1"
    t = doc["target"]
    assert (t["reg"], t["field"], t["value"]) == ("ch[1].regs.csr", "mode", 1)
    assert t["file"].endswith("configure_channel.pss") and doc["stmt"]["line"] == t["line"]
    # only the field's own value is followed: csr.mode = cfg.mode; cfg.mode = input
    toks = [(n["token"], n["kind"]) for n in doc["chain"]]
    assert toks == [("l:csr.mode", "stmt"), ("l:cfg.mode", "input")]
    assert "cfg.mode" in doc["chain"][0]["text"]
    assert doc["chain"][1]["via"] == 0


def test_component_field_is_followed_across_frames(trace, capsys):
    rc, out = _why(capsys, trace, "ch[1].regs.adr0", "--json")
    assert rc == 0, out.err
    chain = json.loads(out.out)["chain"]
    h = next(n for n in chain if n["token"] == "c:ch[1].regs.$handle")
    assert h["file"].endswith("wb_dma_ch_c.pss")
    bank = [n for n in chain if n["via"] == chain.index(h)]
    assert [(n["token"], n["kind"]) for n in bank] == [("l:bank", "input")]
    assert bank[0]["fn"].startswith("initialize") and len(bank[0]["args"]) == 2
    assert ("l:cfg.src", "input") in [(n["token"], n["kind"]) for n in chain]
    # text form
    rc, out = _why(capsys, trace, "ch[1].regs.adr0")
    assert rc == 0 and "l:bank: operation input (call initialize" in out.out


def test_trace_without_statements_gives_the_access_only(trace, tmp_path, capsys):
    rows = trace.read_text().splitlines()
    bare = tmp_path / "c.ndjson"
    bare.write_text("\n".join(r for r in rows if '"kind":"stmt"' not in r) + "\n")
    rc, out = _why(capsys, bare, "ch[1].regs.csr", "--json")
    assert rc == 0, out.err
    doc = json.loads(out.out)
    assert doc["stmt"] is None and doc["chain"] == [] and "no stmt events" in doc["note"]


def test_unknown_register_names_the_written_ones(trace, capsys):
    rc, out = _why(capsys, trace, "ch[1].regs.nope")
    assert rc == 2 and "ch[1].regs.csr" in out.err


def test_schema_is_listed_in_help(capsys):
    from pssc.devenv.help_cmd import OUTPUTS
    assert OUTPUTS[("trace", "why")][0]["schema"] == "pssc.trace-why/1"
    s = json.loads((REPO / "docs/schemas/pssc.trace-why-1.json").read_text())
    assert s["$id"] == "pssc.trace-why/1"


def _run(capsys, *args):
    capsys.readouterr()
    rc = main(["trace", *map(str, args)])
    return rc, capsys.readouterr()


def test_slice_lists_each_access_with_its_statement(trace, capsys):
    rc, out = _run(capsys, "slice", trace, "--reg", "ch[1].regs.csr", "--json")
    assert rc == 0, out.err
    doc = json.loads(out.out)
    assert doc["schema"] == "pssc.trace-slice/1" and doc["reg"] == "ch[1].regs.csr"
    acc = doc["accesses"]
    assert acc and [a["index"] for a in acc] == sorted(a["index"] for a in acc)
    w = [a for a in acc if a["rw"] == "w"]
    assert w[-1]["fields"]["mode"] == 1
    assert w[-1]["stmt"]["file"].endswith("configure_channel.pss")
    assert w[-1]["stmt"]["index"] < w[-1]["index"]
    rc, out = _run(capsys, "slice", trace, "--reg", "ch[1].regs.csr")
    assert rc == 0 and "mode=1" in out.out and "configure_channel.pss" in out.out
    rc, out = _run(capsys, "slice", trace, "--reg", "ch[1].regs.nope")
    assert rc == 2 and "ch[1].regs.csr" in out.err


def _second(wb_dma, path, first):  # noqa: F811
    from pssc.testing.trace import TraceWriter
    _, model = wb_dma
    out = io.StringIO()
    w = TraceWriter(out, model, test="t", seed=2)
    s = bc_session.Session(model, events=w, watch=w.watch)
    s.call(s.construct(), "initialize", BASE)
    n = len(model.entries["wb_dma_ch_c::configure_channel"].params)
    s.call(s.instance("ch[1]"), "configure_channel", first, *range(2, n + 1))
    path.write_text(out.getvalue())
    return path


def test_diff_names_the_first_divergent_access(trace, wb_dma, tmp_path,  # noqa: F811
                                               capsys):
    same = _second(wb_dma, tmp_path / "same.ndjson", 1)
    rc, out = _run(capsys, "diff", trace, same, "--json")
    assert rc == 0, out.err
    doc = json.loads(out.out)
    assert doc["schema"] == "pssc.trace-diff/1" and doc["divergence"] is None
    assert doc["a"]["steps"] == doc["b"]["steps"] > 0 and doc["b"]["seed"] == 2
    other = _second(wb_dma, tmp_path / "b.ndjson", 3)
    rc, out = _run(capsys, "diff", trace, other, "--json")
    assert rc == 1, out.err
    dv = json.loads(out.out)["divergence"]
    a, b = dv["a"], dv["b"]
    assert dv["step"] >= 1 and a["kind"] == b["kind"] == "access"
    assert (a["addr"], a["rw"]) == (b["addr"], b["rw"]) and a["data"] != b["data"]
    assert a["file"] == b["file"] and a["index"] is not None
    rc, out = _run(capsys, "diff", trace, other)
    assert rc == 1 and f"first divergence at step {dv['step']}" in out.out


def test_diff_of_a_run_that_ends_early(trace, tmp_path, capsys):
    rows = trace.read_text().splitlines()
    last = max(i for i, r in enumerate(rows) if '"kind":"access"' in r)
    short = tmp_path / "short.ndjson"
    short.write_text("\n".join(rows[:last]) + "\n")
    rc, out = _run(capsys, "diff", trace, short, "--json")
    assert rc == 1, out.err
    dv = json.loads(out.out)["divergence"]
    assert dv["b"] is None and dv["a"]["kind"] == "access"
    assert dv["step"] == json.loads(out.out)["a"]["steps"]


@pytest.mark.parametrize("cmd", ["slice", "diff"])
def test_slice_and_diff_schemas_are_listed_in_help(cmd):
    from pssc.devenv.help_cmd import OUTPUTS
    o = OUTPUTS[("trace", cmd)][0]
    assert o["schema"] == f"pssc.trace-{cmd}/1"
    assert json.loads((REPO / o["file"]).read_text())["$id"] == o["schema"]
