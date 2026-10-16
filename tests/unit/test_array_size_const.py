"""Array sizes given by a named constant must fold (design §3E).

    static const int WB_DMA_MAX_CH = 4;
    ...
    wb_dma_ch_c ch[WB_DMA_MAX_CH];

A real model names its channel count. Only literal sizes used to fold, so this
reached the IR as `size = -1` -- and every consumer of an array size (the
generated `ch_size()` accessor, the constructor loop that builds one
sub-component per element, `foreach` unrolling) has nothing to work from.

-1 is at least a value that shows up. The reason it is worth a test rather than
a shrug is that it shows up *far* from here, in generated code.
"""
import pytest

from pssc import Parser
from pssc.ast2ir import AstToIrTranslator


def sizes(pss_code: str, comp="c_c"):
    parser = Parser()
    parser.parses([("test.pss", pss_code)])
    ctx = AstToIrTranslator(debug=False).translate(parser.link())
    return {f.name: getattr(f.datatype, "size", None) for f in ctx.type_map[comp].fields}


def test_literal_array_size_still_folds():
    assert sizes("component s_c {} component c_c { s_c a[3]; }")["a"] == 3


def test_static_const_array_size_folded():
    assert sizes("""
        package p { static const int N = 4; }
        component s_c {}
        import p::*;
        component c_c { s_c a[N]; }
    """)["a"] == 4


def test_qualified_const_array_size_folded():
    assert sizes("""
        package p { static const int N = 4; }
        component s_c {}
        component c_c { s_c a[p::N]; }
    """)["a"] == 4


def test_a_constant_expression_size_folds():
    """`N + 1` folds from the constants the LINKER resolved, evaluated as
    the IR expression it translates to -- not guessed at. It used to stay
    -1, and nothing reported it (plan P2, I9). A size that does not fold is
    an error now (tests/unit/ir/test_ir_types.py).

    (An *undeclared* name never gets this far; pssparser rejects it.)"""
    assert sizes("""
        package p { static const int N = 4; }
        component s_c {}
        import p::*;
        component c_c { s_c a[N + 1]; }
    """)["a"] == 5
