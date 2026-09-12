"""Phase 0/2 tests: function-kind + component classification, target
registration, and root resolution. Pure-Python (no simulator).
"""
import os

import pytest

from pssc import driver, targets as _targets
from pssc.targets.progseq_model import (
    func_kind, FuncKind, comp_kind, CompKind, is_reg_group, walk_tree,
)
from pssc.targets.progseq_tgt import ProgSeqTarget

from .op_model import small_model_dir, small_model_sources

_DATA = small_model_dir()
_SRCS = small_model_sources()


@pytest.fixture(scope="module")
def ctx():
    c = driver.translate(_SRCS)
    assert not c.errors, c.errors
    return c


def _comp(ctx, name):
    return ctx.type_map[name]


# --- target registration ---------------------------------------------------

def test_target_registered():
    _targets.discover()
    assert "op-model-sv" in _targets.list_targets()
    # `sv-progseq` / `progseq` are back-compat aliases for `op-model-sv`.
    assert _targets.get("progseq") is _targets.get("op-model-sv")
    assert _targets.get("sv-progseq") is _targets.get("op-model-sv")


# --- component classification ----------------------------------------------

def test_reg_group_classification(ctx):
    assert is_reg_group(_comp(ctx, "dma_regs_c"))
    assert is_reg_group(_comp(ctx, "dma_channel_regs_c"))
    assert comp_kind(_comp(ctx, "dma_regs_c")) is CompKind.REG_GROUP


def test_regular_classification(ctx):
    assert not is_reg_group(_comp(ctx, "dma_engine_c"))
    assert comp_kind(_comp(ctx, "dma_engine_c")) is CompKind.REGULAR


# --- function kinds --------------------------------------------------------

def test_func_kinds(ctx):
    eng = _comp(ctx, "dma_engine_c")
    kinds = {fn.name: func_kind(fn) for fn in eng.functions}
    assert kinds["ctor"] is FuncKind.CONSTRUCTOR
    assert kinds["configure_channel"] is FuncKind.EXPORT_OP
    assert kinds["mem_to_mem_copy"] is FuncKind.EXPORT_OP
    assert kinds["mem_to_mem_copy_masked"] is FuncKind.EXPORT_OP


def test_reg_offset_funcs_are_internal(ctx):
    grp = _comp(ctx, "dma_regs_c")
    for fn in grp.functions:
        assert func_kind(fn) is FuncKind.REG_OFFSET


# --- tree walk -------------------------------------------------------------

def test_walk_tree(ctx):
    root = _comp(ctx, "dma_engine_c")

    def resolve(dt):
        ref = getattr(dt, "ref_name", None)
        return ctx.type_map[ref] if ref and ref in ctx.type_map else dt

    tree = walk_tree(root, resolve)
    assert tree.kind is CompKind.REGULAR
    # dma_engine_c -> regs (dma_regs_c) -> channels[] (dma_channel_regs_c)
    names = _collect(tree)
    assert any("dma_regs_c" in n for n in names)
    assert any("dma_channel_regs_c" in n for n in names)


def _collect(node, acc=None):
    acc = acc if acc is not None else []
    acc.append(node.name)
    for c in node.children:
        _collect(c, acc)
    return acc


# --- root resolution -------------------------------------------------------

def test_resolve_root_qualified_and_bare(ctx):
    """`resolve_root` is public and lives on `OpModelTarget` since P2.T2 --
    every target in the family resolves `--root` the same way."""
    r1 = ProgSeqTarget.resolve_root(ctx, "dma_engine_c")
    r2 = ProgSeqTarget.resolve_root(ctx, "dma_engine_pkg::dma_engine_c")
    assert r1 is not None and r2 is not None


def test_resolve_root_unknown(ctx):
    with pytest.raises(ValueError, match="unknown --root"):
        ProgSeqTarget.resolve_root(ctx, "does_not_exist_c")
