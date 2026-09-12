"""Phase 0 tests: the backend-neutral helpers hoisted into progseq_model.

These lock the shared contract that the SV, C, and C++ register-model emitters
all depend on (affine offset evaluation + ordered type-list collection), so a
later refactor cannot silently change it under three backends at once.
"""
import os

import pytest

from pssc import driver
from pssc.targets import progseq_model as pm

from .op_model import small_model_dir, small_model_sources

_DATA = small_model_dir()
_SRCS = small_model_sources()


@pytest.fixture(scope="module")
def root():
    """Resolve the dma_engine_c root datatype from a compiled context."""
    from pssc.targets.progseq_tgt import ProgSeqTarget
    ctx = driver.translate(_SRCS)
    return ProgSeqTarget.resolve_root(ctx, "dma_engine_c")


def _group_named(root, name):
    groups = pm.collect_reg_groups(root)
    for g in groups:
        if g.name.split("::")[-1] == name:
            return g
    raise AssertionError(f"reg group {name} not found in {[g.name for g in groups]}")


def test_collect_reg_groups_postorder(root):
    groups = pm.collect_reg_groups(root)
    names = [g.name.split("::")[-1] for g in groups]
    # nested channel block must precede the top-level file that contains it
    assert "dma_channel_regs_c" in names and "dma_regs_c" in names
    assert names.index("dma_channel_regs_c") < names.index("dma_regs_c")


def test_collect_value_structs_first_use_order(root):
    groups = pm.collect_reg_groups(root)
    structs = [s.name.split("::")[-1] for s in pm.collect_value_structs(groups)]
    # the channel block is emitted first, so its value structs lead
    assert {"dma_ch_csr_s", "dma_ch_sz_s", "dma_ch_swptr_s", "dma_csr_s"} <= set(structs)
    assert structs.index("dma_ch_csr_s") < structs.index("dma_csr_s")


def test_scalar_offset(root):
    ch = _group_named(root, "dma_channel_regs_c")
    assert pm._scalar_offset(ch, "CSR") == 0x00
    assert pm._scalar_offset(ch, "SWPTR") == 0x1c
    top = _group_named(root, "dma_regs_c")
    assert pm._scalar_offset(top, "INT_SRC_B") == 0x010


def test_array_base_stride(root):
    top = _group_named(root, "dma_regs_c")
    base, stride = pm._array_base_stride(top, "channels")
    assert base == 0x020
    assert stride == 0x020
