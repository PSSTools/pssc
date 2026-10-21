"""One list of what a generic IR walk does not descend into
(``targets/ir_walk.py``).

Five walkers each kept a hand-copied tuple of the fields to skip, and four
kept none and walked into ``provenance`` (which holds IR nodes of *other*
bodies) and ``loc``. ``Base.annotations`` (LRM 7.13) made a sixth field to
add to every one of them.
"""
import re
from pathlib import Path

import zuspec.ir.core as ir

from pssc.targets import field_writes, group_binding, ir_walk

SRC = Path(__file__).resolve().parents[2] / "src" / "pssc"

#: The walkers converted to ir_walk. A walker that should not skip these
#: fields says so where it walks (comp_inherit._rewrite).
WALKERS = ["array_methods", "field_writes", "body_kinds", "index_bounds",
           "comp_foreach", "group_binding", "reserved_access",
           "validate_calls", "pkg_functions"]


def test_every_base_field_is_either_a_child_or_listed():
    base = {f for f in ir.Base.__dataclass_fields__}
    assert base == set(ir_walk.NON_CHILD_FIELDS)


def test_the_walkers_use_the_one_list():
    for name in WALKERS:
        text = (SRC / "targets" / f"{name}.py").read_text()
        assert "from .ir_walk import" in text, name


def test_no_walker_keeps_its_own_copy():
    # A tuple that names "provenance" or "doc_trailing" beside "loc" is a
    # hand-kept skip list.
    pat = re.compile(r"\(\s*\"loc\"\s*,\s*\"(provenance|doc)")
    offenders = [str(p.relative_to(SRC)) for p in SRC.rglob("*.py")
                 if p.name != "ir_walk.py" and pat.search(p.read_text())]
    assert offenders == []


def _fn_with_annotated_call():
    call = ir.ExprCall(func=ir.ExprRefUnresolved(name="g"), args=[])
    stmt = ir.StmtExpr(expr=call)
    other = ir.ExprCall(func=ir.ExprRefUnresolved(name="h"), args=[])
    stmt.provenance = ir.Provenance(pass_name="test", source_nodes=[other])
    stmt.annotations = [ir.Annotation(type_qname="p::a_s",
                                      values={"x": 1})]
    return ir.Function(name="f", body=[stmt]), call


def test_children_skip_annotations_and_provenance():
    fn, call = _fn_with_annotated_call()
    stmt = fn.body[0]
    kids = list(ir_walk.children(stmt))
    assert call in kids
    assert not any(isinstance(k, list) and k and isinstance(k[0], ir.Annotation)
                   for k in kids)
    assert stmt.provenance not in kids


def test_a_converted_walker_does_not_yield_an_annotation():
    fn, call = _fn_with_annotated_call()
    nodes = list(field_writes._walk(fn.body))
    assert call in nodes
    assert not [n for n in nodes if isinstance(n, ir.Annotation)]
    names = [getattr(getattr(n, "func", None), "name", None)
             for n in group_binding._walk(fn.body)]
    assert "g" in names and "h" not in names
