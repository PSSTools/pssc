"""Phase 3 (P3.T6): discovery against a really-installed plugin.

`test_target_discovery.py` injects entry points, which proves the policy but
never touches `importlib.metadata`, a `.dist-info`, or packaging. Those are
where a discovery mechanism actually breaks -- the group name is subtly wrong,
the object reference does not resolve, the metadata is present but the module
is not importable -- and none of it shows up against a fake.

The plugin is installed into a session-scoped temp directory, never into the
developer's environment: a test suite that mutates the environment it runs in
is a test suite people stop running. Marked ``plugin``, and skipped rather than
failed when no installer is available, so a constrained environment loses this
file and nothing else.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.plugin

_PLUGIN_SRC = (Path(__file__).resolve().parents[1] / "plugins"
               / "pssc_fixture_plugin")
_PSSC_SRC = Path(__file__).resolve().parents[2] / "src"


def _install_with_pip(target: Path, src: Path = _PLUGIN_SRC):
    return subprocess.run(
        [sys.executable, "-m", "pip", "install", "--no-deps",
         "--no-build-isolation", "--disable-pip-version-check",
         "--target", str(target), str(src)],
        capture_output=True, text=True)


def _unpack_wheel(proc, dist: Path, target: Path):
    import zipfile
    wheels = sorted(dist.glob("*.whl"))
    if proc.returncode != 0 or not wheels:
        return subprocess.CompletedProcess(
            proc.args, proc.returncode or 1, proc.stdout,
            proc.stderr or "no wheel was produced")
    with zipfile.ZipFile(wheels[-1]) as zf:
        zf.extractall(target)
    return proc


def _install_with_build(target: Path, src: Path = _PLUGIN_SRC):
    """Build a wheel and unpack it -- the same bytes pip would have written.

    Not a shortcut around installation: for a pure-Python wheel, "install" IS
    "extract into a directory on sys.path", `.dist-info/entry_points.txt`
    included. This exists because a venv can legitimately have `build` and
    `setuptools` without `pip`, and the alternative -- a test file that silently
    skips forever -- is worse than either.
    """
    dist = target / "_wheel"
    # Run from the prefix: a `build/` directory in the working directory
    # would be imported as the `build` module.
    proc = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--no-isolation",
         "--outdir", str(dist), str(src)],
        capture_output=True, text=True, cwd=str(target))
    return _unpack_wheel(proc, dist, target)


def _install_with_setuptools(target: Path, src: Path = _PLUGIN_SRC):
    """The PEP 517 hook `build` would call, called directly: for a venv
    with setuptools and wheel but neither pip nor build. On a copy of the
    plugin, since setuptools writes `build/` and `*.egg-info` beside it."""
    import shutil
    work = target / "_src"
    shutil.copytree(src, work, ignore=shutil.ignore_patterns(
        "build", "*.egg-info", "__pycache__"))
    dist = target / "_wheel"
    dist.mkdir()
    proc = subprocess.run(
        [sys.executable, "-c",
         "import sys, setuptools.build_meta as b; b.build_wheel(sys.argv[1])",
         str(dist)], capture_output=True, text=True, cwd=str(work))
    return _unpack_wheel(proc, dist, target)


def install_plugin(src: Path, target: Path) -> Path:
    """Build and install the plugin at ``src`` into ``target``, or skip the
    test when nothing here can (no pip, and no build + setuptools)."""
    if not src.is_dir():
        pytest.skip(f"fixture plugin not present at {src}")
    last = None
    for install in (_install_with_pip, _install_with_build,
                    _install_with_setuptools):
        last = install(target, src)
        if last.returncode == 0:
            return target
    pytest.skip(f"cannot build {src.name} here (needs pip, build or "
                f"setuptools + wheel): {(last.stderr or '').strip()[-400:]}")


@pytest.fixture(scope="session")
def installed_plugin(tmp_path_factory):
    """Build and install the fixture plugin into a throwaway prefix."""
    return install_plugin(_PLUGIN_SRC, tmp_path_factory.mktemp("plugin-prefix"))


@contextlib.contextmanager
def discovering(prefix: Path, monkeypatch):
    """Put a plugin installed at ``prefix`` on this process's path, rerun
    discovery, and put every registry back afterwards."""
    from pssc import targets
    from pssc.targets import call_legality as _cl

    saved = dict(targets._REGISTRY)
    saved_errors = list(targets._PLUGIN_ERRORS)
    saved_latch = targets._discovered
    # The plugin registers its own Tier 2 as an import side effect, and the
    # target registry is not the only global it touches. Missing this leaked
    # `fixture` into the tier-contract tests, which then failed depending on
    # file order -- the exact bug class this fixture exists to prevent.
    saved_ext = {k: dict(v) for k, v in _cl._EXTENSIONS.items()}

    monkeypatch.delenv(targets.NO_PLUGINS_ENV, raising=False)
    monkeypatch.syspath_prepend(str(prefix))
    # importlib.metadata caches its finders against sys.path; a path added
    # after the first scan is invisible without this.
    import importlib
    importlib.invalidate_caches()
    targets.discover(force=True)
    try:
        yield targets
    finally:
        targets._REGISTRY.clear()
        targets._REGISTRY.update(saved)
        targets._PLUGIN_ERRORS.clear()
        targets._PLUGIN_ERRORS.extend(saved_errors)
        targets._discovered = saved_latch
        _cl._EXTENSIONS.clear()
        _cl._EXTENSIONS.update(saved_ext)


@pytest.fixture
def discovered(installed_plugin, monkeypatch):
    """Put the installed plugin on this process's path and rerun discovery."""
    with discovering(installed_plugin, monkeypatch) as targets:
        yield targets


# -- the happy path ----------------------------------------------------------

def test_the_plugin_target_is_discovered_and_listed(discovered):
    assert "fixture" in discovered.list_targets()
    assert not discovered.is_builtin("fixture")
    assert "pssc-fixture-plugin" in discovered.get("fixture").description
    assert discovered.get("fixture-alias") is discovered.get("fixture")
    assert "fixture-alias" not in discovered.list_targets()


def test_the_plugin_target_generates(discovered, tmp_path):
    from pssc import driver
    src = tmp_path / "m.pss"
    src.write_text("component pss_top { action A { } }")
    out = tmp_path / "out"
    res = driver.compile([str(src)], target="fixture", output_dir=str(out),
                         fixture_note="hello")
    assert res.outputs == [out / "fixture.txt"]
    text = (out / "fixture.txt").read_text()
    assert "hello" in text and "pss_top" in text


def test_a_plugin_reads_its_x_options(discovered, tmp_path):
    from pssc import driver
    src = tmp_path / "m.pss"
    src.write_text("component pss_top { action A { } }")
    out = tmp_path / "out"
    driver.compile([str(src)], target="fixture", output_dir=str(out),
                   target_opts=["fixture-style=loud"])
    assert "PSS_TOP" in (out / "fixture.txt").read_text()


def test_a_plugin_declares_its_own_call_legality(discovered):
    from pssc.targets.call_legality import (COMMON, Ctx, classify, renderable,
                                            registered_targets)
    assert "fixture" in registered_targets()
    assert classify("print", context=Ctx.TARGET, target="fixture").ok
    # ...and is held to the Tier 1 contract like any built-in
    assert not (set(COMMON) - renderable("fixture"))


# -- the failure paths -------------------------------------------------------

def test_the_broken_entry_point_is_reported_not_fatal(discovered):
    errs = {e.entry_point: e for e in discovered.plugin_errors()}
    assert "broken" in errs
    assert "the_vendor_sdk" in str(errs["broken"])
    assert "pssc-fixture-plugin 0.1.0" in str(errs["broken"])
    # the working target from the SAME distribution still loaded
    assert "fixture" in discovered.list_targets()


def test_the_shadowing_entry_point_cannot_take_a_builtin(discovered):
    errs = {e.entry_point: e for e in discovered.plugin_errors()}
    assert "shadow" in errs
    assert "op-model-c" in str(errs["shadow"]) and "replaces=" in str(errs["shadow"])
    assert discovered.get("op-model-c").__class__.__name__ == "CProgSeqTarget"


# -- through the command line ------------------------------------------------

def _run_cli(installed_plugin, *argv):
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(installed_plugin), str(_PSSC_SRC), env.get("PYTHONPATH", "")])
    return subprocess.run([sys.executable, "-m", "pssc", *argv],
                          capture_output=True, text=True, env=env)


def test_cli_lists_the_plugin_and_reports_the_failures(installed_plugin):
    """A separate process, so nothing about this depends on the test session's
    already-imported pssc or its patched sys.path."""
    proc = _run_cli(installed_plugin, "targets")
    assert proc.returncode == 0, proc.stderr
    assert "fixture" in proc.stdout
    assert "broken" in proc.stderr and "the_vendor_sdk" in proc.stderr
    assert "shadow" in proc.stderr and "op-model-c" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_cli_no_plugins_env_hides_the_plugin(installed_plugin):
    env_run = _run_cli(installed_plugin, "targets")
    assert "fixture" in env_run.stdout          # ...without the switch

    import pssc.targets as _t
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(installed_plugin), str(_PSSC_SRC), env.get("PYTHONPATH", "")])
    env[_t.NO_PLUGINS_ENV] = "1"
    proc = subprocess.run([sys.executable, "-m", "pssc", "targets"],
                          capture_output=True, text=True, env=env)
    assert proc.returncode == 0
    assert "fixture" not in proc.stdout
    assert proc.stderr.strip() == "", "and no failure block either"
    assert "python" in proc.stdout


def test_the_testing_kit_works_against_an_installed_plugin(discovered):
    """P4.T1's accept criterion, checked where it matters: the kit is for
    plugin authors, so it has to work on a target that is not a built-in."""
    from pssc import testing
    testing.assert_common_tier("fixture")
    with testing.compile_op_model(
            "fixture", sources=testing.op_model_sources(),
            root="dma_engine_c") as out:
        assert out.names == ["fixture.txt"]


# -- a style shipped by a plugin ---------------------------------------------

@pytest.fixture
def styles_discovered(discovered, monkeypatch):
    """The style registry, refreshed against the installed plugin."""
    from pssc.targets import style as st
    saved = dict(st._REGISTRY)
    saved_errors = list(st._STYLE_ERRORS)
    saved_latch = st._discovered
    st.discover(force=True)
    yield st
    st._REGISTRY.clear()
    st._REGISTRY.update(saved)
    st._STYLE_ERRORS.clear()
    st._STYLE_ERRORS.extend(saved_errors)
    st._discovered = saved_latch


def test_the_plugin_style_is_discovered(styles_discovered):
    assert "acme" in styles_discovered.list_styles("op-model-c")
    assert not styles_discovered.is_builtin("op-model-c", "acme")


def test_a_plugin_style_restyles_a_builtin_target(styles_discovered, tmp_path):
    """P5b.T2's accept criterion, and tier A's whole claim: a house style is a
    class and an entry point in a package that does NOT own the backend, and it
    tracks upstream C semantics automatically because it is not a fork."""
    from pssc import testing
    with testing.compile_op_model(
            "op-model-c", output_dir=tmp_path,
            c_style="acme", c_link_style="direct") as out:
        # Header AND impl: the prototypes are in one and the register accesses
        # in the other, and a style that reached only one of them is exactly
        # the partial wiring worth catching.
        text = "\n".join(out.read(n) for n in out.names)

    assert "acme_dma_engine_mem_to_mem_copy(" in text
    assert "ACME_WR32(" in text and "ACME_RD32(" in text
    # ACME supplies its own mechanism, so none of pssc's seam comes along
    import re
    assert not re.search(r"pssc_[rw]\d|pssc_bus\s*\(", text)
    assert not [n for n in out.names if n.startswith("pssc_")]


def test_cli_lists_the_plugin_style(installed_plugin):
    proc = _run_cli(installed_plugin, "targets")
    assert proc.returncode == 0, proc.stderr
    assert "acme" in proc.stdout
    assert "Traceback" not in proc.stderr


# -- the tier-B extension (P6b.T6) -------------------------------------------
#
# The validation for the whole of Phase 6b. A mid-weight extension -- one that
# changes what is GENERATED, not just what things are called -- is written
# against the published override surface from another distribution, and these
# tests are what "the surface is the right one" means: it registers, it
# generates, it stays conforming, its differences are exactly the declared
# ones, and it is SHORT.

_TIER_B = (_PLUGIN_SRC / "pssc_fixture_plugin" / "backend.py")


def test_tier_b_extension_is_registered_with_everything_inherited(discovered):
    """It states four things (name, description, ancestor, backend) and gets
    the rest: options, legality, capabilities, styles."""
    from pssc.targets.call_legality import renderable

    tgt = discovered.get("op-model-acme-c")
    assert not discovered.is_builtin("op-model-acme-c")
    assert renderable("op-model-acme-c") == renderable("op-model-c")
    assert tgt.resolved_target_cfg() == discovered.get("op-model-c").target_cfg
    assert "default" in tgt.available_styles()


def test_tier_b_extension_generates(discovered):
    """All four override kinds, in the output, from an installed plugin."""
    from pssc import testing

    with testing.compile_op_model("op-model-acme-c") as out:
        assert "dma_engine_acme_map.h" in out.names       # the extra file
        header = out.read("dma_engine.h")
        assert "ACME-INTERNAL" in header                  # the inserted section
        assert "acme_dma_engine_init" in header           # the swapped style
        assert "ACME_TRACE(" in out.read("dma_engine.c")  # the wrapped operation


def test_tier_b_extension_is_conforming(discovered):
    """An extension is a target, and is held to the same contract: it renders
    the common tier and it is deterministic."""
    from pssc import testing

    testing.assert_common_tier("op-model-acme-c")
    testing.assert_deterministic("op-model-acme-c")


def test_tier_b_extension_differs_from_its_baseline_only_where_intended(discovered):
    """P6b.T5 applied to P6b.T6 -- the claim an extension of an evolving
    backend can actually make, and the one that keeps holding as pssc moves.

    `decls` and `impl` change because the house style renames every exported
    symbol; the other two are the extension's own additions.
    """
    from pssc import testing

    diffs = testing.assert_differs_from_baseline(
        "op-model-acme-c", "op-model-c",
        expect_changed=["acme_compliance", "decls", "impl", "extra_files"])
    assert diffs["dma_engine.h:acme_compliance"] == "added"
    assert diffs["dma_engine_acme_map.h:extra_files"] == "added"


def test_tier_b_extension_is_small():
    """THE accept criterion (design §6). The claim tier B makes is that a
    house backend is a short file; if this one is not short, the surface is
    wrong and the marked set should change before it ships -- so the number is
    a test, not a note in a review.
    """
    body = [ln for ln in _TIER_B.read_text().splitlines() if ln.strip()]
    assert len(body) < 150, (
        f"the tier-B extension is {len(body)} non-blank lines. Either it is "
        f"doing more than a mid-weight extension should, or the override "
        f"surface is making it copy something pssc should have offered")


def test_tier_b_extension_reaches_past_nothing_unmarked():
    """It may only use the PUBLISHED surface. An extension that has to reach
    for a private name is evidence the surface has a hole in it -- and that is
    the finding this test exists to produce, not a style rule.
    """
    from pssc.targets.c.backend import COpModelBackend
    from pssc.targets.overridable import surface

    text = _TIER_B.read_text()
    published = set(surface(COpModelBackend))
    used = {ln.split("def ", 1)[1].split("(", 1)[0]
            for ln in text.splitlines() if ln.strip().startswith("def ")}
    used |= {name for name in ("style_cls", "body_emitter_cls")
             if f"{name} =" in text}
    # Its own helper methods are not overrides; everything else must be marked.
    own = {"emit_compliance", "symbol"}
    assert (used - own) <= published, (
        f"the extension overrides {sorted(used - own - published)}, which pssc "
        f"does not publish as overridable")
    assert "._" not in text.replace("self._", ""), (
        "the extension reaches for a private name")


# -- the tier-3 target (PD.T4) -----------------------------------------------
#
# The guide's Level 3 example, and the claim it makes: a plugin can bring its
# own output shape while pssc keeps the elaboration. Nothing else in this file
# subclasses `OpModelTarget` from another distribution -- the tier-B extension
# subclasses a built-in TARGET, which inherits an emitter and so never asks
# whether the base class is usable on its own.

def test_tier_3_target_emits_over_psss_elaborated_model(discovered):
    from pssc import testing

    with testing.compile_op_model("api-listing") as out:
        assert out.names == ["api_listing.txt"]
        lines = out.read("api_listing.txt").splitlines()

    assert "dma_engine_c  mem_to_mem_copy(channel, src, dst, nbytes)" in lines
    # The listing describes the SAME API the C target generates, because both
    # read one elaboration -- which is the reason to write a target rather than
    # a script that reads the generated header.
    with testing.compile_op_model("op-model-c") as c_out:
        header = c_out.read("dma_engine.h")
    for ln in lines:
        assert f"{ln.split('  ')[1].split('(')[0]}(" in header


def test_tier_3_target_inherits_options_and_legality_from_its_ancestor(discovered):
    """`derives_from` on a target that shares no emitter with its ancestor.

    Its own `--root` comes from `OpModelTarget`, but `print` being legal does
    not: without the ancestor a model calling it would be refused by a gate
    belonging to a target that emits a text file and cares about neither.
    """
    from pssc import testing
    from pssc.targets.call_legality import renderable

    assert renderable("api-listing") == renderable("op-model-c")
    with testing.compile_op_model("api-listing", api_listing_sep=" | ") as out:
        assert " | mem_to_mem_copy(" in out.read("api_listing.txt")
    testing.assert_common_tier("api-listing")
    testing.assert_deterministic("api-listing")
