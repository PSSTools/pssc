"""C has one namespace: each file-scope name the generated C declares is
declared once (`targets/c/c_names.py`).

A type is named as PSS names it, so no function name can equal it (a function
is `<prefix>_<name>`). The rules that remain -- operations, sub-component
accessors, the lifecycle, register accessors, imports -- can still produce one
name twice, and that is refused before any file is written, naming both
sources. The construct matrix holds the cases that USED to collide and must
now run (`a sub-component named t`, `a component, a struct and an enum with
one stem`).
"""
from __future__ import annotations

import argparse
import re

import pytest

from pssc import driver, targets
from pssc.driver import CompileError
from pssc.targets import op_model as om
from pssc.targets.c import c_names
from pssc.targets.c.backend import COpModelBackend
from pssc.targets.c.style import CSettings, CStylePolicy

from . import trace_harness as th
from .conftest import diagnostic_text
from .op_model import op_model_sources


def _refusal(tmp_path, pss, target="c"):
    with pytest.raises(CompileError) as ei:
        th.compile_model(tmp_path, pss, target)
    return diagnostic_text(ei)


def test_a_sub_component_named_create_is_refused_naming_both(tmp_path):
    text = _refusal(tmp_path, """
component leaf_c { target function int f() { return 1; } }
component pss_top {
  leaf_c create;
  target function void run() { }
}""")
    assert "'pss_top_create'" in text
    assert "the factory of 'pss_top'" in text
    assert "sub-component 'pss_top.create'" in text


def test_an_operation_named_init_or_destroy_is_refused(tmp_path):
    text = _refusal(tmp_path, """
component pss_top {
  target function int destroy() { return 1; }
  target function int init() { return 2; }
  target function void run() { }
}""")
    assert "'pss_top_destroy' is both the destructor of 'pss_top' and " \
           "operation 'pss_top.destroy'" in text
    assert "'pss_top_init' is both the constructor of 'pss_top' and " \
           "operation 'pss_top.init'" in text


def test_the_other_targets_take_the_same_names(tmp_path):
    """Only C flattens members into one namespace."""
    pss = """
component leaf_c { target function int f() { return 1; } }
component pss_top {
  leaf_c create;
  target function int destroy() { return 1; }
  target function void run() { }
}"""
    for tgt in ("cpp", "py"):
        (tmp_path / tgt).mkdir()
        th.compile_model(tmp_path / tgt, pss, tgt)


def test_two_structs_with_one_short_name_are_refused(tmp_path):
    """`p::cfg_s` and `q::cfg_s` are two PSS types and would be one C type."""
    text = _refusal(tmp_path, """
package p { struct cfg_s { bit[32] v; } }
package q { struct cfg_s { bit[16] w; } }
component pss_top {
  target function int f(p::cfg_s a, q::cfg_s b) { return 1; }
  target function void run() { }
}""")
    assert "'cfg_s' is both struct 'p::cfg_s' and struct 'q::cfg_s'" in text


@pytest.mark.parametrize("target", ["c", "cpp"])
def test_two_components_with_one_short_name_are_refused(tmp_path, target):
    """...a user error, not an internal one, and it names the way out."""
    text = _refusal(tmp_path, """
package p { component x_c { target function int f() { return 1; } } }
package q { component x_c { target function int g() { return 2; } } }
component pss_top {
  p::x_c a;
  q::x_c b;
  target function void run() { }
}""", target)
    assert "p::x_c" in text and "q::x_c" in text
    assert "--prefix-map" in text


# -- the check names what is emitted, and nothing else ------------------------
#
# A name the check invents is a model refused for a collision that would not
# have happened. So every name it gathers must be in the generated text.

@pytest.fixture(scope="module")
def ctx():
    tgt = targets.get("op-model-c")
    return driver.translate(op_model_sources(),
                            prelude=tgt.prelude(argparse.Namespace()))


@pytest.mark.parametrize("kw", [
    {},
    {"lifecycle": "static"},
    {"link_style": "direct", "reg_map": True,
     "seam_include": "pssc_mem_direct.h"},
], ids=["vtable", "static", "reg-map"])
def test_every_gathered_name_is_one_the_backend_emits(ctx, tmp_path, kw):
    model = om.elaborate(ctx, ctx.type_map["wb_dma_c"], tmp_path / "out")
    kw = dict(kw)
    kw.setdefault("seam_include", "pssc_mem_vtable.h")
    s = CSettings(prefix="wb_dma", **kw)
    be = COpModelBackend()
    be.prepare(model, s)
    # The private regs header carries whatever value layout and (under
    # `reg_map`) register-map struct the public header does not need to name
    # -- `be._regs_text`, cached by `prepare()`, is "" when there is none.
    text = (be.header_text(model, s) + "\n" + be.impl_text(model, s)
           + "\n" + be._regs_text)
    names = c_names.declared_names(be, model, s)
    assert len(names) > 20
    missing = [(n, who) for n, who in names
               if not re.search(rf"\b{re.escape(n)}\b", text)]
    assert not missing, missing


# -- enumerators ---------------------------------------------------------------
#
# PSS scopes an item by its enum; C puts every enumerator at file scope. The
# default style prefixes each with its enum's name (`CStylePolicy.enum_item`),
# and the construct matrix runs two enums with an `A` each. A style may spell
# items verbatim, and then a clash is refused here, before any file is written.

_TWO_AS = """
enum dir_e { A, B = 3 }
enum op_e { A = 7 }
component pss_top {
  target function bit[32] f(dir_e d, op_e o) {
    if (d == A) { return 1; }
    return 0;
  }
  target function void run() { }
}"""


class _Verbatim(CStylePolicy):
    def enum_item(self, enum_name, item):
        return item


def _check(tmp_path, style):
    p = tmp_path / "m.pss"
    p.write_text(th._PRELUDE + _TWO_AS)
    tgt = targets.get("op-model-c")
    ctx = driver.translate([str(p)], prelude=tgt.prelude(argparse.Namespace()))
    model = om.elaborate(ctx, ctx.type_map["pss_top"], tmp_path / "out")
    s = CSettings(prefix="pss_top", seam_include="pssc_mem_vtable.h")
    be = COpModelBackend(style)
    be.prepare(model, s)
    c_names.check(be, model, s)
    return be.header_text(model, s)


def test_enumerators_are_prefixed_with_their_enum(tmp_path):
    h = _check(tmp_path, None)
    assert "DIR_A = 0" in h and "OP_A = 7" in h


def test_a_style_whose_enumerators_clash_is_refused(tmp_path):
    with pytest.raises(CompileError) as ei:
        _check(tmp_path, _Verbatim())
    assert ("'A' is both item 'A' of enum 'dir_e' and item 'A' of enum "
            "'op_e'") in diagnostic_text(ei)
