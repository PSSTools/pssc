"""Turning what the user typed into an ordered list of PSS files.

``pssc test examples/wb_dma`` has to mean something, and ``pssc compile``'s
answer -- an explicit list of file paths -- is the wrong one for a test runner:
a suite is a directory that grows files, not a list someone maintains by hand.

A spec is a file, a directory (recursively, ``*.pss``), a glob, or ``@filelist``.
Deliberately NOT here: ivpm/dv-flow integration, incremental caching, per-file
include paths. A directory walk covers every early user, and the smaller this
contract is the longer it stays changeable.

ORDER IS PART OF THE CONTRACT. The resulting list feeds ``driver.translate``,
and pssc has been bitten by file order before (see
``tests/unit/test_file_order_independence.py``). Every expansion here sorts
explicitly rather than inheriting whatever order the filesystem hands back, so
two runs on the same tree translate the same way.
"""
from __future__ import annotations

import glob as _glob
from pathlib import Path
from typing import List, Optional, Sequence

#: Directory of ``pssc``'s built-in PSS packages, relative to the package root.
STD_LIBS = "std_libs"

#: The built-in framework package, injected ahead of the user's sources.
#: One flat file, like the rest of ``std_libs/`` -- which is also what the
#: ``std_libs/*.pss`` package-data glob matches. A ``pssunit_pkg/`` directory
#: would need a second pattern, and would otherwise ship as nothing at all in a
#: wheel while working perfectly in a development checkout.
PSSUNIT_PKG = "pssunit_pkg.pss"


class SourceSpecError(Exception):
    """A source spec named nothing, or named something unreadable.

    A user error, not a test failure: it exits 2 and produces no report.
    """


def _rebase(path: Path, base: Optional[Path]) -> Path:
    if base is None or path.is_absolute():
        return path
    return base / path


def _expand_filelist(path: Path, seen: set) -> List[Path]:
    """One path per line, ``#`` comments stripped, relative to the filelist.

    Relative to the filelist's own directory rather than the cwd, so a filelist
    can be checked in and used from anywhere. Lines are themselves specs, so a
    filelist may name directories and globs.
    """
    real = path.resolve()
    if real in seen:
        raise SourceSpecError(
            f"filelist '{path}' includes itself (directly or through another "
            f"filelist)")
    seen.add(real)

    try:
        text = path.read_text()
    except OSError as e:
        raise SourceSpecError(f"cannot read filelist '{path}': {e}") from None

    out: List[Path] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        try:
            out.extend(_expand_one(entry, base=path.parent, seen=seen))
        except SourceSpecError as e:
            raise SourceSpecError(f"{path}:{lineno}: {e}") from None
    return out


def _expand_one(spec: str, base: Optional[Path], seen: set) -> List[Path]:
    if spec.startswith("@"):
        return _expand_filelist(_rebase(Path(spec[1:]), base), seen)

    path = _rebase(Path(spec), base)

    if path.is_dir():
        found = sorted(path.rglob("*.pss"))
        if not found:
            raise SourceSpecError(f"no .pss files under directory '{spec}'")
        return found

    if path.is_file():
        return [path]

    # Not an existing path -- the only remaining reading is a glob. A pattern
    # that matches nothing is an error rather than an empty contribution: a
    # mistyped spec that quietly contributes no files ends as a green run over
    # a suite that was never loaded.
    matched = sorted(Path(p) for p in _glob.glob(str(path), recursive=True))
    if matched:
        return matched

    raise SourceSpecError(f"source spec matches nothing: '{spec}'")


def resolve_sources(specs: Sequence[str]) -> List[Path]:
    """Expand ``specs`` to an ordered, de-duplicated list of ``.pss`` files.

    With no specs, the current directory -- ``pssc test`` inside a suite should
    run the suite.

    Duplicates are dropped rather than rejected: overlapping specs
    (``tests/ tests/smoke_a.pss``) are a reasonable thing to type, and handing
    the same file to the parser twice is not.
    """
    if not specs:
        specs = ["."]

    out: List[Path] = []
    seen_files: set = set()
    seen_lists: set = set()
    for spec in specs:
        for path in _expand_one(spec, base=None, seen=seen_lists):
            key = path.resolve()
            if key not in seen_files:
                seen_files.add(key)
                out.append(path)
    return out


def pssunit_sources(package: str = "pssc") -> List[Path]:
    """The built-in ``pssunit_pkg`` source shipped inside ``package``.

    Through ``importlib.resources`` rather than ``__file__`` arithmetic: the
    latter works right up until someone builds a zipped or relocated
    distribution, and then fails as a missing file with no mention of the
    package that was supposed to provide it.

    A list, though it holds one path today: the framework package growing a
    second file should not change this function's contract.
    """
    try:
        from importlib.resources import files
        root = files(package)
    except ModuleNotFoundError as e:
        raise SourceSpecError(
            f"cannot locate {PSSUNIT_PKG}: package '{package}' is not "
            f"importable ({e})") from None

    path = Path(str(root / STD_LIBS / PSSUNIT_PKG))
    if not path.is_file():
        raise SourceSpecError(
            f"package '{package}' ships no {PSSUNIT_PKG}: expected {path}. "
            f"It must be listed in {package}'s package-data as "
            f"'{STD_LIBS}/*.pss'")
    return [path]


def all_sources(specs: Sequence[str], package: str = "pssc") -> List[Path]:
    """:func:`pssunit_sources` ahead of :func:`resolve_sources`.

    The framework package goes first so that a user source referring to
    ``pssunit_pkg::test_a`` sees it declared. The user never names this file and
    never installs anything to get it.
    """
    return pssunit_sources(package) + resolve_sources(specs)
