"""PC-14: a derived target owns its inherited options, and a target states
the pssc releases it needs.

`fixed_options` sets inherited options; `accepted_options` lists the ones the
user may still set; every other inherited option the user sets is refused, in
one message. `requires_pssc` is checked when the target registers.
"""
from __future__ import annotations

import argparse

import pytest

from pssc.driver import CompileError
from pssc.targets import TargetError, register
from pssc.targets import call_legality as cl
from pssc.targets.c_progseq_tgt import CProgSeqTarget


@pytest.fixture
def registry():
    """Restore both registries, so a test target does not outlive its test."""
    import pssc.targets as t
    saved_targets = dict(t._REGISTRY)
    saved_legality = {k: dict(v) for k, v in cl._EXTENSIONS.items()}
    yield
    t._REGISTRY.clear()
    t._REGISTRY.update(saved_targets)
    cl._EXTENSIONS.clear()
    cl._EXTENSIONS.update(saved_legality)


def _derived(**attrs):
    attrs.setdefault("name", "op-model-acme")
    attrs.setdefault("derives_from", "op-model-c")
    attrs.setdefault("description", "derived test target")
    return type("_Derived", (CProgSeqTarget,), attrs)()


def _ns(**kw):
    return argparse.Namespace(**kw)


# -- resolve_options ---------------------------------------------------------

def test_no_policy_passes_the_command_line_through(registry):
    tgt = _derived()
    register(tgt)
    opts = _ns(c_reg_style="bitfields", c_link_style="direct")
    assert tgt.resolve_options(opts) is opts


def test_fixed_options_are_applied_on_a_copy(registry):
    tgt = _derived(fixed_options={"c_reg_style": "bitfields"},
                   accepted_options=("progseq_root",))
    register(tgt)
    opts = _ns(progseq_root="pkg::top_c")
    out = tgt.resolve_options(opts)
    assert out.c_reg_style == "bitfields"
    assert out.progseq_root == "pkg::top_c"
    assert not hasattr(opts, "c_reg_style")


def test_a_fixed_option_given_its_fixed_value_is_accepted(registry):
    tgt = _derived(fixed_options={"c_reg_style": "bitfields"})
    register(tgt)
    out = tgt.resolve_options(_ns(c_reg_style="bitfields"))
    assert out.c_reg_style == "bitfields"


def test_every_refused_option_is_named_in_one_message(registry):
    tgt = _derived(fixed_options={"c_reg_style": "bitfields"},
                   accepted_options=("progseq_root",))
    register(tgt)
    with pytest.raises(ValueError) as e:
        tgt.resolve_options(_ns(progseq_root="t", c_reg_style="shifts",
                                c_prefix="x", c_addr_bits=32))
    msg = str(e.value)
    assert "'op-model-acme' does not take" in msg
    for opt in ("--reg-style", "--prefix", "--addr-bits"):
        assert opt in msg
    assert "--root" not in msg
    assert "c_reg_style='bitfields'" in msg


def test_an_option_at_its_default_is_not_refused(registry):
    tgt = _derived(accepted_options=())
    register(tgt)
    defaults = {d: v for d, (_, v) in tgt.inherited_options().items()}
    assert tgt.resolve_options(_ns(**defaults)) is not None


def test_accepting_is_opt_in(registry):
    """An inherited option nobody named -- as one the ancestor adds later
    would be -- is refused, not passed through."""
    tgt = _derived(accepted_options=("progseq_root",))
    register(tgt)
    assert "c_emit_stubs" in tgt.inherited_options()
    with pytest.raises(ValueError, match="--emit-stubs"):
        tgt.resolve_options(_ns(c_emit_stubs=True))


def test_naming_an_option_the_ancestor_lacks_is_an_error(registry):
    tgt = _derived(fixed_options={"c_no_such": 1})
    register(tgt)
    with pytest.raises(ValueError, match="c_no_such.*'op-model-c' does not"):
        tgt.resolve_options(_ns())


def test_the_driver_refuses_before_parsing(registry, tmp_path):
    from pssc.testing import compile_op_model
    register(_derived(accepted_options=("progseq_root",)))
    with pytest.raises(CompileError, match="does not take --prefix"):
        compile_op_model("op-model-acme", output_dir=str(tmp_path),
                         c_prefix="zz")
    assert not any(tmp_path.iterdir())


def test_the_fixed_value_reaches_the_output(registry, tmp_path):
    from pssc.testing import compile_op_model
    register(_derived(fixed_options={"c_prefix": "acme"},
                      accepted_options=("progseq_root",)))
    with compile_op_model("op-model-acme",
                          output_dir=str(tmp_path)) as out:
        assert any(n.startswith("acme") for n in out.names)


# -- requires_pssc -----------------------------------------------------------

@pytest.mark.parametrize("spec", ["", ">=0.1", ">=0.2, <99", "!=0.0.1", ">0"])
def test_requires_pssc_met(registry, spec):
    register(_derived(requires_pssc=spec))


@pytest.mark.parametrize("spec", [">=99", "<0.1", ">=0.1, <0.2"])
def test_requires_pssc_unmet_is_refused_at_registration(registry, spec):
    import pssc.targets as t
    with pytest.raises(TargetError, match="requires pssc"):
        register(_derived(requires_pssc=spec))
    assert "op-model-acme" not in t._REGISTRY


def test_requires_pssc_malformed(registry):
    with pytest.raises(TargetError, match="'~0.3' is not an operator"):
        register(_derived(requires_pssc="~0.3"))
