"""Gate-1 guardrail: the `pssc` package imports, exposes its frozen public API,
and ships its data files (migration from zuspec-fe-pss)."""
from importlib.resources import files

import pytest

import pssc

FROZEN_API = [
    "Parser", "ParseException", "PssTranslationError",
    "load_pss", "load_pss_files", "generate_sv", "generate_sv_files",
    "AstToIrTranslator", "AstToIrContext", "IrToRuntimeBuilder", "ClassRegistry",
    "get_deps", "get_libs", "get_libdirs", "get_incdirs",
    # Phase 2 additions
    "compile", "to_core_context", "CompileResult", "CompileError",
]


@pytest.mark.parametrize("name", FROZEN_API)
def test_frozen_api_importable(name):
    assert hasattr(pssc, name), f"pssc.{name} missing"


def test_version_exposed():
    assert isinstance(pssc.__version__, str) and pssc.__version__


def test_data_files_resolvable():
    root = files("pssc")
    assert root.joinpath("share/sv/zsp_rt_pkg.sv").is_file()
    assert root.joinpath("std_libs/sml_pkg.pss").is_file()


def test_ast2ir_uses_ir_core_directly():
    """ast2ir must target zuspec.ir.core directly, not via zuspec.dataclasses."""
    import zuspec.ir.core as core
    import pssc.ast2ir as a
    assert a.ir is core
    src = files("pssc").joinpath("ast2ir.py").read_text()
    assert "from zuspec.dataclasses import ir" not in src


def test_no_dataclasses_ir_shim_in_pssc():
    """No production pssc module may import the IR via the zuspec.dataclasses shim.

    The canonical IR lives in zuspec.ir.core; importing it through
    ``from zuspec.dataclasses import ir`` is a legacy alias that re-couples pssc
    to zuspec-dataclasses.  Every pssc source file must target zuspec.ir.core
    directly (see docs/design/be-py-runtime-relocation-design.md, Phase 0).
    """
    import pathlib
    import pssc
    pkg_root = pathlib.Path(pssc.__file__).parent
    offenders = [
        str(p.relative_to(pkg_root))
        for p in pkg_root.rglob("*.py")
        if "from zuspec.dataclasses import ir" in p.read_text()
    ]
    assert not offenders, (
        "pssc modules still import the IR via the dataclasses shim: " + ", ".join(offenders)
    )


def test_import_pssc_does_not_pull_in_dataclasses():
    """``import pssc`` must not transitively import ``zuspec.dataclasses``.

    The Python runtime now lives in ``zuspec.be.py``; ``zuspec.dataclasses`` is a
    frontend that pssc depends on only for tests.  Importing pssc must stay free
    of it (see docs/design/be-py-runtime-relocation-design.md, Phase 4).  Run in a
    subprocess so an already-imported dataclasses (from another test) can't mask a
    regression.
    """
    import subprocess, sys, textwrap
    code = textwrap.dedent(
        """
        import sys
        import pssc  # noqa: F401
        leaked = sorted(
            m for m in sys.modules
            if m == 'zuspec.dataclasses' or m.startswith('zuspec.dataclasses.')
        )
        if leaked:
            print('LEAKED:' + ','.join(leaked))
            raise SystemExit(1)
        """
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, (
        "import pssc pulled in zuspec.dataclasses:\n" + res.stdout + res.stderr
    )


def test_backends_import_clean_of_dataclasses():
    """Importing the Zuspec backends must not transitively import dataclasses.

    The Python/SV/C backends consume ``zuspec.ir.core`` and own their runtime;
    they reach the dataclasses frontend only via lazy, @zdc-authored-only paths.
    Importing them (as pssc does, lazily, per target) must stay clean.
    """
    import subprocess, sys, textwrap
    for backend in ("zuspec.be.py", "zuspec.be.py.builder", "zuspec.be.sv", "zuspec.be.sw"):
        code = textwrap.dedent(
            f"""
            import sys
            import {backend}  # noqa: F401
            leaked = [m for m in sys.modules
                      if m == 'zuspec.dataclasses' or m.startswith('zuspec.dataclasses.')]
            if leaked:
                print('LEAKED:' + ','.join(sorted(leaked)))
                raise SystemExit(1)
            """
        )
        res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        assert res.returncode == 0, (
            f"import {backend} pulled in zuspec.dataclasses:\n" + res.stdout + res.stderr
        )


def test_no_duplicate_op_model_helpers():
    """P2.T6: the three op-model backends share one walk.

    Each of `progseq_gen.py`, `c/c_progseq_gen.py` and `cpp/cpp_progseq_gen.py`
    used to carry its own `_resolver`, its own `_count`, its own
    regular-component walk and its own value-struct de-duplication. Identical
    by intent and not by construction, which is how the SV and C copies came to
    disagree about the order of sibling components -- invisibly, because both
    orders compiled.

    A grep, deliberately, rather than an import check: the failure mode is
    someone RE-ADDING a private copy to one backend, and a copy that is defined
    and used locally imports nothing.
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.parent / "src" / "pssc"
    backends = [
        root / "targets" / "progseq_gen.py",
        root / "targets" / "c" / "c_progseq_gen.py",
        root / "targets" / "cpp" / "cpp_progseq_gen.py",
    ]
    banned = ["def _resolver(", "def _count(", "def _regular_components(",
              "def _reg_value_structs", "def node_key(",
              "def _value_structs("]
    offenders = []
    for path in backends:
        text = path.read_text()
        for name in banned:
            if name in text:
                offenders.append(f"{path.name}: {name}")
    assert not offenders, (
        "an op-model backend has grown a private copy of a shared helper; "
        f"use `targets.op_model.OpModel` instead: {offenders}")


def test_the_op_model_backends_do_not_walk_the_tree_themselves():
    """The other half: `walk_tree` is called once, by `op_model.elaborate`."""
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent.parent / "src" / "pssc"
    for path in (root / "targets" / "progseq_gen.py",
                 root / "targets" / "c" / "c_progseq_gen.py",
                 root / "targets" / "cpp" / "cpp_progseq_gen.py"):
        assert "walk_tree(" not in path.read_text(), (
            f"{path.name} walks the tree itself; the walk belongs to "
            f"op_model.elaborate() so every backend sees one answer")




def _code_lines(path):
    """The lines of `path` that are code, with comments and prose dropped.

    A grep over a generator's source has one recurring false positive: the
    thing being grepped for is exactly what its comments are ABOUT, and a
    guardrail that forbids naming the seam in a docstring is a guardrail people
    route around by not writing the docstring. So prose comes out first --
    Python comments, bare string expressions (every docstring is one), and
    lines of C comment inside an emitted banner, which are prose too and happen
    to live in a string.
    """
    import ast
    import io
    import tokenize

    src = path.read_text()
    skip = set()
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            skip.add(tok.start[0])
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, str):
            skip.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    prose = ("*", "/*", "//", "#:", '"/*', '" *', "' *")
    return [(n, ln) for n, ln in enumerate(src.splitlines(), 1)
            if n not in skip and not ln.strip().startswith(prose)]


def test_bus_spelling_has_one_home():
    """P5a.T3: `pssc_r32` / `pssc_w32` / `pssc_bus(...)` are emitted from one
    module.

    They used to be written out at eight sites across `lower_reg_model.py` and
    `lower_progseq.py`. Nothing was wrong with any one of them -- they agreed,
    and the golden snapshots kept them agreeing -- but a style that wants to
    change how this target reaches memory would have had to find all eight, and
    one that finds seven emits C calling two seams and links against one.

    A grep for the same reason as `test_no_duplicate_op_model_helpers`: the
    failure is someone re-inlining the spelling at a call site, which imports
    nothing and reads perfectly well in review.

    Two lines carry `# seam-ok`, and both do something other than emit an
    access: `_bus_macro` DEFINES `pssc_bus` (its expansion is the one line that
    varies by link style), and the shim check READS generated text looking for
    one.
    """
    import pathlib
    import re
    c_dir = (pathlib.Path(__file__).resolve().parents[2] / "src" / "pssc"
             / "targets" / "c")
    # `pssc_r3`/`pssc_w3` rather than the full names, so that a hard-coded
    # `pssc_r32` and a hard-coded `"pssc_r%d"` template are one rule.
    spelling = re.compile(r"pssc_[rw]\d|pssc_bus\s*\(")
    offenders = []
    for path in sorted(c_dir.glob("*.py")):
        if path.name == "mem_access.py":
            continue
        for n, line in _code_lines(path):
            if spelling.search(line) and "seam-ok" not in line:
                offenders.append(f"{path.name}:{n}: {line.strip()}")
    assert not offenders, (
        "the memory seam is spelled outside `targets/c/mem_access.py`; route "
        "it through the MemAccess funnel so a style has one place to change:\n"
        + "\n".join(offenders))


def test_the_seam_grep_would_catch_an_inlined_access():
    """The guardrail's own control: a check that cannot fail is not a check.

    Written against the real matcher rather than a re-derived one, so a regex
    edit that stops matching `pssc_w64(` fails here.
    """
    import re
    spelling = re.compile(r"pssc_[rw]\d|pssc_bus\s*\(")
    for line in ('    return f"pssc_r32({bus}, {addr})"',
                 '    prim = "pssc_w64"',
                 '    return f"pssc_bus({self.h})"'):
        assert spelling.search(line), line
    assert not spelling.search("    return mem.read(32, self.h, addr)")
