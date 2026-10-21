"""Phase 6a: the C backend as an object.

What is under test is not that the class exists -- the golden snapshots already
prove the output did not move -- but the two properties that make subclassing it
a real path: that no text-producing method writes a file, and that the emitted
order is derivable from the section list rather than from a straight-line
function nobody can insert into.
"""
from __future__ import annotations

import argparse
import inspect
import re
from pathlib import Path

import pytest

from pssc import driver, targets
from pssc.targets import op_model as om
from pssc.targets.c.backend import COpModelBackend
from pssc.targets.c.style import CSettings, CStylePolicy

from .op_model import op_model_sources


@pytest.fixture(scope="module")
def ctx():
    tgt = targets.get("op-model-c")
    return driver.translate(op_model_sources(),
                            prelude=tgt.prelude(argparse.Namespace()))


@pytest.fixture
def op_model(ctx, tmp_path):
    """A freshly elaborated model per test -- `prepare()` writes derived state
    onto the backend, not onto the model, but a shared model would still make
    one test's `out_dir` another's."""
    return om.elaborate(ctx, ctx.type_map["wb_dma_c"], tmp_path / "out")


def _settings(**kw) -> CSettings:
    kw.setdefault("prefix", "wb_dma")
    kw.setdefault("seam_include", "pssc_mem_vtable.h")
    return CSettings(**kw)


@pytest.fixture
def prepared(op_model):
    """A backend that has lowered the model, ready to be interrogated."""
    be = COpModelBackend()
    s = _settings()
    be.prepare(op_model, s)
    return be, op_model, s


# -- property 1: only `generate`/`write_files` touch the filesystem ----------

def test_no_emit_method_writes_files(prepared, monkeypatch):
    """Every `emit_*` returns text.

    Held by introspection rather than by reading them, because the rule has to
    survive the method someone adds next year: a method that writes as a side
    effect cannot be wrapped by a subclass calling `super()`, which is the
    entire mid-weight path.
    """
    be, model, s = prepared

    def _refuse(*a, **kw):
        raise AssertionError("an emit_* method wrote to the filesystem")

    monkeypatch.setattr(Path, "write_text", _refuse)
    monkeypatch.setattr(Path, "write_bytes", _refuse)
    monkeypatch.setattr(Path, "mkdir", _refuse)

    emitters = [n for n in dir(be)
                if n.startswith("emit_") and callable(getattr(be, n))]
    assert emitters, "no emit_* methods found -- the introspection is wrong"
    for name in emitters:
        params = list(inspect.signature(getattr(be, name)).parameters)
        if params[:2] in (["fn", "ctx"], ["node", "ctx"]):
            # A per-operation or per-component emitter, which has no meaning
            # without one. It is still covered by this test's monkeypatch --
            # `emit_impl` below runs the whole loop through it -- and by
            # `test_emit_operation_is_reached` / `test_c_op_api.py`.
            continue
        assert params[:2] == ["model", "s"], (
            f"{name} takes {params}, which no caller here recognises. Either "
            f"give it the (model, s) shape or teach this test how it is "
            f"called -- an emit_* nobody can call generically is one nobody "
            f"can check for writing to disk")
        out = getattr(be, name)(model, s)
        # Lines, a whole file's text, or `{name: text}` for the one method that
        # names its own files. All three are TEXT; none of them is a path,
        # which is the property that matters -- a method returning a path has
        # already written it.
        assert isinstance(out, (str, list, dict)), f"{name} returned {type(out)}"
        for value in (out.values() if isinstance(out, dict) else ()):
            assert isinstance(value, str), f"{name} returned a non-text value"

    # ...and the text-assembling methods above them, which are the ones a
    # subclass is most likely to wrap.
    for name in ("header_text", "impl_text", "stubs_text"):
        assert isinstance(getattr(be, name)(model, s), str)


def test_generate_is_the_only_writer(op_model):
    """The complement: `generate` DOES write, so the test above is not passing
    because nothing writes anywhere."""
    written = COpModelBackend().generate(op_model, _settings())
    assert written and all(p.exists() for p in written)


# -- property 2: the section list is the emitted order ----------------------

def test_section_list_matches_emitted_order(prepared):
    """Each section's text appears in the header in section-list order.

    The check is on FIRST OCCURRENCE of each section's own first line: it is
    the assembly order that is under test, and a section whose text happens to
    recur later says nothing about where it was assembled.
    """
    be, model, s = prepared
    header = be.header_text(model, s)
    at = -1
    for name, emit in be.header_sections(model, s):
        lines = emit(model, s)
        if not lines or not lines[0].strip():
            continue
        pos = header.index(lines[0])
        assert pos > at, f"section {name!r} is emitted out of order"
        at = pos


def test_the_header_is_exactly_its_sections(prepared):
    """Nothing is added between sections by the assembler -- so a subclass
    reading `header_sections()` is reading the whole story."""
    be, model, s = prepared
    lines = []
    for _, emit in be.header_sections(model, s):
        lines += emit(model, s)
    assert be.header_text(model, s) == "\n".join(lines)


def test_a_subclass_can_insert_a_section(op_model):
    """The mid-weight path end to end: an extension adds a company header block
    without reimplementing the other ten sections."""
    from pssc.targets.sections import Section, insert_after

    class _Acme(COpModelBackend):
        def header_sections(self, model, s):
            return insert_after(super().header_sections(model, s), "banner",
                                Section("acme", lambda m, s: ["/* ACME */"]))

    plain, acme = COpModelBackend(), _Acme()
    s = _settings()
    plain.prepare(op_model, s)
    acme.prepare(op_model, s)
    added = acme.header_text(op_model, s)
    assert "/* ACME */" in added
    # Directly after the banner, and nothing else moved.
    assert added.replace("/* ACME */\n", "") == plain.header_text(op_model, s)


# -- property 3: the collaborators are swappable ----------------------------

def test_body_emitter_cls_is_honoured(op_model, tmp_path):
    """A subclass swapping the body emitter changes body rendering and nothing
    else."""
    from pssc.targets.c.lower_progseq import CBodyEmitter

    class _Loud(CBodyEmitter):
        def stmt(self, s, ind):
            return ["    " * ind + "/* loud */"] + super().stmt(s, ind)

    class _LoudBackend(COpModelBackend):
        body_emitter_cls = _Loud

    plain = COpModelBackend()
    plain.prepare(op_model, _settings())
    loud = _LoudBackend()
    loud.prepare(op_model, _settings())

    assert "/* loud */" not in plain.impl
    assert "/* loud */" in loud.impl
    # The declarations are untouched: a body emitter emits bodies.
    s = _settings()
    assert plain.emit_decls(op_model, s) == loud.emit_decls(op_model, s)


def test_a_swapped_body_emitter_reaches_ctor_bodies(op_model):
    """`_init` is emitted by a different class, so a swap that stopped at
    operations would render the same model two ways in one file."""
    from pssc.targets.c.lower_progseq import CBodyEmitter

    class _Marked(CBodyEmitter):
        def expr(self, e):
            return "/*m*/" + super().expr(e)

    class _MarkedBackend(COpModelBackend):
        body_emitter_cls = _Marked

    be = _MarkedBackend()
    be.prepare(op_model, _settings())
    # A constructor body is `<prefix>_pss_ctor`; `_init` only calls it.
    inits = re.findall(r"^static void \w+_pss_ctor\(.*?^\}", be.impl,
                       re.S | re.M)
    assert inits, "no constructor body found -- the extraction is wrong"
    # EVERY one of them: a swap honoured for the root and not for the children
    # is the same inconsistency in a smaller place.
    assert all("/*m*/" in body for body in inits)


def test_emit_operation_is_reached(op_model):
    """P6b: the per-operation seam. Every exported operation goes through it --
    a wrap honoured for some of them is worse than none, because the file then
    contains two conventions and neither is stated."""
    seen = []

    class _Counting(COpModelBackend):
        def emit_operation(self, fn, ctx):
            seen.append((getattr(ctx.comp, "name", "?"), fn.name))
            return super().emit_operation(fn, ctx)

    be = _Counting()
    be.prepare(op_model, _settings())
    expected = [(getattr(c, "name", "?"), fn.name)
                for c in op_model.comp_dtypes
                for fn in op_model.operations(c)]
    assert seen == expected
    # ...and delegating to `super()` reproduces the unwrapped text exactly.
    plain = COpModelBackend()
    plain.prepare(op_model, _settings())
    assert be.impl == plain.impl


def test_emit_operation_wraps_the_whole_function(op_model):
    """What the seam is FOR: a prologue in every generated operation, which a
    body emitter cannot add -- it never sees the signature."""
    class _Traced(COpModelBackend):
        def emit_operation(self, fn, ctx):
            lines = super().emit_operation(fn, ctx)
            brace = lines.index(next(ln for ln in lines if ln.endswith(" {")))
            return (lines[:brace + 1]
                    + [f'    ACME_TRACE("{ctx.prefix}_{fn.name}");']
                    + lines[brace + 1:])

    be = _Traced()
    be.prepare(op_model, _settings())
    n_ops = sum(len(op_model.operations(c)) for c in op_model.comp_dtypes)
    assert be.impl.count("ACME_TRACE(") == n_ops


def test_style_cls_is_used_when_no_style_is_passed():
    class _Loud(CStylePolicy):
        name = "loud"

        def header_name(self, prefix):
            return f"{prefix}.LOUD.h"

    class _LoudBackend(COpModelBackend):
        style_cls = _Loud

    assert isinstance(_LoudBackend().style, _Loud)
    # An explicitly passed policy still wins -- `--style` is the user's answer,
    # and a backend subclass must not silently override it.
    assert isinstance(_LoudBackend(CStylePolicy()).style, CStylePolicy)


class _WithMap(COpModelBackend):
    """A backend with one extra file, used by the three tests below."""

    def emit_extra_files(self, model, s):
        return {f"{s.prefix}_map.h": "/* map */\n"}


def test_extra_files_land_beside_the_api(op_model, tmp_path):
    """The tier-B hook: a subclass emitting a register-map header gets it
    written by the same call, not by a second pass over the directory."""
    written = _WithMap().generate(op_model, _settings())
    names = [p.name for p in written]
    assert "wb_dma_map.h" in names
    assert (tmp_path / "out" / "wb_dma_map.h").read_text() == "/* map */\n"


def test_extra_files_reported_in_order(op_model):
    """P6b.T4. The returned list is a COMPILATION order, so an extra comes
    after the artifacts it accompanies -- an extra header may include the
    generated one, and nothing generated includes an extra. The private regs
    header is NOT an extra for exactly that reason -- the generated `.c`
    includes it -- so it sits between the public header and the `.c`."""
    written = _WithMap().generate(op_model, _settings(emit_stubs=True))
    names = [p.name for p in written]
    assert names == ["wb_dma.h", "wb_dma_regs.h", "wb_dma.c", "wb_dma_stubs.c",
                     "wb_dma_map.h"]


def test_an_extra_file_may_not_overwrite_the_api(op_model):
    """Silently replacing the generated header with an extra is the one way
    this hook could produce a directory that looks generated and is not."""
    class _Clobber(COpModelBackend):
        def emit_extra_files(self, model, s):
            return {self.style.header_name(s.prefix): "/* mine now */\n"}

    with pytest.raises(ValueError, match="already generated"):
        _Clobber().generate(op_model, _settings())
