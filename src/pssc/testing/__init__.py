"""Test helpers for people writing pssc target plugins.

Public and supported. Everything a plugin author needs to write their first
tests lives here, so they do not have to read pssc's own test tree and take a
dependency on its private fixtures -- which is what actually happens otherwise,
and which turns pssc's internal refactors into other people's broken builds.

    from pssc.testing import assert_common_tier, assert_deterministic

    def test_my_target_is_conforming():
        assert_common_tier("acme-c")
        assert_deterministic("acme-c", acme_c_style="terse")

WHAT THE BUNDLED MODEL COVERS, AND WHAT IT DOES NOT. `op_model_sources()`
returns a small self-contained PSS model shipped as package data: register
groups, a nested register array with an affine offset fold, packed value
structs, read-modify-write, a do-while poll, and a constructor taking an
`addr_handle_t`. That is enough to exercise the register model, the body
lowering and the export API.

It does NOT have a component tree, a channel, an enum or a declared import.
pssc's own suite covers those against the real WB DMA model, which is not
shipped: it is 176 KB of one specific device, kept in sync with an upstream
copy by a script, and putting it in every wheel would make a vendored artifact
part of the public surface. A plugin whose backend handles those should point
`compile_op_model(sources=...)` at a model of its own that does -- and if this
kit ought to bundle a richer one, that is a real request rather than something
to fake by shipping the device model.
"""
from __future__ import annotations

import filecmp
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

__all__ = [
    "model_dir", "op_model_sources", "op_model_root",
    "compile_op_model", "CompileOutcome",
    "assert_common_tier", "assert_deterministic", "golden_dir_compare",
    "assert_dirs_match", "generated_sections", "diff_from_baseline",
    "assert_differs_from_baseline",
]

#: The root component of the bundled model, as `--root` wants it.
op_model_root = "dma_engine_c"

#: The bundled model's files, IN DEPENDENCY ORDER. Not sorted and not
#: discovered by walking the directory: PSS resolves against previously
#: processed source units, so a file presented before what it references is
#: left unresolved while the front end still reports zero errors.
_MODEL_FILES = ("dma_regs.pss", "dma_engine.pss")


def model_dir() -> Path:
    """Directory of the bundled PSS model."""
    from .. import resources
    from importlib.resources import files
    path = Path(str(files("pssc.testing") / "models"))
    if not path.is_dir():
        raise resources.ResourceError(
            f"pssc.testing ships no model: expected {path}. Check pssc's "
            f"package-data configuration for pssc.testing/models/*.pss")
    return path


def op_model_sources() -> List[str]:
    """The bundled model's source paths, in dependency order."""
    d = model_dir()
    out = []
    for name in _MODEL_FILES:
        p = d / name
        if not p.is_file():
            from .. import resources
            raise resources.ResourceError(
                f"'{name}' is missing from {d}; the bundled test model is "
                f"incomplete")
        out.append(str(p))
    return out


# -- compiling ---------------------------------------------------------------

class CompileOutcome:
    """What :func:`compile_op_model` produced.

    ``outputs`` is the path list the target returned, **in the order it
    returned them** -- that order is a compilation order for some targets, so
    it is carried rather than sorted.
    """

    def __init__(self, outputs: Sequence[Path], out_dir: Path,
                 target: str, tempdir: Optional[str] = None):
        self.outputs = [Path(p) for p in outputs]
        self.out_dir = Path(out_dir)
        self.target = target
        self._tempdir = tempdir

    @property
    def names(self) -> List[str]:
        """Output basenames, in emission order."""
        return [p.name for p in self.outputs]

    def read(self, name: str) -> str:
        """Text of one produced file, by basename."""
        for p in self.outputs:
            if p.name == name:
                return p.read_text()
        raise KeyError(
            f"{self.target} produced no file named '{name}'; it produced: "
            f"{', '.join(self.names) or '(nothing)'}")

    def cleanup(self) -> None:
        """Remove the temporary output directory, if this call made one."""
        if self._tempdir:
            shutil.rmtree(self._tempdir, ignore_errors=True)
            self._tempdir = None

    def __enter__(self) -> "CompileOutcome":
        return self

    def __exit__(self, *exc) -> None:
        self.cleanup()


def compile_op_model(target: str, *, sources: Optional[Sequence[str]] = None,
                     root: Optional[str] = None,
                     output_dir=None, **opts: Any) -> CompileOutcome:
    """Compile a model with ``target`` and return what it wrote.

    Defaults to the bundled model and its root, so the smallest useful call is
    ``compile_op_model("acme-c")``. ``**opts`` become attributes on the options
    namespace, which is how a target's own options are set without building a
    CLI argv -- ``compile_op_model("acme-c", acme_c_style="terse")``.

    With no ``output_dir`` a temporary one is created; use the result as a
    context manager, or call ``cleanup()``, to remove it.
    """
    from .. import driver

    tempdir = None
    if output_dir is None:
        tempdir = tempfile.mkdtemp(prefix="pssc-testing-")
        output_dir = tempdir

    params: Dict[str, Any] = dict(opts)
    params.setdefault("progseq_root", root or op_model_root)

    try:
        res = driver.compile(list(sources or op_model_sources()),
                             target=target, output_dir=str(output_dir),
                             **params)
    except BaseException:
        if tempdir:
            shutil.rmtree(tempdir, ignore_errors=True)
        raise
    return CompileOutcome(res.outputs, output_dir, target, tempdir)


# -- assertions --------------------------------------------------------------

def assert_common_tier(target: str) -> None:
    """Assert ``target`` renders every Tier-1 (`COMMON`) call.

    The contract pssc's built-ins are held to, available to a plugin as one
    call. A target that cannot render COMMON is not a conforming target: every
    model is written assuming those calls work everywhere, so the failure lands
    on the model author as a bogus "unsupported call" in code that is correct.

    A declared subset target (``register_extension(..., subset=True)``) is held
    to COMMON less the names it narrowed, each with its reason.
    """
    from ..targets.call_legality import COMMON, renderable, subset_of_common
    missing = sorted(set(COMMON) - renderable(target)
                     - subset_of_common(target))
    assert not missing, (
        f"target '{target}' does not render the common tier: {missing}. "
        f"Declare them with pssc.targets.call_legality.register_extension, or "
        f"implement them -- a target may extend COMMON, and shrinks it only as a "
        f"declared subset (register_extension(..., subset=True))")


def assert_deterministic(target: str, *, runs: int = 2, **opts: Any) -> None:
    """Assert two compiles of the same input produce byte-identical output.

    Nondeterminism here is not cosmetic. It defeats every downstream
    incremental build (dv-flow's memento, `make`, a git diff on checked-in
    generated code), and it is almost always a set or a dict iterated without
    an order -- which means the output is also unstable across Python versions,
    not just across runs.
    """
    outs = []
    try:
        for _ in range(max(2, runs)):
            outs.append(compile_op_model(target, **opts))

        first = outs[0]
        for other in outs[1:]:
            assert first.names == other.names, (
                f"target '{target}' is not deterministic: it produced "
                f"{first.names} then {other.names}. The path list is a "
                f"compilation order for some targets, so its ORDER matters too")
            diffs = golden_dir_compare(other.out_dir, first.out_dir)
            assert not diffs, (
                f"target '{target}' is not deterministic; two compiles of the "
                f"same input differ:\n  " + "\n  ".join(diffs))
    finally:
        for o in outs:
            o.cleanup()


# -- the differential test ---------------------------------------------------
#
# The test an EXTENSION needs and a golden snapshot cannot give it. An
# extension of pssc's C backend has no stable output to freeze -- upstream
# changes it -- so the checkable claim is not "my output is this" but "my
# output is my baseline's, differing HERE and nowhere else". That claim keeps
# holding as pssc moves, and it fails in both directions: on a change nobody
# declared, and on a declared change that did not happen.

def generated_sections(target: str, *, sources: Optional[Sequence[str]] = None,
                       root: Optional[str] = None, **opts: Any) -> Dict[str, str]:
    """What ``target`` WOULD generate, keyed ``"<file>:<section>"``.

    Nothing is written. ``{}`` from a target that does not assemble from named
    sections, which is not an error -- see `OpModelTarget.sections`.
    """
    import argparse

    from .. import driver, targets as _targets

    tgt = _targets.get(target)
    if not hasattr(tgt, "sections"):
        return {}
    ns = argparse.Namespace(**dict(opts))
    ns.progseq_root = getattr(ns, "progseq_root", None) or root or op_model_root
    ns.output_dir = getattr(ns, "output_dir", None) or "."
    ctx = driver.translate(list(sources or op_model_sources()),
                           prelude=tgt.prelude(ns))
    return dict(tgt.sections(tgt.build_model(ctx, ns), ns))


def _file_texts(target: str, **kw: Any) -> Dict[str, str]:
    """Fallback attribution for a target with no section map: whole files."""
    with compile_op_model(target, **kw) as out:
        return {p.name: p.read_text() for p in out.outputs if p.is_file()}


def diff_from_baseline(target: str, baseline: str, **kw: Any) -> Dict[str, str]:
    """``{key: "added"|"removed"|"changed"}`` for every way ``target``'s output
    differs from ``baseline``'s, keyed by section (or by file, when the targets
    publish no sections)."""
    mine = generated_sections(target, **kw)
    theirs = generated_sections(baseline, **kw)
    if bool(mine) != bool(theirs):
        with_, without = ((target, baseline) if mine else (baseline, target))
        raise ValueError(
            f"'{with_}' publishes a section map and '{without}' does not, so "
            f"the two are not comparable: every one of {with_}'s sections "
            f"would be reported as a difference. A baseline is the target an "
            f"extension DERIVES from, which assembles the same way it does")
    if not mine:
        mine, theirs = _file_texts(target, **kw), _file_texts(baseline, **kw)

    out: Dict[str, str] = {}
    for key in sorted(set(mine) | set(theirs)):
        if key not in theirs:
            out[key] = "added"
        elif key not in mine:
            out[key] = "removed"
        elif mine[key] != theirs[key]:
            out[key] = "changed"
    return out


def _section_name(key: str) -> str:
    """`"wb_dma.h:impl"` -> `"impl"`. A key with no colon is its own name."""
    return key.split(":", 1)[1] if ":" in key else key


def assert_differs_from_baseline(target: str, baseline: str, *,
                                 expect_changed: Sequence[str],
                                 **kw: Any) -> Dict[str, str]:
    """Assert ``target`` differs from ``baseline`` in exactly the named places.

    ``expect_changed`` names SECTIONS (`"impl"`, `"acme_compliance"`,
    `"extra_files"`), not files: an extension declares what it set out to
    change, and everything else is asserted to be untouched.

    Fails in BOTH directions, which is the point:

      * a difference in a section nobody declared -- the extension changed
        something it did not mean to, which is how a tier-B extension breaks
        an API it only meant to decorate;
      * a declared section that did NOT differ -- the override stopped taking
        effect (an upstream rename, a signature change, a typo in a section
        name) and the extension is silently generating its baseline. A stale
        expectation is exactly as misleading as a missing one, and only this
        half catches it.

    Returns the difference map, so a caller can assert further.
    """
    diffs = diff_from_baseline(target, baseline, **kw)
    declared = set(expect_changed)
    changed = {_section_name(k) for k in diffs}

    undeclared = sorted(k for k in diffs if _section_name(k) not in declared)
    assert not undeclared, (
        f"'{target}' differs from '{baseline}' in {len(undeclared)} place(s) "
        f"it did not declare:\n"
        + "\n".join(f"  {k} ({diffs[k]})" for k in undeclared)
        + f"\n\nDeclared: {sorted(declared) or '(nothing)'}. Either the change "
        f"is a mistake, or add the section to expect_changed.\n"
        + _diff_text(target, baseline, undeclared[0], **kw))

    missing = sorted(declared - changed)
    assert not missing, (
        f"'{target}' declares it changes {missing}, and those sections are "
        f"IDENTICAL to '{baseline}'. Either the override stopped taking effect "
        f"-- an upstream rename is the usual cause -- or the expectation is "
        f"stale. Sections that did differ: {sorted(changed) or '(none)'}")
    return diffs


def _diff_text(target: str, baseline: str, key: str, **kw: Any) -> str:
    """A unified diff of one differing section. For the failure message: the
    section NAME says where, and this says what."""
    import difflib

    mine = generated_sections(target, **kw).get(key, "")
    theirs = generated_sections(baseline, **kw).get(key, "")
    lines = list(difflib.unified_diff(
        theirs.splitlines(), mine.splitlines(),
        fromfile=f"{baseline} {key}", tofile=f"{target} {key}", lineterm=""))
    return "\n".join(lines[:60]) or f"(no textual diff for {key})"


def golden_dir_compare(actual, expected) -> List[str]:
    """Compare two directory trees; return human-readable differences.

    Returns a list rather than raising, so a caller can build a report over
    several targets instead of stopping at the first. Empty means the trees
    match. Comparison is byte-exact and covers files present in one tree and
    not the other -- a missing file is the difference most worth catching, and
    a comparison that only walks the intersection cannot see it.
    """
    actual, expected = Path(actual), Path(expected)
    diffs: List[str] = []

    def rel_files(root: Path):
        if not root.is_dir():
            return set()
        return {str(Path(dirpath, f).relative_to(root))
                for dirpath, _dirs, files in os.walk(root) for f in files}

    if not actual.is_dir():
        return [f"missing directory: {actual}"]
    if not expected.is_dir():
        return [f"missing directory: {expected}"]

    a_files, e_files = rel_files(actual), rel_files(expected)
    for name in sorted(e_files - a_files):
        diffs.append(f"missing from {actual}: {name}")
    for name in sorted(a_files - e_files):
        diffs.append(f"unexpected in {actual}: {name}")
    for name in sorted(a_files & e_files):
        if not filecmp.cmp(actual / name, expected / name, shallow=False):
            diffs.append(f"differs: {name}")
    return diffs


def assert_dirs_match(actual, expected) -> None:
    """:func:`golden_dir_compare`, as an assertion."""
    diffs = golden_dir_compare(actual, expected)
    assert not diffs, (f"{actual} does not match {expected}:\n  "
                       + "\n  ".join(diffs))
