"""PC-1: source annotations (LRM 7.13) reach the IR, and ``pssc.annotations``
reads them.

ast2ir takes every value from pssparser (``pssparser.annotations``; ruling
R-H1) and records it on the node translated from the annotated declaration.
"""
import pytest

import zuspec.ir.core as ir
from pssc import annotations
from pssc.ast2ir import AstToIrTranslator
from pssc.frontend import Parser
from pssc.targets import comp_inherit

PKG = """
package hal_pkg {
    enum mode_e { M0, M1 = 5 };
    annotation hal_s { string name; string prefix = ""; mode_e m = mode_e::M1; }
    annotation note_s { string text = ""; }
    annotation extern_s { string c_name; }
}
"""

SRC = PKG + """
@hal_pkg::note_s {.text = "pkg enum"}
enum std_rv_e { OK, ERR };

@hal_pkg::hal_s {.name = "UART"}
component dev_c {
    @hal_pkg::note_s {.text = "field"} int count;

    @hal_pkg::hal_s {.name = "en"}
    function void enable() { }

    @hal_pkg::note_s {.text = "exec 1"}
    exec init_down { count = 0; }
    @hal_pkg::note_s {.text = "exec 2"}
    exec init_down { }

    @hal_pkg::note_s {.text = "standalone"};

    @hal_pkg::note_s {.text = "action"}
    action A { }

    @hal_pkg::note_s {.text = "struct"}
    struct s_s { int x; }

    @hal_pkg::note_s {.text = "nested enum"}
    enum inner_e { I0 };
}

@hal_pkg::note_s {.text = "extension"}
extend component dev_c { }

@hal_pkg::note_s {.text = "enum extension"}
extend enum std_rv_e { WARN };

@hal_pkg::note_s {.text = "import fn"}
import target function void plat_init();

component pss_top {
    dev_c dev;
}
"""


def _translate(src=SRC):
    p = Parser()
    p.parses([("m.pss", src)])
    ctx = AstToIrTranslator().translate(p.link(), files=p.file_map)
    assert not ctx.errors, ctx.errors
    return ctx


@pytest.fixture(scope="module")
def ctx():
    return _translate()


def _texts(node):
    return [a.values.get("text") for a in node.annotations
            if a.type_qname == "hal_pkg::note_s"]


def test_component_annotation_has_every_field(ctx):
    dev = ctx.type_map["dev_c"]
    hal = annotations.of(dev, "hal_pkg::hal_s")
    assert hal == {"name": "UART", "prefix": "",
                   "m": ir.AnnotationEnum(enum="hal_pkg::mode_e", item="M1",
                                          value=5)}
    a = next(a for a in dev.annotations if a.type_qname == "hal_pkg::hal_s")
    assert a.loc is not None and a.loc.file == "m.pss"


def test_extension_and_standalone_join_the_component(ctx):
    # Declaration first, then the body's standalone, then the extension's.
    assert _texts(ctx.type_map["dev_c"]) == ["standalone", "extension"]


def test_field_function_and_nested_declarations(ctx):
    dev = ctx.type_map["dev_c"]
    assert _texts(next(f for f in dev.fields if f.name == "count")) == ["field"]
    en = next(f for f in dev.functions if f.name == "enable")
    assert annotations.of(en, "hal_pkg::hal_s")["name"] == "en"
    assert _texts(ctx.type_map["dev_c::A"]) == ["action"]
    struct = next(t for n, t in ctx.type_map.items() if n.endswith("s_s"))
    assert _texts(struct) == ["struct"]
    inner = next(t for n, t in ctx.type_map.items() if n.endswith("inner_e"))
    assert _texts(inner) == ["nested enum"]


def test_merged_exec_blocks_keep_each_blocks_annotations(ctx):
    dev = ctx.type_map["dev_c"]
    execs = [f for f in dev.functions
             if (f.metadata or {}).get("exec_kind") == "init_down"]
    assert len(execs) == 1
    assert _texts(execs[0]) == ["exec 1", "exec 2"]


def test_enum_and_its_extension(ctx):
    e = ctx.type_map["std_rv_e"]
    assert _texts(e) == ["pkg enum", "enum extension"]
    assert e.loc is not None and e.loc.line > 0


def test_import_function(ctx):
    fn = next(f for f in ctx.import_functions if f.name == "plat_init")
    assert _texts(fn) == ["import fn"]


def test_unannotated_nodes_have_none(ctx):
    assert ctx.type_map["pss_top"].annotations == []
    assert next(f for f in ctx.type_map["pss_top"].fields
                if f.name == "dev").annotations == []


def test_unresolved_annotation_type_is_disregarded():
    ctx = _translate(PKG + """
    component pss_top {
        @nosuch_s {.x = 1} @hal_pkg::note_s {.text = "kept"}
        function void f() { }
    }
    """)
    f = next(f for f in ctx.type_map["pss_top"].functions if f.name == "f")
    assert [a.type_qname for a in f.annotations] == ["hal_pkg::note_s"]


def test_equality_ignores_annotations(ctx):
    dev = ctx.type_map["dev_c"]
    f = next(f for f in dev.fields if f.name == "count")
    import dataclasses as dc
    assert dc.replace(f, annotations=[]) == f


# -- inheritance (comp_inherit) ----------------------------------------------

INHERIT = PKG + """
component base_c {
    @hal_pkg::note_s {.text = "base h"}
    function void h() { }
    @hal_pkg::note_s {.text = "base g"}
    function void g() { }
}
component der_c : base_c {
    @hal_pkg::note_s {.text = "der g"}
    function void g() { super.g(); }
}
component pss_top {
    der_c d;
}
"""


def test_an_inherited_function_keeps_its_annotations_an_override_its_own():
    ctx = _translate(INHERIT)
    comp_inherit.complete(ctx)
    der = ctx.type_map["der_c"]
    by = {f.name: f for f in der.functions}
    assert _texts(by["h"]) == ["base h"]
    assert _texts(by["g"]) == ["der g"]
    supers = [f for f in der.functions if f.name.startswith("_pss_super_")]
    assert supers and _texts(supers[0]) == ["base g"]


# -- the reader --------------------------------------------------------------

def test_reader_requires_the_qualified_name(ctx):
    dev = ctx.type_map["dev_c"]
    with pytest.raises(ValueError, match="hal_pkg::hal_s"):
        annotations.of(dev, "hal_s")
    assert annotations.of(dev, "::hal_pkg::hal_s")["name"] == "UART"
    assert annotations.of(dev, "hal_pkg::extern_s") is None


def test_reader_refuses_a_repeated_type_and_all_of_reads_each():
    ctx = _translate(PKG + """
    component pss_top {
        @hal_pkg::note_s {.text = "a"} @hal_pkg::note_s {.text = "b"}
        function void f() { }
    }
    """)
    f = next(f for f in ctx.type_map["pss_top"].functions if f.name == "f")
    with pytest.raises(ValueError, match="all_of"):
        annotations.of(f, "hal_pkg::note_s")
    assert [v["text"] for v in annotations.all_of(f, "hal_pkg::note_s")] == ["a", "b"]


def test_an_exported_actions_entry_carries_the_actions_annotations():
    from pssc.targets.export_action import entry_point
    ctx = _translate(PKG + """
    component pss_top {
        @hal_pkg::note_s {.text = "action"}
        action A {
            @hal_pkg::note_s {.text = "body"}
            exec body { }
        }
    }
    """)
    ep = entry_point(ctx, "pss_top::A")
    assert _texts(ep.functions[0]) == ["action", "body"]
