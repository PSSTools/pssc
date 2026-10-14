"""`--emit-manifest`: what the generated API contains, without reading it.

The manifest exists so a consumer does not have to parse generated code, which
means the only test worth much is one that checks the manifest against the
generated code -- for the C target, whose accessors carry the offsets as
literals, and for the Python target, whose accessors can simply be CALLED.

Plan: P8.T2.
"""
from __future__ import annotations

import importlib
import json
import re
import sys

import pytest

from pssc.targets import manifest as m
from pssc.testing import compile_op_model

from .op_model import op_model_sources as wb_dma_sources


@pytest.fixture(scope="module")
def c_manifest(tmp_path_factory):
    """The bundled model through `op-model-c`, with a manifest beside it."""
    out = tmp_path_factory.mktemp("c")
    # vtable: the offset check below reads the baked `_addr` accessors, which
    # the default (mmio) replaces with a layout struct.
    with compile_op_model("op-model-c", output_dir=str(out),
                          c_link_style="vtable",
                          progseq_manifest=str(out / "manifest.json")) as o:
        yield json.loads((out / "manifest.json").read_text()), o


@pytest.fixture(scope="module")
def wb_dma_manifest(tmp_path_factory):
    """The real model: a component tree, channels, imports."""
    out = tmp_path_factory.mktemp("wb")
    with compile_op_model("op-model-c", sources=wb_dma_sources(),
                          root="wb_dma_c", output_dir=str(out),
                          progseq_manifest=str(out / "manifest.json")) as o:
        yield json.loads((out / "manifest.json").read_text()), o


# --- the document -----------------------------------------------------------

def test_nothing_is_written_without_the_flag(tmp_path):
    with compile_op_model("op-model-c", output_dir=str(tmp_path)) as o:
        assert not [n for n in o.names if n.endswith(".json")]


def test_the_manifest_is_in_the_returned_path_list(tmp_path):
    """It is an OUTPUT, so a build system sees it and cleans it."""
    with compile_op_model("op-model-c", output_dir=str(tmp_path),
                          progseq_manifest=str(tmp_path / "m.json")) as o:
        assert o.names[-1] == "m.json"


def test_it_names_and_versions_itself(c_manifest):
    doc, _ = c_manifest
    assert doc["schema"] == m.SCHEMA
    assert doc["version"] == m.VERSION
    assert doc["target"] == "op-model-c"
    assert doc["root"] == "dma_engine_c"


def test_it_round_trips(c_manifest, tmp_path):
    doc, _ = c_manifest
    path = m.write(tmp_path / "again.json", doc)
    assert json.loads(path.read_text()) == doc


def test_it_is_deterministic(tmp_path):
    """A manifest that differs run to run defeats everything that reads it."""
    texts = []
    for i in range(2):
        out = tmp_path / f"run{i}"
        with compile_op_model("op-model-c", output_dir=str(out),
                              progseq_manifest=str(out / "m.json")):
            texts.append((out / "m.json").read_text())
    assert texts[0] == texts[1]


# --- what it says -----------------------------------------------------------

def test_every_operation_appears_with_its_signature(c_manifest):
    doc, _ = c_manifest
    ops = {o["name"]: o for c in doc["components"] for o in c["operations"]}
    assert set(ops) == {"configure_channel", "mem_to_mem_copy",
                        "mem_to_mem_copy_masked", "mem_to_mem_copy_desc"}
    cc = ops["configure_channel"]
    assert cc["returns"] == "void"
    assert [p["name"] for p in cc["params"]] == [
        "channel", "priority", "mode", "src_sel", "dst_sel"]
    assert cc["params"][0]["type"] == "int"
    assert "WITHOUT arming it" in cc["doc"]


def test_a_type_is_the_models_spelling_not_a_languages(c_manifest):
    """`bit[1]`, not `bool` and not `logic`.

    The manifest describes the MODEL. A consumer wanting C types asks the C
    target; one comparing two backends' manifests must not find that they
    disagree about what the model said.
    """
    doc, _ = c_manifest
    cc = [o for c in doc["components"] for o in c["operations"]
          if o["name"] == "configure_channel"][0]
    assert [p["type"] for p in cc["params"]][2:] == ["bit[1]"] * 3


def test_the_constructor_is_reported_separately_from_the_operations(c_manifest):
    """And `--ctor-name`'s answer for this run is in the document.

    The constructor is not an operation -- it takes the address binding and is
    not callable on a live object -- so a consumer enumerating "what can I
    invoke" must not find it in the same list. Which names count is a per-run
    decision, so the run records it rather than leaving a reader to assume the
    default.

    Its address argument reports as `chandle`, not `addr_handle_t`: the PSS
    typedef name does not reach the IR, so `addr_handle_t` would be the
    manifest inventing a name the model no longer carries.
    """
    doc, _ = c_manifest
    comp = doc["components"][0]
    assert comp["constructor"]["name"] == "ctor"
    assert comp["constructor"]["params"] == [{"name": "base",
                                              "type": "chandle"}]
    assert "ctor" not in [o["name"] for o in comp["operations"]]
    assert doc["ctor_names"] == ["ctor", "init", "initialize"]


def test_reserved_registers_are_not_in_the_manifest(c_manifest):
    """The same rule the generated API follows, so the two agree on what
    exists."""
    doc, _ = c_manifest
    names = {r["path"][-1] for c in doc["components"] for r in c["registers"]}
    assert not [n for n in names if n.startswith("_")]


def test_value_structs_carry_their_bit_layout(c_manifest):
    doc, _ = c_manifest
    csr = {s["name"]: s for s in doc["value_structs"]}["dma_ch_csr_s"]
    assert csr["bits"] == 32
    fields = {f["name"]: f for f in csr["fields"]}
    assert (fields["CH_EN"]["lsb"], fields["CH_EN"]["width"]) == (0, 1)
    assert (fields["PRIORITY"]["lsb"], fields["PRIORITY"]["width"]) == (13, 3)


def test_the_tree_channels_and_imports_are_reported(wb_dma_manifest):
    doc, _ = wb_dma_manifest
    by_name = {c["name"]: c for c in doc["components"]}
    assert set(by_name) == {"wb_dma_c", "wb_dma_ch_c"}
    root = by_name["wb_dma_c"]
    assert root["is_root"] and not by_name["wb_dma_ch_c"]["is_root"]
    assert root["sub_components"] == [
        {"name": "ch", "type": "wb_dma_ch_c", "count": 4}]
    assert {c["name"] for c in by_name["wb_dma_ch_c"]["channels"]} == {
        "inflight", "wake"}
    assert all(c["depth"] == 1 for c in by_name["wb_dma_ch_c"]["channels"])


def test_generated_and_runtime_files_are_told_apart(c_manifest):
    doc, _ = c_manifest
    roles = {f["name"]: f["role"] for f in doc["files"]}
    assert roles["dma_engine.h"] == "generated"
    assert roles["dma_engine.c"] == "generated"
    # Same extension, opposite role -- which is why the label is not derived
    # from the name.
    assert roles["pssc_mem.h"] == "runtime"
    assert "manifest.json" not in roles


def test_the_file_order_is_the_order_the_target_returned(c_manifest):
    doc, outcome = c_manifest
    assert [f["name"] for f in doc["files"]] == outcome.names[:-1]


def test_abi_affecting_settings_are_recorded(tmp_path):
    """The options a consumer holding a stale manifest needs to notice."""
    out = tmp_path / "static"
    with compile_op_model("op-model-c", output_dir=str(out),
                          c_lifecycle="static", c_link_style="direct",
                          c_reg_style="accessors",
                          progseq_manifest=str(out / "m.json")):
        s = json.loads((out / "m.json").read_text())["settings"]
    assert s["lifecycle"] == "static"
    assert s["link_style"] == "direct"
    assert s["reg_style"] == "accessors"
    assert s["prefix"] == "dma_engine"


def test_a_target_with_no_abi_options_declares_none(tmp_path):
    """Empty is a legitimate answer, not a gap: the Python target has no option
    that moves a symbol, an offset or a signature."""
    with compile_op_model("op-model-py", output_dir=str(tmp_path),
                          progseq_manifest=str(tmp_path / "m.json")):
        doc = json.loads((tmp_path / "m.json").read_text())
    assert doc["settings"] == {}
    assert doc["target"] == "op-model-py"


# --- the offsets ------------------------------------------------------------
#
# The claim the manifest exists to make. Checked against BOTH backends, because
# a manifest that agreed with one generator and not the other would be worse
# than none.

def test_every_offset_appears_in_the_generated_c(c_manifest):
    doc, outcome = c_manifest
    # The .c: the manifest's job is to describe the addresses the generator
    # baked, and the baked accessors are implementation.
    header = outcome.read("dma_engine.c")
    for comp in doc["components"]:
        for reg in comp["registers"]:
            stem = "_".join(["dma_engine"] + reg["path"]).lower()
            match = re.search(
                rf"{stem}_addr\([^)]*\)\s*\{{\s*return\s+([^;]+);", header,
                re.IGNORECASE)
            assert match, f"no _addr accessor for {reg['path']} in the .c"
            expr = match.group(1)
            assert f"0x{reg['offset']:x}u" in expr, (reg["path"], expr)
            for stride in reg["strides"]:
                assert f"0x{stride:x}u" in expr, (reg["path"], expr)


def test_every_offset_matches_a_live_python_accessor(tmp_path):
    """The manifest against a RUNNING model, which is the strongest form.

    The C check above compares two renderings of the same number. This one
    calls the accessor the generator emitted and compares the address it
    returns, so an error anywhere between the fold and the emitted expression
    fails here.
    """
    with compile_op_model("op-model-py", output_dir=str(tmp_path),
                          progseq_manifest=str(tmp_path / "m.json")) as o:
        doc = json.loads((tmp_path / "m.json").read_text())
        sys.path.insert(0, str(o.out_dir))
        try:
            for name in ("dma_engine", "pssc_rt"):
                sys.modules.pop(name, None)
            rt = importlib.import_module("pssc_rt")
            mod = importlib.import_module("dma_engine")
        finally:
            sys.path.remove(str(o.out_dir))

        base = 0x8000
        dut = mod.DmaEngine(rt.MemoryBus(), base)
        for comp in doc["components"]:
            for reg in comp["registers"]:
                accessor = getattr(dut, "_".join(reg["path"] + ["addr"]))
                idx = [3] * len(reg["strides"])
                expected = base + reg["offset"] + sum(
                    i * s for i, s in zip(idx, reg["strides"]))
                assert accessor(*idx) == expected, reg["path"]


# --- enums -------------------------------------------------------------------
#
# C and SV prefix an item (`DIR_A`), C++ and Python scope it by its enum
# (`dir_e::A`, `dir_e.A`). The manifest says which, so a consumer -- a
# testbench generator, say -- need not know each target's rule.

_ENUMS = """
enum dir_e { A, B = 3 }
enum op_e { A = 7 }
component pss_top {
  target function bit[32] run() {
    dir_e d = B;
    op_e o = A;
    return (bit[32])d + (bit[32])o;
  }
}"""

_SYMBOL = {"c": ("DIR_A", "OP_A"), "cpp": ("dir_e::A", "op_e::A"),
           "py": ("dir_e.A", "op_e.A"), "sv": ("DIR_A", "OP_A")}


@pytest.mark.parametrize("target", sorted(_SYMBOL))
def test_enum_items_carry_their_generated_spelling(tmp_path, target):
    from . import trace_harness as th
    man = tmp_path / "manifest.json"
    out = th.compile_model(tmp_path, _ENUMS, target,
                           progseq_manifest=str(man))
    doc = json.loads(man.read_text())
    enums = {e["name"]: e for e in doc["enums"]}
    assert enums["dir_e"]["items"][0] == {
        "name": "A", "value": 0, "symbol": _SYMBOL[target][0]}
    assert enums["op_e"]["items"] == [
        {"name": "A", "value": 7, "symbol": _SYMBOL[target][1]}]
    # ...and it is the generated code's: the two items the body writes.
    text = "\n".join(p.read_text() for p in out.rglob("*") if p.is_file())
    for sym in (enums["dir_e"]["items"][1]["symbol"],
                enums["op_e"]["items"][0]["symbol"]):
        assert re.search(r"(?<![\w.:])" + re.escape(sym) + r"\b", text), sym
