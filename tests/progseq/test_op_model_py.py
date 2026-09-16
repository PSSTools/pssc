"""`op-model-py`: the generated module is imported and DRIVEN, not grepped.

Every other backend's tests can only read the text they produce -- checking
generated C means compiling it, which is a slow marked test, and checking
generated SystemVerilog means a simulator. This one runs in-process, so the
questions a golden snapshot cannot answer ("is the address right", "does the
completion poll terminate", "does the read-modify-write preserve the bits it
should") are asked of the RUNNING model here.

That is also why this file matters beyond its own target: the addresses it
checks come from `targets/reg_layout.py`, which is the same walk the C backend
folds its accessors from. An offset wrong here is wrong in three languages.

WHAT IS DELIBERATELY NOT HERE: a cocotb-in-simulator run of the async form. It
is the obvious next thing, it needs a simulator in the loop, and none of the
design decisions depend on it -- the duck-typed event (`pssc_rt_async.Chan1`
takes the platform's `event()` and never names a scheduler) is what keeps it
reachable later without regenerating anything. Its absence is a decision, not an
oversight.

Plan: P8.T1; `docs/op-model-py-async-plan.md`.
"""
from __future__ import annotations

import importlib
import re
import subprocess
import sys
import textwrap

import pytest

from pssc.testing import compile_op_model, conformance

from .op_model import op_model_sources as wb_dma_sources

#: The WB DMA operation model exercises what the bundled one does not: a
#: component TREE, a sub-component array, channels, declared imports and
#: `yield`. Both are used here, and the split is deliberate -- the bundled
#: model is what a plugin author has, so anything checked only against the real
#: one is a claim they cannot reproduce.
WB_DMA_ROOT = "wb_dma_c"


def _load(outcome, module: str):
    """Import a freshly generated module out of its output directory."""
    sys.path.insert(0, str(outcome.out_dir))
    try:
        for name in (module, "pssc_rt"):
            sys.modules.pop(name, None)
        rt = importlib.import_module("pssc_rt")
        mod = importlib.import_module(module)
        return mod, rt
    finally:
        sys.path.remove(str(outcome.out_dir))


@pytest.fixture(scope="module")
def bundled():
    """The bundled model, generated, imported and ready to drive."""
    with compile_op_model("op-model-py") as outcome:
        mod, rt = _load(outcome, "dma_engine")
        yield mod, rt, outcome


@pytest.fixture(scope="module")
def wb_dma():
    """The real WB DMA operation model: a tree, channels, imports, `yield`."""
    with compile_op_model("op-model-py", sources=wb_dma_sources(),
                          root=WB_DMA_ROOT) as outcome:
        mod, rt = _load(outcome, "wb_dma")
        yield mod, rt, outcome


# --- what is produced -------------------------------------------------------

def test_it_produces_a_module_and_the_runtime(bundled):
    _, _, outcome = bundled
    assert outcome.names == ["dma_engine.py", "pssc_rt.py"]


#: What a generated module is allowed to import. Everything here ships with the
#: interpreter; `pssc_rt` deliberately does not appear, which is the whole
#: content of the assertion below.
_STDLIB_ONLY = {"__future__", "typing"}


def test_the_generated_module_imports_only_the_standard_library(bundled):
    """The zero-install property, asserted rather than hoped for.

    A generated driver gets copied onto a lab machine. A single file that needs
    no install still runs there, and that is worth a test because it is the kind
    of property one convenience import silently ends.

    NOT "imports nothing", which is what this asserted before the generated
    import API landed: `typing.Protocol` is how the seam states its own
    requirement, and `from __future__ import annotations` is what lets it name
    classes defined further down. Both ship with Python. The line that would
    break the property is one naming a package that does not -- `pssc_rt`
    included, which a model without channels must not need.
    """
    _, _, outcome = bundled
    modules = [l.split()[1] for l in outcome.read("dma_engine.py").splitlines()
               if l.startswith(("import ", "from "))]
    assert set(modules) <= _STDLIB_ONLY, modules


# --- the generated import API ------------------------------------------------

def test_the_protocol_declares_exactly_what_the_bodies_call(wb_dma):
    """T5.1. Both directions, and they are deliberately not symmetric.

    Anything a body calls on the seam MUST be a member -- a Protocol that omits
    one is a lie, and a platform satisfying it still gets an AttributeError.
    A member that nothing calls is allowed only if the model DECLARED it as an
    import: that set is the platform's contract, exactly as the C++ target
    treats it (`cpp/lower_progseq.emit_import_api`).
    """
    from pssc.targets.py.lower_import_api import imports_used

    _, _, outcome = wb_dma
    text = outcome.read("wb_dma.py")
    proto = text.split("class WbDmaImportApi(Protocol):", 1)[1]
    proto = proto.split("\n# -----", 1)[0]
    declared = set(re.findall(r"def (\w+)\(self", proto))

    called = imports_used(text)
    assert called <= declared, sorted(called - declared)

    # The model declares one import (`message` is a built-in, `notify_irq` is
    # the declared one), so the only slack allowed is the declared set.
    model_imports = {"notify_irq"}
    assert declared - called <= model_imports, sorted(declared - called)


def test_a_width_the_model_never_uses_is_not_demanded_of_the_platform(wb_dma):
    """The Protocol is per-model, which is the whole reason it beats a base
    class: WB DMA is a 32-bit device, so nothing should be asked to implement
    `read8` or `read64`."""
    _, _, outcome = wb_dma
    proto = outcome.read("wb_dma.py").split(
        "class WbDmaImportApi(Protocol):", 1)[1].split("\n# -----", 1)[0]
    assert "read32" in proto
    for absent in ("read8", "read16", "read64", "write8", "write16", "write64"):
        assert absent not in proto, absent


def test_the_protocol_is_runtime_checkable_and_the_stub_satisfies_it(wb_dma):
    """T5.7. `@runtime_checkable` is emitted, and `MemoryBus` really does meet
    the seam -- a check that fails only at a user's `isinstance` otherwise."""
    mod, rt, _ = wb_dma
    assert isinstance(rt.MemoryBus(), mod.WbDmaImportApi)


def test_check_import_api_names_what_is_missing(wb_dma):
    """T5.6. The half `isinstance` cannot do: WHICH member, not just no."""
    mod, rt, _ = wb_dma
    assert rt.check_import_api(rt.MemoryBus(), mod.WbDmaImportApi) == []

    class Partial:
        def read32(self, addr): return 0

    problems = rt.check_import_api(Partial(), mod.WbDmaImportApi)
    assert any("write32" in p and "missing" in p for p in problems), problems
    assert any("message" in p for p in problems), problems


def test_check_import_api_catches_a_mis_coloured_method(wb_dma):
    """T5.6, the half that is the reason this function exists at all.

    An `async def` where the sync form wants a plain one passes `isinstance`
    and then hands the model a coroutine object where it expects an integer --
    a register value of the wrong type, arbitrarily far from its cause.
    """
    mod, rt, _ = wb_dma

    class Coloured(rt.MemoryBus):
        async def read32(self, addr): return 0

    problems = rt.check_import_api(Coloured(), mod.WbDmaImportApi)
    assert any("read32" in p and "async" in p for p in problems), problems


def test_check_import_api_reports_a_wrong_arity(wb_dma):
    mod, rt, _ = wb_dma

    class Narrow(rt.MemoryBus):
        def write32(self, addr): pass

    problems = rt.check_import_api(Narrow(), mod.WbDmaImportApi)
    assert any("write32" in p for p in problems), problems


#: A model whose imports are the platform's, not pssc's: neither name is known
#: to the generator, one is a target function and one is a solve function, and
#: `plat_reset` is DECLARED AND NEVER CALLED -- which is the case that separates
#: "what the bodies need" from "what the platform is contracted to supply".
_IMPORTS_MODEL = """
package plat_pkg {
    import target function void plat_delay_us(int us);
    import solve  function int  plat_ticks();
    import target function void plat_reset();
}

component imp_c {
    import plat_pkg::*;

    target function int spin(int n) {
        int t;
        plat_delay_us(n);
        t = plat_ticks();
        return t;
    }
}
"""


@pytest.fixture(scope="module")
def imports_model(tmp_path_factory):
    src = tmp_path_factory.mktemp("imp") / "imp.pss"
    src.write_text(_IMPORTS_MODEL)
    with compile_op_model("op-model-py", sources=[str(src)],
                          root="imp_c") as outcome:
        yield outcome


def test_every_declared_import_reaches_the_protocol_called_or_not(imports_model):
    """The C++ target's rule, and the reason for it is unchanged: the declared
    set is the platform's CONTRACT. A platform implementing one function too
    many pays nothing; one discovering a requirement later pays a rebuild."""
    text = imports_model.read("imp.py")
    proto = text.split("class ImpImportApi(Protocol):", 1)[1].split(
        "\n# -----", 1)[0]
    assert "def plat_delay_us(self, us: int) -> None:" in proto
    assert "def plat_ticks(self) -> int:" in proto
    # Never called by any body, and required anyway.
    assert "def plat_reset(self) -> None:" in proto
    assert "self._imports.plat_reset(" not in text


def test_a_call_on_the_seam_the_protocol_cannot_describe_is_refused():
    """The Protocol is generated from the output, so a member the bodies call
    and nothing declares would leave a platform meeting the Protocol and still
    failing at the call. That is a generator defect, and it raises."""
    from pssc.targets.py.lower_import_api import lower_import_api

    model = type("M", (), {"root": type("R", (), {"name": "imp_c"}),
                           "imports": {}, "components": (),
                           "channels": staticmethod(lambda d: ())})()
    with pytest.raises(ValueError, match="plat_delay_us"):
        lower_import_api(model, used={"plat_delay_us"})


def test_a_model_with_channels_imports_the_channel_and_says_why(wb_dma):
    _, _, outcome = wb_dma
    text = outcome.read("wb_dma.py")
    assert "from pssc_rt import Chan1" in text
    assert "declares channels" in text


def test_no_core_copy_leaves_the_module_alone(tmp_path):
    with compile_op_model("op-model-py", output_dir=str(tmp_path),
                          progseq_core_copy=False) as outcome:
        assert outcome.names == ["dma_engine.py"]


def test_py_module_renames_the_module(tmp_path):
    with compile_op_model("op-model-py", output_dir=str(tmp_path),
                          py_module="acme_dma") as outcome:
        assert "acme_dma.py" in outcome.names
        assert "from acme_dma import DmaEngine" in outcome.read("acme_dma.py")


# --- addresses --------------------------------------------------------------

def test_folded_addresses_match_the_model(bundled):
    """The offsets in `dma_regs.pss`, recomputed by the accessor methods.

    Hard-coded on purpose. These come from the map the model's own header
    comment documents, so a change to the offset fold that agreed with itself
    would still fail here.
    """
    mod, rt, _ = bundled
    dut = mod.DmaEngine(rt.MemoryBus(), 0x4000)
    assert dut.regs_CSR_addr() == 0x4000
    assert dut.regs_INT_MSK_A_addr() == 0x4004
    assert dut.regs_INT_SRC_B_addr() == 0x4010
    # channels[] is at 0x20, stride 0x20; CSR is +0x00 and SWPTR is +0x1c.
    assert dut.regs_channels_CSR_addr(0) == 0x4020
    assert dut.regs_channels_CSR_addr(3) == 0x4080
    assert dut.regs_channels_SWPTR_addr(3) == 0x409c


def test_reserved_registers_are_not_surfaced(bundled):
    """`_reserved0..2` hold the 0x14..0x1f gap open and get no accessor."""
    mod, _, _ = bundled
    assert not [n for n in dir(mod.DmaEngine) if "_reserved" in n]


def test_a_sub_component_carries_its_own_base(wb_dma):
    """The same physical register, reached two ways, is the same address.

    `ch[2].regs.csr` and the root's `regs.bank[2].csr` are one register. The
    channel object was constructed at its own base, so its accessor folds a
    different constant and must arrive at the same place -- which is the
    property `conformance._group_bases` exists to allow for, checked here on a
    live object rather than by grepping for a number.
    """
    mod, rt, _ = wb_dma
    dut = mod.WbDma(rt.MemoryBus(), 0x2000)
    assert dut.regs_bank_csr_addr(2) == dut.ch_at(2).regs_csr_addr()
    assert dut.ch_at(2).regs_csr_addr() == 0x2000 + 0x20 + 2 * 0x20


# --- value classes ----------------------------------------------------------

def test_a_value_class_round_trips(bundled):
    mod, _, _ = bundled
    v = mod.dma_ch_csr_s(CH_EN=1, PRIORITY=5, DONE=1)
    raw = v.pack()
    assert raw == (1 << 0) | (5 << 13) | (1 << 11)
    back = mod.dma_ch_csr_s.unpack(raw)
    assert back == v and back.PRIORITY == 5


def test_a_field_is_masked_to_its_own_width(bundled):
    """An out-of-range assignment corrupts its own field and no other."""
    mod, _, _ = bundled
    v = mod.dma_ch_csr_s(PRIORITY=0xff, REST_EN=1)
    assert mod.dma_ch_csr_s.unpack(v.pack()).REST_EN == 1


def test_a_misspelled_field_is_refused(bundled):
    mod, _, _ = bundled
    with pytest.raises(TypeError) as exc:
        mod.dma_ch_csr_s(PRIORTY=1)
    assert "has no field" in str(exc.value)


# --- driving ----------------------------------------------------------------

def test_an_operation_issues_the_accesses_the_model_states(bundled):
    mod, rt, _ = bundled
    bus = rt.MemoryBus()
    dut = mod.DmaEngine(bus, 0x1000)
    dut.configure_channel(2, 3, 1, 0, 1)
    # One write, to channel 2's CSR, with exactly the fields the body sets and
    # CH_EN LEFT CLEAR -- which is the whole contract of configure_channel.
    assert [(k, hex(a)) for k, _, a, _ in bus.log] == [("write", "0x1060")]
    csr = mod.dma_ch_csr_s.unpack(bus.log[0][3])
    assert (csr.PRIORITY, csr.MODE, csr.SRC_SEL, csr.DST_SEL) == (3, 1, 0, 1)
    assert (csr.INC_SRC, csr.INC_DST, csr.CH_EN) == (1, 1, 0)


def test_the_arming_write_is_a_read_modify_write(bundled):
    """The bits a previous configure left in the CSR survive being armed.

    The model says so in its own comment ("never a blind write"), and this is
    the check that the lowering kept it: a blind write would clear PRIORITY.
    """
    mod, rt, _ = bundled
    bus = _completing_bus(rt, channel_base=0x1000 + 0x20 + 2 * 0x20)
    dut = mod.DmaEngine(bus, 0x1000)
    dut.configure_channel(2, 3, 1, 0, 1)
    bus.log.clear()
    assert dut.mem_to_mem_copy(2, 0xdead0000, 0xbeef0000, 64) == 0
    armed = [e for e in bus.log if e[0] == "write" and e[2] == 0x1060][-1]
    csr = mod.dma_ch_csr_s.unpack(armed[3])
    assert csr.CH_EN == 1 and csr.PRIORITY == 3


def test_sizes_are_programmed_in_words(bundled):
    """`nbytes / 4` is INTEGER division in PSS, and `/` in Python is not.

    64 bytes is 16 words. Rendered with Python's `/` this would be `16.0`, and
    `pack()` would raise or the bus would be handed a float -- so the failure
    is loud, but the point is that the operator is a translation and not a
    spelling.
    """
    mod, rt, _ = bundled
    bus = _completing_bus(rt, channel_base=0x1020)
    dut = mod.DmaEngine(bus, 0x1000)
    dut.mem_to_mem_copy(0, 0, 0, 64)
    sz = [e for e in bus.log if e[0] == "write" and e[2] == 0x1024][0]
    assert isinstance(sz[3], int)
    assert mod.dma_ch_sz_s.unpack(sz[3]).TOT_SZ == 16


def test_the_completion_poll_returns_the_devices_error(bundled):
    """ERR set on the first read returns 1 and stops polling."""
    mod, rt, _ = bundled
    bus = rt.MemoryBus()
    bus.mem[0x1020] = 1 << 12         # channel 0 CSR: ERR
    dut = mod.DmaEngine(bus, 0x1000)
    assert dut.mem_to_mem_copy(0, 0, 0, 4) == 1


def test_the_poll_reads_at_least_once(bundled):
    """`repeat {} while` is a do-while, and the rewrite has to preserve that.

    With DONE already set, a `while` would read zero times and a `do-while`
    reads once. The device that completed before the poll began is exactly the
    case the model wrote `repeat` for.
    """
    mod, rt, _ = bundled
    bus = _completing_bus(rt, channel_base=0x1020)
    dut = mod.DmaEngine(bus, 0x1000)
    bus.log.clear()
    dut.mem_to_mem_copy(0, 0, 0, 4)
    reads = [e for e in bus.log if e[0] == "read" and e[2] == 0x1020]
    assert len(reads) >= 2      # the read-modify-write's, then the poll's


def _completing_bus(rt, *, channel_base: int):
    """A bus whose channel reports DONE once the channel has been armed.

    `MemoryBus` alone cannot terminate a completion poll -- it is a memory, and
    a status bit nobody sets never sets. Its own docstring says so. This is the
    smallest thing that is a DEVICE.
    """
    class Device(rt.MemoryBus):
        def read32(self, addr):
            v = super().read32(addr)
            return (v | (1 << 11)) if (addr == channel_base and v & 1) else v

    return Device()


# --- the tree, channels, imports --------------------------------------------

def test_the_component_tree_is_constructed_with_folded_bases(wb_dma):
    mod, rt, _ = wb_dma
    dut = mod.WbDma(rt.MemoryBus(), 0x2000)
    assert dut.ch_size() == 4
    assert [dut.ch_at(i)._base for i in range(4)] == [
        0x2000 + 0x20 + 0x20 * i for i in range(4)]


def test_a_channel_output_local_is_a_cell(wb_dma):
    """`try_get(tok)` writes an output argument, which Python cannot do.

    The local is a one-element list and every read of it is `tok[0]`. Checked
    against the generated text as well as by driving, because the defect this
    replaced -- the token silently never assigned -- produced a module that
    imported, ran, and handed the wrong value back.
    """
    mod, rt, outcome = wb_dma
    text = outcome.read("wb_dma.py")
    assert "tok = [0]" in text
    assert "self.inflight.try_get(tok)" in text
    assert "self.inflight.try_put(tok[0])" in text


def test_a_completion_token_survives_a_probe_that_finds_it_running(wb_dma):
    """`check_completion` takes the token, finds the channel busy, returns it.

    The round trip is the whole reason `try_get`'s output argument matters: the
    value handed back by `try_put` is the one `try_get` wrote.
    """
    mod, rt, _ = wb_dma
    bus = rt.MemoryBus()
    dut = mod.WbDma(bus, 0x2000)
    ch = dut.ch_at(0)
    ch.inflight.try_put(0x5a)
    bus.mem[ch._base] = 1 << 10          # BUSY, not DONE: still running
    ch.check_completion()
    assert ch.inflight.full and ch.inflight.value == 0x5a


def test_a_blocking_channel_call_raises_rather_than_inventing_an_answer(wb_dma):
    _, rt, _ = wb_dma
    with pytest.raises(rt.ChannelEmpty):
        rt.Chan1().get()


def test_yield_lowers_to_a_comment_and_the_suite_still_parses(wb_dma):
    """A body of only `yield` is a C block with nothing in it and a Python
    syntax error. `block()` supplies the `pass`; the module imported, which is
    the check."""
    _, _, outcome = wb_dma
    text = outcome.read("wb_dma.py")
    assert "# yield: nothing to yield to on this target" in text
    assert "\n            pass\n" in text or "\n        pass\n" in text


def test_an_import_function_is_called_on_the_import_api(wb_dma):
    """A declared `import` is a PLATFORM function, so it is not a method of the
    component and is not invented as one."""
    _, _, outcome = wb_dma
    text = outcome.read("wb_dma.py")
    assert "self._imports.message(" in text


# --- the form knob -----------------------------------------------------------

#: Members the async form adds to the import API and the sync form has no use
#: for. Not drift: `yield_` is what a PSS `yield` lowers to when there IS a
#: scheduler, and `event` is what a blocking channel needs. Excluded here so
#: the comparison is about everything else.
_ASYNC_ONLY_MEMBERS = {"yield_", "event"}


def _uncoloured(text: str):
    """*text* as a syntax tree with the colouring removed.

    A TREE rather than the text, and the difference matters. Stripping `async `
    and `await ` from the source and comparing strings would be a comparison of
    LAYOUT: it fails on a wrapped line and it cannot see that `await f() & mask`
    and `(await f()) & mask` are different programs. Parsing both and erasing
    the colour compares what the two modules MEAN, which is the claim -- the
    async form is the sync form, coloured.

    Comments do not survive, deliberately. The one comment that legitimately
    differs is the sync form's `# yield: ...`, whose async counterpart is a call.
    """
    import ast

    class _Erase(ast.NodeTransformer):
        def visit_AsyncFunctionDef(self, node):
            self.generic_visit(node)
            return ast.FunctionDef(
                name=node.name, args=node.args, body=node.body,
                decorator_list=node.decorator_list, returns=node.returns,
                type_comment=None, type_params=[])

        def visit_Await(self, node):
            self.generic_visit(node)
            return node.value

        def visit_ClassDef(self, node):
            node.body = [b for b in node.body
                         if getattr(b, "name", None) not in _ASYNC_ONLY_MEMBERS]
            self.generic_visit(node)
            return node

    tree = _Erase().visit(ast.parse(text))
    # The MODULE docstring is dropped: it is the banner, whose usage snippet
    # names a different runtime and wraps the call in an event loop. That
    # difference is prose about how to drive the module, not a difference in
    # what the module does -- and it has its own test, which runs it.
    if (tree.body and isinstance(tree.body[0], ast.Expr)
            and isinstance(tree.body[0].value, ast.Constant)):
        tree.body = tree.body[1:]
    return ast.dump(ast.fix_missing_locations(tree), indent=1)


def _gen_both(tmp_path, order=("sync", "async"), **kw):
    """Both forms, generated IN ONE INTERPRETER, in the order given.

    The order is a parameter because the per-run legality registration
    (`PyProgSeqTarget._register_legality`) is process-global: it is re-published
    per generation, and a registration left where it cannot see the form would
    hand the second generation the first one's answer. Running both orders is
    what fails if that hook moves.
    """
    out = {}
    for style in order:
        with compile_op_model(
                "op-model-py", output_dir=str(tmp_path / f"{style}-{order[0]}"),
                py_await=style, **kw) as o:
            out[style] = o.read("dma_engine.py")
    return out


@pytest.mark.parametrize("order", [("sync", "async"), ("async", "sync")])
def test_the_async_form_is_the_sync_form_with_a_colour_on_it(tmp_path, order):
    """T5.2. Token equivalence, in both generation orders.

    `HAVE_EVENT_WAIT` is pinned FALSE, and that pin is the whole reason this
    test says something. Left true, the async form would compile a DIFFERENT
    MODEL -- the `compile if` branches the model guards with it -- and the
    comparison would be vacuous. The true case is covered separately, where the
    difference is the point rather than the noise.
    """
    both = _gen_both(tmp_path, order=order,
                     target_cfg=["HAVE_EVENT_WAIT=false"])
    assert _uncoloured(both["async"]) == _uncoloured(both["sync"])


def test_the_form_decides_what_the_model_is_told_it_can_do():
    """`HAVE_EVENT_WAIT` is a property of the FORM. Not a cosmetic difference:
    the model reads it in a `compile if` and takes a different branch."""
    import argparse

    from pssc.targets import get as get_target

    tgt = get_target("op-model-py")
    sync = argparse.Namespace(py_await="sync", target_cfg=None)
    aio = argparse.Namespace(py_await="async", target_cfg=None)
    assert tgt.resolved_target_cfg_for(sync)["HAVE_EVENT_WAIT"] is False
    assert tgt.resolved_target_cfg_for(aio)["HAVE_EVENT_WAIT"] is True
    # The no-options answer -- what `pssc targets` prints -- is the DEFAULT
    # form's, which is what makes the listing truthful rather than merely
    # non-empty.
    assert tgt.resolved_target_cfg()["HAVE_EVENT_WAIT"] is False


# --- driving the async form --------------------------------------------------

@pytest.fixture(scope="module")
def wb_dma_async(tmp_path_factory):
    """The WB DMA model in its async form -- with `HAVE_EVENT_WAIT` TRUE.

    Which is the point of the fixture: the async form publishes that capability,
    so `wait_hint()` compiles to `wake.get()` rather than to a `yield`, and
    `notify_irq()` exists to release it. That is a different model, deliberately,
    and it is where the blocking channel gets exercised at all.
    """
    out = tmp_path_factory.mktemp("wb-async")
    with compile_op_model("op-model-py", sources=wb_dma_sources(),
                          root=WB_DMA_ROOT, output_dir=str(out),
                          py_await="async") as outcome:
        sys.path.insert(0, str(outcome.out_dir))
        try:
            for name in ("wb_dma", "pssc_rt", "pssc_rt_async"):
                sys.modules.pop(name, None)
            rt = importlib.import_module("pssc_rt_async")
            mod = importlib.import_module("wb_dma")
        finally:
            sys.path.remove(str(outcome.out_dir))
        yield mod, rt, outcome


@pytest.fixture(scope="module")
def bundled_async(tmp_path_factory):
    """The bundled model in its async form, imported and ready to await."""
    out = tmp_path_factory.mktemp("async")
    with compile_op_model("op-model-py", output_dir=str(out),
                          py_await="async") as outcome:
        sys.path.insert(0, str(outcome.out_dir))
        try:
            for name in ("dma_engine", "pssc_rt", "pssc_rt_async"):
                sys.modules.pop(name, None)
            rt = importlib.import_module("pssc_rt_async")
            mod = importlib.import_module("dma_engine")
        finally:
            sys.path.remove(str(outcome.out_dir))
        yield mod, rt, outcome


def test_the_async_form_ships_both_runtime_halves(bundled_async):
    _, _, outcome = bundled_async
    assert outcome.names == ["dma_engine.py", "pssc_rt.py", "pssc_rt_async.py"]


@pytest.mark.asyncio
async def test_the_two_forms_issue_the_same_accesses(bundled, bundled_async):
    """T5.3. The test the parenthesisation hazard cannot survive.

    Both forms run the same operation against the same starting memory, and the
    two access logs must be IDENTICAL -- width, address, data, and order. That
    is a claim no golden snapshot can make: `await self.f() & mask` parses,
    means `await (self.f() & mask)`, raises nothing, and writes a value that is
    simply wrong. It shows up here as a different `data` on one entry.

    The order matters as much as the values. An `await` moved across an
    assignment does not change what is computed but does change WHEN, and on a
    register whose read clears bits that is a different program.
    """
    sync_mod, sync_rt, _ = bundled
    aio_mod, aio_rt, _ = bundled_async

    sync_bus = _completing_bus(sync_rt, channel_base=0x1000 + 0x20 + 2 * 0x20)
    aio_bus = _completing_async_bus(
        aio_rt, channel_base=0x1000 + 0x20 + 2 * 0x20)

    sync_dut = sync_mod.DmaEngine(sync_bus, 0x1000)
    aio_dut = aio_mod.DmaEngine(aio_bus, 0x1000)

    sync_dut.configure_channel(2, 3, 1, 0, 1)
    await aio_dut.configure_channel(2, 3, 1, 0, 1)
    assert sync_dut.mem_to_mem_copy(2, 0xdead0000, 0xbeef0000, 64) == \
        await aio_dut.mem_to_mem_copy(2, 0xdead0000, 0xbeef0000, 64)

    assert aio_bus.log == sync_bus.log


def _completing_async_bus(rt, *, channel_base: int):
    """`_completing_bus`, coloured. Same device, same DONE rule."""
    class Device(rt.AsyncMemoryBus):
        async def read32(self, addr):
            v = await super().read32(addr)
            return (v | (1 << 11)) if (addr == channel_base and v & 1) else v

    return Device()


@pytest.mark.asyncio
async def test_a_blocking_channel_receive_really_suspends(wb_dma_async):
    """T5.4. The whole payoff of the async form, and the one thing a token
    comparison cannot check.

    `wait_completion()` polls the CSR and then waits on `wake`. In the async
    form that wait is a real suspension: nothing else in this test can run until
    it yields, and it is a concurrent `notify_irq()` that releases it. If
    `wake.get()` had been lowered to a spin, the CSR read count would climb with
    the sleep below instead of staying at its minimum.
    """
    import asyncio

    mod, rt, _ = wb_dma_async
    bus = rt.AsyncMemoryBus()
    dut = mod.WbDma(bus, 0x2000)
    ch = dut.ch_at(0)
    bus.mem[ch._base] = 1 << 10          # BUSY: the poll will not terminate

    async def complete():
        # Let the waiter reach its suspension, then post -- via the model's own
        # event producer, which is what a platform's ISR would call.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        bus.mem[ch._base] = 1 << 11      # DONE
        await dut.notify_irq()

    ch.inflight.try_put(1)
    status, _ = await asyncio.wait_for(
        asyncio.gather(ch.wait_completion(), complete()), timeout=2.0)
    assert status == mod.WB_DMA_DONE

    reads = [e for e in bus.log if e[0] == "read" and e[2] == ch._base]
    # Two probes: the one that found it BUSY and the one after the wake. A spin
    # would have made this grow with the sleeps above.
    assert len(reads) == 2, bus.log


def test_a_blocking_channel_call_is_refused_in_sync_and_rendered_in_async():
    """T5.5. The one call whose legality depends on the form.

    `get()`/`put()` suspend. Sync has nowhere to suspend to, so the registry
    refuses them -- and the diagnostic must name the form and the way out,
    because "this backend cannot" stopped being true of one of the two forms.
    """
    from pssc.targets import get as get_target
    from pssc.targets.call_legality import Ctx, classify

    tgt = get_target("op-model-py")
    ask = lambda n: classify(n, context=Ctx.TARGET,   # noqa: E731
                             target="op-model-py")

    tgt._register_legality("sync")
    res = ask("get")
    assert not res.ok
    assert "--py-await async" in res.message, res.message

    tgt._register_legality("async")
    assert ask("get").ok
    assert ask("put").ok

    # Left as the default, so nothing later in this process inherits the async
    # answer from a test.
    tgt._register_legality("sync")


def test_the_async_module_still_imports_only_the_standard_library(
        bundled_async):
    """T5.10. The zero-install property survives the second form.

    A model with no channels must reach nothing outside the interpreter in
    EITHER form -- the async lowering adds `await`, which is syntax, not a
    dependency. `pssc_rt_async` appears only where a channel does.
    """
    _, _, outcome = bundled_async
    modules = [l.split()[1] for l in outcome.read("dma_engine.py").splitlines()
               if l.startswith(("import ", "from "))]
    assert set(modules) <= _STDLIB_ONLY, modules


def test_an_async_model_with_channels_takes_the_async_channel(wb_dma_async):
    """And the platform's event with it: a channel built on `asyncio.Event`
    would not be driven by a cocotb scheduler, which is the run this form
    exists to make possible."""
    _, _, outcome = wb_dma_async
    text = outcome.read("wb_dma.py")
    assert "from pssc_rt_async import Chan1" in text
    assert "Chan1(imports.event)" in text


#: T5.8's fixtures. Each pairs a generated module with a platform object and
#: states whether a type checker should accept it. The interesting row is the
#: third: a synchronous bus handed to an async model type-checks as `read32`
#: returning `int` only if the Protocol's colour is real.
_TYPECHECK_CASES = (
    ("sync", ("def read32(self, addr: int) -> int: return 0",
              "def write32(self, addr: int, data: int) -> None: pass",
              "def message(self, text: str) -> None: pass"), True),
    ("async", ("async def read32(self, addr: int) -> int: return 0",
               "async def write32(self, addr: int, data: int) -> None: pass",
               "def message(self, text: str) -> None: pass",
               "async def yield_(self) -> None: pass"), True),
    ("async", ("def read32(self, addr: int) -> int: return 0",
               "def write32(self, addr: int, data: int) -> None: pass",
               "def message(self, text: str) -> None: pass",
               "def yield_(self) -> None: pass"), False),
)


@pytest.mark.parametrize("form,body,should_pass", _TYPECHECK_CASES)
def test_a_mis_coloured_platform_is_a_static_error(tmp_path, form, body,
                                                   should_pass):
    """T5.8. The static half of the guarantee.

    `check_import_api` catches a mis-coloured platform at run time and names it.
    This is the same question asked before anything runs, and it is the reason
    the generated seam is a `Protocol` rather than prose: a synchronous
    `read32` handed to a model generated with `--py-await async` must not
    type-check, because at run time it hands the model a coroutine object where
    a register value belongs.
    """
    mypy = pytest.importorskip("mypy.api",
                               reason="mypy is a `test` extra; CI installs it")
    out = tmp_path / form
    with compile_op_model("op-model-py", output_dir=str(out),
                          py_await=form) as outcome:
        cls = "DmaEngine"
        (out / "_check.py").write_text("\n".join(
            [f"from dma_engine import {cls}, DmaEngineImportApi",
             "",
             "class Plat:"]
            + [f"    {m}" for m in body]
            + ["",
               "def use(p: DmaEngineImportApi) -> None: ...",
               "use(Plat())",
               f"{cls}(Plat(), 0x1000)",
               ""]))
        stdout, _, status = mypy.run(
            ["--no-error-summary", "--no-incremental", str(out / "_check.py")])
    assert (status == 0) is should_pass, stdout


def _banner_snippet(text: str) -> str:
    """The indented code block out of the generated module's docstring."""
    doc = text.split('"""')[1]
    lines = [l[4:] for l in doc.splitlines() if l.startswith("    ") or not l.strip()]
    # Everything from the first import to the end of the block.
    start = next(i for i, l in enumerate(lines) if l.startswith(("import ", "from ")))
    return textwrap.dedent("\n".join(lines[start:])).strip() + "\n"


@pytest.mark.parametrize("form", ["sync", "async"])
def test_the_banner_snippet_runs(tmp_path, form):
    """T5.9. Documentation that is still true.

    The snippet in a generated module's docstring is the first thing anyone
    reads and the thing most likely to rot: it names a runtime module, a stub
    class and -- in the async form -- an event loop, and each of those is a
    thing this work moved. Executing it as a SUBPROCESS, against the files this
    generation actually copied, is what makes the claim checkable rather than
    aspirational.
    """
    out = tmp_path / form
    with compile_op_model("op-model-py", output_dir=str(out),
                          py_await=form) as outcome:
        snippet = _banner_snippet(outcome.read("dma_engine.py"))
        (out / "_snippet.py").write_text(snippet)
        res = subprocess.run([sys.executable, "_snippet.py"], cwd=str(out),
                             capture_output=True, text=True)
    assert res.returncode == 0, f"{snippet}\n---\n{res.stderr}"


# --- the contracts every target is held to ----------------------------------

def test_it_conforms():
    report = conformance.run("op-model-py")
    assert report.ok, str(report)


def test_it_refuses_exactly_what_the_c_target_refuses_on_the_real_model():
    """The two targets reach the same verdict on the same model, call for call.

    `conformance.run` is NOT used here, and the reason is worth recording. Its
    `_elaborate` translates without a target, so `wake.get()` -- which the model
    guards with `compile if (target_cfg_pkg::HAVE_EVENT_WAIT)` and which a real
    compile therefore elides -- survives into the IR and the gate refuses it.
    That happens identically for `op-model-c`, which is the point: it is a
    property of the harness and the model, not of this backend. Asserting the
    two AGREE is the check that survives the difference.
    """
    from pssc.targets.validate_calls import validate_calls

    model = conformance._elaborate(wb_dma_sources(), WB_DMA_ROOT)
    py = validate_calls(model.root, model.ctx, "op-model-py")
    c = validate_calls(model.root, model.ctx, "op-model-c")
    assert len(py) == len(c) == 1
    assert all("wait_hint" in m and "'get'" in m for m in py + c)


def test_it_renders_the_common_tier():
    from pssc.testing import assert_common_tier
    assert_common_tier("op-model-py")


def test_the_generated_module_compiles_under_the_bare_interpreter(wb_dma):
    """`py_compile` on a SUBPROCESS interpreter, not the one running the tests.

    Importing it here already proves it parses. This proves it parses with no
    pssc on the path and nothing else imported -- which is the situation the
    file is actually shipped into.
    """
    _, _, outcome = wb_dma
    res = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(f"""
            import py_compile, sys
            py_compile.compile({str(outcome.out_dir / 'wb_dma.py')!r},
                               doraise=True, cfile=None)
        """)], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
