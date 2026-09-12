"""Phase 6/7: cpp-progseq registration, arg parsing, generation structure."""
import argparse
import os

import pytest

from pssc import driver
from pssc import targets as _targets

from .op_model import small_model_dir, small_model_sources

_DATA = small_model_dir()
_SRCS = small_model_sources()


def _gen(out_dir, dispatch="virtual", namespace="wb_dma"):
    ns = argparse.Namespace(progseq_root="dma_engine_c", cpp_namespace=namespace,
                            cpp_dispatch=dispatch, cpp_single_header=True,
                            progseq_core_copy=True, output_dir=str(out_dir))
    return driver.compile(_SRCS, target="cpp-progseq", opts=ns)


def test_registered():
    assert "op-model-cpp" in _targets.list_targets()
    # `cpp-progseq` / `progseq-cpp` are back-compat aliases for `op-model-cpp`.
    assert _targets.get("progseq-cpp") is _targets.get("op-model-cpp")
    assert _targets.get("cpp-progseq") is _targets.get("op-model-cpp")


def test_missing_root():
    with pytest.raises(ValueError, match="requires --root"):
        driver.compile(_SRCS, target="cpp-progseq",
                       opts=argparse.Namespace(progseq_root=None, output_dir="/tmp/x"))


def test_template_dispatch_not_yet(tmp_path):
    with pytest.raises(NotImplementedError, match="template"):
        _gen(tmp_path, dispatch="template")


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    out = tmp_path_factory.mktemp("cpp_gen")
    _gen(out)
    return out


def _hpp(d):
    return (d / "wb_dma.hpp").read_text()


def test_outputs(gen):
    assert (gen / "wb_dma.hpp").is_file()
    assert (gen / "pssc_reg.hpp").is_file()       # core copied alongside


def test_value_unions_and_groups(gen):
    h = _hpp(gen)
    assert "namespace wb_dma {" in h
    assert "typedef union { std::uint32_t raw; struct {" in h
    assert "class dma_channel_regs_c {" in h
    assert "pssc::reg<dma_ch_csr_t> CSR;" in h
    # READONLY mapped to access::ro
    assert "pssc::reg<std::uint32_t, pssc::access::ro> INT_SRC_A;" in h
    # array of groups via std::array + index helper
    assert "std::array<dma_channel_regs_c, 31> channels;" in h
    assert "std::make_index_sequence<31>" in h


def test_apis_and_component(gen):
    h = _hpp(gen)
    assert "struct wb_dma_if {" in h
    assert "virtual int mem_to_mem_copy(int channel," in h
    # The platform seam is named uniformly in every signature. This model
    # declares no imports, so the name IS pssc::mem_if -- see emit_import_api.
    assert "using wb_dma_import_if = pssc::mem_if;" in h
    assert "class wb_dma : public wb_dma_if {" in h
    assert "wb_dma_import_if &imp_;" in h
    assert "std::unique_ptr<wb_dma_if> create(wb_dma_import_if &imp, " \
        "pssc::addr_t base)" in h
    # native member-call body + native do...while
    assert "this->regs.channels[channel].CSR.read();" in h
    assert "do {" in h and "} while (" in h


def test_construction_is_two_phase(gen):
    """The constructor takes the seam; the PSS constructor is `initialize`.

    It has to be, and the reason is the tree rather than this model: a parent
    computes its children's base addresses in its own constructor BODY, which
    runs after the children -- as members -- already exist. `create()` does both
    for the root, which is what a caller normally wants.
    """
    h = _hpp(gen)
    assert "explicit wb_dma(wb_dma_import_if &imp)" in h
    assert "void initialize(pssc::addr_t base) {" in h
    # The group is bound in initialize, not at construction: constructed at 0
    # so an operation called before initialize faults rather than reading
    # somewhere plausible.
    assert "regs(dma_regs_c(imp, 0))" in h
    assert "this->regs = dma_regs_c(this->imp_, base);" in h


def test_an_import_declaring_model_gets_a_real_interface(tmp_path):
    """With imports, the seam gains them -- one object the platform implements,
    rather than a second parameter."""
    model = """
package plat_pkg {
    import target function void plat_delay_us(int us);
}
component imp_c {
    import plat_pkg::*;
    target function void spin(int n) { plat_delay_us(n); }
}
"""
    src = tmp_path / "imp.pss"
    src.write_text(model)
    ns = argparse.Namespace(progseq_root="imp_c", cpp_namespace="imp",
                            cpp_dispatch="virtual", cpp_single_header=True,
                            progseq_core_copy=True, output_dir=str(tmp_path))
    driver.compile([str(src)], target="cpp-progseq", opts=ns)
    h = (tmp_path / "imp.hpp").read_text()
    assert "struct imp_import_if : pssc::mem_if {" in h
    assert "virtual void plat_delay_us(int us) = 0;" in h
    # ...and the body reaches it through the same reference as memory access.
    assert "this->imp_.plat_delay_us(n);" in h
