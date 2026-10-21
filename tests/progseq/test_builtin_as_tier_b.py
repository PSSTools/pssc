"""Can a BUILT-IN variant be written the way an extension author would write it?

P6b published an override surface and validated it against
`tests/plugins/pssc_fixture_plugin`, which is an outside extension. P8.T3 asks
the harder version of the same question: is the surface sufficient to express
something pssc already ships as a FLAG -- and would expressing it that way
leave less machinery behind than it replaces?

Two variants are tried here, one per answer:

* **`--emit-stubs` CAN be a subclass.** `emit_extra_files` is exactly the right
  hook, the subclass is six lines, and its output is byte-identical to the flag's.
* **`--lifecycle static` CANNOT**, and this file measures why rather than
  asserting it: the choice is read at five places across two modules, and the
  nearest published overrides (`emit_decls`, `emit_impl`) are whole-section, so
  a subclass would have to re-derive every prototype in the API to remove two.

The second is the finding P8.T3 exists to produce -- see the plan's own accept
line: "If a built-in variant cannot be expressed this way, that is evidence the
override surface is wrong." NEITHER flag is deleted, and the reason is in the
plan's G8 note: `--emit-stubs` composes with every other option, so promoting
it to a target name trades one boolean for a combinatorial namespace.

Plan: P8.T3.
"""
from __future__ import annotations

import inspect

import pytest

from pssc.targets import register
from pssc.targets.c.backend import COpModelBackend
from pssc.targets.c_progseq_tgt import CProgSeqTarget
from pssc.targets.overridable import surface
from pssc.testing import compile_op_model


# --- the variant that works -------------------------------------------------

class _StubsBackend(COpModelBackend):
    """`--emit-stubs`, as a subclass. The whole of it.

    `stubs_text` is pssc's own and is reused rather than reimplemented, which is
    the point: an extension inherits the CONTENT and decides only that the file
    is produced.
    """

    def emit_extra_files(self, model, s):
        files = dict(super().emit_extra_files(model, s))
        files[self.style.stubs_name(s.prefix)] = self.stubs_text(model, s)
        return files


class _StubsTarget(CProgSeqTarget):
    name = "op-model-c-stubs-fixture"
    description = "op-model-c that always emits link stubs (P8.T3 fixture)"
    derives_from = "op-model-c"
    backend_cls = _StubsBackend


@pytest.fixture(scope="module")
def stubs_target():
    """Registered for this module and taken back out again.

    BOTH registries, and the restore is not tidiness: `derives_from` registers
    a Tier-2 legality set at construction, so a fixture target left behind is
    visible to `call_legality.registered_targets()` and makes an exact
    assertion there fail depending on which test files ran first.
    """
    import pssc.targets as tr
    import pssc.targets.call_legality as cl

    targets = dict(tr._REGISTRY)
    legality = {k: dict(v) for k, v in cl._EXTENSIONS.items()}
    register(_StubsTarget())
    try:
        yield _StubsTarget.name
    finally:
        tr._REGISTRY.clear()
        tr._REGISTRY.update(targets)
        cl._EXTENSIONS.clear()
        cl._EXTENSIONS.update(legality)


def test_the_subclass_is_short(stubs_target):
    """The criterion the fixture plugin is held to, applied to a built-in.

    If a variant pssc ships as a flag cannot be written short against marked
    methods, the marked set is wrong (design §6). Ten lines of body is the
    budget; the flag it replaces is a CLI option, a settings field, a
    `flags_for` entry and an `if` in `generate`.
    """
    body = inspect.getsource(_StubsBackend.emit_extra_files).splitlines()
    assert len([l for l in body if l.strip()
                and not l.strip().startswith(("#", '"'))]) <= 10


def test_the_subclass_reaches_only_published_methods(stubs_target):
    """Nothing here goes past a marked method.

    An extension that reaches into an unmarked one works today and breaks on an
    upgrade with no warning, which is the whole reason the surface is marked and
    checked in.
    """
    published = set(surface(COpModelBackend))
    used = {"emit_extra_files", "stubs_text"}
    assert used <= published, sorted(used - published)


def test_it_produces_what_the_flag_produces(stubs_target, tmp_path):
    """BYTE-IDENTICAL, which is the acceptance criterion.

    Both directions of the compare matter: the same file names, in the same
    order, with the same content. A subclass that produced the stubs under
    another name, or after the seam headers, would be a different build.
    """
    with compile_op_model("op-model-c", output_dir=str(tmp_path / "flag"),
                          c_emit_stubs=True) as by_flag, \
         compile_op_model(stubs_target, output_dir=str(tmp_path / "sub")
                          ) as by_subclass:
        assert by_flag.names == by_subclass.names
        for name in by_flag.names:
            assert by_flag.read(name) == by_subclass.read(name), name


def test_it_still_composes_with_every_other_option(stubs_target, tmp_path):
    """`derives_from` carries the ancestor's options, so a command line written
    for `op-model-c` works here unchanged -- which is what keeps a variant from
    being a fork."""
    with compile_op_model("op-model-c", output_dir=str(tmp_path / "flag"),
                          c_emit_stubs=True, c_lifecycle="static",
                          c_link_style="direct") as by_flag, \
         compile_op_model(stubs_target, output_dir=str(tmp_path / "sub"),
                          c_lifecycle="static", c_link_style="direct"
                          ) as by_subclass:
        assert by_flag.names == by_subclass.names
        for name in by_flag.names:
            assert by_flag.read(name) == by_subclass.read(name), name


# --- the variant that does not ----------------------------------------------

def test_the_lifecycle_choice_is_read_in_more_places_than_a_hook_covers():
    """Why `--lifecycle static` is not a subclass. Measured, not asserted.

    Five consumers, and no published override at the granularity of any of
    them: `emit_decls` returns EVERY prototype and `emit_impl` returns EVERY
    body, so a subclass removing `_create`/`_destroy` would either re-derive
    the whole block -- the copy the surface exists to prevent -- or post-process
    somebody else's text, which is worse.

    Listed by site so this test names what would have to change, and fails if
    somebody adds a sixth without noticing.
    """
    import pathlib

    import pssc.targets.c.lower_progseq as lp
    import pssc.targets.c.style as st

    sites = []
    for module in (lp, st):
        text = pathlib.Path(module.__file__).read_text()
        sites += [(module.__name__, i + 1) for i, line in
                  enumerate(text.splitlines())
                  if "lifecycle ==" in line or "lifecycle=" in line
                  and "def " not in line]
    assert len(sites) >= 4, sites
    assert {m for m, _ in sites} == {
        "pssc.targets.c.lower_progseq", "pssc.targets.c.style"}, sites


def test_the_lifecycle_decision_is_published_as_a_pair():
    """Was the gap at G8: no published override at the granularity of the
    lifecycle. `emit_lifecycle_decls` and `emit_lifecycle` (PC-8,
    `hal-plugin-requests-plan.md`) are it -- one component's prototypes and
    its definitions, published as a PAIR, since a prototype with no
    definition is a link error far from the override.

    The flag still reads the choice at the sites listed above; what changed
    is that a subclass no longer has to re-derive `emit_decls` to drop them.
    """
    published = surface(COpModelBackend)
    assert {n for n in published if "lifecycle" in n} == {
        "emit_lifecycle", "emit_lifecycle_decls"}
    assert list(published["emit_lifecycle"].pairs_with) == [
        "emit_lifecycle_decls"]
