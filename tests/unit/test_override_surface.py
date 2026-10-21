"""P6b.T1/T2: the published override surface.

Every public method of a Python class is overridable, which is the problem
rather than the feature. These tests hold two things:

  * the MARKED set and the checked-in manifest agree, so growing the surface is
    a reviewable diff in two files instead of a decorator somebody added;
  * a subclass cannot take one half of a paired override, which is the
    mechanical part of the fragile-base-class risk (design I13).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pssc.targets import overridable as ov
from pssc.targets.c.backend import COpModelBackend

MANIFEST = Path(__file__).resolve().parents[2] / "docs" / "override-surface.json"


@pytest.fixture(scope="module")
def manifest():
    return json.loads(MANIFEST.read_text())


# -- the manifest ------------------------------------------------------------

def _resolve(dotted: str):
    import importlib
    module, _, name = dotted.rpartition(".")
    return getattr(importlib.import_module(module), name)


SURFACES = ["pssc.targets.c.backend.COpModelBackend",
            "pssc.targets.c.lower_progseq.CBodyEmitter"]


def test_every_surface_is_in_the_manifest(manifest):
    assert sorted(manifest["surfaces"]) == sorted(SURFACES)


@pytest.mark.parametrize("surface", SURFACES)
def test_manifest_matches_code(manifest, surface):
    """The one test that makes the manifest mean anything.

    It fails when a method is marked, unmarked, or has its stability changed
    without the manifest being updated -- which is the whole mechanism: the
    surface cannot grow by accident, because growing it does not compile
    without a second, human-written edit.
    """
    expected = ov.manifest_for(_resolve(surface))
    stored = manifest["surfaces"][surface]
    added = sorted(set(expected) - set(stored))
    removed = sorted(set(stored) - set(expected))
    assert not added and not removed, (
        f"the override surface moved: added {added}, removed {removed}. Run "
        f"scripts/regen_override_surface.py and review the diff -- a new entry "
        f"is a promise pssc then has to keep")
    for name in sorted(expected):
        assert expected[name] == stored[name], (
            f"'{name}' promises {expected[name]} in code and {stored[name]} in "
            f"the manifest")


def test_the_manifest_is_exactly_what_the_script_writes():
    """...including formatting, so `--check` in CI and this test cannot
    disagree about whether the file is stale."""
    import sys
    sys.path.insert(0, str(MANIFEST.parents[1] / "scripts"))
    try:
        import regen_override_surface as regen
    finally:
        sys.path.pop(0)
    assert MANIFEST.read_text() == json.dumps(regen.build(), indent=2) + "\n"


def test_provisional_methods_are_listed_as_such(manifest):
    """`provisional` is a real statement, not a default nobody set: it says
    this member is published for a real extension and is expected to move."""
    stored = manifest["surfaces"]["pssc.targets.c.backend.COpModelBackend"]
    stabilities = {e["stability"] for e in stored.values()}
    assert stabilities == {"stable", "provisional"}
    # The body emitter is the one Phase 7 is expected to replace outright, so
    # promising it as stable now would be a promise pssc intends to break.
    assert stored["body_emitter_cls"]["stability"] == "provisional"
    assert stored["header_sections"]["stability"] == "stable"


def test_every_marked_method_has_a_docstring():
    """A marked method's docstring IS the contract an extension codes against
    -- what it is handed, what it must return, what the caller does with it."""
    bare = [f"{surface}.{name}" for surface in SURFACES
            for name, info in ov.surface(_resolve(surface)).items()
            if not info.attribute
            and not (getattr(_resolve(surface), name).__doc__ or "").strip()]
    assert not bare, (
        f"marked as overridable with nothing said about the contract: {bare}")


def test_the_marked_set_covers_what_the_tier_b_extension_needs():
    """The surface is validated by a real extension (P6b.T6), so the members
    that extension uses must be in it -- if one is not, the manifest is
    describing a smaller surface than pssc actually supports."""
    used = {"header_sections", "emit_operation", "emit_extra_files",
            "style_cls"}
    assert used <= set(ov.surface(COpModelBackend))


# -- pairs -------------------------------------------------------------------

def test_half_pair_override_rejected():
    """The include guard is one decision with two halves: overriding the open
    and inheriting the close produces a header that is unbalanced or names two
    different macros, and neither failure appears where the override is."""
    class _Half(COpModelBackend):
        def emit_guard_open(self, model, s):
            return ["#pragma once", ""]

    with pytest.raises(ov.OverrideError) as exc:
        ov.check_pairs(_Half)
    assert "emit_guard_open" in str(exc.value)
    assert "emit_guard_close" in str(exc.value)


def test_full_pair_override_accepted():
    class _Both(COpModelBackend):
        def emit_guard_open(self, model, s):
            return ["#pragma once", ""]

        def emit_guard_close(self, model, s):
            return []

    ov.check_pairs(_Both)               # must not raise


def test_an_unpaired_override_is_fine():
    """The check must not turn into "overriding anything needs permission"."""
    class _Banner(COpModelBackend):
        def emit_banner(self, model, s):
            return ["/* ACME */", ""]

    ov.check_pairs(_Banner)


def test_the_type_pair_is_checked_too():
    """The other pair: api_types and the register value unions partition one
    set of type declarations, and an overlap declares one type twice."""
    class _Types(COpModelBackend):
        def emit_api_types(self, model, s):
            return []

    with pytest.raises(ov.OverrideError, match="emit_value_unions"):
        ov.check_pairs(_Types)


def test_pairs_are_checked_at_registration():
    """Not at generation time: the answer must arrive before any model is
    read, and certainly before a C compiler sees the result."""
    from pssc.targets import register
    from pssc.targets.c_progseq_tgt import CProgSeqTarget

    class _Backend(COpModelBackend):
        def emit_guard_open(self, model, s):
            return ["#pragma once", ""]

    class _Target(CProgSeqTarget):
        name = "x-half-pair"
        backend_cls = _Backend

    with pytest.raises(ov.OverrideError):
        register(_Target())
    import pssc.targets as t
    assert "x-half-pair" not in t._REGISTRY, (
        "a target that fails its override check must not be half-registered")


# -- the decorator itself ----------------------------------------------------

def test_a_bad_stability_is_refused_at_import_time():
    with pytest.raises(ov.OverrideError, match="stability"):
        ov.overridable(since="0.1", stability="pretty-stable")


def test_surface_includes_inherited_marks():
    """An extension does not re-publish pssc's surface by using it: a subclass
    that overrides a marked method shows the same published member."""
    class _Sub(COpModelBackend):
        def emit_banner(self, model, s):
            return []

    assert ov.surface(_Sub) == ov.surface(COpModelBackend)
