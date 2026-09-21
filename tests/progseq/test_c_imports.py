"""C4.1 -- `import target/solve function` reaches C under its own PSS name.

The property this file exists to hold: **adding a platform hook is a model edit,
not a generator edit.** Before C4.1 the C backend knew exactly one set of
callable names -- the `pssc_mem_*` table and a handful of built-ins -- so a model
that declared `import target function void plat_delay_us(int)` and called it
failed generation with "no lowering", and the fix was to edit the generator.

That is right for the MEMORY SEAM, which is fixed and identical for every model,
and wrong for an import, which is by definition whatever this model declared.
"""
import argparse
import re
import subprocess

import pytest

from pssc import driver

from .conftest import available_c_compilers, diagnostic_text

_CC = available_c_compilers()
needs_cc = pytest.mark.skipif(not _CC, reason="no C compiler on PATH")


#: Two imports the generator has never heard of: one `target` returning void
#: with an argument, one `solve` returning a value with none. Neither name
#: appears anywhere in pssc.
_MODEL = """
package plat_pkg {
    import target function void plat_delay_us(int us);
    import solve  function int  plat_ticks();
}

component imp_c {
    import plat_pkg::*;

    target function int spin(int n) {
        int t;
        plat_delay_us(n);
        t = plat_ticks();
        return t;
    }
}
"""


def _gen(tmp_path, model=_MODEL, root="imp_c"):
    src = tmp_path / "imp.pss"
    src.write_text(model)
    ns = argparse.Namespace(progseq_root=root, c_prefix="imp",
                            output_dir=str(tmp_path))
    driver.compile([str(src)], target="op-model-c", opts=ns)
    return ((tmp_path / "imp.h").read_text(), (tmp_path / "imp.c").read_text())


def test_a_custom_import_needs_no_generator_change(tmp_path):
    """The whole point. Neither name is known to pssc, and both get a correct
    prototype and a correct call."""
    h, c = _gen(tmp_path)
    assert "void plat_delay_us(int us);" in h
    assert "int plat_ticks(void);" in h
    assert "plat_delay_us(n);" in c
    assert "t = plat_ticks();" in c


def test_an_import_call_takes_no_handle(tmp_path):
    """An import is a PLATFORM function, not a method of the component. Passing
    `s` would invent a parameter its own declaration does not have -- and since
    the prototype is generated from that same declaration, the mismatch would be
    a compile error rather than a silent one. Asserted anyway: getting this
    wrong once cost a whole debug cycle in the SV projection."""
    _, c = _gen(tmp_path)
    assert "plat_delay_us(s," not in c
    assert "plat_ticks(s)" not in c


def test_solve_and_target_imports_are_both_plain_calls(tmp_path):
    """`is_solve` vs `is_target` is about WHEN a function runs relative to
    solving. This target has no solver (HAVE_RUNTIME_SOLVER=false), so the
    distinction has nothing to attach to and both are ordinary C calls. If a
    solver ever lands here this test is the one that should start failing."""
    h, _ = _gen(tmp_path)
    for proto in ("void plat_delay_us(int us);", "int plat_ticks(void);"):
        assert proto in h


def test_a_declared_but_uncalled_import_is_still_declared(tmp_path):
    """The import set is the PLATFORM'S CONTRACT, not a call-graph summary. A
    platform that implements one function too many pays nothing; one that
    discovers a requirement at link time pays a rebuild."""
    model = _MODEL.replace("        t = plat_ticks();\n", "        t = n;\n")
    h, _ = _gen(tmp_path, model)
    assert "int plat_ticks(void);" in h


def test_a_call_to_nothing_is_refused_by_the_front_end(tmp_path):
    """C4.1 widens what is legal; it must not make everything legal.

    The refusal comes from the FRONT END, not from this backend -- "unknown
    identifier" with a file and a line, which is a better answer than anything
    a lowering pass can give. Pinned here because it is the reason the backend's
    own check (below) sees so few cases: by the time a call reaches lowering it
    has almost always resolved to something.
    """
    # Through the DRIVER the front end's refusal arrives as CompileError: a
    # user error, reported and exit-1'd rather than surfacing as a traceback.
    # The parser still raises ParseException to its own callers; the driver
    # converts it and renders the markers (see tests/unit/errors/
    # test_parse_diagnostics.py).
    model = _MODEL.replace("        plat_delay_us(n);\n",
                           "        no_such_function(n);\n")
    with pytest.raises(driver.CompileError) as exc:
        _gen(tmp_path, model)
    text = "\n".join(exc.value.errors)
    assert "unknown identifier" in text
    assert "no_such_function" in text


def test_a_component_scope_import_is_refused_rather_than_mislowered(tmp_path):
    """The one case the backend's own check still catches, and it is a real one.

    A `import target function` declared inside a COMPONENT parses, and then does
    not reach the IR at all -- it is absent from `comp.functions` and absent
    from `ctx.import_functions`. So the call arrives at lowering resolved to
    nothing, and emitting it verbatim would produce C naming a function that
    neither the model nor the platform declared: it would compile under C89
    rules as an implicit declaration and fail only at link, or with -Werror fail
    somewhere confusing.

    Refusing here is the correct outcome for as long as the front end drops the
    declaration. If that is fixed, this test should be changed to assert the
    component-scope import GENERATES -- not deleted.

    The refusal moved from the body emitter to the `validate_calls` gate
    (P1.T1), so it now arrives as a `CompileError` before any file is opened
    and lists every offending call rather than the first. The message is what
    matters and it is unchanged in substance -- see `diagnostic_text`.
    """
    model = """
component imp_c {
    import target function void plat_delay_us(int us);
    target function void spin(int n) { plat_delay_us(n); }
}
"""
    with pytest.raises(Exception) as exc:
        _gen(tmp_path, model)
    msg = diagnostic_text(exc)
    assert "plat_delay_us" in msg
    assert "no function named" in msg or "no lowering" in msg


def test_the_refusal_names_imports_as_an_option(tmp_path):
    """The diagnostic has to say what the reader can do about it. Before C4.1
    "declare it as an import" was not among the answers, so the message did not
    mention imports; now it is, and it does -- which for the component-scope
    case above is precisely the fix (move the declaration to package scope)."""
    model = """
component imp_c {
    import target function void plat_delay_us(int us);
    target function void spin(int n) { plat_delay_us(n); }
}
"""
    with pytest.raises(Exception) as exc:
        _gen(tmp_path, model)
    assert "declare it as an `import` function" in diagnostic_text(exc)


def test_no_imports_emits_no_import_section(tmp_path):
    """A header with an empty "supplied by the PLATFORM" banner reads as a
    contract with nothing in it, which is not the same as no contract."""
    model = """
component bare_c {
    target function int twice(int n) { return n + n; }
}
"""
    src = tmp_path / "bare.pss"
    src.write_text(model)
    ns = argparse.Namespace(progseq_root="bare_c", c_prefix="bare",
                            output_dir=str(tmp_path))
    driver.compile([str(src)], target="op-model-c", opts=ns)
    h = (tmp_path / "bare.h").read_text()
    assert "supplied by the PLATFORM" not in h


@needs_cc
@pytest.mark.parametrize("cc", _CC)
def test_the_generated_import_surface_compiles_and_links(tmp_path, cc):
    """The prototype and the call agree, and a platform implementing exactly
    what the header declares produces a complete program. That is the claim; a
    link is the only thing that actually checks it."""
    _gen(tmp_path)
    (tmp_path / "plat.c").write_text(
        '#include "imp.h"\n'
        "void plat_delay_us(int us) { (void)us; }\n"
        "int plat_ticks(void) { return 42; }\n"
        "void pssc_message(const char *fmt, ...) { (void)fmt; }\n"
        "int main(void) { imp_t o; imp_init(&o, 0); "
        "return imp_spin(&o, 1) == 42 ? 0 : 1; }\n")
    exe = tmp_path / "a.out"
    r = subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror", "imp.c", "plat.c",
         "-o", str(exe)],
        cwd=str(tmp_path), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert subprocess.run([str(exe)]).returncode == 0


# --- C4.2: --emit-stubs ----------------------------------------------------

def _gen_stubs(tmp_path):
    src = tmp_path / "imp.pss"
    src.write_text(_MODEL)
    ns = argparse.Namespace(progseq_root="imp_c", c_prefix="imp",
                            output_dir=str(tmp_path), c_emit_stubs=True)
    driver.compile([str(src)], target="op-model-c", opts=ns)
    return (tmp_path / "imp_stubs.c").read_text()


def test_stubs_are_off_by_default(tmp_path):
    """A file that makes an incomplete link succeed is exactly the kind of thing
    that must be asked for."""
    _gen(tmp_path)
    assert not (tmp_path / "imp_stubs.c").exists()


def test_only_message_is_stubbed(tmp_path):
    """The test for whether something belongs in this file is whether DOING
    NOTHING IS A CORRECT IMPLEMENTATION of it. Logging passes; nothing else in
    the seam does.

    A stubbed `pssc_w32` is a driver that reports the device is fine because it
    never talked to it -- the worst thing this generator could emit. A stubbed
    `plat_delay_us` is a delay that does not delay, so the poll loop above it
    can never be right. Both must stay unresolved symbols.
    """
    # Comments stripped first: the banner NAMES the un-stubbed functions in
    # order to explain why they are absent, and a test that could not tell a
    # definition from an explanation would force that explanation out.
    code = re.sub(r"/\*.*?\*/", "", _gen_stubs(tmp_path), flags=re.S)
    assert "pssc_message" in code
    for never in ("pssc_r8", "pssc_r16", "pssc_r32", "pssc_r64",
                  "pssc_w8", "pssc_w16", "pssc_w32", "pssc_w64",
                  "plat_delay_us", "plat_ticks"):
        assert never not in code, never
    # And exactly one definition, so a later addition has to come here first.
    assert len(re.findall(r"^\w[\w \*]*\w\s*\([^;]*\)\s*\{", code, re.M)) == 1


def test_the_stub_is_weak(tmp_path):
    """So a real definition anywhere in the link wins with no build change. A
    strong stub would silently beat the platform's own implementation on some
    link orders and collide on others."""
    stubs = _gen_stubs(tmp_path)
    assert "__attribute__((weak))" in stubs
    assert "PSSC_WEAK void pssc_message" in stubs


@needs_cc
@pytest.mark.parametrize("cc", _CC)
def test_the_platform_definition_beats_the_weak_stub(tmp_path, cc):
    """The property the `weak` is for, and the only way to check it is to link
    both and run. A stub that quietly won would send every message to nowhere
    while the platform's own logger sat unused in the image."""
    _gen_stubs(tmp_path)
    (tmp_path / "plat.c").write_text(
        '#include "imp.h"\n'
        "#include <stdarg.h>\n"
        "int called = 0;\n"
        "void plat_delay_us(int us) { (void)us; }\n"
        "int plat_ticks(void) { return 42; }\n"
        "void pssc_message(const char *fmt, ...) { (void)fmt; called = 1; }\n"
        "int main(void) { imp_t o; imp_init(&o, 0);\n"
        "  if (imp_spin(&o, 1) != 42) return 1;\n"
        "  pssc_message(\"x\");\n"
        "  return called ? 0 : 2; }\n")
    exe = tmp_path / "a.out"
    r = subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
         "imp.c", "imp_stubs.c", "plat.c", "-o", str(exe)],
        cwd=str(tmp_path), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert subprocess.run([str(exe)]).returncode == 0


@needs_cc
@pytest.mark.parametrize("cc", _CC)
def test_the_stub_alone_completes_the_message_seam(tmp_path, cc):
    """What the flag is FOR: bring up the driver before the logging side
    exists. The imports still have to be supplied -- that is the point of not
    stubbing them."""
    _gen_stubs(tmp_path)
    (tmp_path / "plat.c").write_text(
        '#include "imp.h"\n'
        "void plat_delay_us(int us) { (void)us; }\n"
        "int plat_ticks(void) { return 42; }\n"
        "int main(void) { imp_t o; imp_init(&o, 0);\n"
        "  return imp_spin(&o, 1) == 42 ? 0 : 1; }\n")
    exe = tmp_path / "a.out"
    r = subprocess.run(
        [cc, "-std=c99", "-Wall", "-Wextra", "-Werror",
         "imp.c", "imp_stubs.c", "plat.c", "-o", str(exe)],
        cwd=str(tmp_path), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert subprocess.run([str(exe)]).returncode == 0
