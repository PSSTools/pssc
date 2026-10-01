"""Component arrays in bc procedural code (bc procedural gaps G2, B-D2).

``foreach`` over a component array is unrolled, element 0 first (20.7.8 d), so
each copy's index is a constant and its element a static instance. An element
whose index is known only at run time (``comp.ch[j]``) is a dispatch: the
access, call or store is lowered once per element, and the copy whose index
matches runs; an index out of bounds is a run-time error.
"""
import pytest

from zuspec.be.bc.interp.vm import VMError
from zuspec.be.bc.lower.errors import LoweringError, PssSemanticError

from .test_activity_bc_runs import trace

_SRC = """\
import std_pkg::*;
component ch_c {
    int id;
    int hits;
    function int key() { hits += 1; return id * 10; }
    function void set(int v) { id = v; }
}
component pss_top {
    ch_c ch[3];
    exec init_down { %s }
    function int sum() { int s = 0; foreach (ch[i]) { s += ch[i].key(); } return s; }
    action T {
        exec body { %s }
    }
}
"""


def _run(body: str, init: str = "foreach (ch[i]) { ch[i].id = i + 1; }"):
    return trace(_SRC % (init, body))


def test_foreach_visits_every_element_in_order():
    assert _run('message(NONE, "%d %d %d", comp.ch[0].id, comp.ch[1].id, comp.ch[2].id);') \
        == ["1 2 3"]


def test_foreach_iterator_form_names_the_element():
    assert _run('message(NONE, "%d %d", comp.ch[0].id, comp.ch[2].id);',
                init="foreach (c : ch) { c.set(7); }") == ["7 7"]


def test_foreach_in_a_component_function():
    assert _run('message(NONE, "%d %d", comp.sum(), comp.ch[1].hits);') == ["60 1"]


@pytest.mark.parametrize("j,want", [(0, "1 10"), (1, "2 20"), (2, "3 30")])
def test_a_computed_index_reads_and_calls_the_element_it_selects(j, want):
    assert _run(f'int j = {j}; message(NONE, "%d %d", comp.ch[j].id, comp.ch[j].key());') \
        == [want]


def test_a_computed_index_stores_to_one_element_and_calls_it_once():
    assert _run('int j = 1; comp.ch[j].id = 9; comp.ch[j].set(comp.ch[j].id + 1); '
                'int k = comp.ch[j + 1].key(); '
                'message(NONE, "%d %d %d %d", comp.ch[0].id, comp.ch[1].id, '
                'comp.ch[2].hits, k);') == ["1 10 1 30"]


def test_break_and_continue_end_the_loop_and_the_copy():
    assert _run('message(NONE, "%d %d %d", comp.ch[0].id, comp.ch[1].id, comp.ch[2].id);',
                init="foreach (ch[i]) { if (i == 1) { continue; } "
                     "if (i == 2) { break; } ch[i].id = 5; }") == ["5 0 0"]


def test_an_index_out_of_bounds_is_a_run_time_error():
    with pytest.raises(VMError, match=r"out of bounds of component array 'ch' \[3\]"):
        _run('int j = 3; message(NONE, "%d", comp.ch[j].id);')


@pytest.mark.parametrize("init,exc,match", [
    ("foreach (ch[i]) { i = 2; }", PssSemanticError, "foreach variable 'i' is read-only"),
    ("int a; foreach (c : ch) { a = c; }", PssSemanticError, "is a component instance"),
])
def test_refused(init, exc, match):
    with pytest.raises(exc, match=match):
        _run("", init=init)
