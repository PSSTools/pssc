"""The operation model, generated end to end as a SystemVerilog package.

This is the component-tree projection: sub-component accessors, an `init`
lowered into the constructor, and the blocking `yield` contract. The flat model
in `src/pssc/testing/models` covers the same backend from the other
direction, and both must keep working -- a change that fixes one and breaks the
other is not progress.

Assertions come in pairs wherever a defect had a plausible-looking output: the
absence checks (`has_not=`) are load-bearing, because an empty interface class
and a missing accessor both look like ordinary generated code.

The Verilator lint at the bottom is the cheapest strong signal available. It
does not prove the model is right; it proves the output is SystemVerilog, which
every structural assertion above quietly assumes.
"""
import os
import shutil
import subprocess

import pytest

from pssc import driver

from .op_model import OP_MODEL as _MODEL, op_model_sources as _sources


def assert_generated(text, *, has=(), has_not=()):
    for frag in has:
        assert frag in text, f"missing from generated output: {frag!r}"
    for frag in has_not:
        assert frag not in text, f"should not appear in generated output: {frag!r}"


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    import argparse
    out = tmp_path_factory.mktemp("op_model_sv")
    ns = argparse.Namespace(progseq_root="wb_dma_c",
                            progseq_package="wb_dma_c_pkg",
                            output_dir=str(out))
    res = driver.compile(_sources(), target="sv-progseq", opts=ns)
    return out, res


@pytest.fixture(scope="module")
def sv(gen):
    out, _ = gen
    return (out / "wb_dma_c_pkg.sv").read_text()


# --- outputs ---------------------------------------------------------------

def test_outputs_written(gen):
    out, res = gen
    names = {os.path.basename(str(p)) for p in res.outputs}
    assert names == {"wb_dma_c_pkg.sv", "pssc_reg_pkg.sv"}


def test_outputs_are_in_compilation_order(gen):
    """The core package comes first, because the generated one uses its types.

    The returned list IS the compile order -- dv-flow's `classify_outputs`
    preserves it into the fileset a SimImage compiles. Emitted the other way
    round, `wb_dma_c_pkg.sv` still lints on its own and fails only when
    something compiles both together: 38 x "Reference to 'addr_handle_t'
    before declaration".
    """
    out, res = gen
    order = [os.path.basename(str(p)) for p in res.outputs]
    assert order == ["pssc_reg_pkg.sv", "wb_dma_c_pkg.sv"]


# --- export API ------------------------------------------------------------

def test_all_operations_exported(sv):
    """The count defect A destroyed. Asserted on the generated text as well as
    on the IR, because the two failed independently."""
    for op in ("configure_interrupt_routing", "pause_engine",
               "read_descriptor_residual", "write_descriptor"):
        assert f"pure virtual task {op}(" in sv, op
    for op in ("configure_channel", "set_auto_restart", "set_software_pointer",
               "stop_channel", "transfer_list", "transfer_single",
               "wait_completion"):
        assert f"pure virtual task {op}(" in sv, op


def test_no_interface_class_is_empty(sv):
    """An interface class immediately followed by `endclass` is the signature of
    a projection that produced nothing."""
    assert "interface class" in sv
    assert "_if;\n  endclass" not in sv


# --- sub-components --------------------------------------------------------

def test_array_accessor_emitted(sv):
    assert_generated(sv, has=[
        "pure virtual function wb_dma_ch_c_if ch(int index);",
        "pure virtual function int ch_size();",
        "virtual function wb_dma_ch_c_if ch(int index);",
        "return m_ch[index];",
    ])


def test_subcomponent_class_not_parameterized(sv):
    """Only the root takes `#(type IMP_T)`. A parameterized sub-component would
    leak its parameter into the root's accessor return type, and the export API
    would stop being a plain handle."""
    assert_generated(sv,
                     has=["class wb_dma_ch_c implements wb_dma_ch_c_if;",
                          "class wb_dma_c #(type IMP_T = wb_dma_c_import_if)"],
                     has_not=["class wb_dma_ch_c #("])


def test_subcomponent_declared_before_the_root_that_builds_it(sv):
    """SV has no forward references inside a package."""
    assert sv.index("class wb_dma_ch_c implements") < sv.index("class wb_dma_c #(")
    assert sv.index("interface class wb_dma_c_import_if") < \
        sv.index("class wb_dma_ch_c implements")


# --- construction and address binding --------------------------------------

def test_ctor_takes_base(sv):
    assert_generated(sv, has=[
        "static function wb_dma_c_if create(IMP_T imp, addr_handle_t base);",
        "function new(IMP_T imp, addr_handle_t base);",
    ])


def test_init_lowered_to_construction(sv):
    """`foreach (ch[i]) ch[i].\\init(i, make_handle_from_handle(base, ...))`
    becomes a bounded loop of constructions with the address folded."""
    assert_generated(sv, has=[
        "m_regs = new(this, base);",
        "for (int i = 0; i < 4; i++) begin",
        # Sub-expressions are bracketed: the IR tree says how the expression
        # groups, and SV precedence only sometimes agrees. Same arithmetic.
        #
        # The literals are the GENERATED register package's -- base 0x20,
        # stride 0x20, from `bank[NUM_CH] @0x20 += 0x20` in the RDL. They are
        # spelled in hex and grouped as `base + (off + stride*i)` because that
        # is the shape `get_offset_of_instance_array` folds to; the
        # hand-written package this model used to carry produced
        # `base + 32 + (i * 32)`, which is the same arithmetic and is why the
        # value is checked below rather than only the text.
        "m_ch[i] = new(this, i, (base + (64'h20 + 64'h20 * i)));",
    ])


def test_channel_offsets_are_the_rdl_geometry(sv):
    """The numbers, independent of how the folder spells them.

    The assertion above is a string match and would survive an off-by-one in
    the stride if someone updated it to match new output. This one states the
    geometry the RDL declares -- channel n's bank is at 0x20 + 0x20*n -- so a
    fold that changed the arithmetic fails here even if the text was refreshed.
    """
    import re
    m = re.search(r"m_ch\[i\] = new\(this, i, \(base \+ \((.*?)\)\)\);", sv)
    assert m, "channel construction not found"
    expr = m.group(1).replace("64'h", "0x").replace("'h", "0x")
    for i in range(4):
        assert eval(expr, {"i": i}) == 0x20 + 0x20 * i, (expr, i)


def test_channel_binds_its_own_bank(sv):
    """Each channel's register group is bound to the handle it was constructed
    with -- the per-channel bank is the reason the model has a component tree at
    all."""
    assert_generated(sv, has=[
        "function new(wb_dma_c_import_if bus, int id, addr_handle_t bank);",
        "m_regs = new(m_imp, bank);",
    ])


def test_field_defaults_emitted(sv):
    """A capability struct that defaults to all-false silently disables every
    operation gated on it, so the defaults have to survive."""
    assert_generated(sv, has=["m_caps.ars = 1;", "m_caps.cbuf = 1;",
                              "m_num_ch = 4;"])


# --- body constructs -------------------------------------------------------

def test_yield_maps_to_import_task(sv):
    assert_generated(sv, has=["m_imp.yield_();",
                              "pure virtual task yield_();",
                              "virtual task yield_(); m_imp.yield_(); endtask"])


def test_yield_contract_is_stated_in_the_output(sv):
    """The `yield` contract's violation is a hang, not an error, so the
    obligation is written where an implementer will meet it."""
    assert "MUST NOT wait on an interrupt alone" in sv


def test_task_results_return_through_output_arguments(sv):
    """`return wait_completion();` is not an assignment: a task has no return
    value. Getting this wrong is a syntax error in one simulator and something
    subtly different in another."""
    assert_generated(sv,
                     has=["wait_completion(status);", "read32(desc_ptr, desc_csr);"],
                     has_not=["status = wait_completion();",
                              "desc_csr = read32(desc_ptr);"])


def test_enum_typedefs_and_mnemonics(sv):
    """Enum values are emitted explicitly, and used by mnemonic.

    Three statuses, not two: `WB_DMA_PENDING` is what the non-blocking
    `check_completion()` answers while a transfer is still running. The
    blocking operations never return it -- they return only from a terminal
    state -- which is why the model documents "exactly two outcomes" for
    `transfer_single()` and still declares three.

    Explicit values matter because these are not an arbitrary ordering: the
    generated code compares against the mnemonic, and anything that reads the
    raw number depends on the numbering being the model's.
    """
    assert_generated(sv,
                     has=["typedef enum {WB_DMA_DONE = 0, WB_DMA_ERROR = 1, "
                          "WB_DMA_PENDING = 2} wb_dma_status_e;",
                          "status = WB_DMA_ERROR;"],
                     has_not=["status = 1;"])


def test_match_lowered_to_case(sv):
    assert_generated(sv, has=["case (bank)", "endcase"])


def test_forever_and_break(sv):
    assert_generated(sv, has=["forever begin", "break;"])


def test_struct_arg_signature(sv):
    assert "pure virtual task configure_channel(input wb_dma_ch_cfg_s cfg);" in sv


def test_packed_and_unpacked_structs(sv):
    """`packed_s<>` descendants have a bit layout to honour; a plain struct does
    not, and forcing it packed would invent one."""
    assert_generated(sv, has=["typedef struct packed {", "} wb_dma_desc_s;",
                              "typedef struct {", "} wb_dma_ch_cfg_s;"])


def test_address_builtins_are_arithmetic(sv):
    """There is no address-space object in generated SV, only addresses."""
    assert_generated(sv, has_not=["make_handle_from_handle(", "addr_value("])


# --- the compile gate ------------------------------------------------------

@pytest.mark.skipif(not shutil.which("verilator"), reason="verilator not on PATH")
def test_generated_package_lints_clean(gen):
    out, _ = gen
    r = subprocess.run(
        ["verilator", "--lint-only", "-sv", "--timing",
         str(out / "pssc_reg_pkg.sv"), str(out / "wb_dma_c_pkg.sv")],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# --- the anti-silence guard ------------------------------------------------

def test_zero_operation_model_is_an_error(tmp_path):
    """A model that projects to nothing must fail the build. Every front-end
    defect in this generator's history produced exactly this output and exited
    0."""
    import argparse
    src = tmp_path / "empty.pss"
    src.write_text("component empty_c { int x; }\n")
    ns = argparse.Namespace(progseq_root="empty_c", progseq_package="empty_pkg",
                            output_dir=str(tmp_path))
    with pytest.raises(ValueError, match="zero operations"):
        driver.compile([str(src)], target="sv-progseq", opts=ns)
