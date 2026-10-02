"""The pool-binding table (B5a, LRM 12.3): which pool an action's reference
uses, in each component instance it may run in.

ir-core ``xf/pss_lower/pools.py`` is the one walk that answers it: explicit
bindings over default ones (c), the top-most instance's over a lower one's
(e), ``{sub.*}`` reaching only ``sub``'s subtree, and the errors 12.3 names.
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir.core.xf.pss_lower.comp_tree import CompLayouts
from zuspec.ir.core.xf.pss_lower.pools import PoolTable
from zuspec.ir.core.xf.validate import UnsupportedConstructError

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _ctx(src):
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        ctx = AstToIrTranslator().translate(parser.link(), files=parser.file_map)
    finally:
        os.unlink(fname)
    assert not ctx.errors, ctx.errors
    return ctx


def _table(src, root="pss_top"):
    ctx = _ctx(src)
    return ctx, PoolTable(CompLayouts(ctx.type_map), root)


def _pool(ctx, table, inst_path, action, field):
    """``pool@instance path`` reference *field* of *action* uses in the
    instance at *inst_path*, or None."""
    inst = next(i for i, (_, p, _) in table.insts.items() if p == inst_path)
    f = next(f for f in ctx.type_map[action].fields if f.name == field)
    p = table.pool_of(inst, action, f)
    return None if p is None else "%s@%s" % (p.name, table.insts[p.inst][1])


_EX134 = """
resource cpu_core_s {}
component dma_c {
    resource channel_s {}
    pool[2] channel_s channels;
    bind channels {*};
    action transfer { lock channel_s chan; lock cpu_core_s core; }
}
component pss_top {
    dma_c dma0, dma1;
    pool[4] cpu_core_s cpu;
    bind cpu {dma0.*, dma1.*};
    action T { activity { do dma_c::transfer; } }
}
"""


def test_ex134_each_instance_its_own_channels_one_cpu_pool():
    ctx, t = _table(_EX134)
    assert _pool(ctx, t, "dma0", "dma_c::transfer", "chan") == "channels@dma0"
    assert _pool(ctx, t, "dma1", "dma_c::transfer", "chan") == "channels@dma1"
    assert _pool(ctx, t, "dma0", "dma_c::transfer", "core") == "cpu@"
    assert _pool(ctx, t, "dma1", "dma_c::transfer", "core") == "cpu@"


def test_a_wildcard_under_an_instance_reaches_only_its_subtree():
    """`bind p {a.*}` was recorded as a bare `*` once: it bound b too."""
    ctx, t = _table("""
buffer d_b { rand bit[4] v; }
component sub_c { action A { output d_b o; } }
component pss_top {
    sub_c a, b;
    pool d_b p;
    bind p {a.*};
    action T { activity { do sub_c::A; } }
}""")
    assert _pool(ctx, t, "a", "sub_c::A", "o") == "p@"
    assert _pool(ctx, t, "b", "sub_c::A", "o") is None


def test_explicit_over_default_and_top_down():
    ctx, t = _table("""
buffer d_b { rand bit[4] v; }
component sub_c {
    pool d_b own;
    bind own *;
    action A { output d_b o; input d_b i; }
}
component pss_top {
    sub_c a, b;
    pool d_b top;
    pool d_b special;
    bind top *;
    bind special {a.A.i};
    action T { activity { do sub_c::A; } }
}""")
    # The top's default binding wins over sub_c's own (12.3 e) ...
    assert _pool(ctx, t, "a", "sub_c::A", "o") == "top@"
    assert _pool(ctx, t, "b", "sub_c::A", "i") == "top@"
    # ... and an explicit one over both (12.3 c).
    assert _pool(ctx, t, "a", "sub_c::A", "i") == "special@"


def test_a_pool_of_another_type_is_not_a_default():
    ctx, t = _table("""
buffer d_b { rand bit[4] v; }
buffer e_b { rand bit[4] v; }
component pss_top {
    pool e_b ep;
    bind ep *;
    pool d_b dp;
    bind dp *;
    action A { output d_b o; }
    action T { activity { do A; } }
}""")
    assert _pool(ctx, t, "", "pss_top::A", "o") == "dp@"


def test_an_explicit_bind_to_a_pool_of_another_type_is_an_error():
    ctx, t = _table("""
buffer d_b { rand bit[4] v; }
buffer e_b { rand bit[4] v; }
component pss_top {
    pool e_b ep;
    bind ep {A.o};
    action A { output d_b o; }
    action T { activity { do A; } }
}""")
    with pytest.raises(UnsupportedConstructError, match="12.3 g"):
        _pool(ctx, t, "", "pss_top::A", "o")


def test_two_defaults_from_one_component_are_an_error():
    ctx, t = _table("""
buffer d_b { rand bit[4] v; }
component pss_top {
    pool d_b p1;
    pool d_b p2;
    bind p1 *;
    bind p2 *;
    action A { output d_b o; }
    action T { activity { do A; } }
}""")
    with pytest.raises(UnsupportedConstructError, match="12.3 f"):
        _pool(ctx, t, "", "pss_top::A", "o")
