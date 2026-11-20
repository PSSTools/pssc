"""Bus-trace differential: the bytecode backend against op-model-py (P1.3d1).

Every exported WB DMA operation is run twice -- on a bc :class:`Session`
(``bc_session.lower(..., functions=["*"])``) and on the op-model-py module
generated from the same sources -- each against a plain memory, and the two
bus logs are compared access by access as ``(kind, width_bits, addr, data)``.
op-model-py is the reference: its register addresses are what
tests/progseq checks against the WB DMA register map.

The comparison is calibrated: with every bc register offset moved by 4 the
traces must differ, so a test that cannot see an address error cannot pass.
"""
from __future__ import annotations

import importlib
import sys

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, compile_op_model

op_model = pytest.importorskip("progseq.op_model")

ROOT = "wb_dma_c"
BASE = 0x1000
DESC = 0x8000
PREV = 0x8100

#: (operation, bc args (struct args flat, leaf order), op-model-py args builder)
DESC_LEAVES = dict(tot_sz=0x123, reserved0=0, dst_sel=1, src_sel=0, inc_dst=1,
                   inc_src=1, eol=1, reserved1=0)
DESC_WORDS = dict(adr0=0xdead0000, adr1=0xbeef0000, next=0x55)


def _py_desc(mod):
    d = mod.wb_dma_desc_s()
    for k, v in DESC_LEAVES.items():
        setattr(d.csr, k, v)
    for k, v in DESC_WORDS.items():
        setattr(d, k, v)
    return d


OPS = [
    ("configure_interrupt_routing", (0, 0x5), lambda mod: (0, 0x5)),
    ("configure_interrupt_routing", (1, 0x7fffffff), lambda mod: (1, 0x7fffffff)),
    ("pause_engine", (1,), lambda mod: (1,)),
    ("pause_engine", (0,), lambda mod: (0,)),
    ("read_descriptor_residual", (DESC,), lambda mod: (DESC,)),
    ("write_descriptor", (DESC, 0) + tuple(DESC_LEAVES.values())
     + tuple(DESC_WORDS.values()), lambda mod: (DESC, 0, _py_desc(mod))),
    ("write_descriptor", (DESC, PREV) + tuple(DESC_LEAVES.values())
     + tuple(DESC_WORDS.values()), lambda mod: (DESC, PREV, _py_desc(mod))),
]
IDS = [f"{op}-{i}" for i, (op, _, _) in enumerate(OPS)]


@pytest.fixture(scope="module")
def bc_model():
    ctx = driver.translate(op_model.op_model_sources() + [op_model.WB_DMA_EXPORTS])
    assert not ctx.errors
    return ctx, bc_session.lower(ctx, ROOT, functions=["*"])


@pytest.fixture(scope="module")
def py_model():
    with compile_op_model("op-model-py", sources=op_model.op_model_sources(),
                          root=ROOT) as outcome:
        sys.path.insert(0, str(outcome.out_dir))
        try:
            for name in ("wb_dma", "pssc_rt"):
                sys.modules.pop(name, None)
            rt = importlib.import_module("pssc_rt")
            mod = importlib.import_module("wb_dma")
        finally:
            sys.path.remove(str(outcome.out_dir))
        yield mod, rt


def _bc_trace(model, op, args):
    s = bc_session.Session.load(model)
    root = s.construct()
    assert f"{ROOT}::initialize" in model.entries
    s.call(root, "initialize", BASE)
    s.vm.memory.log.clear()
    s.call(root, op, *args)
    kind = {"r": "read", "w": "write"}
    return [(kind[k], n * 8, a, v) for k, a, n, v in s.vm.memory.log]


def _py_trace(py, op, build):
    mod, rt = py
    bus = rt.MemoryBus()
    dut = mod.WbDma(bus, BASE)
    bus.log.clear()
    getattr(dut, op)(*build(mod))
    return [tuple(e) for e in bus.log]


def test_every_exported_operation_is_covered(bc_model):
    _, model = bc_model
    exported = {s.split("::")[1] for s in model.entries
                if s.startswith(f"{ROOT}::") and s != f"{ROOT}::initialize"}
    # notify_irq posts to channels only: no bus access to compare.
    assert exported - {"notify_irq"} == {op for op, _, _ in OPS}


@pytest.mark.parametrize("op,args,build", OPS, ids=IDS)
def test_bc_and_op_model_py_issue_the_same_accesses(bc_model, py_model, op, args, build):
    _, model = bc_model
    py = _py_trace(py_model, op, build)
    assert py, "the reference issued no access; nothing is being compared"
    assert _bc_trace(model, op, args) == py


def test_the_differential_sees_a_misplaced_register(bc_model, py_model, monkeypatch):
    ctx, _ = bc_model
    real = bc_session._reg_offset

    monkeypatch.setattr(bc_session, "_reg_offset",
                        lambda *a: real(*a) + 4)
    model = bc_session.lower(ctx, ROOT, functions=["*"])
    op, args, build = OPS[0]
    assert _bc_trace(model, op, args) != _py_trace(py_model, op, build)


def test_the_image_carries_the_entry_points(bc_model):
    """P1.3d2: the ENTRY section brings every entry back from the bytes, so a
    session can run from a saved image."""
    from zuspec.be.bc.model import ZbcModel

    _, model = bc_model
    back = ZbcModel.from_bytes(model.to_bytes())
    assert back.entries == model.entries
