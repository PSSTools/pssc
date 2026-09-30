"""`export T(...);` (LRM 20.10) is recorded, and picks what bc runs (P0, F12).

The declaration used to fall through ast2ir unrecorded, so the scenario pass
chose its root and its exports by heuristics -- "the component owning the
most actions", "every action nothing traverses" -- whatever the model said.
"""
import os
import tempfile

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir.core.xf import PSSToScenarioPass

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


_TWO_COMPONENTS = """\
component big_c {
    action X { exec body { } }
    action Y { exec body { } }
    action Z { exec body { } }
}
component pss_top {
    action A { exec body { } }
    action B { exec body { } }
}
%s
"""


def test_export_recorded():
    ctx = _translate(_TWO_COMPONENTS % "export pss_top::A();")
    assert not ctx.errors, ctx.errors
    assert ctx.export_actions == ["pss_top::A"]


def test_export_in_a_component_is_qualified():
    ctx = _translate("component pss_top { action A { exec body { } } export A(); }")
    assert not ctx.errors, ctx.errors
    assert ctx.export_actions == ["pss_top::A"]


def test_export_selects_root_and_exports():
    """With no root and no exports given, the recorded export decides both:
    `pss_top` although `big_c` owns more actions, and only `A`."""
    ctx = _translate(_TWO_COMPONENTS % "export pss_top::A();")
    module = PSSToScenarioPass().lower(ctx)
    assert module.root.type_name == "pss_top"
    assert module.export_actions == ["A"]


def test_explicit_exports_still_win():
    ctx = _translate(_TWO_COMPONENTS % "export pss_top::A();")
    module = PSSToScenarioPass(root="pss_top", exports=["B"]).lower(ctx)
    assert module.export_actions == ["B"]
