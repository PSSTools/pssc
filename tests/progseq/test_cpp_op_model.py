"""The real operation model, generated end to end as a C++ driver.

The C++ counterpart of `test_c_op_model.py`, and the gate on the thing that was
missing until 2026-08-14: this backend could not generate the real model at all.
It handled the ROOT component only, had no enum in its type mapper, no
`foreach`/`match`/`break`/`yield`, no channel runtime and no way to reach a
memory primitive -- so the one model anyone actually cares about failed with
"unsupported C++ type for DataTypeEnum" three statements in.

`has_not=` assertions carry real weight here. An absent sub-component class, an
absent operation and an absent register access all look exactly like ordinary
generated code.

The compile at the bottom is the cheapest strong signal available: it does not
prove the driver is right, it proves the output is C++, which every structural
assertion above quietly assumes. `test_op_model_behaviour_cpp.py` is the one
that runs the result against a mock bus and compares its register trace with
the C driver's.
"""
import argparse
import os
import subprocess

import pytest

from pssc import driver

from .codetext import code_only
from .conftest import available_cpp_compilers
from .op_model import op_model_sources as _sources

_CXX = available_cpp_compilers()
needs_cxx = pytest.mark.skipif(not _CXX, reason="no C++ compiler on PATH")


def assert_cpp(text, *, has=(), has_not=(), code=False):
    """Structural assertions about the generated C++.

    Pass ``code=True`` when the fragments are code: the output carries the
    model's own prose, and that prose names the very calls some tests assert
    are absent. See .codetext.
    """
    if code:
        text = code_only(text)
    for frag in has:
        assert frag in text, f"missing from generated C++: {frag!r}"
    for frag in has_not:
        assert frag not in text, f"should not appear in generated C++: {frag!r}"


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    out = tmp_path_factory.mktemp("op_model_cpp")
    ns = argparse.Namespace(progseq_root="wb_dma_c", output_dir=str(out))
    res = driver.compile(_sources(), target="op-model-cpp", opts=ns)
    return out, res


@pytest.fixture(scope="module")
def h(gen):
    out, _ = gen
    return (out / "wb_dma.hpp").read_text()


# --- outputs ----------------------------------------------------------------

def test_outputs_written(gen):
    """The header plus exactly the core headers this model needs.

    `pssc_chan.hpp` because the model has channels and `pssc_env.hpp` because
    something can call `pssc::message`. Copying every core header regardless
    would put files in an output directory that read as dependencies the
    platform has to satisfy.
    """
    out, res = gen
    names = [os.path.basename(str(p)) for p in res.outputs]
    assert names == ["wb_dma.hpp", "pssc_reg.hpp", "pssc_chan.hpp",
                     "pssc_env.hpp"]


# --- the tree ---------------------------------------------------------------

def test_each_component_gets_an_interface_and_a_class(h):
    """The projection is of a component TREE. Before this, the backend emitted
    the root alone -- for WB DMA that is four engine-global operations and none
    of the thirteen per-channel ones."""
    assert_cpp(h, has=["struct wb_dma_ch_c_exp_if {", "class wb_dma_ch_c : public wb_dma_ch_c_exp_if {",
                       "struct wb_dma_c_exp_if {", "class wb_dma_c : public wb_dma_c_exp_if {"])


def test_the_child_precedes_the_parent(h):
    """The parent holds its children BY VALUE, so the child has to be a
    complete type by the time the parent's class is declared."""
    assert h.index("class wb_dma_ch_c :") < h.index("class wb_dma_c :")


def test_sub_components_are_members_not_pointers(h):
    """The PSS tree is static (elaboration-time), so the C++ tree is too: one
    object covers every level and there is nothing to allocate below the root."""
    assert_cpp(h, has=["std::array<wb_dma_ch_c, 4> ch_;"],
               has_not=["std::array<wb_dma_ch_c *, 4>",
                        "std::vector<wb_dma_ch_c>",
                        "std::unique_ptr<wb_dma_ch_c> ch_"])


def test_sub_component_access_is_on_the_interface(h):
    """`create()` hands back the interface, so anything not reachable through
    it is not reachable at all."""
    assert_cpp(h, has=["std::size_t ch_size() const { return 4; }",
                       "virtual wb_dma_ch_c_exp_if &ch(std::size_t i) = 0;",
                       "wb_dma_ch_c_exp_if &ch(std::size_t i) override "
                       "{ return ch_[i]; }"])


def test_no_parent_back_pointer(h):
    """Nothing in the model refers upward -- checked, not assumed -- so nothing
    stores a way to go up. One added "just in case" would sit in every channel."""
    assert_cpp(h, has_not=["wb_dma_c *parent", "wb_dma_c &parent_", "wb_dma_c *owner"])


def test_component_state_is_declared(h):
    """`chan` and `caps` are the channel's own state; without them
    `this->caps.ars` in an operation body has nowhere to resolve to."""
    ch = h[h.index("class wb_dma_ch_c :"):h.index("class wb_dma_c :")]
    assert "int chan{};" in ch
    assert "wb_dma_ch_caps_s caps{};" in ch


# --- construction -----------------------------------------------------------

def test_construction_is_two_phase(h):
    """A parent computes its CHILDREN's base addresses in its own constructor
    body, which runs after the children -- as members -- already exist. So the
    C++ constructor takes the seam, and `initialize` runs the PSS one."""
    assert_cpp(h, has=["explicit wb_dma_ch_c(wb_dma_import_if &imp)",
                       "void initialize(int id, pssc::addr_t bank) {",
                       "explicit wb_dma_c(wb_dma_import_if &imp)",
                       "void initialize(pssc::addr_t base) {"])


def test_the_children_are_initialized_with_folded_offsets(h):
    """`get_offset_of_instance_array` is evaluated at generation time: it is a
    `pure function` over an elaborated register map, and there is no register
    group object at runtime to ask."""
    assert_cpp(h, has=["this->ch_[i].initialize(i, (base + (0x20u + 0x20u * i)));"])


def test_register_groups_are_bound_once(h):
    """The generator supplies a default binding ONLY where the model states
    none. Emitting both would construct the same 31-bank register group twice,
    the first time at the wrong base."""
    init = h[h.index("void initialize(int id, pssc::addr_t bank) {"):]
    init = init[:init.index("\n    }")]
    assert init.count("this->regs = wb_dma_ch_regs_c(") == 1


def test_the_factory_returns_the_interface(h):
    assert_cpp(h, has=["static std::unique_ptr<wb_dma_c_exp_if> create("
                       "wb_dma_import_if &_imp, pssc::addr_t base) {",
                       "_self->initialize(base);"])


# --- types ------------------------------------------------------------------

def test_enums_reach_the_api(h):
    """The gap that stopped this backend generating the model at all: an
    operation takes an enum-typed argument and `cpp_type` had no arm for it.
    Scoped, as PSS scopes an item by its enum (7.5)."""
    assert_cpp(h, has=["enum class wb_dma_int_bank_e : int {",
                       "WB_DMA_INT_A = 0"])


def test_enum_values_are_explicit(h):
    """The numbers are register field encodings and cross-language contract.
    Implicit numbering would reproduce them today and renumber them the day
    someone reorders the PSS declaration."""
    assert_cpp(h, has=["WB_DMA_DONE = 0", "WB_DMA_ERROR = 1"])


def test_an_enumerator_survives_into_a_return(h):
    """An item is written as the item the model wrote -- the one the linker
    resolved (`ExprConstant.ref`), never one looked up by its value."""
    assert_cpp(h, has=["return wb_dma_status_e::WB_DMA_PENDING;",
                       "return wb_dma_status_e::WB_DMA_ERROR;"],
               has_not=["return 2;"], code=True)


def test_plain_structs_are_aggregates_with_initialised_members(h):
    """A generated aggregate stays an aggregate, so a caller can still write
    `wb_dma_ch_caps_s c{true, false};` -- and a default-constructed one holds
    the model's defaults, zero where it gives none, rather than whatever was
    on the stack."""
    assert_cpp(h, has=["struct wb_dma_ch_caps_s {", "bool present = true;",
                       "struct wb_dma_ch_cfg_s {", "std::uint32_t src = {};"])


# --- bodies -----------------------------------------------------------------

def test_register_access_is_the_native_member_path(h):
    """The C++ register model mirrors the PSS structure, so a register access
    is the same path spelled the same way -- no per-register generation at all,
    which is the whole difference from the C backend."""
    assert_cpp(h, has=["this->regs.csr.read()",
                       "this->regs.csr.write_val_masked("])


def test_memory_primitives_reach_the_seam(h):
    """`read32(h)` is a PSS built-in, not a function anything declares. Emitting
    it verbatim would produce C++ naming a symbol nothing defines."""
    assert_cpp(h, has=["this->imp_.read32(", "this->imp_.write32("],
               has_not=["= read32(", " write32("], code=True)


def test_address_builtins_fold_to_arithmetic(h):
    """`addr_handle_t` is opaque in PSS and a plain address here, so deriving a
    handle from another is an add -- not a call to a function that would have to
    exist."""
    assert_cpp(h, has_not=["make_handle_from_handle", "addr_value("], code=True)


def test_component_members_are_reached_through_this(h):
    """The one defect in the C emitter that no compiler catches was a bare
    member name resolving against an unrelated object of the same name.
    `this->` cannot do that."""
    assert_cpp(h, has=["this->caps.", "this->chan"])


def test_a_sibling_operation_call_goes_through_this(h):
    assert_cpp(h, has=["this->wait_hint();", "this->probe_status()"])


def test_channels_are_typed(h):
    """`pssc::chan1<T>` carries the model's declared element type. The C runtime
    has one channel struct and a 64-bit payload, so a generated body there has
    to widen the model's own local to match."""
    assert_cpp(h, has=["pssc::chan1<bool> inflight;", "pssc::chan1<bool> wake;",
                       "this->inflight.try_put(", "this->inflight.try_get("])


def test_yield_lowers_to_nothing(h):
    """`yield` is a hint to a scheduler and this target generates none, so the
    surrounding loop becomes a tight poll -- which is what a model reaching
    `yield` on this profile asked for, having been told it has no event to wait
    on. A comment rather than silence: a reader comparing the PSS to the C++
    needs to see that the wait point is still here and is free."""
    assert_cpp(h, has=["// yield: nothing to yield to on this target"])


def test_compile_if_kept_the_polling_branch(h):
    """`wait_hint()` is gated on HAVE_EVENT_WAIT, which this target declares
    false -- so the `wake.get()` branch is compiled out before the backend ever
    sees it. If it were not, generation would fail: blocking `get` needs a
    scheduler this backend does not generate."""
    assert_cpp(h, has_not=["wake.get()"], code=True)


def test_the_match_becomes_a_switch_with_breaks(h):
    """PSS arms do not fall through, so omitting the break would silently
    change the model's meaning into C++'s."""
    assert_cpp(h, has=["switch (", "break;", "default:"])


def test_foreach_becomes_a_bounded_loop(h):
    assert_cpp(h, has=["for (std::size_t i = 0; i < 4u; ++i) {"],
               has_not=["i < 0u", "i < -1", "foreach"])


def test_the_prose_comes_across(h):
    """The generated API is a close transcription of its PSS source, and the
    doc comments are most of what makes it usable."""
    assert_cpp(h, has=["The wake is a hint; CHn_CSR is the",
                       "/**"])


# --- it is C++ --------------------------------------------------------------

@needs_cxx
@pytest.mark.c_toolchain
@pytest.mark.parametrize("cxx", _CXX)
def test_generated_header_compiles(gen, tmp_path, cxx):
    """-Werror, because a warning in generated code is a defect in the
    generator: the reader cannot fix it where it appears."""
    out, _ = gen
    tu = tmp_path / "tu.cpp"
    tu.write_text(
        '#include "wb_dma.hpp"\n'
        '// The platform side of the seam, so the translation unit links.\n'
        'namespace pssc { void message(const char *, ...) {} }\n'
        'int main() { return 0; }\n')
    res = subprocess.run(
        [cxx, "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(out),
         "-c", str(tu), "-o", os.devnull],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


# --- the yield seam ---------------------------------------------------------

@needs_cxx
@pytest.mark.c_toolchain
def test_yield_import_declares_the_hook_on_the_seam(tmp_path):
    """`--yield import` lowers `yield` to a call ON THE PLATFORM, so the
    platform has to be able to see its declaration.

    A bare `yield_();` would be an implicit declaration -- an error in C++ and,
    under `-Werror`, in C99 too. Putting it on the import interface is what the
    SV projection does with `import_api_if`, and it makes the mode's cost
    explicit: choose it and the platform owes one more method.
    """
    ns = argparse.Namespace(progseq_root="wb_dma_c", cpp_namespace="wb_dma",
                            c_yield="import", output_dir=str(tmp_path))
    driver.compile(_sources(), target="op-model-cpp", opts=ns)
    h = (tmp_path / "wb_dma.hpp").read_text()
    assert "struct wb_dma_import_if : pssc::mem_if {" in h
    assert "virtual void yield_() = 0;" in h
    assert "this->imp_.yield_();" in h

    tu = tmp_path / "tu.cpp"
    tu.write_text(
        '#include "wb_dma.hpp"\n'
        'namespace pssc { void message(const char *, ...) {} }\n'
        'struct seam : wb_dma::wb_dma_import_if, pssc::mmio_mem {\n'
        '    void write8(pssc::addr_t a, std::uint8_t d) override'
        ' { pssc::mmio_mem::write8(a, d); }\n'
        '    std::uint8_t read8(pssc::addr_t a) override'
        ' { return pssc::mmio_mem::read8(a); }\n'
        '    void write16(pssc::addr_t a, std::uint16_t d) override'
        ' { pssc::mmio_mem::write16(a, d); }\n'
        '    std::uint16_t read16(pssc::addr_t a) override'
        ' { return pssc::mmio_mem::read16(a); }\n'
        '    void write32(pssc::addr_t a, std::uint32_t d) override'
        ' { pssc::mmio_mem::write32(a, d); }\n'
        '    std::uint32_t read32(pssc::addr_t a) override'
        ' { return pssc::mmio_mem::read32(a); }\n'
        '    void write64(pssc::addr_t a, std::uint64_t d) override'
        ' { pssc::mmio_mem::write64(a, d); }\n'
        '    std::uint64_t read64(pssc::addr_t a) override'
        ' { return pssc::mmio_mem::read64(a); }\n'
        '    void yield_() override {}\n'
        '};\n'
        'int main() { seam s; auto d = wb_dma::wb_dma_c::create(s, 0x1000);'
        ' d->ch(0).wait_hint(); return 0; }\n')
    res = subprocess.run(
        [_CXX[0], "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I",
         str(tmp_path), "-c", str(tu), "-o", os.devnull],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_an_enumerator_survives_into_a_comparison(h):
    """`status != PENDING`, not `status != 2`: the item, by name."""
    assert_cpp(h, has=["if (status != wb_dma_status_e::WB_DMA_PENDING) {"],
               has_not=["status != 2", "status == 2"], code=True)
