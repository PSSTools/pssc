"""Phase 4 (P4.T1): the public test kit.

A plugin author's first three tests should not require reading pssc's own test
tree -- because what actually happens then is that they import a private
fixture, and pssc's next internal refactor breaks their build.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from pssc import testing
from pssc.targets.op_model import OpModelTarget


# -- the bundled model -------------------------------------------------------

def test_the_model_ships_and_is_in_dependency_order():
    srcs = testing.op_model_sources()
    assert [Path(s).name for s in srcs] == ["dma_regs.pss", "dma_engine.pss"]
    assert all(Path(s).is_file() for s in srcs)
    # Order is not cosmetic: PSS resolves against previously processed source
    # units, so the engine after the registers. A sorted or walked list would
    # silently produce a different model with zero reported errors.


# There used to be a drift guard here, comparing the package copy against
# `examples/export/programming_seqs`. There is nothing left to compare: the
# example tree was untracked, never made it into the repository, and the model
# now lives ONLY as package data under `src/pssc/testing/models/`. The guard
# existed because there were two copies; the right way to keep two copies in
# step turned out to be to stop having two. It skipped rather than failed when
# the tree went missing, so it reported "guarded" for as long as the thing it
# guarded did not exist -- which is why it is deleted rather than left skipping.


def test_the_wb_dma_model_is_deliberately_not_shipped():
    """Recorded as a decision, not left implicit: the kit bundles a ~12 KB
    model, not the 176 KB device model, which is a vendored copy kept in sync
    by a script and has no business in every wheel."""
    assert not (testing.model_dir() / "wb_dma_c.pss").exists()
    assert sum(p.stat().st_size for p in testing.model_dir().glob("*.pss")) < 64_000


# -- compile_op_model --------------------------------------------------------

def test_compile_op_model_defaults_to_the_bundled_model(tmp_path):
    with testing.compile_op_model("op-model-c", output_dir=tmp_path) as out:
        assert out.names[0] == "dma_engine.h"
        assert "dma_engine_mem_to_mem_copy" in out.read("dma_engine.h")


def test_compile_op_model_makes_and_cleans_a_temp_dir():
    out = testing.compile_op_model("op-model-c")
    d = out.out_dir
    assert d.is_dir() and out.outputs
    out.cleanup()
    assert not d.exists()


def test_compile_op_model_cleans_up_when_the_target_fails():
    """A failed compile must not leave a temp tree behind -- and must not
    swallow the diagnostic either."""
    with pytest.raises(Exception):
        testing.compile_op_model("op-model-c", root="no_such_component_c")


def test_target_options_pass_through(tmp_path):
    with testing.compile_op_model("op-model-c", output_dir=tmp_path,
                                  c_prefix="acme") as out:
        assert "acme.h" in out.names


def test_read_names_the_files_it_did_produce(tmp_path):
    with testing.compile_op_model("op-model-c", output_dir=tmp_path) as out:
        with pytest.raises(KeyError) as exc:
            out.read("nope.h")
        assert "dma_engine.h" in str(exc.value)


# -- assertions --------------------------------------------------------------

@pytest.mark.parametrize("target", ["op-model-sv", "op-model-c", "op-model-cpp"])
def test_assert_common_tier_passes_for_the_builtins(target):
    testing.assert_common_tier(target)


def test_assert_common_tier_fails_for_a_target_that_shrinks_common(monkeypatch):
    import pssc.targets.call_legality as cl
    saved = {k: dict(v) for k, v in cl._EXTENSIONS.items()}
    try:
        common_name = next(iter(cl.COMMON))
        # Bypass register_extension, which refuses this at the point of the
        # mistake -- the point here is that the ASSERTION also catches it, for
        # a target that reached the same state some other way.
        cl._EXTENSIONS["x-shrunk"] = {
            common_name: cl.Entry(common_name, cl.Disposition.UTILITY,
                                  cl.BOTH, unsupported="nope")}
        with pytest.raises(AssertionError) as exc:
            testing.assert_common_tier("x-shrunk")
        assert common_name in str(exc.value)
        assert "never shrink" in str(exc.value)
    finally:
        cl._EXTENSIONS.clear()
        cl._EXTENSIONS.update(saved)


@pytest.mark.parametrize("target", ["op-model-sv", "op-model-c", "op-model-cpp"])
def test_assert_deterministic_passes_for_the_builtins(target):
    testing.assert_deterministic(target)


def test_assert_deterministic_fails_for_a_nondeterministic_target(registered):
    counter = {"n": 0}

    class _Flaky(OpModelTarget):
        name = "x-flaky"
        language = "text"
        legality_target = "op-model-c"

        def emit(self, model, opts):
            counter["n"] += 1
            p = model.out_dir / "out.txt"
            p.write_text(f"run {counter['n']}\n")
            return [p]

    registered(_Flaky())
    with pytest.raises(AssertionError) as exc:
        testing.assert_deterministic("x-flaky")
    assert "not deterministic" in str(exc.value)
    assert "differs: out.txt" in str(exc.value)


def test_assert_deterministic_catches_a_reordered_path_list(registered):
    """Content-identical but ORDER-unstable. The returned list is a
    compilation order for some targets, so this is a real failure and a
    set-based comparison would miss it."""
    calls = {"n": 0}

    class _Shuffled(OpModelTarget):
        name = "x-shuffled"
        language = "text"
        legality_target = "op-model-c"

        def emit(self, model, opts):
            calls["n"] += 1
            a = model.out_dir / "a.txt"
            b = model.out_dir / "b.txt"
            a.write_text("a")
            b.write_text("b")
            return [a, b] if calls["n"] % 2 else [b, a]

    registered(_Shuffled())
    with pytest.raises(AssertionError) as exc:
        testing.assert_deterministic("x-shuffled")
    assert "ORDER" in str(exc.value)


# -- golden_dir_compare ------------------------------------------------------

def test_golden_dir_compare_reports_every_kind_of_difference(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    (a / "sub").mkdir(parents=True)
    (b / "sub").mkdir(parents=True)
    (a / "same.txt").write_text("x")
    (b / "same.txt").write_text("x")
    (a / "sub" / "changed.txt").write_text("one")
    (b / "sub" / "changed.txt").write_text("two")
    (a / "extra.txt").write_text("e")
    (b / "gone.txt").write_text("g")

    diffs = testing.golden_dir_compare(a, b)
    assert any("missing from" in d and "gone.txt" in d for d in diffs)
    assert any("unexpected in" in d and "extra.txt" in d for d in diffs)
    assert any(d.startswith("differs:") and "changed.txt" in d for d in diffs)
    assert not any("same.txt" in d for d in diffs)


def test_golden_dir_compare_returns_empty_for_identical_trees(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / "f.txt").write_text("same")
    assert testing.golden_dir_compare(a, b) == []
    testing.assert_dirs_match(a, b)


def test_golden_dir_compare_names_a_missing_tree(tmp_path):
    diffs = testing.golden_dir_compare(tmp_path / "nope", tmp_path)
    assert len(diffs) == 1 and "missing directory" in diffs[0]


@pytest.fixture
def registered():
    """Register throwaway targets and take them back out.

    BOTH registries: a target with `derives_from` registers call-legality
    entries under its own name at construction, and leaving those behind makes
    `test_call_legality.py` fail somewhere else in the run.
    """
    from pssc import targets
    from pssc.targets import call_legality as cl
    added = []
    saved_legality = {k: dict(v) for k, v in cl._EXTENSIONS.items()}

    def add(target):
        targets.register(target)
        added.append(target.name)
        return target

    yield add
    for name in added:
        targets._REGISTRY.pop(name, None)
    cl._EXTENSIONS.clear()
    cl._EXTENSIONS.update(saved_legality)


# -- the differential test (P6b.T5) -----------------------------------------
#
# An extension of pssc's C backend has no stable output to freeze -- upstream
# moves it -- so its checkable claim is "my baseline's output, differing HERE
# and nowhere else". These tests are on the helper that checks that claim.

def _acme(**attrs):
    """A derived target that changes exactly one named section."""
    from pssc.targets.c.backend import COpModelBackend
    from pssc.targets.c_progseq_tgt import CProgSeqTarget
    from pssc.targets.sections import Section, insert_after

    class _Backend(COpModelBackend):
        def header_sections(self, model, s):
            return insert_after(super().header_sections(model, s), "banner",
                                Section("acme_note",
                                        lambda m, s: ["/* ACME */", ""]))

    return type("_T", (CProgSeqTarget,), dict(
        name="x-acme", derives_from="op-model-c",
        backend_cls=_Backend, **attrs))()


def test_generated_sections_attributes_text_to_a_section(registered):
    registered(_acme())
    secs = testing.generated_sections("x-acme")
    assert "dma_engine.h:acme_note" in secs
    assert secs["dma_engine.h:acme_note"] == "/* ACME */\n"
    # ...and it wrote nothing: the map is what WOULD be generated.
    assert not Path("dma_engine.h").exists()


def test_differential_passes_when_the_declared_change_is_the_only_one(registered):
    registered(_acme())
    diffs = testing.assert_differs_from_baseline(
        "x-acme", "op-model-c", expect_changed=["acme_note"])
    assert diffs == {"dma_engine.h:acme_note": "added"}


def test_differential_detects_unexpected_change(registered):
    """The failure a tier-B extension is most likely to cause: it decorated
    something and moved an API it never meant to touch."""
    registered(_acme())
    with pytest.raises(AssertionError) as exc:
        testing.assert_differs_from_baseline(
            "x-acme", "op-model-c", expect_changed=[])
    msg = str(exc.value)
    assert "did not declare" in msg
    assert "dma_engine.h:acme_note (added)" in msg
    assert "/* ACME */" in msg          # the diff, not just the section name


def test_differential_detects_missing_declared_change(registered):
    """The other direction, and the one a one-sided check misses entirely: the
    override stopped taking effect and the extension is silently generating
    its baseline."""
    registered(_acme())
    with pytest.raises(AssertionError) as exc:
        testing.assert_differs_from_baseline(
            "x-acme", "op-model-c", expect_changed=["acme_note", "handles"])
    msg = str(exc.value)
    assert "['handles']" in msg
    assert "IDENTICAL" in msg


def test_differential_of_a_target_against_itself_is_empty(registered):
    """The control: no differences at all, so the machinery is not reporting
    noise that would make every extension's expectations grow."""
    assert testing.diff_from_baseline("op-model-c", "op-model-c") == {}


def test_differential_falls_back_to_files_without_a_section_map():
    """A target that publishes no sections is still comparable -- at file
    granularity, which is what `op-model-sv` and `op-model-cpp` get."""
    diffs = testing.diff_from_baseline("op-model-cpp", "op-model-sv")
    assert diffs and all(":" not in k for k in diffs)
    assert diffs.get("dma_engine.hpp") == "added"


def test_differential_refuses_two_targets_of_different_granularity():
    """Comparing a sectioned backend with an unsectioned one would report
    every section as a difference -- a result that looks like a diff and means
    nothing."""
    with pytest.raises(ValueError, match="not comparable"):
        testing.diff_from_baseline("op-model-c", "op-model-cpp")
