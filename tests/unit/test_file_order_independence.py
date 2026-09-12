"""Elaboration must not depend on the order the files were presented in.

PSS 3.1 18.2: "Most elements may be referenced before their declaration in the
same source unit." A tool that resolves each construct against whatever it has
translated *so far* therefore produces a different model per file order -- and
`ast2ir` did, silently, at exit 0:

  - an `extend` listed before its target's file found no target and returned,
    dropping every action the extension declared. On the WB DMA model's
    companion-directory layout that was all 13 actions and 38% of the IR;
  - a component array sized by a constant declared in a later file folded to
    `size: -1`.

Both are fixed by the three-pass elaboration in
`AstToIrTranslator._translate_global_scope` (CONST, DECLARE, EXTEND).

**These tests assert on the IR, not on whether the sources parsed.** That
distinction is the whole point: every input below parses cleanly and exits 0 in
both orders, before and after the fix. A test that asserted "no exception" would
have passed throughout -- which is exactly how this survived. See
`docs/design/pssparser-defects-2026-08-02.md` D5 for the same lesson learned the same
way.
"""
import pytest

from pssc.driver import translate


def _write(tmp_path, **files):
    """Write ``name=source`` pairs; return {name: path}."""
    paths = {}
    for name, src in files.items():
        p = tmp_path / f"{name}.pss"
        p.write_text(src)
        paths[name] = str(p)
    return paths


def _type_names(ctx):
    return set(ctx.type_map)


def _field_names(ctx, type_name):
    dtype = ctx.type_map[type_name]
    return {f.name for f in getattr(dtype, "fields", [])}


# --------------------------------------------------------------------------
# extend

_COMP = """
component leaf_c { int n; }
component pss_top { leaf_c u; }
"""

_EXT_ACTION = """
extend component leaf_c {
    action probe_a { rand bit[8] v; constraint v < 10; }
}
"""


@pytest.mark.parametrize("reverse", [False, True], ids=["decl-first", "extend-first"])
def test_an_extension_declaring_an_action_survives_either_order(tmp_path, reverse):
    """The minimal form of the 13-action loss."""
    f = _write(tmp_path, a_comp=_COMP, b_ext=_EXT_ACTION)
    sources = [f["a_comp"], f["b_ext"]]
    if reverse:
        sources.reverse()

    ctx = translate(sources)

    assert ctx.errors == []
    assert any(name.endswith("probe_a") for name in _type_names(ctx)), (
        "the action declared by the extension is missing from the IR; "
        "file order changed what the model contains")


def test_extend_produces_the_same_type_set_in_either_order(tmp_path):
    f = _write(tmp_path, a_comp=_COMP, b_ext=_EXT_ACTION)
    forward = translate([f["a_comp"], f["b_ext"]])
    reverse = translate([f["b_ext"], f["a_comp"]])

    assert _type_names(forward) == _type_names(reverse)


@pytest.mark.parametrize("reverse", [False, True], ids=["decl-first", "extend-first"])
def test_an_extension_adding_a_field_survives_either_order(tmp_path, reverse):
    f = _write(
        tmp_path,
        a_comp="component leaf_c { int n; }\ncomponent pss_top { leaf_c u; }\n",
        b_ext="extend component leaf_c { int extra; }\n",
    )
    sources = [f["a_comp"], f["b_ext"]]
    if reverse:
        sources.reverse()

    ctx = translate(sources)

    assert ctx.errors == []
    assert "extra" in _field_names(ctx, "leaf_c")


# --------------------------------------------------------------------------
# constants folded into an array size (packages/dv-flow-libpss/docs/file-order-probes.md E2)

_CONST = "package m_pkg { const int N_CH = 4; }\n"
_ARRAY = """
import m_pkg::*;
component leaf_c { }
component pss_top { leaf_c ch[N_CH]; }
"""


@pytest.mark.parametrize("reverse", [False, True], ids=["const-first", "use-first"])
def test_an_array_sized_by_a_cross_file_const_folds_in_either_order(tmp_path, reverse):
    """`size: -1` is the silent form of this defect: an array that quietly
    sizes to nothing, with no diagnostic anywhere."""
    f = _write(tmp_path, a_const=_CONST, b_array=_ARRAY)
    sources = [f["a_const"], f["b_array"]]
    if reverse:
        sources.reverse()

    ctx = translate(sources)

    assert ctx.errors == []
    top = ctx.type_map["pss_top"]
    ch = [fld for fld in top.fields if fld.name == "ch"]
    assert ch, "the array field is missing entirely"
    size = getattr(ch[0].datatype, "size", None)
    assert size == 4, (
        f"array sized by a cross-file const folded to size={size}, not 4")


# --------------------------------------------------------------------------
# the completeness check itself

def test_a_reference_to_a_type_nothing_declares_is_reported():
    """Calibration for `_check_refs_resolve`.

    A completeness check that never fires is worth nothing, and this one is
    deliberately generous (it accepts a bare name matching any declared type's
    last segment, mirroring how the backends resolve). This pins that it still
    reports a name that resolves nowhere -- otherwise the fixes above and a
    deleted check look identical from the outside.
    """
    import zuspec.ir.core as ir
    from pssc.ast2ir import AstToIrContext, AstToIrTranslator

    ctx = AstToIrContext()
    holder = ir.DataTypeStruct(name="holder_s", super=None)
    holder.fields.append(ir.Field(
        name="f",
        datatype=ir.DataTypeRef(ref_name="no_such_type_s"),
        kind=ir.FieldKind.Field))
    ctx.add_type("holder_s", holder)

    AstToIrTranslator()._check_refs_resolve(ctx)

    assert any("no_such_type_s" in e for e in ctx.errors), ctx.errors


def test_a_template_parameter_name_is_not_reported_as_unresolved():
    """The other half: `struct packed_s <endianness_e E>` puts `E` in the IR as
    a DataTypeRef. It names a parameter, not a missing type."""
    import zuspec.ir.core as ir
    from pssc.ast2ir import AstToIrContext, AstToIrTranslator

    ctx = AstToIrContext()
    ctx.template_param_names.add("E")
    holder = ir.DataTypeStruct(name="holder_s", super=None)
    holder.fields.append(ir.Field(
        name="f",
        datatype=ir.DataTypeRef(ref_name="E"),
        kind=ir.FieldKind.Field))
    ctx.add_type("holder_s", holder)

    AstToIrTranslator()._check_refs_resolve(ctx)

    assert ctx.errors == []
