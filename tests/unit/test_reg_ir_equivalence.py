"""The invariant of docs/design/reg-masked-access-plan.md §1.1.

    The IR for every spelling of a masked register write is the same IR, and no
    field-name string survives into it.

`"ch_en"` is a reference to a declared field that happens to be spelled as a
string because the LRM's signature is `write_field(string, bit[SZ])`. It is not
data. If it were still data by the time a backend saw it, every backend would
need its own name table, and any two of them could disagree about what a name
means -- silently, in the register bits.

The second test here is the one that keeps that from ever happening again: it
walks the whole translated IR and asserts that no register receiver carries a
field-name method or a string argument. A future backend cannot start depending
on a name it is never given.
"""
import pytest

from pssc import Parser
from pssc.ast2ir import AstToIrTranslator
from zuspec.dataclasses import ir


_PRELUDE = """
package p {
    import std_pkg::*;
    import addr_reg_pkg::*;
    struct csr_s : packed_s<> {
        rand bit[1]  ch_en;
        rand bit[3]  prio;
        rand bit[1]  use_ed;
        rand bit[26] rsvd;
        rand bit[1]  msb;
    }
    pure component csr_r : reg_c<csr_s, READWRITE, 32> {}
    pure component grp_c : reg_group_c { csr_r csr; }
    component top_c {
        grp_c regs;
%s
    }
}
"""


def translate(body: str):
    parser = Parser()
    parser.parses([("test.pss", _PRELUDE % body)])
    return AstToIrTranslator(debug=False).translate(parser.link())


def _fn_body(ctx, name):
    top = ctx.type_map["p::top_c"]
    for fn in top.functions:
        if fn.name == name:
            return fn.body
    raise AssertionError(f"no function {name}")


def _norm(node):
    """A comparable rendering of an IR subtree, with ``loc`` normalised away.

    The spellings sit on different source lines, and discarding that to make a
    dump compare equal would be the wrong trade -- so the test drops it here
    instead, and requires everything else to match exactly (§1.1).
    """
    import dataclasses as dc
    if isinstance(node, list):
        return [_norm(x) for x in node]
    if dc.is_dataclass(node):
        return (type(node).__name__,
                {f.name: _norm(getattr(node, f.name))
                 for f in dc.fields(node) if f.name != "loc"})
    return node


# --- the equivalence ------------------------------------------------------

def test_three_spellings_produce_identical_ir():
    """write_field / write_masked / write_val_masked are one operation."""
    ctx = translate("""
        target function void a() { regs.csr.write_field("ch_en", 1); }
        target function void b() { regs.csr.write_masked({.ch_en=1}, {.ch_en=1}); }
        target function void c() { regs.csr.write_val_masked(1, 1); }
    """)
    assert ctx.errors == []
    a, b, c = (_norm(_fn_body(ctx, n)) for n in "abc")
    assert a == b, "write_field and write_masked disagree"
    assert a == c, "write_field and write_val_masked disagree"


def test_multi_bit_field_spellings_agree():
    """The same, for a field that is neither one bit nor at bit 0.

    `prio` is bits [3:1], so mask 0xe and value 2<<1 -- a case where getting the
    shift or the width wrong still produces a plausible-looking constant.
    """
    ctx = translate("""
        target function void a() { regs.csr.write_field("prio", 2); }
        target function void b() { regs.csr.write_masked({.prio=7}, {.prio=2}); }
        target function void c() { regs.csr.write_val_masked(0xe, 4); }
    """)
    assert ctx.errors == []
    a, b, c = (_norm(_fn_body(ctx, n)) for n in "abc")
    assert a == b
    assert a == c


# --- no strings in the IR -------------------------------------------------

_FIELD_METHODS = {"write_field", "write_fields", "write_masked"}


def _walk(node, fn, seen=None):
    import dataclasses as dc
    if seen is None:
        seen = set()
    if not dc.is_dataclass(node) or id(node) in seen:
        return
    seen.add(id(node))
    fn(node)
    for f in dc.fields(node):
        v = getattr(node, f.name, None)
        if isinstance(v, list):
            for x in v:
                _walk(x, fn, seen)
        else:
            _walk(v, fn, seen)


def assert_no_field_names_in_ir(ctx):
    """No register call in ``ctx`` names a field, anywhere in the type table."""
    offenders = []

    def visit(n):
        if not isinstance(n, ir.ExprCall):
            return
        f = n.func
        if isinstance(f, ir.ExprAttribute) and f.attr in _FIELD_METHODS:
            offenders.append(f.attr)
        if isinstance(f, ir.ExprAttribute) and f.attr == "write_val_masked":
            for a in n.args:
                assert not (isinstance(a, ir.ExprConstant)
                            and isinstance(a.value, str)), \
                    f"write_val_masked carries a string argument {a.value!r}"

    for dt in ctx.type_map.values():
        _walk(dt, visit)
    assert offenders == [], \
        f"field-name method(s) survived into the IR: {sorted(set(offenders))}"


def test_no_field_name_survives_translation():
    ctx = translate("""
        target function void f() {
            regs.csr.write_field("ch_en", 1);
            regs.csr.write_fields({"use_ed", "prio"}, {1, 3});
            regs.csr.write_masked({.ch_en=1, .prio=7}, {.ch_en=1, .prio=2});
            regs.csr.write_val_masked(1, 1);
        }
    """)
    assert ctx.errors == []
    assert_no_field_names_in_ir(ctx)


def test_write_fields_coalesces_to_one_call():
    """The plural form is one bus read-modify-write, not N.

    That is the whole reason it exists, and on a register whose read has side
    effects the difference is observable -- so it is pinned rather than left to
    whichever loop shape a later refactor prefers.
    """
    ctx = translate("""
        target function void f() { regs.csr.write_fields({"ch_en", "prio"}, {1, 2}); }
    """)
    assert ctx.errors == []
    body = _fn_body(ctx, "f")
    assert len(body) == 1, "write_fields did not coalesce"
    call = body[0].expr
    assert call.func.attr == "write_val_masked"
    # ch_en is bit 0 (mask 0x1), prio is [3:1] (mask 0xe) -> 0xf; 1 | (2<<1) = 5
    assert [a.value for a in call.args] == [0xf, 0x5]
