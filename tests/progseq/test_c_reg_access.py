"""PC-5: a register access reaches `CBodyEmitter` resolved
(`reg_layout.RegAccess`), and a masked write's shape is pssc's (ruling
R-H8): one call by default, or read / set each field / write, spelled by an
override's three primitives.

The defaults are held by the golden snapshots. What is held here: the
resolver, the composed form's text, and -- driven, not grepped -- that the
composed form programs the device exactly as the single call does.
"""
from __future__ import annotations

import argparse
import dataclasses
import shutil
import subprocess

import pytest

from pssc import driver, targets
from pssc.targets import reg_layout
from pssc.targets.c.backend import COpModelBackend
from pssc.targets.c.lower_progseq import CBodyEmitter

from .conftest import available_c_compilers

MODEL = """
package r_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;
    struct ctl_s : packed_s<> {
        rand bit[1] en; rand bit[3] mode; rand bit[12] lvl; rand bit[16] hi;
    }
    pure component ctl_r : reg_c<ctl_s, READWRITE, 32> {}
    pure component g_c : reg_group_c {
        ctl_r ctl;
        ctl_r bank[4];
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["ctl"]: return 0x10; default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { ["bank"]: return 0x40 + index * 8; default: return -1; }
        }
    }
}
import addr_reg_pkg::*;
component leaf_c {
    r_pkg::g_c regs;
    solve function void initialize(addr_handle_t a) { regs.set_handle(a); }
}
component top_c {
    r_pkg::g_c regs;
    leaf_c leaf;
    solve function void initialize(addr_handle_t a, addr_handle_t b) {
        regs.set_handle(a);
        leaf.initialize(b);
    }
    target function void set(bit[32] m) {
        regs.ctl.write_fields({"en", "mode"}, {1, m});
    }
    target function void raw(bit[32] m, bit[32] v) {
        regs.ctl.write_val_masked(m, v);
    }
    target function bit[32] get_mode() { return regs.ctl.read().mode; }
    target function void poke(int i, bit[32] v) {
        leaf.regs.bank[i].write_val(v);
    }
}
"""


def _gen(tmp_path, monkeypatch, backend_cls, **opts):
    tgt = targets.get("op-model-c")
    monkeypatch.setattr(type(tgt), "backend_cls", backend_cls)
    p = tmp_path / "m.pss"
    p.write_text(MODEL)
    out = tmp_path / "out"
    driver.compile([str(p)], target="op-model-c", output_dir=str(out),
                   progseq_root="top_c", **opts)
    return out, (out / "top.c").read_text()


def _body(c, sig):
    return c.split(sig + " {\n", 1)[1].split("\n}", 1)[0]


def _cc(out):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    for c in out.glob("*.c"):
        r = subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
                            "-c", str(c), "-o", str(c.with_suffix(".o"))],
                           cwd=str(out), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


# -- the resolver ------------------------------------------------------------

def _model(tmp_path):
    p = tmp_path / "m.pss"
    p.write_text(MODEL)
    tgt = targets.get("op-model-c")
    ctx = driver.translate([str(p)], prelude=tgt.prelude(argparse.Namespace()))
    return tgt.build_model(ctx, argparse.Namespace(
        progseq_root="top_c", output_dir=str(tmp_path)))


def _calls(fn):
    from pssc.targets.ir_walk import children

    def walk(n):
        if isinstance(n, (list, tuple)):
            for c in n:
                yield from walk(c)
            return
        if not dataclasses.is_dataclass(n):
            return
        if type(n).__name__ == "ExprCall":
            yield n
        for c in children(n):
            yield from walk(c)
    for s in fn.body:
        yield from walk(s)


def _fn(model, comp, name):
    c = next(c for c in model.comp_dtypes if c.name.endswith(comp))
    return c, next(f for f in c.functions if f.name == name)


def test_resolve_access_names_the_register_and_its_fields(tmp_path):
    m = _model(tmp_path)
    comp, fn = _fn(m, "top_c", "set")
    ra = next(filter(None, (reg_layout.resolve_access(comp, c)
                            for c in _calls(fn))))
    assert ra.method == "write_val_masked"
    assert ra.accessor.path == ("regs", "ctl") and ra.accessor.const_off == 0x10
    assert ra.group == "regs" and ra.via == () and ra.comp is comp
    assert [(w.slice.name, w.mask) for w in ra.fields] == [("en", 0x1),
                                                           ("mode", 0xE)]


def test_resolve_access_through_a_sub_component(tmp_path):
    m = _model(tmp_path)
    comp, fn = _fn(m, "top_c", "poke")
    ra = next(filter(None, (reg_layout.resolve_access(comp, c)
                            for c in _calls(fn))))
    assert [n for n, _ in ra.via] == ["leaf"]
    assert ra.comp.name.endswith("leaf_c")
    assert ra.accessor.path == ("regs", "bank")
    assert len(ra.index_exprs) == len(ra.accessor.strides) == 1
    assert ra.accessor.strides == (8,) and ra.fields is None


def test_a_group_call_is_no_register_access(tmp_path):
    m = _model(tmp_path)
    comp, fn = _fn(m, "top_c", "initialize")
    assert not any(reg_layout.resolve_access(comp, c) for c in _calls(fn))


# -- the composed masked write -------------------------------------------------

class _Hal(CBodyEmitter):
    """The shape a device-access macro set spells: a `data` word, a macro per step."""

    def reg_masked_write_inline(self, ra, lw):
        return None

    def reg_value_local(self, ra, lw):
        return "uint32_t", "data"

    def reg_read_into(self, ra, lw, local):
        return f"DEV_READ32({ra.group}, R_{ra.accessor.name.upper()}, {local})"

    def reg_value_field_set(self, local, fs, value, ra, lw, mask):
        return (f"DEV_FIELD_SET(R_{ra.accessor.name.upper()}, {fs.name}, "
                f"({value}), {local})")

    def reg_write_from(self, ra, lw, local):
        return f"DEV_WRITE32({ra.group}, R_{ra.accessor.name.upper()}, {local})"

    def reg_value_field_get(self, value, fs, ra, lw):
        return f"DEV_FIELD_GET(R_{ra.accessor.name.upper()}, {fs.name}, {value})"


class _HalBackend(COpModelBackend):
    body_emitter_cls = _Hal


def test_a_composed_masked_write_is_one_step_per_field(tmp_path, monkeypatch):
    out, c = _gen(tmp_path, monkeypatch, _HalBackend)
    body = _body(c, "void top_set(top_c *_self, uint32_t m)")
    lines = [ln.strip() for ln in body.splitlines()]
    # The local first; the handle, unused by these macros, marked so.
    assert lines[:2] == ["uint32_t data;", "(void)_self;"]
    assert lines[2:] == ["DEV_READ32(regs, R_CTL, data);",
                          "DEV_FIELD_SET(R_CTL, en, (1), data);",
                          "DEV_FIELD_SET(R_CTL, mode, (m), data);",
                          "DEV_WRITE32(regs, R_CTL, data);"]
    # A field of a whole read goes through its hook.
    assert "DEV_FIELD_GET(R_CTL, mode, " in _body(
        c, "uint32_t top_get_mode(top_c *_self)")


class _HalBlock(_Hal):
    def reg_value_local_scope(self, ra, lw):
        return "block"


class _HalBlockBackend(COpModelBackend):
    body_emitter_cls = _HalBlock


def test_block_scoped_locals_are_the_writes_own(tmp_path, monkeypatch):
    out, c = _gen(tmp_path, monkeypatch, _HalBlockBackend)
    body = _body(c, "void top_set(top_c *_self, uint32_t m)")
    lines = [ln.strip() for ln in body.splitlines()]
    # No function-top local: the word is declared in the write's block.
    assert lines == ["(void)_self;", "{", "uint32_t data;",
                     "DEV_READ32(regs, R_CTL, data);",
                     "DEV_FIELD_SET(R_CTL, en, (1), data);",
                     "DEV_FIELD_SET(R_CTL, mode, (m), data);",
                     "DEV_WRITE32(regs, R_CTL, data);", "}"]
    # The block's statements are one level in.
    blk = body.splitlines()
    i = next(n for n, ln in enumerate(blk) if ln.strip() == "{")
    assert len(blk[i + 1]) - len(blk[i + 1].lstrip()) > \
        len(blk[i]) - len(blk[i].lstrip())


def test_an_unknown_local_scope_is_an_error(tmp_path, monkeypatch):
    class _Bad(_Hal):
        def reg_value_local_scope(self, ra, lw):
            return "file"

    class _BadBackend(COpModelBackend):
        body_emitter_cls = _Bad

    with pytest.raises(Exception, match="reg_value_local_scope gave 'file'"):
        _gen(tmp_path, monkeypatch, _BadBackend)


class _Composed(CBodyEmitter):
    """pssc's own primitives, composed."""

    def reg_masked_write_inline(self, ra, lw):
        return None


class _ComposedBackend(COpModelBackend):
    body_emitter_cls = _Composed


def test_with_no_field_list_the_mask_is_merged(tmp_path, monkeypatch):
    out, c = _gen(tmp_path, monkeypatch, _ComposedBackend)
    lines = [ln.strip() for ln in
             _body(c, "void top_raw(top_c *_self, uint32_t m, uint32_t v)")
             .splitlines()]
    assert lines[0] == "uint32_t _pss_reg;"
    assert lines[1].startswith("_pss_reg = pssc_read32(")
    assert lines[2] == "_pss_reg = (_pss_reg & ~(m)) | ((v) & (m));"
    assert lines[3].startswith("pssc_write32(") and \
        lines[3].endswith(", _pss_reg);")
    _cc(out)


def test_the_default_is_one_call(tmp_path, monkeypatch):
    out, c = _gen(tmp_path, monkeypatch, COpModelBackend)
    body = _body(c, "void top_set(top_c *_self, uint32_t m)")
    assert "pssc_write_masked32(" in body and "_pss_reg" not in body


# -- driven: the composed form programs the device as the call does -------------

_CC = available_c_compilers()


class _ComposedBlock(_Composed):
    def reg_value_local_scope(self, ra, lw):
        return "block"


class _ComposedBlockBackend(COpModelBackend):
    body_emitter_cls = _ComposedBlock


@pytest.mark.c_toolchain
@pytest.mark.skipif(not _CC, reason="no C compiler")
@pytest.mark.parametrize("backend", [_ComposedBackend, _ComposedBlockBackend],
                         ids=["function", "block"])
def test_the_composed_form_passes_the_wb_dma_trace_gate(tmp_path, monkeypatch,
                                                        backend):
    """The WB DMA tree's trace gate (`test_op_model_behaviour_c.py`) checks
    every register access its masked writes make; it must pass unchanged
    when each one is composed from pssc's default primitives, with its
    locals at the top of the function or in a block of its own."""
    from .test_op_model_behaviour_c import _build_and_run, _generate

    tgt = targets.get("op-model-c")
    monkeypatch.setattr(type(tgt), "backend_cls", backend)
    out = _generate(tmp_path, c_lifecycle="static")
    text = (tmp_path / "wb_dma.c").read_text()
    assert "_pss_reg = wb_dma_" in text, "nothing was composed"
    if backend is _ComposedBlockBackend:
        lines = [ln.strip() for ln in text.splitlines()]
        assert any(a == "{" and b.endswith(" _pss_reg;")
                   for a, b in zip(lines, lines[1:])), "no block-scoped local"
    assert "_write_masked(" not in text.split("/* ----- Component", 1)[1]
    res = _build_and_run(out, _CC[0])
    assert res.returncode == 0, res.stdout + res.stderr
    assert "WB_DMA OP MODEL PASS" in res.stdout, res.stdout
