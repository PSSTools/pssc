"""Which `solve function` is the constructor (design §3D).

The generator folds the root's constructor into the generated class: its
arguments become the factory's arguments and its first argument is the base
address the register model is built against. Recognition was hard-coded to the
name `ctor`.

A model whose constructor is called `init` -- the PSS coding guidelines' name,
written `\\init` because `init` is reserved -- had it classified as an ordinary
export function instead. The generated class then read

    m_regs = new(this, base);

with no `base` in scope: output that looks right and does not compile. That is
the failure mode this file exists to prevent, so the tests assert the *kind*,
not that generation succeeded.
"""
import pytest

from pssc import Parser
from pssc.ast2ir import AstToIrTranslator
from pssc.targets.progseq_model import (DEFAULT_CTOR_NAMES, FuncKind,
                                        ctor_names_scope, func_kind,
                                        current_ctor_names, set_ctor_name)


def translate(pss_code: str):
    """Translate ``pss_code`` and return its type map."""
    parser = Parser()
    parser.parses([("test.pss", pss_code)])
    return AstToIrTranslator(debug=False).translate(parser.link()).type_map


def _kinds(comp):
    return {fn.name: func_kind(fn) for fn in comp.functions}


@pytest.fixture(autouse=True)
def _restore_default_ctor_names():
    """Belt and braces: `ctor_names_scope` restores on exit, so nothing here
    should need this. It stays because a test that DOES leak would otherwise
    break a later test in a different file, which is a miserable thing to
    debug."""
    yield
    assert current_ctor_names() == DEFAULT_CTOR_NAMES, (
        "a test left the constructor-name set narrowed")


_MODEL = """
import addr_reg_pkg::*;
component c_c {
    int x;
    solve function void %s(addr_handle_t base) { x = 1; }
    solve function void configure(int n) { x = n; }
    function void op() { x = 2; }
    export target function op;
}
"""


def test_ctor_recognized():
    assert _kinds(translate(_MODEL % "ctor")["c_c"])["ctor"] is FuncKind.CONSTRUCTOR


def test_init_recognized_as_ctor():
    kinds = _kinds(translate(_MODEL % r"\init ")["c_c"])
    assert kinds["init"] is FuncKind.CONSTRUCTOR


def test_other_solve_functions_are_not_constructors():
    """Only the constructor is folded away; every other solve function is still
    part of the exported API and must keep being emitted."""
    kinds = _kinds(translate(_MODEL % "ctor")["c_c"])
    assert kinds["configure"] is FuncKind.EXPORT_SOLVE
    assert kinds["op"] is FuncKind.EXPORT_OP


def test_ctor_name_override():
    """`--ctor-name` narrows recognition to exactly one name, for a model that
    uses `init` as an ordinary API function."""
    with ctor_names_scope("ctor"):
        kinds = _kinds(translate(_MODEL % r"\init ")["c_c"])
    assert kinds["init"] is FuncKind.EXPORT_SOLVE


def test_the_override_does_not_outlive_its_scope():
    """The defect: `--ctor-name` used to be a process-global setter with no
    restore, so one compile changed how every later compile in the same
    process classified its constructor -- and the failure is silent, producing
    a class that constructs its register model from an undeclared variable."""
    with ctor_names_scope("ctor"):
        assert current_ctor_names() == frozenset({"ctor"})
    assert current_ctor_names() == DEFAULT_CTOR_NAMES
    assert _kinds(translate(_MODEL % r"\init ")["c_c"])["init"] is \
        FuncKind.CONSTRUCTOR


def test_two_compiles_do_not_interfere(tmp_path):
    """End to end, through the target: two compiles in one process, the first
    with `--ctor-name`, and the second must be unaffected.

    This is the test that fails on the pre-fix code.
    """
    import argparse
    from pssc import driver

    src = tmp_path / "m.pss"
    src.write_text(_MODEL % r"\init ")

    narrowed = argparse.Namespace(
        progseq_root="c_c", progseq_package="a_pkg",
        progseq_ctor_name="ctor", output_dir=str(tmp_path / "a"))
    driver.compile([str(src)], target="op-model-sv", opts=narrowed)

    default = argparse.Namespace(
        progseq_root="c_c", progseq_package="b_pkg",
        output_dir=str(tmp_path / "b"))
    driver.compile([str(src)], target="op-model-sv", opts=default)

    # The constructor's arguments become the factory's arguments, so whether
    # `init` was recognised is visible in `create`'s signature -- which is also
    # the thing that breaks when it is misclassified.
    a = (tmp_path / "a" / "a_pkg.sv").read_text()
    b = (tmp_path / "b" / "b_pkg.sv").read_text()
    assert "create(Timp imp);" in a, (
        "with --ctor-name ctor, `init` is an ordinary solve function and the "
        "factory takes no base address")
    assert "create(Timp imp, addr_handle_t base);" in b, (
        "the previous compile's --ctor-name leaked into this one: `init` was "
        "not recognised as the constructor")


def test_the_deprecated_setter_still_works_and_warns():
    """Kept for one release for anything calling it directly."""
    try:
        with pytest.warns(DeprecationWarning, match="ctor_names_scope"):
            set_ctor_name("ctor")
        assert current_ctor_names() == frozenset({"ctor"})
    finally:
        with pytest.warns(DeprecationWarning):
            set_ctor_name(None)


# --- P6a.T5: the emitters read the model, not the process -------------------

_AMBIENT_MODEL = """
import addr_reg_pkg::*;
component c_c {
    int x;
    solve function void initialize(addr_handle_t base) { x = 1; }
    function void op() { x = 2; }
    export target function op;
}
"""


def _generate(target, tmp_path, ambient, tag):
    """Generate with `ambient` in effect but the MODEL carrying the defaults."""
    import argparse
    from pssc import driver, targets
    from pssc.targets import op_model as om

    src = tmp_path / "m.pss"
    src.write_text(_AMBIENT_MODEL)
    tgt = targets.get(target)
    ctx = driver.translate([str(src)],
                           prelude=tgt.prelude(argparse.Namespace()))
    out = tmp_path / f"{target}-{tag}"
    with ctor_names_scope(ambient):
        model = om.elaborate(ctx, ctx.type_map["c_c"], out,
                             ctor_names=DEFAULT_CTOR_NAMES)
        opts = argparse.Namespace(progseq_root="c_c", progseq_package="p_pkg",
                                  output_dir=str(out))
        files = tgt.emit(model, opts)
    import pathlib
    return "\n".join(pathlib.Path(f).read_text() for f in files
                     if pathlib.Path(f).suffix in (".h", ".c", ".hpp", ".cpp",
                                                   ".sv"))


@pytest.mark.parametrize("target",
                         ["op-model-c", "op-model-cpp", "op-model-sv"])
def test_generation_does_not_read_the_ambient_ctor_names(target, tmp_path):
    """Generated output is a function of the MODEL, not of the process.

    A ContextVar is per-thread and restored on exit, so the leak it replaced
    cannot come back the same way. What can, and what this catches, is an
    emitter that answers "is this the constructor?" by asking the process
    instead of the model it was handed -- because then a caller who elaborates
    a model directly (`pssc.testing`, a plugin target, an embedding tool) gets
    silently different output depending on what some other compile set.

    Here the model says `initialize` is the constructor and the ambient value
    says it is not. Before P6a.T5 all three backends believed the ambient one
    and emitted `initialize` as an ordinary operation -- with the base-address
    binding dropped, which is generated code that compiles and drives nothing.
    """
    honest = _generate(target, tmp_path, None, "honest")
    lying = _generate(target, tmp_path, "nonsense_ctor", "lying")
    assert honest == lying
