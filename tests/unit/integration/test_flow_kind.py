"""`flow_kind` in the IR is an `ir.FlowKind`, never a string (P0, F14).

ir-core declares ``DataTypeStruct.flow_kind: Optional[FlowKind]``; ast2ir
wrote ``"buffer"``/``"state"``/... into it, and eleven consumers compared
against those strings. A consumer written to the declared type then reads
``False`` for every flow object, silently. The SV target keeps strings in
its OWN binding records; it converts where it reads the IR.
"""
import glob
import os

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
import zuspec.ir.core as ir

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_PATTERNS = sorted(glob.glob(os.path.join(
    os.path.dirname(__file__), "..", "..", "patterns", "*.pss")))


@pytest.mark.parametrize("path", _PATTERNS, ids=[os.path.basename(p) for p in _PATTERNS])
def test_flow_kind_is_enum(path):
    parser = pssc.Parser()
    parser.parse([path])
    ctx = AstToIrTranslator().translate(parser.link())
    for name, dt in ctx.type_map.items():
        kind = getattr(dt, "flow_kind", None)
        assert kind is None or isinstance(kind, ir.FlowKind), (name, kind)


def test_each_struct_kind_maps():
    parser = pssc.Parser()
    parser.parses([("k.pss", "buffer b_t { } stream s_t { } state st_t { } "
                             "resource r_t { } struct p_t { }")])
    tm = AstToIrTranslator().translate(parser.link()).type_map
    assert [tm[n].flow_kind for n in ("b_t", "s_t", "st_t", "r_t", "p_t")] == [
        ir.FlowKind.BUFFER, ir.FlowKind.STREAM, ir.FlowKind.STATE,
        ir.FlowKind.RESOURCE, None]
