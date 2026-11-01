"""The shared op-model layer (P2.T1), proven equivalent before anything uses it.

`OpModel` replaces three per-backend walks. The only way to know it is a
replacement and not a fourth answer is to compare it, element for element,
against what each backend computes today -- which is what most of this file
does, and which is why it lands before the ports rather than with them.

One deliberate difference is recorded rather than reconciled: the SV and C
walks disagreed on SIBLING order, and `OpModel` picks SV's. See
`test_siblings_come_out_in_declaration_order`.

Since P2.T3-T5 the three backends CONSUME this layer, so the equivalence
assertions that justified it have become identity assertions -- `regular_nodes`
and `post_order` are now views onto the model. That is the intended end state
and the tests are kept in that form: they are what fails if a backend ever
grows a private walk again.
"""
import argparse
import tempfile

import pytest

from pssc import driver, targets
from pssc.targets import op_model as om
from pssc.targets.c.lower_progseq import post_order, regular_nodes
from pssc.targets.progseq_model import CompKind, walk_tree

from .op_model import op_model_sources


@pytest.fixture(scope="module")
def ctx():
    tgt = targets.get("op-model-sv")
    return driver.translate(op_model_sources(),
                            prelude=tgt.prelude(argparse.Namespace()))


@pytest.fixture(scope="module")
def model(ctx):
    return om.elaborate(ctx, ctx.type_map["wb_dma_c"], tempfile.mkdtemp())


@pytest.fixture(scope="module")
def tree(ctx):
    return walk_tree(ctx.type_map["wb_dma_c"], om.resolver(ctx))


# --- equivalence with what the backends compute today -----------------------

def test_all_targets_share_one_walk(model):
    """The C backend's `regular_nodes`/`post_order` ARE the model's orders.

    They kept their names because the call sites read better for them; what
    they must not be is a second walk. This is the assertion that fails if one
    grows back.
    """
    assert [n.name for n in post_order(model)] == \
        [n.name for n in model.components]
    assert [n.name for n in regular_nodes(model)] == \
        [n.name for n in model.components_root_first]


def test_children_come_before_parents(model):
    """The invariant every backend depends on: a parent embeds its children by
    value and its `_init` calls theirs, so the child has to come first."""
    names = [n.name for n in model.components]
    assert names.index("wb_dma_ch_c") < names.index("wb_dma_c")


def test_the_root_is_first_in_the_other_order(model):
    """Symbol-prefix assignment gives index 0 the caller's `--prefix`, so
    root-first is not merely a reversal -- it has to be the root."""
    assert model.components_root_first[0].dtype is model.root


def test_value_structs_are_what_the_register_model_emits(model):
    """The API-type pass must skip exactly this set; a mismatch means one type
    declared twice, incompatibly. Both backends are handed `model.value_structs`
    now, so the two cannot disagree -- this pins the CONTENT."""
    names = [s.name.split("::")[-1] for s in model.value_structs]
    assert "wb_dma_csr_s" in names
    assert "wb_dma_gcsr_s" in names, \
        "the root's own register values are missing: the collection is not " \
        "spanning the whole tree"


def test_value_structs_are_deduplicated_and_first_use_ordered(model):
    names = [s.name for s in model.value_structs]
    assert len(names) == len(set(names)), "a value struct is declared twice"
    assert names[0].endswith("wb_dma_csr_s"), \
        "first-use order changed; the register model's output depends on it"


def test_reg_groups_are_collected_across_the_whole_tree(model):
    """Not just the root's. A per-channel bank is reached through the channel
    component, and collecting only the root's left every generated channel
    class naming a type nothing declared."""
    names = {g.name.split("::")[-1] for g in model.reg_groups}
    assert "wb_dma_regs_c" in names
    assert "wb_dma_ch_regs_c" in names


def test_count_matches_the_backends_log_line(model, tree):
    assert om.count(tree, CompKind.REGULAR) == 2
    assert om.count(tree, CompKind.REG_GROUP) >= 2


# --- the sibling-order decision --------------------------------------------

_SIBLINGS = """
component alpha_c { target function int a() { return 1; } }
component beta_c  { target function int b() { return 2; } }
component top_c   {
    alpha_c al;
    beta_c  be;
    target function int t() { return 3; }
}
"""


@pytest.fixture(scope="module")
def sibling_model(tmp_path_factory):
    src = tmp_path_factory.mktemp("siblings") / "m.pss"
    src.write_text(_SIBLINGS)
    c = driver.translate([str(src)])
    return om.elaborate(c, c.type_map["top_c"], tempfile.mkdtemp()), c


def test_siblings_come_out_in_declaration_order(sibling_model):
    """THE decision this layer exists to make.

    SV walked siblings in declaration order and C in reverse -- its
    "post-order" was `reversed(pre_order)`, which is only the same thing when
    no component has two children. Both outputs compiled, so nothing failed and
    nothing noticed. `OpModel` adopts declaration order, because it is the one
    of the two that corresponds to something (what the model says).

    This changes C's output for a model with sibling component types. No such
    model is under snapshot, and none was before -- which is exactly why the
    divergence survived.
    """
    model, _ = sibling_model
    assert [n.name for n in model.components] == ["alpha_c", "beta_c", "top_c"]


def test_the_c_backend_now_agrees(sibling_model):
    """The divergence, closed. This asserted the disagreement until P2.T4; it
    asserts the agreement now, which is the whole point of the phase."""
    model, ctx = sibling_model
    assert [n.name for n in post_order(model)] == \
        [n.name for n in model.components] == ["alpha_c", "beta_c", "top_c"]


def test_children_precede_parents_whatever_the_sibling_order(sibling_model):
    """The invariant that actually has to hold: a parent embeds its children by
    value, so the child's type must be complete where the parent is declared."""
    model, _ = sibling_model
    names = [n.name for n in model.components]
    assert names.index("alpha_c") < names.index("top_c")
    assert names.index("beta_c") < names.index("top_c")


# --- constructor names ------------------------------------------------------

def test_ctor_names_are_carried_on_the_model(ctx):
    """`--ctor-name` belongs to the compile that asked for it. It used to be a
    process-global whose value outlived that compile (P1.T3)."""
    m = om.elaborate(ctx, ctx.type_map["wb_dma_c"], ".",
                     ctor_names=frozenset({"ctor"}))
    assert m.ctor_names == frozenset({"ctor"})
    # With only `ctor` recognised, this model's `initialize` is an ordinary
    # solve function and no component has a constructor.
    assert all(m.ctor(n) is None for n in m.components)


def test_the_default_ctor_names_find_this_models_constructor(model):
    ch = next(n for n in model.components if n.name.endswith("wb_dma_ch_c"))
    assert model.ctor(ch) is not None
    assert model.ctor(ch).name == "initialize"


def test_classification_uses_the_models_own_names(ctx):
    """The point of carrying them: two models elaborated in one process get
    their own answers, not the last one set."""
    a = om.elaborate(ctx, ctx.type_map["wb_dma_c"], ".")
    b = om.elaborate(ctx, ctx.type_map["wb_dma_c"], ".",
                     ctor_names=frozenset({"ctor"}))
    ch_a = next(n for n in a.components if n.name.endswith("wb_dma_ch_c"))
    ch_b = next(n for n in b.components if n.name.endswith("wb_dma_ch_c"))
    assert a.ctor(ch_a) is not None
    assert b.ctor(ch_b) is None


# --- accessors --------------------------------------------------------------

def test_operations_are_the_exported_ones(model):
    ch = next(n for n in model.components if n.name.endswith("wb_dma_ch_c"))
    names = [fn.name for fn in model.operations(ch)]
    assert "transfer_single" in names
    assert "initialize" not in names, "the constructor is not an operation"
    assert model.total_operations() > 10


def test_channels_and_sub_components(model):
    ch = next(n for n in model.components if n.name.endswith("wb_dma_ch_c"))
    root = next(n for n in model.components if n.dtype is model.root)
    assert {f.name for f in model.channels(ch)} == {"inflight", "wake"}
    assert [s.name for s in model.sub_components(root)] == ["ch"]


def test_offset_folding_delegates(model):
    """Not reimplemented -- the fold lives in `progseq_model` and is the same
    arithmetic every backend already used."""
    group = next(g for g in model.reg_groups
                 if g.name.endswith("wb_dma_regs_c"))
    base, stride = model.base_stride_of(group, "bank")
    assert (base, stride) == (0x20, 0x20)


def test_an_unfoldable_offset_still_raises_with_the_instance_named(model):
    from pssc.targets.progseq_model import OffsetFoldError
    group = next(g for g in model.reg_groups
                 if g.name.endswith("wb_dma_regs_c"))
    with pytest.raises((OffsetFoldError, Exception)) as exc:
        model.base_stride_of(group, "no_such_instance")
    assert "no_such_instance" in str(exc.value)


def test_imports_are_gathered(model):
    """This model declares none; the mapping still exists, so a backend does
    not have to know whether to expect one."""
    assert model.imports == {}


def test_the_model_is_frozen(model):
    """Handed to emitters in sequence: one must not be able to change what the
    next sees."""
    import dataclasses as dc
    with pytest.raises(dc.FrozenInstanceError):
        model.root = None


# --- the API (hierarchical, by visibility: op_api.py) -----------------------

_HIER = """
component leaf_c {
    target function int peek() { return 1; }
    private target function int hid() { return 2; }
}
component quiet_c {
    private target function int nothing() { return 0; }
}
component mid_c {
    leaf_c  lf;
    quiet_c q;
}
component base_c {
    target function int b() { return 2; }
}
component dma_c : base_c {
    target function int go(int n) { return n; }
protected:
    target function int helper() { return 0; }
}
component soc_c {
    dma_c   dma0, dma1;
    leaf_c  pit[4];
    mid_c   m;
    quiet_c q;
    private leaf_c hidden;
    target function int top() { return 3; }
    export target function top;
}
"""


@pytest.fixture(scope="module")
def hier_model(tmp_path_factory):
    src = tmp_path_factory.mktemp("hier") / "m.pss"
    src.write_text(_HIER)
    c = driver.translate([str(src)])
    return om.elaborate(c, c.type_map["soc_c"], tempfile.mkdtemp()), c


def _api(m, c, name):
    return m.api(c.type_map[name])


def test_each_type_carries_its_public_functions(hier_model):
    """A type's API is its public target functions, its bases' included;
    private and protected ones (prefix or group) are not on it. `export` is
    accepted and changes nothing."""
    m, c = hier_model
    assert [f.name for f in _api(m, c, "leaf_c").exports] == ["peek"]
    assert sorted(f.name for f in _api(m, c, "dma_c").exports) == ["b", "go"]
    assert [f.name for f in _api(m, c, "soc_c").exports] == ["top"]
    assert [f.name for f in m.exports] == ["top"]


def test_only_public_children_that_lead_to_an_api_get_an_accessor(hier_model):
    """`mid_c` has no public function but holds `lf`, which does: it has an
    API of accessors alone. `quiet_c` has only a private function and leads
    nowhere; `hidden` is a private instance."""
    m, c = hier_model
    assert _api(m, c, "quiet_c") is None
    mid = _api(m, c, "mid_c")
    assert mid.exports == () and [s.name for s in mid.children] == ["lf"]
    soc = _api(m, c, "soc_c")
    assert [(s.name, s.size) for s in soc.children] == [
        ("dma0", None), ("dma1", None), ("pit", 4), ("m", None)]
    assert soc.children[0].dtype is c.type_map["dma_c"]


def test_apis_come_children_first(hier_model):
    m, _ = hier_model
    names = [a.comp.name for a in m.apis]
    assert names[-1].endswith("soc_c")
    assert names.index(next(n for n in names if n.endswith("leaf_c"))) < \
        names.index(next(n for n in names if n.endswith("mid_c")))
