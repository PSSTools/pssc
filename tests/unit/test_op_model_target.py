"""`OpModelTarget` (P2.T2) -- the contract for the operation-model family.

The class exists so a backend cannot forget the parts that must not be
forgotten. Two of the three checks here are regressions of real defects:

* the legality gate ran for SystemVerilog only, so the two backends with the
  NARROWEST lowering emitted whatever they met (P1.T1);
* a generated API with zero operations was written, exited 0, and contained
  interface classes with nothing in them.

Neither is now a backend's responsibility, which is the point: `run` is
`elaborate -> check -> emit`, and a subclass supplies only `emit`.
"""
import argparse
import tempfile

import pytest

from pssc import driver
from pssc.driver import CompileError
from pssc.targets.op_model import OpModel, OpModelTarget

_MODEL = """
import addr_reg_pkg::*;
component probe_c {
    int scale = 3;
    target function int scaled(int x) { return scale * x; }
}
"""

_NO_OPS = """
component empty_c {
    int x;
}
"""

_ILLEGAL = """
component gate_c {
    target function int roll() { int v; v = urandom(); return v; }
}
"""


class _Recording(OpModelTarget):
    """The whole of a minimal backend. If this needs to grow, the base class
    is not carrying enough."""

    name = "op-model-test"
    description = "test backend"
    legality_target = "op-model-c"
    language = "test"

    def __init__(self):
        self.seen = None

    def emit(self, model, opts):
        self.seen = model
        path = model.out_dir / "out.txt"
        path.write_text("\n".join(n.name for n in model.components))
        return [path]


def _compile(target, src_text, tmp_path, **kw):
    src = tmp_path / "m.pss"
    src.write_text(src_text)
    ns = argparse.Namespace(output_dir=str(tmp_path), **kw)
    ctx = driver.translate([str(src)], prelude=target.prelude(ns))
    return target.run(ctx, ns)


# --- the shape --------------------------------------------------------------

def test_emit_is_abstract():
    """A subclass that forgets `emit` must fail at construction, not produce a
    target that registers and then does nothing."""
    class Incomplete(OpModelTarget):
        name = "incomplete"

    with pytest.raises(TypeError):
        Incomplete()


def test_a_minimal_backend_is_small(tmp_path):
    """The acceptance criterion for this task: a working target in ~15 lines of
    subclass. Everything else -- --root, --ctor-name, the walk, the gate -- is
    inherited."""
    tgt = _Recording()
    out = _compile(tgt, _MODEL, tmp_path, progseq_root="probe_c")
    assert [p.name for p in out] == ["out.txt"]
    assert out[0].read_text() == "probe_c"
    assert isinstance(tgt.seen, OpModel)


def test_the_model_reaches_emit_elaborated(tmp_path):
    tgt = _Recording()
    _compile(tgt, _MODEL, tmp_path, progseq_root="probe_c")
    model = tgt.seen
    assert model.components[0].name == "probe_c"
    assert [fn.name for fn in model.operations(model.components[0])] == ["scaled"]
    assert model.out_dir == tmp_path


# --- --root -----------------------------------------------------------------

def test_a_missing_root_names_the_target(tmp_path):
    with pytest.raises(ValueError, match="op-model-test requires --root"):
        _compile(_Recording(), _MODEL, tmp_path, progseq_root=None)


def test_an_unknown_root_lists_the_candidates(tmp_path):
    """"unknown component" without a list is a question, not a diagnostic."""
    with pytest.raises(ValueError) as exc:
        _compile(_Recording(), _MODEL, tmp_path, progseq_root="nope_c")
    assert "probe_c" in str(exc.value)


def test_a_qualified_root_resolves(tmp_path):
    src = "package p { } component q_c { target function int f() { return 1; } }"
    out = _compile(_Recording(), src, tmp_path, progseq_root="q_c")
    assert out


# --- check ------------------------------------------------------------------

def test_check_gates_illegal_calls_before_emit(tmp_path):
    """The gate runs first, so a model the backend cannot lower produces a
    diagnostic and NO FILES -- a part-written artifact is worse than none,
    because a later incremental build treats it as up to date."""
    with pytest.raises(CompileError) as exc:
        _compile(_Recording(), _ILLEGAL, tmp_path, progseq_root="gate_c")
    assert "urandom" in "\n".join(exc.value.errors)
    assert not (tmp_path / "out.txt").exists()


def test_check_reports_every_offending_call_not_the_first(tmp_path):
    src = """
component gate_c {
    target function int roll() {
        int a; int b;
        a = urandom();
        b = urandom_range(0, 3);
        return a + b;
    }
}
"""
    with pytest.raises(CompileError) as exc:
        _compile(_Recording(), src, tmp_path, progseq_root="gate_c")
    joined = "\n".join(exc.value.errors)
    assert "urandom" in joined and "urandom_range" in joined
    assert len(exc.value.errors) == 2


def test_the_empty_api_assertion_fires(tmp_path):
    """The shape every front-end defect in this generator's history took: the
    model translated, the file was written, the run exited 0, and it held
    interfaces with nothing in them."""
    with pytest.raises(ValueError, match="zero operations"):
        _compile(_Recording(), _NO_OPS, tmp_path, progseq_root="empty_c")
    assert not (tmp_path / "out.txt").exists()


def test_legality_target_selects_whose_rules_apply(tmp_path):
    """A derived style reuses a built-in's Tier 2 rather than restating it.
    `print` is legal for the C target and this one borrows its set."""
    src = """import std_pkg::*;
component p_c {
    target function void say() { print("hi"); }
}
"""
    assert _compile(_Recording(), src, tmp_path, progseq_root="p_c")


# --- --ctor-name ------------------------------------------------------------

_CTOR = """
import addr_reg_pkg::*;
component c_c {
    int x;
    solve function void \\init (addr_handle_t base) { x = 1; }
    target function int f() { return x; }
}
"""


def test_ctor_name_reaches_the_model(tmp_path):
    tgt = _Recording()
    _compile(tgt, _CTOR, tmp_path, progseq_root="c_c")
    comp = tgt.seen.components[0]
    assert tgt.seen.ctor(comp) is not None, "`init` is a default ctor spelling"

    tgt2 = _Recording()
    _compile(tgt2, _CTOR, tmp_path, progseq_root="c_c",
             progseq_ctor_name="ctor")
    assert tgt2.seen.ctor_names == frozenset({"ctor"})
    assert tgt2.seen.ctor(tgt2.seen.components[0]) is None


def test_the_setting_does_not_outlive_the_run(tmp_path):
    """P1.T3's defect, at the level that now owns the setting."""
    from pssc.targets.progseq_model import DEFAULT_CTOR_NAMES, current_ctor_names
    _compile(_Recording(), _CTOR, tmp_path, progseq_root="c_c",
             progseq_ctor_name="ctor")
    assert current_ctor_names() == DEFAULT_CTOR_NAMES


# --- shared options ---------------------------------------------------------

def test_the_family_options_are_declared_once():
    """`--root`, `--ctor-name` and `--no-core-copy` come from the base class,
    so a new target in the family gets them by existing."""
    parser = argparse.ArgumentParser()
    _Recording().add_args(parser)
    ns = parser.parse_args(["--root", "x_c", "--ctor-name", "build",
                            "--no-core-copy"])
    assert ns.progseq_root == "x_c"
    assert ns.progseq_ctor_name == "build"
    assert ns.progseq_core_copy is False
