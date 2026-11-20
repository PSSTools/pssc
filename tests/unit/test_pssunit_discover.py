"""Discovery: which action types are tests.

Synthetic contexts throughout -- discovery reads three things off a translated
context (``type_map``, ``parent_comp_names``, and each type's ``super`` /
``is_abstract``), so standing those up by hand is both faster than parsing and
more honest: it lets the hostile cases below exist at all. The rule is checked
against real IR in tests/pssunit/.
"""
from __future__ import annotations

import pytest

from pssc.testing.discover import (
    DiscoveryError, discover, derives_from, resolve_type_name)


class _Ref:
    def __init__(self, ref_name):
        self.ref_name = ref_name


class _Type:
    def __init__(self, super_name=None, is_abstract=False, loc=None):
        self.super = _Ref(super_name) if super_name else None
        self.is_abstract = is_abstract
        self.loc = loc


class _Ctx:
    def __init__(self, type_map, parent_comp_names=None):
        self.type_map = type_map
        self.parent_comp_names = parent_comp_names or {}


def _model(**actions):
    """A context with `pssunit_pkg::test_a` declared and *actions* under `c_c`."""
    type_map = {
        "pssunit_pkg::test_a": _Type(is_abstract=True),
        "c_c": _Type(),
    }
    parents = {}
    for name, dt in actions.items():
        qname = f"c_c::{name}"
        type_map[qname] = dt
        parents[qname] = "c_c"
    return _Ctx(type_map, parents)


# --- positive ---------------------------------------------------------------

def test_direct_subtype_is_a_test():
    ctx = _model(smoke_a=_Type("pssunit_pkg::test_a"))
    assert [it.qname for it in discover(ctx)] == ["c_c::smoke_a"]


def test_two_hop_subtype_is_a_test():
    ctx = _model(mid_a=_Type("pssunit_pkg::test_a", is_abstract=True),
                 leaf_a=_Type("c_c::mid_a"))
    assert "c_c::leaf_a" in [it.qname for it in discover(ctx)]


def test_unqualified_super_resolves_by_suffix():
    """`action smoke_a : test_a` under `import pssunit_pkg::*`."""
    ctx = _model(smoke_a=_Type("test_a"))
    assert [it.qname for it in discover(ctx)] == ["c_c::smoke_a"]


def test_item_carries_short_name_component_and_loc():
    loc = object()
    ctx = _model(smoke_a=_Type("pssunit_pkg::test_a", loc=loc))
    item, = discover(ctx)
    assert (item.name, item.comp, item.loc) == ("smoke_a", "c_c", loc)
    assert str(item) == "c_c::smoke_a"


def test_declaration_order_is_preserved():
    ctx = _model(b_a=_Type("pssunit_pkg::test_a"),
                 a_a=_Type("pssunit_pkg::test_a"))
    assert [it.name for it in discover(ctx)] == ["b_a", "a_a"]


# --- negative ---------------------------------------------------------------

def test_abstract_subtype_is_not_runnable():
    ctx = _model(base_a=_Type("pssunit_pkg::test_a", is_abstract=True))
    assert discover(ctx) == []


def test_action_not_deriving_from_test_a_is_not_a_test():
    ctx = _model(plain_a=_Type(), other_a=_Type("c_c::plain_a"))
    assert discover(ctx) == []


def test_the_base_type_itself_is_not_a_test():
    """`pssunit_pkg::test_a` is abstract AND not in parent_comp_names."""
    ctx = _model()
    assert discover(ctx) == []


def test_non_action_types_are_not_considered():
    """A component named like a test still is not one.

    `parent_comp_names` is the action test; without it, a component whose
    `super` happened to reach `test_a` would be collected and then fail to run.
    """
    ctx = _model()
    ctx.type_map["looks_like_a"] = _Type("pssunit_pkg::test_a")
    assert discover(ctx) == []


# --- hostile ----------------------------------------------------------------

def test_a_super_cycle_terminates():
    ctx = _model(a_a=_Type("c_c::b_a"), b_a=_Type("c_c::a_a"))
    assert discover(ctx) == []          # and, crucially, returns at all


def test_unresolvable_base_type_raises_and_names_it():
    """The most important test here.

    Treating an unresolvable base type as "not a test" would delete the test
    from the run and still print a green summary -- the one failure mode a test
    framework must never have.
    """
    ctx = _model(smoke_a=_Type("pssunit_pkg::tset_a"))
    with pytest.raises(DiscoveryError) as e:
        discover(ctx)
    assert "pssunit_pkg::tset_a" in str(e.value)


def test_ambiguous_base_type_raises_rather_than_guessing():
    ctx = _model(smoke_a=_Type("test_a"))
    ctx.type_map["other_pkg::test_a"] = _Type()
    with pytest.raises(DiscoveryError) as e:
        discover(ctx)
    assert "ambiguous" in str(e.value)
    assert "pssunit_pkg::test_a" in str(e.value)


def test_resolve_type_name_prefers_an_exact_key():
    ctx = _model()
    ctx.type_map["other::test_a"] = _Type()
    assert resolve_type_name(ctx, "pssunit_pkg::test_a") == "pssunit_pkg::test_a"


def test_derives_from_is_false_for_a_root_type():
    ctx = _model()
    assert derives_from(ctx, _Type()) is False
