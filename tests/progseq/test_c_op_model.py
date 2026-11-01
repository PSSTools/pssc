"""The real operation model, generated end to end as a C driver.

The C counterpart of `test_op_model_sv.py`, and the gate on C1: the projection
of a component TREE, not of a single component. Before C1 this target emitted
the root alone -- for WB DMA that is four engine-global operations and none of
the thirteen per-channel ones, in a header that compiled cleanly and contained
nothing anybody wanted. `has_not=` assertions carry that weight throughout: an
absent sub-component struct, an absent accessor and an absent operation all look
exactly like ordinary generated code.

The compile at the bottom is the cheapest strong signal available. It does not
prove the driver is right; it proves the output is C, which every structural
assertion above quietly assumes. `test_build_wb_dma_c.py` is the one that runs
the result against a mock bus and checks the register trace.
"""
import argparse
import os
import subprocess

import pytest

from pssc import driver

from .codetext import code_only
from .conftest import available_c_compilers
from .op_model import op_model_sources as _sources

_CC = available_c_compilers()
needs_cc = pytest.mark.skipif(not _CC, reason="no C compiler on PATH")


def assert_c(text, *, has=(), has_not=(), code=False):
    """Structural assertions about the generated C.

    Pass ``code=True`` when the fragments are code rather than prose. The
    output now carries the model's own comments, and those comments name the
    very calls some tests assert are absent -- `wait_hint` explains what
    `notify_irq()` does on a profile that has it, so a `has_not` on
    `wb_dma_notify_irq` matches the explanation. See .codetext.

    Not the default: several assertions here are *about* the prose the
    generator emits ("NOT ISR-SAFE"), and stripping comments would delete the
    thing under test.
    """
    if code:
        text = code_only(text)
    for frag in has:
        assert frag in text, f"missing from generated C: {frag!r}"
    for frag in has_not:
        assert frag not in text, f"should not appear in generated C: {frag!r}"


@pytest.fixture(scope="module")
def gen(tmp_path_factory):
    out = tmp_path_factory.mktemp("op_model_c")
    ns = argparse.Namespace(progseq_root="wb_dma_c", output_dir=str(out),
                            c_link_style="vtable")
    res = driver.compile(_sources(), target="op-model-c", opts=ns)
    return out, res


@pytest.fixture(scope="module")
def h(gen):
    out, _ = gen
    return (out / "wb_dma.h").read_text()


@pytest.fixture(scope="module")
def g(gen):
    """The private regs header -- the value layouts the public header does
    not need to name moved here (see docs/design/reg-mem-access-readability-plan.md)."""
    out, _ = gen
    return (out / "wb_dma_regs.h").read_text()


@pytest.fixture(scope="module")
def c(gen):
    out, _ = gen
    return (out / "wb_dma.c").read_text()


# --- outputs ---------------------------------------------------------------

def test_outputs_written(gen):
    """The generated pair plus EVERY seam header, not just the selected one.

    Copying only the style in use was the obvious thing and is wrong once
    `--mem-access selectable` exists: that mode decides at C-compile time, so
    the file it will reach for cannot be known at generation time. Copying the
    lot costs a few hundred lines in an output directory that is meant to be
    self-contained, and removes a failure whose message would explain nothing.
    """
    out, res = gen
    names = {os.path.basename(str(p)) for p in res.outputs}
    assert names == {"wb_dma.h", "wb_dma_regs.h", "wb_dma.c",
                     "pssc_mem.h", "pssc_mem_ptr.h", "pssc_mem_fn.h",
                     "pssc_mem_vtable.h", "pssc_mem_reg.h",
                     # C4.3 shims, still copied so a freshly generated
                     # directory works beside a header generated before the
                     # split.
                     "pssc_mem_direct.h", "pssc_mem_mmio.h",
                     "pssc_env.h", "pssc_chan.h"}


# --- C1.1: one struct per component, sub-components inline ------------------

def test_each_component_gets_its_own_struct(h):
    assert_c(h, has=["typedef struct wb_dma_ch_c {", "} wb_dma_ch_c;",
                     "typedef struct wb_dma_c {", "} wb_dma_c;"])


def test_sub_components_are_inline_members_not_pointers(h):
    """BY VALUE. The PSS tree is static, so the C tree is: one caller-provided
    object covers every level and there is nothing to allocate below the root.
    A `wb_dma_ch_c *ch[4]` would reintroduce exactly the allocator this style
    exists to remove."""
    assert_c(h, has=["    wb_dma_ch_c ch[4];"],
             has_not=["wb_dma_ch_c *ch[4]", "wb_dma_ch_c **ch"])


def test_the_child_struct_precedes_the_parent(h):
    """C needs the child to be a COMPLETE type where the parent embeds it, so
    the structs are emitted children-first. Getting this backwards produces
    `field has incomplete type`, which is at least loud -- but the order is also
    what makes `--header-only` work, where it is not."""
    assert h.index("} wb_dma_ch_c;") < h.index("typedef struct wb_dma_c {")


def test_no_parent_back_pointer(h):
    """Design §4.1: verified against the model that nothing refers upward, so
    nothing stores a way to go up. An emitter that added one "just in case"
    would put a pointer into every one of the four channels."""
    assert_c(h, has_not=["wb_dma_c *parent", "wb_dma_c *owner",
                         "struct wb_dma_c *parent"])


def test_each_component_carries_its_own_bus(h):
    """A sub-component's register accessors call `pssc_bus(_self)` with ITS handle.
    Reaching the root's copy instead would need the back-pointer above."""
    ch = h[h.index("typedef struct wb_dma_ch_c {"):h.index("} wb_dma_ch_c;")]
    assert "const pssc_mem_if *_bus;" in ch
    assert "pssc_addr_t base_regs;" in ch


def test_component_data_members_are_present(h):
    """`chan` and `caps` are the channel's state; without them `self.caps.ars`
    in an operation body has nowhere to resolve to."""
    ch = h[h.index("typedef struct wb_dma_ch_c {"):h.index("} wb_dma_ch_c;")]
    assert "int chan;" in ch
    assert "wb_dma_ch_caps_s caps;" in ch


# --- C1.2: sub-component accessors -----------------------------------------

def test_sub_component_accessor_and_count(h):
    assert_c(h, has=["#define WB_DMA_CH_SIZE 4u",
                     "static inline wb_dma_ch_c *wb_dma_ch(wb_dma_c *_self, unsigned i) "
                     "{ return &_self->ch[i]; }"])


def test_the_count_is_the_models_number(h):
    """4 is `wb_dma_types_pkg::WB_DMA_MAX_CH`, folded. A count that failed to
    fold and came through as -1 would make `WB_DMA_CH_SIZE` a negative loop
    bound -- which is why the array-size helper returns None rather than -1."""
    assert "#define WB_DMA_CH_SIZE 4u" in h
    assert "wb_dma_ch_c ch[4];" in h


# --- C1.3: prefixes come from the type name --------------------------------

def test_per_channel_operations_use_the_type_prefix(h):
    """`wb_dma_ch_c` -> `wb_dma_ch_`. Not an instance path: `ch[0]` and `ch[3]`
    share one body, told apart by the handle, which is the whole reason for
    passing one."""
    for op in ("transfer_single", "transfer_list", "stop_channel",
               "check_completion", "configure_channel", "probe_status",
               "set_auto_restart", "set_software_pointer", "wait_completion",
               "transfer_single_start", "transfer_list_start",
               "stop_channel_start", "wait_hint"):
        assert f"wb_dma_ch_{op}(wb_dma_ch_c *_self" in h, op
    # ...and NOT under the root's prefix, which is what a generator that keyed
    # off the instance would have produced.
    assert_c(h, has_not=["wb_dma_transfer_single(", "wb_dma_ch0_transfer_single("])


def test_the_whole_per_channel_surface_is_present(h):
    """The count, not just a sample. C1's failure mode was emitting SOME of the
    tree, and thirteen operations reduced to four is not a shape a spot check
    reliably notices.

    Counted on the PROTOTYPES rather than the definitions: the .c now also
    holds the baked accessors, which take the same handle and would be counted
    as operations. The header's export API is exactly the callable surface,
    which is what this test is about anyway.
    """
    n = h.count("wb_dma_ch_c *_self);") + h.count("wb_dma_ch_c *_self, ")
    assert n - h.count("wb_dma_ch_init(wb_dma_ch_c *_self") == 13


def test_prefix_collision_is_an_error():
    from pssc.targets.c.lower_progseq import Prefixes
    from pssc.targets.progseq_model import CompNode, CompKind

    class DT:
        def __init__(self, name):
            self.name = name
            self.fields = []

    a = CompNode(name="p::x_c", dtype=DT("p::x_c"), kind=CompKind.REGULAR)
    b = CompNode(name="q::x_c", dtype=DT("q::x_c"), kind=CompKind.REGULAR)
    root = CompNode(name="r_c", dtype=DT("r_c"), kind=CompKind.REGULAR,
                    children=[a, b])

    # `Prefixes` takes the elaborated model since P2.T4, so the component order
    # is the shared one rather than a walk of its own. A stand-in with just the
    # attribute it reads keeps this a test of the COLLISION rule.
    class _Model:
        components_root_first = (root, a, b)

    with pytest.raises(ValueError, match="--prefix-map"):
        Prefixes(_Model(), "r")


def test_prefix_map_is_the_escape_hatch(gen, tmp_path_factory):
    out = tmp_path_factory.mktemp("op_model_c_pfx")
    ns = argparse.Namespace(progseq_root="wb_dma_c", output_dir=str(out),
                            c_link_style="vtable",
                            c_prefix_map=["wb_dma_ch_c=chan"])
    driver.compile(_sources(), target="op-model-c", opts=ns)
    h = (out / "wb_dma.h").read_text()
    assert_c(h, has=["} chan;", "chan_transfer_single(chan *_self"],
             has_not=["} wb_dma_ch_c;"])


# --- C1.4: register accessors are per owning component ----------------------

def test_channel_registers_are_reached_through_the_channel_handle(c):
    """`wb_dma_ch_c.regs.csr` is at `ch->base_regs + 0`, because the channel's base
    IS its bank. That is what makes every per-channel operation body index-free
    -- `regs.csr.read()` with no channel number anywhere.

    In the .c: the accessors are implementation, and a caller reaches the
    device through the operations rather than through them."""
    assert_c(c, has=[
        "PSSC_MAYBE_UNUSED static inline pssc_addr_t "
        "wb_dma_ch_regs_csr_addr(const wb_dma_ch_c *_self) "
        "{ return _self->base_regs + 0x0u; }",
        "wb_dma_ch_regs_csr_write(wb_dma_ch_c *_self, wb_dma_csr_s v)",
    ])


def test_the_same_register_is_also_reachable_from_the_root(c):
    """Both spellings are emitted and both are right, because they take
    different handles: through the root the bank offset is still in the address.
    Folding them into one would mean picking a base, which means picking which
    call site to break."""
    assert ("PSSC_MAYBE_UNUSED static inline pssc_addr_t "
            "wb_dma_regs_bank_csr_addr("
            "const wb_dma_c *_self, int i0) "
            "{ return _self->base_regs + 0x20u + (pssc_addr_t)i0 * 0x20u; }") in c


def test_register_value_types_are_emitted_once(h, c, g):
    """The per-channel bank is reachable twice -- as `wb_dma_ch_c.regs` and as
    `wb_dma_c.regs.bank[i]` -- so an emitter that walked per component without
    deduplicating produced every channel value union twice, which C rejects as a
    redefinition.

    Counted ACROSS all three files, because the layouts are now partitioned
    between them: a struct that two of them claimed would be declared twice in
    one translation unit (the .c includes the .h and the private regs
    header), and counting any one file alone could not see it."""
    assert (h + c + g).count("} wb_dma_csr_s;") == 1
    assert (h + c + g).count("} wb_dma_gcsr_s;") == 1


def test_access_mode_is_honoured_per_register(c):
    """INT_SRC_A is READONLY in the RDL, so it gets no write accessor. A write
    the device ignores is worse than a compile error."""
    assert "wb_dma_regs_int_src_a_read(" in c
    assert_c(c, has_not=["wb_dma_regs_int_src_a_write("])


# --- C1.5: init folds the address arithmetic --------------------------------

def test_init_binds_the_base_and_constructs_every_channel(c):
    """`foreach (ch[i]) ch[i].initialize(i, make_handle_from_handle(base,
    regs.get_offset_of_instance_array("bank", i)))` -- with the offset query
    FOLDED. Nothing of the register group survives into the C: there is no group
    object to ask at run time, so a query that reached the output would name a
    function that does not exist."""
    assert_c(c, has=[
        "for (unsigned i = 0; i < 4u; i++) {",
        "wb_dma_ch_pss_ctor(&_self->ch[i], i, (base + (0x20u + 0x20u * i)));",
    ], has_not=["get_offset_of_instance", "set_handle"])


def test_every_channel_is_constructed_before_any_constructor_runs(c):
    """LRM 20.1.2: construction gives every instance its bus, channels and
    initial values, then the constructor bodies run. The channel's
    `initialize` call runs its body only (`_pss_ctor`), so the bus no longer
    rides on it."""
    body = c[c.index("static void wb_dma_pss_construct("):]
    body = body[:body.index("\n}")]
    assert ("for (unsigned i = 0; i < 4u; i++) "
            "wb_dma_ch_pss_construct(&_self->ch[i], _self->_bus);") in body
    init = c[c.index("void wb_dma_init("):]
    init = init[:init.index("\n}")]
    assert init.index("wb_dma_pss_construct(") < init.index("wb_dma_pss_ctor(")


def test_channel_init_binds_its_own_bank(c):
    """`regs.set_handle(bank)` on the channel -> its `regs` group's base. This is
    the per-instance half of the fold: the constant part is baked into the
    accessors, the instance part lands here."""
    body = c[c.index("void wb_dma_ch_pss_ctor("):]
    body = body[:body.index("\n}")]
    assert "_self->base_regs = bank" in body
    assert "_self->chan = id;" in body


def test_capability_defaults_are_assigned(c):
    """`wb_dma_ch_caps_s` declares every capability true, and operations gate on
    them. A channel initialised to all-false silently refuses work the device
    can do -- a driver reporting a device limitation that is really its own."""
    assert_c(c, has=["_self->caps.present = 1;", "_self->caps.ars = 1;",
                     "_self->caps.ed = 1;", "_self->caps.cbuf = 1;"])


def test_create_and_destroy_are_root_only(h):
    """A `wb_dma_ch_create()` would malloc a channel that no `wb_dma_c`
    contains: an allocation that compiles, runs, and is bound to nothing."""
    assert_c(h, has=["wb_dma_c *wb_dma_create(", "void wb_dma_destroy("],
             has_not=["wb_dma_ch_create(", "wb_dma_ch_destroy("])


# --- channels (C3, pulled forward -- the tree contains them) ----------------

def test_the_channel_members_are_real(h, c):
    assert_c(h, has=["    pssc_chan1_t inflight;", "    pssc_chan1_t wake;"])
    assert_c(c, has=["pssc_chan1_init(&_self->inflight);",
                     "pssc_chan1_init(&_self->wake);"])


def test_the_inflight_guard_survives_into_c(c):
    """The guard is the reason `channel_c` had to be lowered rather than
    rejected: it never blocks, and it is what detects polling a channel nobody
    armed. A C driver without it answers a stale CSR read the same way it
    answers a live one."""
    assert_c(c, has=["pssc_chan1_try_get(&_self->inflight, &tok)",
                     "pssc_chan1_try_put(&_self->inflight, tok)"])


def test_the_blocking_wait_is_absent_and_the_poll_is_present(c):
    """HAVE_EVENT_WAIT is false for this target, so `wait_hint()` takes its
    `yield` branch and `notify_irq()` -- the event PRODUCER -- does not exist.
    What must NOT happen is the end-to-end layer disappearing with it: that was
    the pre-M0 behaviour, and it gave firmware and UVM two different APIs for
    one device."""
    assert_c(c, has=["void wb_dma_ch_wait_hint(wb_dma_ch_c *_self) {",
                     "wb_dma_ch_wait_completion(wb_dma_ch_c *_self)",
                     "wb_dma_ch_transfer_single(wb_dma_ch_c *_self"],
             has_not=["wb_dma_notify_irq", "pssc_chan1_get(", "wake.get"],
             code=True)


# --- C0.8: write_field, which needed no C code at all -----------------------

def test_write_field_folds_to_a_masked_write(c):
    """`regs.csr.write_field("ars", enable)` -> `_write_masked(64, ...)`.

    C0.8 was deferred into C1 on the grounds that every `write_field` site in
    this model is per-channel and therefore untestable until the tree lowered.
    That was right about the testability and wrong about the work: the fold from
    a field NAME to a (mask, shift) pair happens in the shared front-end pass
    (`reg_rmw.py`), so the C target only ever sees `write_val_masked`, which it
    already rendered. This test is C0.8's entire deliverable.

    64 is bit 6, which is where `ars` sits in `wb_dma_csr_s`. A field resolver
    that silently returned 0 would produce `_write_masked(0, 0)` -- a
    read-modify-write that changes nothing and reports success.
    """
    assert "wb_dma_ch_regs_csr_write_masked(_self, 64, ((uint32_t)enable & 1) << 6);" in c


def test_each_write_field_is_its_own_read_modify_write(c):
    """`transfer_list_start` sets `use_ed` then `ch_en` as TWO masked writes.

    Coalescing them into one would be a different device interaction: arming the
    channel and selecting external descriptors in a single store, rather than in
    the order the model states. src/pss/README.md rules out `write_fields` for
    exactly this reason, and the C projection has to keep the separation the PSS
    was careful to make.
    """
    body = c[c.index("void wb_dma_ch_transfer_list_start("):]
    body = body[:body.index("\n}")]
    assert body.count("wb_dma_ch_regs_csr_write_masked(") == 2
    assert body.index("write_masked(_self, 128, 128)") < body.index("write_masked(_self, 1, 1)")


# --- lowering details a compiler would not catch ---------------------------

def test_message_strings_are_c_string_literals(c):
    """Python's `repr` quotes with apostrophes, and `'abc'` in C is a
    multi-character CHARACTER constant -- an int where a format string belongs.
    It survived C0 because no root-component body had a string in it."""
    assert 'pssc_message("wb_dma: check_completion() with no operation' in c
    assert "pssc_message('" not in c


def test_a_discarded_result_is_stated_not_dropped(c):
    """`wait_completion` calls `inflight.try_get(tok);` and discards the answer
    on purpose -- a `*_start()` that refused a double-start left no token, so
    failure here is expected rather than exceptional.

    The property under test is that the emitter **keeps the call**. It would be
    easy to treat a statement whose value nobody reads as dead, and it is not:
    `try_get` mutates the channel, which is the entire point of the line. An
    emitter that decides which of a model's statements are pointless will
    eventually be wrong about it.

    Updated 2026-08-13: the model previously spelled this `ok =
    inflight.try_get(tok)` with `ok` unused, and this test pinned the resulting
    `(void)ok;`. Upstream dropped the temp -- the discard is now direct -- so
    the `(void)` marker has nothing to mark. The assertion moves to the call
    itself, which is what the test was always really about.
    """
    assert "pssc_chan1_try_get(&_self->inflight, &tok);" in c
    # ...and it is a bare statement, not folded into a condition: the value is
    # discarded, not tested.
    assert "if (!(pssc_chan1_try_get(&_self->inflight, &tok)));" not in c


# --- the compile gate ------------------------------------------------------

@needs_cc
@pytest.mark.parametrize("cc", _CC)
def test_generated_driver_compiles_warning_free(gen, cc):
    out, _ = gen
    r = subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-c", "wb_dma.c",
         "-o", os.devnull],
        cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_cc
@pytest.mark.parametrize("link_style", ["vtable", "direct", "mmio"])
def test_every_link_style_still_generates_and_compiles(tmp_path_factory,
                                                       link_style):
    """The seam varies in ONE line (`pssc_bus`), which C1 turned from a function
    into a macro so that every component's handle type could use it. That change
    is invisible under `vtable` alone."""
    out = tmp_path_factory.mktemp(f"op_model_c_{link_style}")
    ns = argparse.Namespace(progseq_root="wb_dma_c", output_dir=str(out),
                            c_link_style=link_style)
    driver.compile(_sources(), target="op-model-c", opts=ns)
    src = "wb_dma.c" if (out / "wb_dma.c").exists() else None
    if src is None:                      # mmio is header-only
        (out / "probe.c").write_text('#include "wb_dma.h"\nint main(void){return 0;}\n')
        src = "probe.c"
    r = subprocess.run(
        [_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror", "-c", src,
         "-o", os.devnull],
        cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_cc
def test_no_allocator_below_the_root(gen):
    """`malloc` appears exactly where `_create` is, and nowhere else. A firmware
    image that links an allocator because a generated header pulled one in is a
    real and annoying failure; `--lifecycle static` removes even this one."""
    out, _ = gen
    c = (out / "wb_dma.c").read_text()
    assert c.count("malloc(") == 1
    assert "wb_dma_ch_init" in c and "malloc" not in c[c.index("wb_dma_ch_init"):
                                                       c.index("/* --- wb_dma_c ---")]


# --- C2: lifecycle ---------------------------------------------------------

@pytest.fixture(scope="module")
def gen_static(tmp_path_factory):
    out = tmp_path_factory.mktemp("op_model_c_static")
    ns = argparse.Namespace(progseq_root="wb_dma_c", output_dir=str(out),
                            c_link_style="vtable",
                            c_lifecycle="static")
    driver.compile(_sources(), target="op-model-c", opts=ns)
    return out


def test_static_lifecycle_emits_no_allocator_at_all(gen_static):
    """THE point of C2, and asserted as an absence over BOTH files because that
    is how it fails: a `_create` left in the header is a `malloc` in the image,
    and the link succeeds -- on a part with no heap it is the first call that
    fails, at runtime, in the field.

    `<stdlib.h>` too: on a freestanding target it need not exist, so an include
    that is merely unused on the workstation is a build failure on the part.
    """
    both = ((gen_static / "wb_dma.h").read_text() +
            (gen_static / "wb_dma.c").read_text())
    assert_c(both, has_not=["malloc", "free(", "<stdlib.h>",
                            "wb_dma_create", "wb_dma_destroy"])


def test_static_lifecycle_still_has_the_whole_api(gen_static):
    """Only the two allocation functions go. `_init` -- which the caller now
    reaches with storage of its own -- keeps its full signature, and every
    operation is still there. A lifecycle knob that quietly shrank the operation
    surface would be indistinguishable from a broken tree walk."""
    h = (gen_static / "wb_dma.h").read_text()
    assert_c(h, has=["void wb_dma_init(wb_dma_c *_self, const pssc_mem_if *_bus,"
                     " pssc_addr_t base);",
                     "void wb_dma_ch_init(",
                     "wb_dma_ch_transfer_single_start(",
                     "wb_dma_ch_wait_completion(",
                     "wb_dma_configure_interrupt_routing(",
                     "wb_dma_pause_engine("])


def test_the_struct_is_complete_not_opaque(gen_static):
    """C2.3. `static wb_dma_c dma;` needs the size, so the layout is in the
    header -- which makes it an ABI. Stated in the banner rather than left for a
    firmware author to discover by shipping a stale object file."""
    h = (gen_static / "wb_dma.h").read_text()
    assert_c(h, has=["} wb_dma_c;", "wb_dma_ch_c ch[4];",
                     "Component structs are COMPLETE types"],
             has_not=["typedef struct wb_dma_c wb_dma_c;"])


def test_the_isr_boundary_is_stated_in_the_generated_header(h):
    """C3.4. pssc_chan.h documents the mechanism; the driver header says which
    functions it applies to -- and that is the one a firmware author reads. The
    RMW in `try_put` is not atomic and this model holds two channels."""
    assert_c(h, has=["NOT ISR-SAFE", "PSSC_CHAN_ENTER"])


@needs_cc
@pytest.mark.parametrize("cc", _CC)
def test_static_lifecycle_compiles_and_the_object_links_no_allocator(
        gen_static, cc):
    """Compiled `-ffreestanding`, which is what the target actually is: it says
    there is no hosted library to fall back on, so an allocator reference would
    be a real unresolved symbol rather than a silently satisfied one."""
    r = subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-ffreestanding",
         "-c", "wb_dma.c", "-o", "wb_dma.o"],
        cwd=str(gen_static), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    nm = subprocess.run(["nm", "-u", "wb_dma.o"], cwd=str(gen_static),
                        capture_output=True, text=True)
    if nm.returncode == 0:
        assert "malloc" not in nm.stdout and "free" not in nm.stdout, nm.stdout


# --- C5.2: the compile gate ------------------------------------------------

def _gen_style(tmp_path, link_style, **kw):
    out = tmp_path / link_style
    out.mkdir(exist_ok=True)
    ns = argparse.Namespace(progseq_root="wb_dma_c", output_dir=str(out),
                            c_link_style=link_style, c_emit_stubs=True, **kw)
    driver.compile(_sources(), target="op-model-c", opts=ns)
    return out


def _tu(out, body):
    (out / "tu.c").write_text(body)
    return "tu.c"


@needs_cc
@pytest.mark.parametrize("cc", _CC)
@pytest.mark.parametrize("link_style", ["vtable", "direct", "mmio"])
def test_freestanding_across_every_link_style(tmp_path, cc, link_style):
    """`-ffreestanding` is what the target actually IS, and it is stricter than
    the plain `-Werror` build already covered: it says there is no hosted
    library, so an accidental dependency on one is an error here and a mystery
    later. Run per style because the seam is the part most likely to reach for
    something hosted.
    """
    out = _gen_style(tmp_path, link_style, c_lifecycle="static")
    src = _tu(out, '#include "wb_dma.h"\nint main(void) { return 0; }\n')
    srcs = [src] + (["wb_dma.c"] if (out / "wb_dma.c").exists() else [])
    r = subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "-ffreestanding",
         "-c", *srcs],
        cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_cc
@pytest.mark.parametrize("link_style", ["vtable", "direct", "mmio"])
def test_the_header_can_be_included_twice(tmp_path, link_style):
    """The include guard, checked by USING it.

    A missing or duplicated guard is invisible in a suite where every TU
    includes the header once. It surfaces in a real firmware build the first
    time two modules both include it through a third -- as a wall of
    redefinition errors far from the cause. `-Werror` catches a duplicated guard
    macro too, which a plain redefinition test would not.
    """
    out = _gen_style(tmp_path, link_style)
    src = _tu(out, '#include "wb_dma.h"\n#include "wb_dma.h"\n'
                   "int main(void) { return 0; }\n")
    r = subprocess.run(
        [_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror", "-c", src],
        cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_cc
def test_the_whole_driver_builds_for_a_32_bit_target(tmp_path):
    """The call seams take the address BY VALUE and never form a pointer from
    it, so a 64-bit `pssc_addr_t` on a 32-bit machine is fine for them. Asserted
    against the real driver rather than a probe header, because it is the
    generated offset arithmetic -- `_self->base + 0x20u + 0x20u * i`, all in
    `pssc_addr_t` -- that has to stay 64-bit-clean.
    """
    cc = _CC[0]
    probe = subprocess.run([cc, "-m32", "-E", "-x", "c", "-"],
                           input="#include <stdint.h>\n",
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip(f"{cc} cannot target 32-bit here (no multilib)")
    out = _gen_style(tmp_path, "vtable", c_lifecycle="static")
    r = subprocess.run(
        [cc, "-m32", "-std=c99", "-Wall", "-Wextra", "-Werror", "-ffreestanding",
         "-c", "wb_dma.c"],
        cwd=str(out), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_cc
def test_the_mmio_driver_refuses_a_32_bit_target(tmp_path):
    """The other half, and the one that matters: C4.5's assertion has to protect
    the REAL driver, not only the seam header in isolation. `mmio` forms a
    pointer from the address, so on a 32-bit target it truncates -- and this is
    the build where a firmware author would otherwise find out by shipping.
    """
    cc = _CC[0]
    probe = subprocess.run([cc, "-m32", "-E", "-x", "c", "-"],
                           input="#include <stdint.h>\n",
                           capture_output=True, text=True)
    if probe.returncode != 0:
        pytest.skip(f"{cc} cannot target 32-bit here (no multilib)")
    out = _gen_style(tmp_path, "mmio", c_lifecycle="static")
    src = _tu(out, '#include "wb_dma.h"\nint main(void) { return 0; }\n')
    r = subprocess.run([cc, "-m32", "-std=c99", "-c", src],
                       cwd=str(out), capture_output=True, text=True)
    assert r.returncode != 0, "the truncating mmio driver compiled for 32-bit"
    assert "use_addr_bits_32_or_vtable" in r.stderr, r.stderr


# --- the hierarchical API (op_api.py) -------------------------------------------

_HIER = """
component leaf_c {
  int id;
  target function int peek() { return id; }
  private target function int hidden() { return id + 1; }
}
component quiet_c { private target function int nothing() { return 0; } }
component mid_c { leaf_c lf; quiet_c q; }
component dma_c {
  int tag;
  target function int go(int n) { return tag + n + helper(); }
private:
  target function int helper() { return 1; }
}
component soc_c {
  dma_c dma0, dma1;
  leaf_c pit[3];
  mid_c m;
  quiet_c q;
  solve function void initialize(int k) {
    dma0.tag = 10 + k; dma1.tag = 20;
    pit[0].id = 0; pit[1].id = 1; pit[2].id = 2;
    m.lf.id = 99;
  }
}
"""

_HIER_MAIN = r"""
#include <stdio.h>
#include "soc.h"
int main(void) {
    soc_c soc;
    unsigned i;
    soc_init(&soc, 0);
    printf("go %d %d\n", dma_go(soc_dma0(&soc), 1), dma_go(soc_dma1(&soc), 2));
    for (i = 0; i < soc_pit_size(&soc); i++)
        printf("peek %d\n", leaf_peek(soc_pit(&soc, i)));
    printf("lf %d size %u\n", leaf_peek(mid_lf(soc_m(&soc))), SOC_PIT_SIZE);
    return 0;
}
"""


@pytest.fixture(scope="module")
def hier(tmp_path_factory):
    d = tmp_path_factory.mktemp("c_hier")
    (d / "m.pss").write_text(_HIER)
    ns = argparse.Namespace(progseq_root="soc_c", c_prefix="soc",
                            c_link_style="mmio", c_lifecycle="static",
                            output_dir=str(d / "gen"))
    driver.compile([str(d / "m.pss")], target="op-model-c", opts=ns)
    return d


def test_the_header_declares_the_api(hier):
    """Only the API's functions are declared; private ones are `static` in
    the .c. Accessors lead only toward the API: `q` (twice) has none."""
    h = (hier / "gen" / "soc.h").read_text()
    assert_c(h, code=True,
             has=["int dma_go(dma_c *_self, int n);",
                  "int leaf_peek(leaf_c *_self);",
                  "static inline dma_c *soc_dma0(soc_c *_self)",
                  "static inline leaf_c *soc_pit(soc_c *_self, unsigned i)",
                  "static inline unsigned soc_pit_size(soc_c *_self)",
                  "#define SOC_PIT_SIZE 3u",
                  "static inline leaf_c *mid_lf(mid_c *_self)"],
             has_not=["dma_helper", "leaf_hidden", "quiet_nothing",
                      "soc_q(", "mid_q("])
    c = (hier / "gen" / "soc.c").read_text()
    assert "static inline int dma_helper(dma_c *_self)" in c


@needs_cc
def test_the_api_reaches_each_instance(hier):
    """Built with -Werror and run: each accessor reaches its own instance."""
    (hier / "main.c").write_text(_HIER_MAIN)
    exe = hier / "a.out"
    build = subprocess.run(
        [_CC[0], "-std=c99", "-Wall", "-Wextra", "-Werror",
         f"-I{hier / 'gen'}", "-o", str(exe), str(hier / "main.c"),
         str(hier / "gen" / "soc.c")], capture_output=True, text=True)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True)
    assert run.stdout.split("\n")[:5] == [
        "go 12 23", "peek 0", "peek 1", "peek 2", "lf 99 size 3"], run.stdout
