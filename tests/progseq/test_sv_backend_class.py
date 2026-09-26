"""`SvOpModelBackend`: the SV package as a backend class.

The two rules `COpModelBackend` and `PyOpModelBackend` hold, held here too:
every `emit_*` returns text and only `generate` writes, and the package is
exactly its `package_sections()`. Byte-identity with what the functional
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

from .op_model import op_model_sources


@pytest.fixture(scope="module")
def ctx():
    tgt = targets.get("op-model-sv")
    return driver.translate(op_model_sources(),
                            prelude=tgt.prelude(argparse.Namespace()))


@pytest.fixture
def op_model(ctx, tmp_path):
    return om.elaborate(ctx, ctx.type_map["wb_dma_c"], tmp_path / "out")


def test_no_emit_method_writes_files(op_model):
    be = SvOpModelBackend("wb_dma_c_pkg")
    for _, emit in be.package_sections(op_model):
        emit(op_model)
    be.emit_package(op_model)
    be.sections(op_model)
    assert not op_model.out_dir.exists()


def test_generate_is_the_only_writer(op_model):
    written = SvOpModelBackend("wb_dma_c_pkg").generate(op_model)
    assert [p.name for p in written] == ["wb_dma_c_pkg.sv"]
    assert written[0].read_text() == \
        SvOpModelBackend("wb_dma_c_pkg").emit_package(op_model)


def test_the_package_is_exactly_its_sections(op_model):
    be = SvOpModelBackend("wb_dma_c_pkg")
    lines = []
    for _, emit in be.package_sections(op_model):
        lines += emit(op_model)
    assert be.emit_package(op_model) == "\n".join(lines)


def test_sections_are_keyed_by_file(op_model):
    keys = list(SvOpModelBackend("wb_dma_c_pkg").sections(op_model))
    assert keys[0] == "wb_dma_c_pkg.sv:banner"
    assert "wb_dma_c_pkg.sv:root" in keys


def test_package_name_defaults_to_the_root(op_model):
    assert SvOpModelBackend().file_name(op_model) == "wb_dma_c_pkg.sv"


def test_a_subclass_can_insert_a_section(op_model):
    class _Acme(SvOpModelBackend):
        def package_sections(self, model):
            return insert_after(super().package_sections(model), "banner",
                                Section("acme", lambda m: ["// ACME"]))

    plain = SvOpModelBackend("p").emit_package(op_model)
    acme = _Acme("p").emit_package(op_model)
    assert acme.replace("// ACME\n", "", 1) == plain


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
