"""Struct values on bc (P1.3, design P1-D1).

A struct is laid out flat: one slot per scalar leaf, base struct's fields
first, by ``pss_lower.layout`` -- the same layout for an action attribute, a
local, a parameter and a return value. Before this, any struct in procedural
code was "DataTypeStruct is not supported", and a rand struct attribute was
solved as ONE 32-bit variable.

Also here: an attribute's declared initial value (``bit[4] g = 3;``) is
applied. Nothing applied it, so it read 0.
"""
import pytest

from zuspec.be.bc.lower.errors import LoweringError
from zuspec.ir.core.xf.validate import UnsupportedConstructError

from .test_activity_bc_runs import trace

_STRUCTS = """\
import std_pkg::*;
struct base_s { bit[4] b = 1; }
struct csr_s : base_s { bit[12] tot; bit[3] prio = 5; }
struct desc_s { csr_s csr; bit[32] adr = 0xa0; }
function desc_s build(bit[32] a) { desc_s d; d.adr = a; d.csr.tot = 12; return d; }
function void bump(desc_s d) { d.csr.tot = d.csr.tot + 1; }
component pss_top {
    action T {
        %s
        exec body { %s }
    }
}
"""


def _run(body: str, attrs: str = "", seed: int = 0):
    return trace(_STRUCTS % (attrs, body), seed=seed)


def test_a_local_takes_its_fields_initial_values():
    """8.3: base fields first; a field with no initializer is 0."""
    assert _run('desc_s d; message(NONE, "%u %u %u %u", '
                'd.csr.b, d.csr.tot, d.csr.prio, d.adr);') == ["1 0 5 160"]


def test_assignment_copies_deeply():
    assert _run('desc_s a; a.csr.tot = 7; desc_s d; d = a; d.csr.tot = 9; '
                'desc_s e = a; e.adr = 1; '
                'message(NONE, "%u %u %u %u", a.csr.tot, d.csr.tot, a.adr, e.adr);') \
        == ["7 9 160 1"]


def test_a_nested_struct_assigns_as_a_value():
    assert _run('desc_s a; csr_s c; c.tot = 3; a.csr = c; c.tot = 4; '
                'message(NONE, "%u %u", a.csr.tot, c.tot);') == ["3 4"]


@pytest.mark.parametrize("expr,want", [("f == a", "1"), ("f != a", "0")])
def test_equality_compares_every_field(expr, want):
    assert _run(f'desc_s a; desc_s f = a; bool r = {expr}; message(NONE, "%u", r);') \
        == [want]


def test_a_differing_nested_field_makes_them_unequal():
    assert _run('desc_s a; desc_s f = a; f.csr.b = 0; bool r = (f == a); '
                'message(NONE, "%u", r);') == ["0"]


def test_a_struct_parameter_is_the_callers_instance():
    """20.3.2: a struct parameter is a handle; the callee's write is seen."""
    assert _run('desc_s d; bump(d); bump(d); message(NONE, "%u", d.csr.tot);') \
        == ["2"]


def test_a_struct_is_returned_by_value():
    assert _run('desc_s r = build(0x80); desc_s r2 = r; r2.adr = 1; '
                'message(NONE, "%u %u %u %u", r.adr, r.csr.tot, r.csr.prio, r2.adr);') \
        == ["128 12 5 1"]


def test_a_call_result_is_a_struct_argument():
    assert _run('bump(build(1)); desc_s d = build(2); bump(d); '
                'message(NONE, "%u", d.csr.tot);') == ["13"]


# -- attributes ---------------------------------------------------------------

def test_an_attributes_initial_value_is_applied():
    """It read 0: no lowering applied a declared initial value."""
    assert _run('message(NONE, "%u", g);', attrs="bit[4] g = 3;") == ["3"]


def test_a_struct_attribute_takes_its_fields_initial_values():
    assert _run('message(NONE, "%u %u", d.csr.prio, d.adr);', attrs="desc_s d;") \
        == ["5 160"]


def test_a_struct_attribute_is_a_value():
    assert _run('desc_s l = d; l.adr = 2; d.csr = l.csr; '
                'message(NONE, "%u %u", d.adr, l.adr);', attrs="desc_s d;") \
        == ["160 2"]


def test_a_struct_field_initializer_reads_its_own_struct():
    """`b = a + 1` in the struct means the attribute's `a`, not the action's."""
    src = """\
import std_pkg::*;
struct s_t { bit[4] a = 2; bit[4] b = a + 1; }
component pss_top {
    action T { bit[4] a = 7; s_t s;
        exec body { message(NONE, "%u %u", s.b, a); } }
}
"""
    assert trace(src) == ["3 7"]


_RAND = """\
import std_pkg::*;
struct base_s { rand bit[4] b; constraint b < 5; }
struct s_t : base_s { rand bit[4] f; bit[4] g = 3; constraint f > b; }
component pss_top {
    action T { rand s_t s; rand bit[4] x; constraint s.f < x;
        exec body { message(NONE, "%u %u %u %u", s.b, s.f, s.g, x); } }
}
"""


def test_a_rand_struct_attribute_solves_each_leaf_under_its_types_constraints():
    """Its own and its base's constraints are in force, with `self` meaning
    the attribute; the action's constraint reaches into it."""
    seen = set()
    for seed in range(30):
        b, f, g, x = map(int, trace(_RAND, seed=seed)[0].split())
        assert b < 5 and f > b and f < x and g == 3
        seen.add((b, f, x))
    assert len(seen) > 5


# -- refusals -----------------------------------------------------------------

@pytest.mark.parametrize("body,match", [
    ('desc_s a; desc_s b; bool r = (a < b);', "compare only with == and !="),
    ('desc_s a; message(NONE, "%u", a);', "used where a scalar is needed"),
    ('desc_s a; int x = a + 1;', "used where a scalar is needed"),
])
def test_a_struct_where_a_scalar_is_needed_is_an_error(body, match):
    with pytest.raises(LoweringError, match=match):
        _run(body)


def test_a_non_constant_initializer_on_a_local_struct_is_refused():
    src = """\
import std_pkg::*;
struct s_t { bit[4] a = 2; bit[4] b = a + 1; }
component pss_top { action T { exec body { s_t s; message(NONE, "%u", s.b); } } }
"""
    with pytest.raises(LoweringError, match="not a constant"):
        trace(src)


def test_a_struct_exec_block_is_refused():
    """Nothing runs a struct's pre_solve yet; skipping it would be silent."""
    src = """\
import std_pkg::*;
struct s_t { rand bit[4] a; exec pre_solve { a = 1; } }
component pss_top { action T { rand s_t s; exec body { } } }
"""
    with pytest.raises(UnsupportedConstructError, match="exec pre_solve of struct"):
        trace(src)
