"""The masked register write, as it reaches C and C++.

Generated *and compiled*. The IR-level tests in `tests/unit` prove the reduction
is right; they cannot see whether the backend emits something a compiler will
take, and both defects this phase closed were invisible in the IR:

  * the C target's `_reg_call` matched only ("read", "write"), so every other
    register method fell through to a generic call -- `regs.csr.read_val()`,
    which names a struct field that does not exist. That had been true of
    `read_val`/`write_val` all along, not just of the new masked form.
  * the expanded form `(cur & ~mask) | (val & mask)` printed without brackets,
    which gcc rejects under -Wparentheses -Werror.

Neither is caught by asserting on strings in the output, so these tests hand the
result to a compiler.
"""
import os
import subprocess

import pytest

from pssc import driver
from .conftest import available_c_compilers

_CFLAGS = ["-std=c11", "-Wall", "-Wextra", "-Werror"]

_SRC = """
import std_pkg::*;
import addr_reg_pkg::*;

package regs_pkg {
    import std_pkg::*;
    import addr_reg_pkg::*;
    struct csr_s : packed_s<> {
        rand bit[1]  ch_en;
        rand bit[3]  prio;
        rand bit[28] rsvd;
    }
    pure component csr_r : reg_c<csr_s, READWRITE, 32> {}
    pure component grp_c : reg_group_c { csr_r csr; }
}

component pss_top {
    import regs_pkg::*;
    grp_c regs;
    solve function void initialize(addr_handle_t base) { regs.set_handle(base); }
    target function void arm() {
        regs.csr.write_field("ch_en", 1);
        regs.csr.write_fields({"prio"}, {5});
    }
    action A { exec body { comp.arm(); } }
}
"""


def _generate(tmp_path, target, reg_rmw="native"):
    src = tmp_path / "m.pss"
    src.write_text(_SRC)
    out = tmp_path / f"out_{target}_{reg_rmw}"
    driver.compile([str(src)], target=target, output_dir=str(out),
                   progseq_root="pss_top", progseq_ctor_name="initialize",
                   reg_rmw=reg_rmw, c_link_style="vtable")
    return out


# --- C --------------------------------------------------------------------

def test_c_emits_the_masked_accessor(tmp_path):
    out = _generate(tmp_path, "c-progseq")
    hdr = (out / "pss_top.h").read_text()
    body = (out / "pss_top.c").read_text()

    # In the .c: an accessor is implementation, and the masked form is the
    # accessor set's most implementation-ish member -- it exists to spell one
    # PSS statement, not to be called by hand.
    assert "pss_top_regs_csr_write_masked(pss_top *_self, uint32_t mask, uint32_t val)" in body
    assert "pss_top_regs_csr_write_masked(_self, 1, 1);" in body
    assert "pss_top_regs_csr_write_masked(_self, 14, 10);" in body   # prio=5 -> [3:1]


def test_the_c_accessor_reads_before_it_writes(tmp_path):
    """§21.14.1 defines the masked forms as read-modify-write. Dropping the read
    would look like an optimisation and would change device behaviour on a
    register whose read clears its status bits."""
    src = (_generate(tmp_path, "c-progseq") / "pss_top.c").read_text()
    acc = [ln for ln in src.splitlines() if "_write_masked(" in ln][0]
    assert "_read_val(_self)" in acc
    assert "(cur & ~mask) | (val & mask)" in acc


@pytest.mark.parametrize("reg_rmw", ["native", "expand"])
@pytest.mark.skipif(not available_c_compilers(), reason="no C compiler")
def test_c_output_compiles(tmp_path, reg_rmw):
    """Both lowerings, -Werror-clean. `expand` is the one that caught the
    missing brackets; `native` is the one that caught the fall-through."""
    cc = available_c_compilers()[0]
    out = _generate(tmp_path, "c-progseq", reg_rmw)
    res = subprocess.run(
        [cc, *_CFLAGS, "-c", str(out / "pss_top.c"), "-I", str(out),
         "-o", str(out / "pss_top.o")],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


def test_an_unknown_register_method_is_an_error_not_a_generic_call(tmp_path):
    """The fall-through that made this phase necessary, closed.

    A register method the C target has no accessor for used to become a plain
    call on a struct field -- code that can compile and is wrong. It must fail
    loudly instead, naming the register and what is known.
    """
    from pssc.targets.c.lower_progseq import CBodyEmitter, _REG_ACCESSORS
    import zuspec.ir.core as ir

    src = tmp_path / "m.pss"
    src.write_text(_SRC)
    ctx = driver.translate(str(src))
    comp = ctx.type_map["pss_top"]
    fn = [f for f in comp.functions if f.name == "arm"][0]

    em = CBodyEmitter(fn, comp, "pss_top")
    call = ir.ExprCall(
        func=ir.ExprAttribute(
            value=ir.ExprAttribute(
                value=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="regs"),
                attr="csr"),
            attr="poke"),
        args=[])
    with pytest.raises(ValueError) as e:
        em._reg_call(call)
    assert "poke" in str(e.value)
    assert "regs.csr" in str(e.value)
    for known in _REG_ACCESSORS:
        assert known in str(e.value)


def test_a_group_level_call_is_not_judged_as_a_register(tmp_path):
    """`regs.set_handle(...)` is a call on the GROUP, not on a register.

    The guard that makes the error above safe is the path length: a register is
    always `<group>.<reg>`. Without it, hardening the register path would have
    started rejecting every group-level method.
    """
    from pssc.targets.c.lower_progseq import CBodyEmitter
    import zuspec.ir.core as ir

    src = tmp_path / "m.pss"
    src.write_text(_SRC)
    ctx = driver.translate(str(src))
    comp = ctx.type_map["pss_top"]
    fn = [f for f in comp.functions if f.name == "arm"][0]

    em = CBodyEmitter(fn, comp, "pss_top")
    call = ir.ExprCall(
        func=ir.ExprAttribute(
            value=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="regs"),
            attr="set_handle"),
        args=[ir.ExprConstant(value=0)])
    assert em._reg_call(call) is None


# --- C++ ------------------------------------------------------------------

def test_cpp_uses_the_native_member_call(tmp_path):
    """The C++ target does not share the C target's accessor path -- it emits
    native member calls against `pssc::reg<T>` -- so the masked write is a
    method on that template rather than a generated accessor."""
    out = _generate(tmp_path, "cpp-progseq")
    assert "this->regs.csr.write_val_masked(1, 1);" in \
        (out / "pss_top.hpp").read_text()
    assert "void write_val_masked(raw_t mask, raw_t val)" in \
        (out / "pssc_reg.hpp").read_text()


@pytest.mark.skipif(not available_c_compilers(), reason="no C++ compiler")
def test_cpp_output_compiles(tmp_path):
    """Instantiates the template -- `write_val_masked` is only type-checked when
    something calls it, so generating it is not evidence that it compiles."""
    out = _generate(tmp_path, "cpp-progseq")
    main = out / "main.cpp"
    main.write_text(
        '#include "pss_top.hpp"\n'
        'int main() { pssc::mmio_mem m; '
        'auto t = pss_top::pss_top::create(m, 0x1000); t->arm(); return 0; }\n')

    res = subprocess.run(
        ["g++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-c", str(main),
         "-I", str(out), "-o", str(out / "main.o")],
        capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
