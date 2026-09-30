"""Pools and component binds reach the IR whole (P0, F10 and F11).

A pool whose size was not a literal got ``capacity=None`` -- unbounded --
without a word, and a ``pool``/``bind`` written in an ``extend component``
was dropped: the component path handled them, the extension path did not.
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")


def _translate(src: str):
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        return AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)


def _pool(ctx, comp, name):
    pools = [p for p in ctx.type_map[comp].pools if p.name == name]
    assert len(pools) == 1, ctx.type_map[comp].pools
    return pools[0]


def test_literal_pool_size():
    ctx = _translate("resource r_r { } component pss_top { pool [4] r_r rp; }")
    assert not ctx.errors, ctx.errors
    assert _pool(ctx, "pss_top", "rp").capacity == 4


def test_const_expr_pool_size():
    """A package constant sizes a pool (LRM 12.1: a constant expression)."""
    ctx = _translate("""\
package cfg_pkg { static const int N = 3; }
resource r_r { }
component pss_top { pool [cfg_pkg::N] r_r rp; }
""")
    assert not ctx.errors, ctx.errors
    assert _pool(ctx, "pss_top", "rp").capacity == 3


def test_unsized_pool_is_unbounded():
    ctx = _translate("buffer b_b { } component pss_top { pool b_b bp; }")
    assert not ctx.errors, ctx.errors
    assert _pool(ctx, "pss_top", "bp").capacity is None


def test_nonconst_pool_size_is_an_error():
    """A size that does not fold is refused, not read as unbounded."""
    ctx = _translate("""\
resource r_r { }
component pss_top {
    int n;
    pool [n] r_r rp;
}
""")
    assert any("line 4: pool size" in e for e in ctx.errors), ctx.errors


def test_pool_in_extend_component():
    ctx = _translate("""\
resource r_r { }
component pss_top { }
extend component pss_top {
    pool [2] r_r rp;
}
""")
    assert not ctx.errors, ctx.errors
    assert _pool(ctx, "pss_top", "rp").capacity == 2


def test_bind_in_extend_component():
    ctx = _translate("""\
resource r_r { }
component pss_top { pool [2] r_r rp; }
extend component pss_top {
    bind rp *;
}
""")
    assert not ctx.errors, ctx.errors
    binds = ctx.type_map["pss_top"].pool_binds
    assert [(b.pool_name, b.is_wildcard) for b in binds] == [("rp", True)]
