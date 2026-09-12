"""Phase 3: c-progseq target registration, arg parsing, skeleton emit."""
import argparse
import os

import pytest

from pssc import driver
from pssc import targets as _targets

from .op_model import small_model_dir, small_model_sources

_DATA = small_model_dir()
_SRCS = small_model_sources()


def _opts(**kw):
    ns = argparse.Namespace(progseq_root=kw.pop("root", "dma_engine_c"),
                            c_prefix=kw.pop("prefix", None),
                            c_link_style=kw.pop("link_style", "vtable"),
                            c_reg_style=kw.pop("reg_style", "bitfields"),
                            c_header_only=kw.pop("header_only", False),
                            progseq_core_copy=kw.pop("core_copy", True))
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def test_registered():
    assert "op-model-c" in _targets.list_targets()
    # The canonical name is `op-model-c`; `c-progseq` and `progseq-c` are
    # back-compat aliases and must keep resolving to the same instance.
    assert _targets.get("progseq-c") is _targets.get("op-model-c")
    assert _targets.get("c-progseq") is _targets.get("op-model-c")


def test_default_prefix():
    from pssc.targets.c_progseq_tgt import CProgSeqTarget
    assert CProgSeqTarget.default_prefix("dma_engine_c") == "dma_engine"
    assert CProgSeqTarget.default_prefix("wb_dma") == "wb_dma"


def test_missing_root():
    with pytest.raises(ValueError, match="requires --root"):
        driver.compile(_SRCS, target="c-progseq",
                       opts=argparse.Namespace(progseq_root=None, output_dir="/tmp/x"))


def test_unknown_root():
    with pytest.raises(Exception) as ei:
        driver.compile(_SRCS, target="c-progseq",
                       opts=_opts(root="nope_c", output_dir="/tmp/x"))
    assert "nope_c" in str(ei.value)


@pytest.mark.parametrize("link_style", ["vtable", "direct", "mmio"])
def test_skeleton_emits(tmp_path, link_style):
    out = tmp_path / link_style
    res = driver.compile(_SRCS, target="c-progseq",
                         opts=_opts(link_style=link_style, output_dir=str(out)))
    names = {os.path.basename(str(p)) for p in res.outputs}
    assert "dma_engine.h" in names
    assert "pssc_mem.h" in names
    seam = {"vtable": "pssc_mem_vtable.h", "direct": "pssc_mem_direct.h",
            "mmio": "pssc_mem_mmio.h"}[link_style]
    assert seam in names
    hdr = (out / "dma_engine.h").read_text()
    assert f'#include "{seam}"' in hdr


def test_mmio_emits_both_files(tmp_path):
    """mmio no longer implies a header-only API.

    It used to: with no bus handle to carry, every body COULD be `static
    inline`. But "could" was the whole argument, and it cost every mmio build
    the .c that its register layouts and accessors belong in. The seam is a
    property of how a register access is spelled; whether there is a
    translation unit is a separate question, and `--header-only` is how it is
    asked."""
    res = driver.compile(_SRCS, target="c-progseq",
                         opts=_opts(link_style="mmio", output_dir=str(tmp_path)))
    names = {os.path.basename(str(p)) for p in res.outputs}
    assert {"dma_engine.h", "dma_engine.c"} <= names


def test_header_only_is_asked_for(tmp_path):
    """...and `--header-only` still gets the single self-contained file."""
    res = driver.compile(_SRCS, target="c-progseq",
                         opts=_opts(link_style="mmio", header_only=True,
                                    output_dir=str(tmp_path)))
    names = {os.path.basename(str(p)) for p in res.outputs}
    assert "dma_engine.h" in names
    assert "dma_engine.c" not in names
