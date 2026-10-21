"""PC-7: an operation's C API is asked of the backend -- `signature`,
`handle_param` and `linkage` -- and every place that declares, defines,
calls or names an operation (`c_names`) takes the one answer.

The defaults are held by the golden snapshots; these are the overrides.
"""
from __future__ import annotations

import shutil
import subprocess

import pytest

from pssc import driver, targets
from pssc.targets.c.backend import COpModelBackend

MODEL = """
package r_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;
    struct w_s : packed_s<> { rand bit[32] v; }
    pure component w_r : reg_c<w_s, READWRITE, 32> {}
    pure component g_c : reg_group_c {
        w_r stat;
        function bit[64] get_offset_of_instance(string name) {
            match (name) { ["stat"]: return 0x0; default: return -1; }
        }
        function bit[64] get_offset_of_instance_array(string name, int index) {
            return -1;
        }
    }
}
import addr_reg_pkg::*;
component top_c {
    r_pkg::g_c regs;
    bit[32] count;
    solve function void initialize(addr_handle_t a) { regs.set_handle(a); }
    target function bit[32] twice(bit[32] x) { return helper(x) + helper(x); }
    target function bit[32] helper(bit[32] x) { return x * 2; }
    target function bit[32] five() { return 5; }
    target function bit[32] stat() { return regs.stat.read().v; }
    target function bit[32] get_count() { return count; }
}
"""

PURE = {"twice", "helper", "five"}


def _gen(tmp_path, monkeypatch, backend_cls, src=MODEL):
    tgt = targets.get("op-model-c")
    monkeypatch.setattr(type(tgt), "backend_cls", backend_cls)
    p = tmp_path / "m.pss"
    p.write_text(src)
    out = tmp_path / "out"
    driver.compile([str(p)], target="op-model-c", output_dir=str(out),
                   progseq_root="top_c")
    return out, (out / "top.h").read_text(), (out / "top.c").read_text()


def _cc(out):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    for c in out.glob("*.c"):
        r = subprocess.run([cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
                            "-c", str(c), "-o", str(c.with_suffix(".o"))],
                           cwd=str(out), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr


class _NoHandle(COpModelBackend):
    """The pure operations take no handle."""

    def handle_param(self, fn, ctx):
        return None if fn.name in PURE else super().handle_param(fn, ctx)


def test_no_handle(tmp_path, monkeypatch):
    out, h, c = _gen(tmp_path, monkeypatch, _NoHandle)
    assert "uint32_t top_twice(uint32_t x);" in h
    assert "uint32_t top_helper(uint32_t x);" in h
    # No parameters at all is `(void)`, not `()`.
    assert "uint32_t top_five(void);" in h
    # The call passes no handle, and nothing marks an absent one unused.
    assert "top_helper(x)" in c
    body = c.split("uint32_t top_twice(uint32_t x) {", 1)[1].split("\n}", 1)[0]
    assert "_self" not in body
    # An operation that keeps its handle is as before.
    assert "uint32_t top_stat(top_c *_self);" in h
    _cc(out)


class _NoHandleAnywhere(COpModelBackend):
    def handle_param(self, fn, ctx):
        return None


def test_state_without_a_handle_is_refused_with_its_location(
        tmp_path, monkeypatch):
    """`stat` reads a register and `get_count` a field: both need the
    handle, so dropping it is the model's error, located, never C naming an
    undeclared `_self`."""
    with pytest.raises(driver.CompileError) as ei:
        _gen(tmp_path, monkeypatch, _NoHandleAnywhere)
    errors = "\n".join(map(str, ei.value.errors))
    assert "no component handle" in errors
    # Located at the register read in `stat`, the first one rendered.
    line = MODEL.splitlines().index(
        "    target function bit[32] stat() { return regs.stat.read().v; }")
    assert [m["line"] for m in ei.value.markers] == [line + 1], errors


class _Static(COpModelBackend):
    def linkage(self, fn, ctx):
        return "static" if fn.name == "helper" else "extern"


def test_a_static_operation_is_not_on_the_header(tmp_path, monkeypatch):
    out, h, c = _gen(tmp_path, monkeypatch, _Static)
    assert "top_helper" not in h
    decl = "static inline uint32_t top_helper(top_c *_self, uint32_t x);"
    assert decl in c
    # Declared ahead of every definition: `twice` may call it first.
    assert c.index(decl) < c.index("top_twice(top_c *_self, uint32_t x) {")
    assert "static inline uint32_t top_helper(top_c *_self, uint32_t x) {" in c
    _cc(out)


class _BadLinkage(COpModelBackend):
    def linkage(self, fn, ctx):
        return "internal"


def test_an_unknown_linkage_is_an_error(tmp_path, monkeypatch):
    with pytest.raises(Exception, match="linkage"):
        _gen(tmp_path, monkeypatch, _BadLinkage)


class _Renamed(COpModelBackend):
    def signature(self, fn, ctx):
        ret, name, params = super().signature(fn, ctx)
        return ret, "hal_" + fn.name, params


def test_a_renamed_signature_is_declared_and_defined(tmp_path, monkeypatch):
    """Renaming in `signature` alone is not enough for a call site, which
    `model_call` spells; `five` is called by nothing, so it is checked."""
    out, h, c = _gen(tmp_path, monkeypatch, _Renamed,
                     MODEL.replace("return helper(x) + helper(x);",
                                   "return x;"))
    assert "uint32_t hal_five(top_c *_self);" in h
    assert "uint32_t hal_five(top_c *_self) {" in c
    _cc(out)


class _Clash(COpModelBackend):
    def signature(self, fn, ctx):
        ret, name, params = super().signature(fn, ctx)
        return ret, ("hal_op" if fn.name in ("five", "stat") else name), params


def test_c_names_sees_the_signatures_name(tmp_path, monkeypatch):
    """Two operations a plugin names alike are refused before a file is
    written, as two the default names alike are."""
    with pytest.raises(driver.CompileError) as ei:
        _gen(tmp_path, monkeypatch, _Clash)
    detail = "\n".join(map(str, ei.value.errors))
    assert "'hal_op' is both operation 'top_c.five' and operation " \
           "'top_c.stat'" in detail
