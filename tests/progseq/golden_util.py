"""Byte-exact snapshots of the operation-model backends' output.

The extension work (docs/design/generator-style-extensions-plan.md) has three phases
that claim "no change in generated output" while moving most of the C backend
around. That claim is only worth something if a machine checks it, which is
what this is for.

What a snapshot fixes, and why:

* **File content, byte for byte.** A refactor that reflows one line of
  generated C has changed the artifact a customer compiles.
* **The returned path list, in order.** `generate()` returns the compilation
  order a build system consumes (`dvflow.classify_outputs` preserves it, and
  `progseq_gen.py` has a comment explaining why the core package must come
  first). Comparing content alone would let a reordering through, and a
  reordering is a real regression.

Two things are deliberately *not* stored verbatim:

* The generator version in each banner is normalised out (`_normalise`), so a
  version bump is not a seven-config diff.
* A core seam header the target copies out of `src/pssc/share/` is recorded as
  a pointer to its source rather than as a second copy. The check is stronger
  than storing a copy would be -- it asserts the shipped bytes arrived
  unmodified -- and it keeps the golden tree from carrying a stale duplicate of
  a file that lives in the repo already.

Snapshots prove *sameness*, never correctness. A wrong address frozen into a
golden file stays green forever. Phases that touch address computation must
run the behavioural tests (`test_op_model_behaviour_c.py`,
`test_build_wb_dma_c.py`) as well -- see the plan, §4.2.

Regenerate with `scripts/regen_golden.py` (which refuses to run without
`PSSC_GOLDEN_REGEN=1`), never by hand.
"""
from __future__ import annotations

import difflib
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

from .op_model import op_model_sources, small_model_dir, small_model_sources

_HERE = Path(__file__).resolve().parent

#: Where the frozen snapshots live, one directory per config.
GOLDEN_ROOT = _HERE / "golden"

#: The shipped core seam sources. A generated output that is a byte-identical
#: copy of one of these is recorded as a pointer -- see the module docstring.
SHARE_ROOT = (_HERE / ".." / ".." / "src" / "pssc" / "share").resolve()

#: Separates an output name from the shipped file it is a copy of, in MANIFEST.
_PTR = " -> "

#: MANIFEST is the ordered path list; `files/` holds the generated content.
_MANIFEST = "MANIFEST"
_FILES = "files"


@dataclass(frozen=True)
class Config:
    """One frozen generator configuration.

    ``argv`` is spelled as command-line options rather than as a namespace on
    purpose: it is what a user would type, it goes through the same parser the
    CLI builds (defaults included), and it stays readable in a failure message.
    """

    name: str
    target: str
    argv: Tuple[str, ...] = ()
    #: Why this config is in the set -- printed when it fails.
    rationale: str = ""
    #: Which model to generate from. `"op-model"` is the real WB DMA operation
    #: model; `"small"` is `SMALL_MODEL`, for a config that needs a narrower one.
    model: str = "op-model"
    root: str = "wb_dma_c"

    def sources(self) -> List[str]:
        return op_model_sources() if self.model == "op-model" else list(SMALL_SOURCES)


#: The two-file model the pre-existing C/C++/SV progseq tests use.
#:
#: No config uses it today -- all seven run on the real operation model, C++
#: included since the backend was brought up to it. Kept because a config that
#: needs a deliberately narrow model should reach for this rather than invent
#: another one.
#: It is package data under `src/pssc/testing/`, not a path in the test tree --
#: see `op_model.small_model_dir`.
SMALL_MODEL = Path(small_model_dir())
SMALL_SOURCES = tuple(small_model_sources())


#: The frozen set. Chosen to cover every code path the later refactors touch;
#: `c-accessors` and `c-fn-static` are here specifically because they are the
#: least-exercised C paths and the ones a bus-access refactor is most likely to
#: break silently.
CONFIGS: Tuple[Config, ...] = (
    Config("sv-named", "op-model-sv", ("--sv-reg-fields", "named"),
           "the default SV spelling: write_field(<FIELD_CONST>, v)"),
    Config("sv-folded", "op-model-sv", ("--sv-reg-fields", "folded"),
           "the collapsed (mask, value) SV spelling the C target consumes"),
    Config("c-vtable", "op-model-c", (),
           "C defaults: vtable seam, bitfields, malloc lifecycle, .h + .c"),
    Config("c-mmio-hdr", "op-model-c", ("--link-style", "mmio",
                                        "--header-only"),
           "header-only C against a directly-mapped device -- every body, "
           "layout and accessor `static inline` in one file, and no .c"),
    Config("c-mmio", "op-model-c", ("--link-style", "mmio"),
           "the same device SPLIT: mmio no longer implies header-only, and "
           "this is what the firmware build now gets -- interface in the .h, "
           "layouts + accessors + bodies in the .c"),
    Config("c-fn-static", "op-model-c",
           ("--mem-access", "functions", "--lifecycle", "static",
            "--link-style", "direct"),
           "no heap, no pointer dereference: the bare-metal shape"),
    Config("c-accessors", "op-model-c", ("--reg-style", "accessors",
                                         "--emit-stubs"),
           "shift/mask register access instead of bitfields, plus the stub file"),
    Config("cpp-virtual", "op-model-cpp", (),
           "C++ defaults: single header, virtual dispatch"),
    Config("py-default", "op-model-py", (),
           "Python defaults: one module, generated import-API Protocol, "
           "folded accessors"),
    Config("py-async", "op-model-py", ("--py-await", "async"),
           "the async form: async def throughout, awaited seam, yield_() and a "
           "blocking channel -- and HAVE_EVENT_WAIT true, so this is a "
           "different model, not a restyling of the one above"),
)

CONFIG_BY_NAME: Dict[str, Config] = {c.name: c for c in CONFIGS}


# -- running ----------------------------------------------------------------

def run_config(cfg: Config, out_dir) -> List[Path]:
    """Generate ``cfg`` into ``out_dir``; return the produced paths, in order.

    Goes through `cli.build_parser()` so the snapshot reflects the option
    *defaults* a user gets, not a hand-built namespace that could drift from
    them.
    """
    from pssc import cli, driver

    out_dir = Path(str(out_dir))
    sources = cfg.sources()
    parser = cli.build_parser()
    ns = parser.parse_args(
        ["compile", *sources, "-t", cfg.target,
         "-o", str(out_dir), "--root", cfg.root, *cfg.argv])
    res = driver.compile(sources, target=cfg.target, opts=ns)
    assert res.outputs, f"{cfg.name}: {cfg.target} produced no files"
    return list(res.outputs)


# -- normalisation ----------------------------------------------------------

#: `Generated by pssc 0.1.0 (c-progseq)` -- the one moving part in the output.
_VERSION_RE = re.compile(r"(Generated by pssc )\S+")


def _normalise(text: str) -> str:
    """Remove the generator version. Applied to both sides of every compare."""
    return _VERSION_RE.sub(r"\1<VERSION>", text)


# -- manifest ---------------------------------------------------------------

def _share_origin(path: Path) -> str:
    """`share/...` if *path* is a byte-identical copy of a shipped file, else ""."""
    data = path.read_bytes()
    for candidate in SHARE_ROOT.rglob(path.name):
        if candidate.read_bytes() == data:
            return "share/" + candidate.relative_to(SHARE_ROOT).as_posix()
    return ""


def build_manifest(paths: Sequence[Path], out_dir) -> List[str]:
    """The ordered output list: one line per file, copies noted as pointers."""
    out_dir = Path(str(out_dir)).resolve()
    lines = []
    for p in paths:
        rel = Path(p).resolve().relative_to(out_dir).as_posix()
        origin = _share_origin(Path(p))
        lines.append(f"{rel}{_PTR}{origin}" if origin else rel)
    return lines


def _split_manifest(lines: Sequence[str]) -> List[Tuple[str, str]]:
    out = []
    for ln in lines:
        rel, _, origin = ln.partition(_PTR)
        out.append((rel, origin))
    return out


def _read_manifest(cfg_dir: Path) -> List[str]:
    return (cfg_dir / _MANIFEST).read_text().splitlines()


# -- writing ----------------------------------------------------------------

def write_golden(cfg: Config, paths: Sequence[Path], out_dir) -> Path:
    """(Re)write ``golden/<cfg.name>/`` from a fresh generation. Regen only."""
    cfg_dir = GOLDEN_ROOT / cfg.name
    if cfg_dir.exists():
        shutil.rmtree(cfg_dir)
    (cfg_dir / _FILES).mkdir(parents=True)

    manifest = build_manifest(paths, out_dir)
    (cfg_dir / _MANIFEST).write_text("\n".join(manifest) + "\n")

    for p, (rel, origin) in zip(paths, _split_manifest(manifest)):
        if origin:
            continue        # recorded as a pointer; the shipped file is the copy
        dst = cfg_dir / _FILES / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(_normalise(Path(p).read_text()))
    return cfg_dir


# -- checking ---------------------------------------------------------------

#: Diffs longer than this are truncated -- past a few screens the diff stops
#: being the useful artifact and the reader wants the regen script instead.
_MAX_DIFF_LINES = 60


def _diff(expected: str, actual: str, label: str) -> str:
    lines = list(difflib.unified_diff(
        expected.splitlines(), actual.splitlines(),
        fromfile=f"golden/{label}", tofile=f"generated/{label}", lineterm=""))
    if len(lines) > _MAX_DIFF_LINES:
        lines = lines[:_MAX_DIFF_LINES] + [
            f"... ({len(lines) - _MAX_DIFF_LINES} more diff lines suppressed)"]
    return "\n".join(lines)


class GoldenMismatch(AssertionError):
    """Raised with a readable diff when generated output has drifted."""


def check_golden(cfg: Config, paths: Sequence[Path], out_dir) -> None:
    """Compare a fresh generation of ``cfg`` against its frozen snapshot.

    Raises :class:`GoldenMismatch` naming the first file that differs. Order
    and membership are checked before content, because a manifest difference
    explains a content difference and reporting both is noise.
    """
    cfg_dir = GOLDEN_ROOT / cfg.name
    if not (cfg_dir / _MANIFEST).exists():
        raise GoldenMismatch(
            f"no snapshot for config '{cfg.name}' ({cfg_dir}). "
            f"PSSC_GOLDEN_REGEN=1 python scripts/regen_golden.py {cfg.name}")

    expected_manifest = _read_manifest(cfg_dir)
    actual_manifest = build_manifest(paths, out_dir)
    if actual_manifest != expected_manifest:
        raise GoldenMismatch(
            f"{cfg.name}: the set or ORDER of generated files changed "
            f"({cfg.rationale}).\n"
            f"The order is the compilation order a build system consumes.\n"
            + _diff("\n".join(expected_manifest), "\n".join(actual_manifest),
                    f"{cfg.name}/{_MANIFEST}"))

    for p, (rel, origin) in zip(paths, _split_manifest(actual_manifest)):
        actual = _normalise(Path(p).read_text())
        if origin:
            # A copied core header: assert it arrived from `share/` unmodified.
            # build_manifest already established the bytes match a shipped file
            # of that name; this pins down *which* one.
            src = SHARE_ROOT / Path(origin).relative_to("share")
            expected = _normalise(src.read_text())
        else:
            expected = (cfg_dir / _FILES / rel).read_text()
        if actual != expected:
            raise GoldenMismatch(
                f"{cfg.name}: generated {rel} differs from its snapshot "
                f"({cfg.rationale}).\n"
                f"If this change is intentional, regenerate:\n"
                f"  PSSC_GOLDEN_REGEN=1 python scripts/regen_golden.py {cfg.name}\n"
                + _diff(expected, actual, f"{cfg.name}/{rel}"))


def assert_golden(cfg_name: str, tmp_dir) -> List[Path]:
    """Generate ``cfg_name`` into ``tmp_dir`` and check it. Returns the paths."""
    cfg = CONFIG_BY_NAME[cfg_name]
    paths = run_config(cfg, tmp_dir)
    check_golden(cfg, paths, tmp_dir)
    return paths


def regen_allowed() -> bool:
    """Regeneration is opt-in through the environment -- see scripts/regen_golden.py."""
    return os.environ.get("PSSC_GOLDEN_REGEN") == "1"
