"""`SvOpModelBackend`: the SV package as a backend class.

The two rules `COpModelBackend` and `PyOpModelBackend` hold, held here too:
every `emit_*` returns text and only `generate` writes, and the package file is
exactly its `wrapper_sections()`, each include file exactly one of its
`package_sections()`. Byte-identity with what the functional
generator produced is the golden snapshots' job (`sv-named`, `sv-folded`).
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from pssc import driver, targets
from pssc.targets import op_model as om
from pssc.targets.sections import Section, insert_after
from pssc.targets.sv.backend import SvOpModelBackend

from .op_model import op_model_sv_sources


@pytest.fixture(scope="module")
def ctx():
    tgt = targets.get("op-model-sv")
    return driver.translate(op_model_sv_sources(),
                            prelude=tgt.prelude(argparse.Namespace()))


@pytest.fixture
def op_model(ctx, tmp_path):
    return om.elaborate(ctx, ctx.type_map["wb_dma_c"], tmp_path / "out")


def test_no_emit_method_writes_files(op_model):
    be = SvOpModelBackend("wb_dma_c_pkg")
    for _, emit in be.package_sections(op_model) + be.wrapper_sections(op_model):
        emit(op_model)
    be.emit_package(op_model)
    be.emit_files(op_model)
    be.sections(op_model)
    assert not op_model.out_dir.exists()


def test_generate_is_the_only_writer(op_model):
    written = SvOpModelBackend("wb_dma_c_pkg").generate(op_model)
    files = SvOpModelBackend("wb_dma_c_pkg").emit_files(op_model)
    assert [p.name for p in written] == [n for n, _ in files]
    assert [p.read_text() for p in written] == [t for _, t in files]


def test_one_file_per_class(op_model):
    """The package includes one file per class; the register model is one
    file, the constants another (user decisions D1, D4)."""
    names = [n for n, _ in SvOpModelBackend("wb_dma_c_pkg").emit_files(op_model)]
    assert names == [
        "wb_dma_c_pkg.sv", "wb_dma_c_consts.svh", "wb_dma_c_regs.svh",
        "wb_dma_c_types.svh", "wb_dma_c_imp_if.svh", "wb_dma_ch_c_exp_if.svh",
        "wb_dma_c_exp_if.svh", "wb_dma_c_component.svh", "wb_dma_ch_c.svh",
        "wb_dma_c.svh", "wb_dma_ch_c_exp_impl.svh", "wb_dma_c_root.svh"]
    texts = dict(SvOpModelBackend("wb_dma_c_pkg").emit_files(op_model))
    for name, text in texts.items():
        classes = [l for l in text.splitlines()
                   if l.lstrip().startswith(("class ", "virtual class ",
                                             "interface class "))]
        if name == "wb_dma_c_regs.svh":
            assert len(classes) == 2, classes        # every group, together
        elif name.endswith(".svh") and name not in (
                "wb_dma_c_consts.svh", "wb_dma_c_types.svh"):
            assert len(classes) == 1, (name, classes)
        else:
            assert classes == [], (name, classes)


def test_the_package_is_exactly_its_wrapper_sections(op_model):
    be = SvOpModelBackend("wb_dma_c_pkg")
    lines = []
    for _, emit in be.wrapper_sections(op_model):
        lines += emit(op_model)
    assert be.emit_package(op_model) == "\n".join(lines)


def test_each_include_file_is_one_section(op_model):
    be = SvOpModelBackend("wb_dma_c_pkg")
    texts = dict(be.include_files(op_model))
    for sec in be.package_sections(op_model):
        body = "\n".join(sec.emit(op_model)).rstrip()
        name = be.include_name(op_model, sec.name)
        if not body.strip():
            assert name not in texts           # nothing in it: no file
            continue
        assert body in texts[name]
        assert "`ifndef" not in texts[name]          # no include guard
    includes = [l.strip() for l in be.emit_includes(op_model) if l.strip()]
    assert includes == [f'`include "{n}"' for n in texts]


def test_sections_are_keyed_by_file(op_model):
    keys = list(SvOpModelBackend("wb_dma_c_pkg").sections(op_model))
    assert keys[0] == "wb_dma_c_pkg.sv:banner"
    assert "wb_dma_c_root.svh:factory" in keys
    assert "wb_dma_ch_c.svh:class:wb_dma_ch_c" in keys


def test_package_name_defaults_to_the_root(op_model):
    assert SvOpModelBackend().file_name(op_model) == "wb_dma_c_pkg.sv"


def test_a_subclass_can_insert_a_section(op_model):
    class _Acme(SvOpModelBackend):
        def package_sections(self, model):
            return insert_after(super().package_sections(model), "api_types",
                                Section("acme", lambda m: ["  // ACME"]))

    plain = dict(SvOpModelBackend("p").emit_files(op_model))
    acme = dict(_Acme("p").emit_files(op_model))
    assert "  // ACME" in acme.pop("wb_dma_c_acme.svh")
    assert acme.pop("p.sv").replace('  `include "wb_dma_c_acme.svh"\n', "",
                                    1) == plain.pop("p.sv")
    assert acme == plain


def test_the_target_generates_through_its_backend_class(op_model, monkeypatch):
    """`backend_cls` is the hook a derived target uses to swap the class."""
    class _Marked(SvOpModelBackend):
        def emit_banner(self, model):
            return ["// MARKED"] + super().emit_banner(model)

    tgt = targets.get("op-model-sv")
    monkeypatch.setattr(type(tgt), "backend_cls", _Marked)
    ns = argparse.Namespace(progseq_root="wb_dma_c", progseq_package=None,
                            progseq_reg_fields="named")
    text = tgt.backend_for(ns).emit_package(op_model)
    assert text.startswith("// MARKED\n")
