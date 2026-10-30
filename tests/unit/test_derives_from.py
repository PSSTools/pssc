"""P6b.T3: `derives_from` -- one name, three inheritances.

Three things follow the ancestor: call legality, CLI options and `target_cfg`.
They are three separate mechanisms, so they are tested separately (design I15).
A single "the derived target generates the same thing" integration test would
pass with two of the three silently not working: legality only shows up on a
model using a Tier-2 call, options only on a command line that sets one, and
`target_cfg` only on a model with a `compile if`.
"""
from __future__ import annotations

import argparse

import pytest

from pssc.targets import call_legality as cl
from pssc.targets import register
from pssc.targets.c_progseq_tgt import CProgSeqTarget
from pssc.targets.op_model import OpModelTarget


@pytest.fixture
def registry():
    """Restore both registries, so a test target does not outlive its test."""
    import pssc.targets as t
    saved_targets = dict(t._REGISTRY)
    saved_legality = {k: dict(v) for k, v in cl._EXTENSIONS.items()}
    saved_subsets = dict(cl._SUBSETS)
    yield
    t._REGISTRY.clear()
    t._REGISTRY.update(saved_targets)
    cl._EXTENSIONS.clear()
    cl._EXTENSIONS.update(saved_legality)
    cl._SUBSETS.clear()
    cl._SUBSETS.update(saved_subsets)


def _derived(**attrs):
    """A target deriving from `op-model-c` with nothing of its own."""
    attrs.setdefault("name", "op-model-acme")
    attrs.setdefault("derives_from", "op-model-c")
    attrs.setdefault("description", "derived test target")
    return type("_Derived", (CProgSeqTarget,), attrs)()


# -- 1. call legality --------------------------------------------------------

def test_legality_inherited(registry):
    tgt = _derived()
    register(tgt)
    assert cl.renderable("op-model-acme") == cl.renderable("op-model-c")


def test_legality_subset_target(registry):
    """`legality_subset` lets a derived target narrow COMMON, with a reason."""
    narrow = [cl.Entry("message", cl.Disposition.UTILITY, cl.BOTH,
                       unsupported="no console")]
    with pytest.raises(cl.LegalityError):
        _derived(legality_entries=narrow)
    register(_derived(legality_entries=narrow, legality_subset=True))
    assert cl.subset_of_common("op-model-acme") == frozenset({"message"})
    r = cl.classify("message", context=cl.Ctx.TARGET, target="op-model-acme")
    assert r.outcome is cl.Outcome.UNSUPPORTED_HERE and "no console" in r.message


def test_legality_extended_without_restating_the_ancestors(registry):
    """The shape the flag exists for: the ancestor's set PLUS one entry."""
    tgt = _derived(legality_entries=[
        cl.Entry("acme_trace", cl.Disposition.UTILITY, cl.BOTH, lrm="21.1.2")])
    register(tgt)
    mine = cl.renderable("op-model-acme")
    assert "acme_trace" in mine
    assert cl.renderable("op-model-c") <= mine
    # ...and the ancestor did not acquire the derived target's entry.
    assert "acme_trace" not in cl.renderable("op-model-c")


def test_legality_is_a_snapshot_not_a_link(registry):
    """The ancestor gaining an entry later must not make a call legal in a
    backend that never learned to render it."""
    tgt = _derived()
    register(tgt)
    cl.register_extension("op-model-c", [
        cl.Entry("added_later", cl.Disposition.UTILITY, cl.BOTH, lrm="21.1.2"),
    ], inherit="op-model-c", replace=True)
    assert "added_later" in cl.renderable("op-model-c")
    assert "added_later" not in cl.renderable("op-model-acme")


# -- 2. CLI options ----------------------------------------------------------

def _options(tgt) -> set:
    parser = argparse.ArgumentParser()
    tgt.add_args(parser)
    return {s for a in parser._actions for s in a.option_strings}


def test_options_inherited(registry):
    """A command line written for the ancestor works against the derived name.

    Both the C target's own options (`--link-style`) and the family's shared
    ones (`--root`), which is the half that a naive `super()` delegation gets
    wrong: the derived target must not add `--root` a second time.
    """
    tgt = _derived()
    register(tgt)
    inherited, ancestor = _options(tgt), _options(CProgSeqTarget())
    assert inherited == ancestor
    assert {"--root", "--link-style", "--style"} <= inherited


def test_options_of_the_derived_target_are_added_too(registry):
    class _WithOption(CProgSeqTarget):
        name = "op-model-acme"
        derives_from = "op-model-c"

        def add_args(self, parser):
            super().add_args(parser)
            parser.add_argument("--acme-note", dest="acme_note", default="")

    tgt = _WithOption()
    register(tgt)
    opts = _options(tgt)
    assert "--acme-note" in opts and "--root" in opts


def test_options_are_not_added_twice(registry):
    """An argparse parser raises on a repeated option, so a double-add is a
    hard failure rather than a duplicated help line -- and the CLI's dedup
    proxy would hide it. Asserted on a raw parser for that reason."""
    tgt = _derived()
    register(tgt)
    parser = argparse.ArgumentParser()
    tgt.add_args(parser)            # must not raise


# -- 3. target_cfg -----------------------------------------------------------

def test_target_cfg_inherited(registry):
    tgt = _derived()
    register(tgt)
    assert tgt.resolved_target_cfg() == CProgSeqTarget.target_cfg


def test_target_cfg_override_wins(registry):
    """Per FLAG, not wholesale: a derived target that adds a scheduler says so
    in one line and keeps everything else its ancestor established."""
    tgt = _derived(target_cfg={"HAVE_EVENT_WAIT": True})
    register(tgt)
    cfg = tgt.resolved_target_cfg()
    assert cfg["HAVE_EVENT_WAIT"] is True
    assert cfg["HAVE_RUNTIME_SOLVER"] is False       # the ancestor's, kept


def test_target_cfg_reaches_the_prelude(registry):
    """The merged set is what the MODEL sees -- `resolved_target_cfg` is not a
    display convenience."""
    tgt = _derived(target_cfg={"HAVE_EVENT_WAIT": True})
    register(tgt)
    text = tgt.prelude(argparse.Namespace())[0][1]
    assert "HAVE_EVENT_WAIT" in text and "true" in text


# -- 4. styles ---------------------------------------------------------------

def test_styles_inherited(registry):
    """The `pssc.styles` group is keyed `"<target>:<name>"`, so without this a
    derived target has NO styles -- not even `default`, which every C
    generation resolves. Found by the byte-identical test below failing."""
    tgt = _derived()
    register(tgt)
    assert tgt.style_targets() == ["op-model-acme", "op-model-c"]
    assert "default" in tgt.available_styles()
    assert type(tgt.resolve_style("default")) is type(
        CProgSeqTarget().style_for(argparse.Namespace()))


def test_a_style_of_its_own_beats_the_inherited_one(registry):
    from pssc.targets.c.style import CStylePolicy
    from pssc.targets import style as st

    class _Mine(CStylePolicy):
        name = "default"
        target = "op-model-acme"

    st.register(_Mine())
    try:
        tgt = _derived()
        register(tgt)
        assert isinstance(tgt.resolve_style("default"), _Mine)
    finally:
        st._REGISTRY.pop(("op-model-acme", "default"), None)


def test_an_unknown_style_names_every_registry_it_searched(registry):
    from pssc.targets.style import StyleError

    tgt = _derived()
    register(tgt)
    with pytest.raises(StyleError, match="op-model-acme, op-model-c"):
        tgt.resolve_style("no-such-style")


# -- errors ------------------------------------------------------------------

def test_unknown_ancestor_is_error(registry):
    """Named at construction, where the message can list what does exist."""
    with pytest.raises(cl.LegalityError, match="op-model-nope"):
        _derived(derives_from="op-model-nope")


def test_unknown_ancestor_target_is_error(registry):
    """The legality registry and the target registry are different tables; a
    name in one and not the other is still a broken derivation."""
    cl.register_extension("op-model-ghost", [], replace=True)
    tgt = _derived(derives_from="op-model-ghost")
    with pytest.raises(ValueError, match="not registered"):
        tgt.resolved_target_cfg()


# -- the whole point ---------------------------------------------------------

def test_an_empty_derived_target_behaves_like_its_ancestor(registry, tmp_path):
    """P6b.T3's accept criterion: same input, same bytes, different name."""
    from pssc.testing import assert_dirs_match, compile_op_model

    tgt = _derived()
    register(tgt)
    with compile_op_model("op-model-acme",
                          output_dir=str(tmp_path / "derived")) as derived, \
            compile_op_model("op-model-c",
                             output_dir=str(tmp_path / "base")) as base:
        assert derived.names == base.names
        assert_dirs_match(derived.out_dir, base.out_dir)
