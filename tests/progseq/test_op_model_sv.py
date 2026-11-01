"""The operation model, generated end to end as a SystemVerilog package.

This is the component-tree projection: one class per component type with its
members under their PSS names, an `init` lowered into a method, the public
target functions of the public instances as the API (`op_api.py`; the
`export` declarations `op_model_sv_sources` adds are accepted and change
nothing), and `yield` as a zero delay. The flat model
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

from .op_model import OP_MODEL as _MODEL, op_model_sv_sources as _sources
from .sv_text import sv_package_text


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
    return sv_package_text(out / "wb_dma_c_pkg.sv")


# --- outputs ---------------------------------------------------------------

def test_outputs_written(gen):
    out, res = gen
    names = {os.path.basename(str(p)) for p in res.outputs}
    assert {n for n in names if n.endswith(".sv")} == {"wb_dma_c_pkg.sv",
                                                      "pssc_reg_pkg.sv"}
    # One include file per class, registers together (user decisions D1, D4).
    assert {"wb_dma_c_regs.svh", "wb_dma_ch_c.svh", "wb_dma_c.svh",
            "wb_dma_c_root.svh"} <= names


def test_outputs_are_in_compilation_order(gen):
    """The core package comes first, because the generated one uses its types.

    The returned list IS the compile order -- dv-flow's `classify_outputs`
    preserves it into the fileset a SimImage compiles. Emitted the other way
    round, `wb_dma_c_pkg.sv` still lints on its own and fails only when
    something compiles both together: 38 x "Reference to 'addr_handle_t'
    before declaration".
    """
    out, res = gen
    order = [os.path.basename(str(p)) for p in res.outputs
             if str(p).endswith(".sv")]
    assert order == ["pssc_reg_pkg.sv", "wb_dma_c_pkg.sv"]


# --- the API ---------------------------------------------------------------

def _iface(sv, name):
    """The text of ``interface class <name>`` up to its `endclass`."""
    start = sv.index(f"interface class {name};")
    return sv[start:sv.index("endclass", start)]


def test_the_public_functions_are_the_api(sv):
    """The count defect A destroyed, restated for an API taken from
    visibility (`op_api.py`): every public target function of the root is on
    the context interface and forwarded by the factory; a channel's are on
    the channel type's interface, reached through `ch(i)`."""
    root = _iface(sv, "wb_dma_c_exp_if")
    for op in ("configure_interrupt_routing", "notify_irq", "pause_engine",
               "read_descriptor_residual", "write_descriptor"):
        assert f"pure virtual task {op}(" in root, op
        assert f"      pss_root.{op}(" in sv, op
    assert "pure virtual function wb_dma_ch_c_exp_if ch(int i);" in root
    assert "pure virtual function int ch_size();" in root
    ch = _iface(sv, "wb_dma_ch_c_exp_if")
    for op in ("configure_channel", "transfer_single"):
        assert f"pure virtual task {op}(" in ch, op
        assert f"      pss_comp.{op}(" in sv, op


def test_no_interface_class_is_empty(sv):
    """An interface class immediately followed by `endclass` is the signature of
    a projection that produced nothing. The import API is the exception that
    proves it: it extends `pss_mem_if`, which declares every primitive, and a
    model with no `import` functions adds nothing to it."""
    import re
    assert "interface class" in sv
    empty = re.findall(r"interface class (\w+)(?: extends \w+)?;\n  endclass", sv)
    assert empty == ["wb_dma_c_imp_if"], empty
    assert "interface class wb_dma_c_imp_if extends pss_mem_if;" in sv


# --- component classes ------------------------------------------------------

def test_members_keep_their_pss_names(sv):
    """A member is reached as PSS reaches it (`ch[i].wake`, `sub.a`): its PSS
    name, public. No accessors on the component class -- an accessor `ch()`
    beside a member `ch` would be two members of one name; they are on the
    API's adapter classes instead."""
    assert_generated(sv, has=[
        "    wb_dma_regs_c regs;\n    wb_dma_ch_c ch[WB_DMA_MAX_CH];",
        "    wb_dma_ch_regs_c regs;",
        "    channel_c #(bit, 1) wake;",
    ], has_not=[" m_ch", " m_regs", "protected wb_dma_regs_c",
                 "protected wb_dma_ch_c ch", "_if ch(int index)"])


def test_no_component_class_is_parameterized(sv):
    """Only the factory knows the platform's type (design D5): every
    component class, the root included, takes the import API."""
    assert_generated(sv,
                     has=["class wb_dma_ch_c extends wb_dma_c_component;",
                          "class wb_dma_c extends wb_dma_c_component;",
                          "class wb_dma_c_root #(type Timp = wb_dma_c_imp_if) "
                          "implements wb_dma_c_imp_if, wb_dma_c_exp_if;"],
                     has_not=["class wb_dma_ch_c #(", "class wb_dma_c #("])


def test_classes_are_declared_before_their_use(sv):
    """SV has no forward references inside a package: the component base,
    then a child before the parent that holds it, then the factory."""
    assert sv.index("interface class wb_dma_c_imp_if") < \
        sv.index("virtual class wb_dma_c_component;") < \
        sv.index("class wb_dma_ch_c extends") < \
        sv.index("class wb_dma_c extends") < sv.index("class wb_dma_c_root #(")


# --- construction and address binding --------------------------------------

def test_construction_is_split_from_the_ctor(sv):
    """`new()` takes only the import API (design D2); the model's constructor
    is a method `create()` runs, then PSS construction (D3)."""
    assert_generated(sv, has=[
        "static function wb_dma_c_exp_if create(Timp pss_imp, addr_handle_t base);",
        "protected function new(Timp imp);",
        "function void initialize(addr_handle_t base);",
        "      pss_model.pss_root.initialize(base);\n"
        "      pss_model.pss_root.pss_do_init();",
        "function new(wb_dma_c_imp_if imp);\n      super.new(imp);",
    ], has_not=["function new(Timp imp, addr_handle_t base);"])


def test_every_subcomponent_is_constructed(sv):
    """SV-3: every instance exists after `new()`, whether or not a model
    constructor is ever called for it -- and register groups sit at 0 until one
    binds them. The tree reaches the platform through the factory, which
    passes itself."""
    assert_generated(sv, has=[
        "foreach (ch[i]) ch[i] = new(pss_imp);",
        "regs = new(pss_imp, 0);",
        "pss_root = new(this);",
    ])


def test_pss_construction_reaches_the_subcomponents(sv):
    assert_generated(sv, has=[
        "virtual class wb_dma_c_component;",
        "foreach (ch[i]) ch[i].pss_do_init();",
    ])


def test_init_lowered_to_method_calls(sv):
    """`foreach (ch[i]) ch[i].initialize(i, make_handle_from_handle(base, ...))`
    becomes a loop of calls to the already-built children, with the address
    folded. SV's own `foreach`, bounded by the array: the constructor body
    renders as any other body does."""
    assert_generated(sv, has=[
        "regs = new(pss_imp, base);",
        "foreach (ch[i]) begin",
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
        "ch[i].initialize(i, (base + (64'h20 + 64'h20 * i)));",
    ], has_not=["ch[i] = new(pss_imp, i,"])


def test_channel_offsets_are_the_rdl_geometry(sv):
    """The numbers, independent of how the folder spells them.

    The assertion above is a string match and would survive an off-by-one in
    the stride if someone updated it to match new output. This one states the
    geometry the RDL declares -- channel n's bank is at 0x20 + 0x20*n -- so a
    fold that changed the arithmetic fails here even if the text was refreshed.
    """
    import re
    m = re.search(r"ch\[i\]\.initialize\(i, \(base \+ \((.*?)\)\)\);", sv)
    assert m, "channel binding not found"
    expr = m.group(1).replace("64'h", "0x").replace("'h", "0x")
    for i in range(4):
        assert eval(expr, {"i": i}) == 0x20 + 0x20 * i, (expr, i)


def test_channel_binds_its_own_bank(sv):
    """Each channel's register group is bound to the handle its constructor
    was given -- the per-channel bank is the reason the model has a component
    tree at all."""
    assert_generated(sv, has=[
        "function void initialize(int id, addr_handle_t bank);",
        "regs = new(pss_imp, bank);",
    ])


def test_field_defaults_emitted(sv):
    """A capability struct that defaults to all-false silently disables every
    operation gated on it, so the defaults have to survive."""
    assert_generated(sv, has=["caps.ars = 1;", "caps.cbuf = 1;",
                              "num_ch = WB_DMA_MAX_CH;"])


# --- body constructs -------------------------------------------------------

def test_yield_is_a_zero_delay(sv):
    """PSS `yield` lets other threads run: in SystemVerilog, `#0`. The
    platform supplies nothing for it, so the import API does not declare it."""
    assert_generated(sv, has=["#0;"], has_not=["yield_"])


def test_task_results_return_through_output_arguments(sv):
    """`return wait_completion();` is not an assignment: a task has no return
    value. Getting this wrong is a syntax error in one simulator and something
    subtly different in another."""
    assert_generated(sv,
                     has=["wait_completion(status);",
                          "pss_imp.read32(desc_ptr, desc_csr);"],
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
    raw number depends on the numbering being the model's. An SV enumerator
    is a package-scope name, so it is prefixed with its enum's.
    """
    assert_generated(sv,
                     has=["typedef enum {WB_DMA_STATUS_WB_DMA_DONE = 0, "
                          "WB_DMA_STATUS_WB_DMA_ERROR = 1, "
                          "WB_DMA_STATUS_WB_DMA_PENDING = 2} wb_dma_status_e;",
                          "status = WB_DMA_STATUS_WB_DMA_ERROR;"],
                     has_not=["status = 1;"])


def test_match_lowered_to_case(sv):
    assert_generated(sv, has=["case (bank)", "endcase"])


def test_forever_and_break(sv):
    assert_generated(sv, has=["forever begin", "break;"])


def test_struct_arg_signature(sv):
    """A struct parameter is passed by reference (LRM 20.3.2): `ref`, not an
    `input` copy the caller would never see a change to."""
    assert "virtual task configure_channel(ref wb_dma_ch_cfg_s cfg);" in sv


def test_packed_and_unpacked_structs(sv):
    """`packed_s<>` descendants have a bit layout to honour; a plain struct does
    not, and forcing it packed would invent one."""
    assert_generated(sv, has=["typedef struct packed {", "} wb_dma_desc_s;",
                              "typedef struct {", "} wb_dma_ch_cfg_s;"])


def test_address_builtins_are_arithmetic(sv):
    """There is no address-space object in generated SV, only addresses."""
    assert_generated(sv, has_not=["make_handle_from_handle(", "addr_value("])


# --- PSS constants by name -------------------------------------------------

def test_constants_are_spelled_by_name(sv):
    """A `static const` reference reads as written, not as its value: the
    front end keeps the name it folded (`ExprConstant.ref`)."""
    assert_generated(sv, has=[
        "wb_dma_ch_c ch[WB_DMA_MAX_CH];",
        "num_ch = WB_DMA_MAX_CH;",
        "pss_imp.write32((at + WB_DMA_DESC_NEXT_OFF), d.next);",
        "status = (at + WB_DMA_DESC_BYTES);",
    ], has_not=[" ch[4];", "num_ch = 4;", "(at + 12)", "(at + 16)"])


def test_constants_are_declared_with_their_pss_type(gen):
    """Each referenced constant is a `localparam` of its declared type: a
    `bit[64]` is not the `int` its literal would be."""
    out, _ = gen
    text = (out / "wb_dma_c_consts.svh").read_text()
    decls = [l.split("//")[0].split() for l in text.splitlines()
             if "localparam" in l]
    assert decls == [
        ["localparam", "bit", "[63:0]", "WB_DMA_DESC_BYTES", "=", "64'd16;"],
        ["localparam", "bit", "[63:0]", "WB_DMA_DESC_NEXT_OFF", "=", "64'd12;"],
        ["localparam", "int", "WB_DMA_MAX_CH", "=", "4;"],
    ]
    # Only what the code references: REG_SPAN and the profile switches are
    # constants of the model, and nothing generated reads them.
    assert "WB_DMA_REG_SPAN" not in text and "HAS_EVENT_WAIT" not in text


def test_enum_items_are_spelled_by_name_in_comparisons(sv):
    """An enum item kept its value only; a comparison then read `!= 2`."""
    assert_generated(sv, has=[
        "if (status != WB_DMA_STATUS_WB_DMA_PENDING) begin",
        "if (status == WB_DMA_STATUS_WB_DMA_PENDING) begin"],
                     has_not=["status != 2", "status == 2"])


# --- the compile gate ------------------------------------------------------

@pytest.mark.skipif(not shutil.which("verilator"), reason="verilator not on PATH")
def test_generated_package_lints_clean(gen):
    out, _ = gen
    r = subprocess.run(
        ["verilator", "--lint-only", "-sv", "--timing", f"-I{out}",
         str(out / "pssc_reg_pkg.sv"), str(out / "wb_dma_c_pkg.sv")],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


# --- the anti-silence guard ------------------------------------------------

def test_zero_operation_model_is_an_error(tmp_path):
    """A model that projects to nothing must fail the build. Every front-end
    defect in this generator's history produced exactly this output and exited
    0. A model with no public target function projects to nothing too."""
    import argparse
    src = tmp_path / "empty.pss"
    src.write_text("component empty_c { int x;\n"
                   "  solve function void f() { int y = x; } }\n")
    ns = argparse.Namespace(progseq_root="empty_c", progseq_package="empty_pkg",
                            output_dir=str(tmp_path))
    with pytest.raises(ValueError, match="no public target function"):
        driver.compile([str(src)], target="sv-progseq", opts=ns)


# --- hierarchical APIs: names ---------------------------------------------------

def _compile(tmp_path, text, root="top_c"):
    import argparse
    src = tmp_path / "m.pss"
    src.write_text(text)
    ns = argparse.Namespace(progseq_root=root, progseq_package=f"{root}_pkg",
                            output_dir=str(tmp_path / "gen"))
    return driver.compile([str(src)], target="op-model-sv", opts=ns)


_LEAF = """
component leaf_c {
  target function void peek() { }
}
"""


@pytest.mark.parametrize("body, what", [
    # An array's `_size()` against a function of the same type.
    ("leaf_c pit[2];\n target function void pit_size() { }",
     r"size of sub-component array 'pit' has the name of the "
     r"function 'pit_size'"),
    # An accessor against the factory's own `create()`.
    ("leaf_c create;", r"accessor of sub-component 'create' has the name of "
     r"the factory's create\(\)"),
])
def test_accessor_names_are_checked(tmp_path, body, what):
    """An accessor, an array's size and a function share one SV method
    namespace on a type's API: a clash is refused, not emitted."""
    with pytest.raises(ValueError, match=what):
        _compile(tmp_path, _LEAF + "component top_c {\n " + body + "\n}\n")


def test_only_public_children_that_lead_to_an_api_have_an_accessor(tmp_path):
    """A child is on the API if it is public and leads to a public function.
    `q`'s type has only a private one; `b` is a private instance. A model
    whose only public functions are a sub-component's still has an API."""
    res = _compile(tmp_path, _LEAF + """
component quiet_c { private target function void f() { } }
component top_c {
  leaf_c a;
  private leaf_c b;
  quiet_c q;
}
""")
    text = "".join(p.read_text() for p in res.outputs
                   if str(p).endswith((".sv", ".svh")))
    assert "pure virtual function leaf_c_exp_if a();" in text
    assert "quiet_c_exp_if" not in text and " q()" not in text
    assert " b()" not in text


def test_a_bare_get_on_a_channel_array_element_discards_its_value(tmp_path):
    """`wake[ch].get();` -- a blocking get whose token nobody reads -- lowers
    like the scalar `wake.get();`: a temporary of the element type in a block
    of its own. The receiver is a subscript, so it has no name of its own to
    look the channel up by; the array's is used."""
    res = _compile(tmp_path, """
import sync_pkg::*;
component top_c {
  channel_c<bit, 1> wake[2];
  channel_c<bit, 1> one;
  target function void wait_on(int ch) {
    wake[ch].get();
    one.get();
  }
}
""")
    text = "".join(p.read_text() for p in res.outputs
                   if str(p).endswith((".sv", ".svh")))
    assert "wake[ch].get(pssc_discard);" in text
    assert "one.get(pssc_discard);" in text
