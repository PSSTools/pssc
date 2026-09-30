"""B1b: native component `bind` directives reach the IR (no inference).

The component-level `bind pool targets;` directive is surfaced by pssparser as a
``ComponentBind`` AST node and consumed by ``ast2ir._translate_component_binds``
into real ``ir.PoolBind``s. The old ``_infer_pools_and_binds`` reconstruction is
deleted, so binds must come from the source.
"""
from __future__ import annotations
import os
import tempfile
import pytest

from pssc import Parser, AstToIrTranslator

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _translate(src: str):
    with tempfile.NamedTemporaryFile(suffix='.pss', mode='w', delete=False) as f:
        f.write(src)
        fname = f.name
    try:
        p = Parser()
        p.parse([fname])
        return AstToIrTranslator().translate(root := p.link())
    finally:
        try:
            os.unlink(fname)
        except OSError:
            pass


def test_wildcard_bind_reaches_ir():
    ctx = _translate("""\
component pss_top {
    buffer Data { rand int x; }
    pool Data dpool;
    bind dpool *;
    action producer { output Data out; }
}
""")
    comp = ctx.type_map["pss_top"]
    binds = [b for b in comp.pool_binds if b.pool_name == "dpool"]
    assert len(binds) == 1
    assert binds[0].is_wildcard is True
    assert binds[0].field_paths == []


def test_targeted_bind_paths_reach_ir():
    ctx = _translate("""\
component pss_top {
    buffer Data { rand int x; }
    pool Data dpool;
    bind dpool { producer.out, consumer.inp };
    action producer { output Data out; }
    action consumer { input Data inp; }
}
""")
    comp = ctx.type_map["pss_top"]
    binds = [b for b in comp.pool_binds if b.pool_name == "dpool"]
    assert len(binds) == 1
    assert binds[0].is_wildcard is False
    assert binds[0].field_paths == ["producer.out", "consumer.inp"]


def test_no_bind_inference_without_directive():
    """A flow type used without a `bind` directive yields no synthesized bind."""
    ctx = _translate("""\
component pss_top {
    buffer Data { rand int x; }
    pool Data dpool;
    action producer { output Data out; }
}
""")
    comp = ctx.type_map["pss_top"]
    assert comp.pool_binds == [], \
        f"expected no inferred binds, got {comp.pool_binds}"


# A pool reached through an instance (LRM 12.3, Ex. 132): the bind names the
# pool by its path, and each target names its action through the component
# instances it crosses. Dropping either path binds a different pool/action.
_GFX = """\
component gfx_c {
    buffer Data { rand int x; }
    pool Data dpool;
    action producer { output Data out; }
    action consumer { input Data inp; }
}
component pss_top {
    gfx_c gfx0;
    gfx_c gfx[2];
    %s
}
"""


def test_bind_through_instance_keeps_path():
    ctx = _translate(_GFX % "bind gfx0.dpool *;")
    comp = ctx.type_map["pss_top"]
    assert not ctx.errors, ctx.errors
    assert len(comp.pool_binds) == 1
    b = comp.pool_binds[0]
    assert b.pool_name == "dpool"
    assert b.pool_path == ["gfx0", "dpool"]
    assert b.is_wildcard is True


def test_bind_target_keeps_component_path():
    ctx = _translate(
        _GFX % "bind gfx0.dpool { gfx0.producer.out, gfx0.consumer.inp };")
    comp = ctx.type_map["pss_top"]
    assert not ctx.errors, ctx.errors
    assert comp.pool_binds[0].field_paths == [
        "gfx0.producer.out", "gfx0.consumer.inp"]


def test_bind_range_is_a_located_error():
    """A range selects several instances; until the pool-binding table (P2)
    there is nothing to bind them to, so it is refused, never dropped."""
    ctx = _translate(_GFX % "bind gfx0.dpool { gfx[0..1].producer.out };")
    assert any("bind ranges are not supported" in e and "line 10" in e
               for e in ctx.errors), ctx.errors
