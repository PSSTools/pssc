"""A test's *slice* -- the source its verdict depends on -- and the result
record that lets ``pssc test --changed`` skip a test whose slice is unchanged.

The slice is computed from source text, not from the IR's call graph, so it
over-approximates and never misses:

* a *unit* is a declaration with a body that a test may reach by name: an
  action type, a component function, a package function. Its text is its
  source span (:func:`.catalog.span_end`);
* a test reaches the units whose short name appears as an identifier in its
  own text, then in theirs, to a fixed point, plus its root's set-up and
  tear-down functions. A name shared by two units (an override, a base's
  function called by ``super;``) reaches both;
* everything outside every unit -- types, registers, constants, component
  bodies, imports, comments -- is the *rest*, shared by every test.

The slice hash covers the rest, the engine version, the test's own text and
the text of each unit it reaches. An edit inside one operation's body changes
only the slices of the tests that reach that operation; an edit anywhere else
changes them all. Moving a declaration (its lines shift) changes nothing.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .catalog import _source_lines, hooks, span_end
from .discover import TestItem

#: Where the last result of each test is kept, relative to the working directory.
RESULTS = os.path.join(".pssunit", "results.json")
RESULTS_SCHEMA = "pssunit.results/1"

_IDENT = re.compile(r"[A-Za-z_]\w*")


@dataclass(frozen=True)
class Unit:
    qname: str
    name: str
    file: str
    line: int
    end_line: int

    def text(self) -> str:
        lines = _source_lines(self.file) or []
        return "\n".join(lines[self.line - 1:self.end_line])


def engine_version() -> str:
    """What ran the test: pssc and the bytecode engine, by version."""
    from .. import __version__
    try:
        from importlib.metadata import version
        bc = version("zuspec-be-bc")
    except Exception:  # noqa: BLE001 -- not installed as a distribution
        bc = "?"
    return f"pssc {__version__}; zuspec-be-bc {bc}"


def _unit(qname: str, node) -> Optional[Unit]:
    loc = getattr(node, "loc", None)
    if loc is None or not getattr(loc, "file", None) or not loc.line:
        return None
    end = span_end(loc)
    if end is None:
        return None
    return Unit(qname, qname.rsplit("::", 1)[-1],
                os.path.realpath(loc.file), loc.line, end)


def units(ctx) -> List[Unit]:
    """Every action type, component function and package function with a
    body, in a stable order."""
    out: Dict[str, Unit] = {}
    parents = getattr(ctx, "parent_comp_names", None) or {}
    for qname, dt in ctx.type_map.items():
        if qname in parents:
            u = _unit(qname, dt)
            if u:
                out[qname] = u
        elif type(dt).__name__ == "DataTypeComponent":
            for fn in getattr(dt, "functions", None) or []:
                if getattr(fn, "body", None) is None:
                    continue
                u = _unit(f"{qname}::{fn.name}", fn)
                if u:
                    out[u.qname] = u
    seen: Set[int] = set()
    for name, fn in (getattr(ctx, "functions", None) or {}).items():
        if id(fn) in seen or getattr(fn, "body", None) is None \
                or getattr(fn, "is_import", False):
            continue
        seen.add(id(fn))
        u = _unit(getattr(fn, "name", None) or name, fn)
        if u and u.qname not in out:
            out[u.qname] = u
    return sorted(out.values(), key=lambda u: u.qname)


def source_root(paths: Iterable[str]) -> str:
    """The deepest directory holding every source: what their names in a
    slice hash are relative to."""
    dirs = [os.path.dirname(os.path.realpath(p)) for p in paths]
    return os.path.commonpath(dirs) if dirs else os.sep


def rest_hash(paths: Iterable[str], all_units: Sequence[Unit],
              root: Optional[str] = None) -> str:
    """The hash of every source line outside every unit (blank lines dropped).

    Files are named relative to ``root`` (default :func:`source_root`), so the
    same tree checked out elsewhere (``--base``) hashes the same."""
    paths = list(paths)
    root = root or source_root(paths)
    spans: Dict[str, Set[int]] = {}
    for u in all_units:
        spans.setdefault(u.file, set()).update(range(u.line, u.end_line + 1))
    h = hashlib.sha256()
    for p in sorted({os.path.realpath(p) for p in paths},
                    key=lambda p: os.path.relpath(p, root)):
        skip = spans.get(p, set())
        h.update(os.path.relpath(p, root).encode() + b"\0")
        for i, text in enumerate(_source_lines(p) or [], 1):
            if i not in skip and text.strip():
                h.update(text.strip().encode() + b"\n")
    return h.hexdigest()


class Slicer:
    """Slices of the tests of one translated context."""

    def __init__(self, ctx, paths: Sequence[str], root: Optional[str] = None):
        self.ctx = ctx
        self.units = units(ctx)
        self.by_qname = {u.qname: u for u in self.units}
        self.by_name: Dict[str, List[Unit]] = {}
        for u in self.units:
            self.by_name.setdefault(u.name, []).append(u)
        self.base = hashlib.sha256(
            (engine_version() + "\0" + rest_hash(paths, self.units, root)).encode()
        ).hexdigest()
        self._names: Dict[str, Set[str]] = {}

    def _idents(self, u: Unit) -> Set[str]:
        if u.qname not in self._names:
            self._names[u.qname] = set(_IDENT.findall(u.text()))
        return self._names[u.qname]

    def calls(self, item: TestItem) -> List[str]:
        """The units ``item`` reaches, by qualified name, sorted (itself excluded)."""
        own = self.by_qname.get(item.decl)
        if own is None:
            return []
        setup, teardown = hooks(self.ctx, item.comp)
        todo = [own] + [self.by_qname[q] for q in setup + teardown
                        if q in self.by_qname]
        reached: Dict[str, Unit] = {}
        while todo:
            u = todo.pop()
            if u.qname in reached:
                continue
            reached[u.qname] = u
            for name in self._idents(u):
                todo.extend(v for v in self.by_name.get(name, ())
                            if v.qname not in reached)
        reached.pop(own.qname, None)
        return sorted(reached)

    def slice_hash(self, item: TestItem) -> str:
        """The hash of everything ``item``'s verdict depends on."""
        h = hashlib.sha256(self.base.encode())
        own = self.by_qname.get(item.decl)
        h.update(b"\0" + item.qname.encode())
        if own is None:         # no source span: any edit re-runs it
            h.update(b"\0" + "".join(u.text() for u in self.units).encode())
            return h.hexdigest()
        for q in [own.qname] + self.calls(item):
            h.update(b"\0" + q.encode() + b"\0" + self.by_qname[q].text().encode())
        return h.hexdigest()


# ---- the slices at a git revision (``--changed --base REV``) ------------------

class BaseError(Exception):
    """``--base REV`` cannot be read: not in a git work tree, or no such REV."""


def _git(cwd: str, *argv: str):
    import subprocess
    return subprocess.run(["git", *argv], cwd=cwd, capture_output=True)


def base_slices(paths: Sequence[str], rev: str) -> Optional[Dict[str, str]]:
    """Each test's slice hash with ``paths`` as they are at git ``rev``.

    The sources at ``rev`` are copied, with the same layout, into a temporary
    tree and translated there. A source absent at ``rev`` is empty; one outside
    the work tree is taken as it is now. Returns None when the sources at
    ``rev`` do not translate (every test is then changed)."""
    import shutil
    import tempfile
    real = [os.path.realpath(p) for p in paths]
    root = source_root(real)
    probe = os.path.dirname(real[-1]) if real else os.getcwd()
    r = _git(probe, "rev-parse", "--show-toplevel")
    if r.returncode != 0:
        raise BaseError(f"--base {rev}: {probe} is not in a git work tree")
    top = os.path.realpath(r.stdout.decode().strip())
    r = _git(top, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if r.returncode != 0:
        raise BaseError(f"--base {rev}: no such git revision")
    commit = r.stdout.decode().strip()
    tmp = os.path.realpath(tempfile.mkdtemp(prefix="pssc-base-"))
    try:
        copies = []
        for p in real:
            dst = os.path.join(tmp, os.path.relpath(p, root))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            rel = os.path.relpath(p, top)
            if rel.startswith(os.pardir + os.sep):
                shutil.copyfile(p, dst)
            else:
                shown = _git(top, "show", f"{commit}:{rel}")
                with open(dst, "wb") as f:
                    f.write(shown.stdout if shown.returncode == 0 else b"")
            copies.append(dst)
        from .. import driver
        from .discover import discover
        try:
            ctx = driver.translate(copies)
            if getattr(ctx, "errors", None):
                return None
            found = discover(ctx)
        except Exception:  # noqa: BLE001 -- a REV that does not compile
            return None
        slicer = Slicer(ctx, copies, tmp)
        return {it.qname: slicer.slice_hash(it) for it in found}
    finally:
        from .catalog import _LINES
        for k in [k for k in _LINES if str(k).startswith(tmp)]:
            _LINES.pop(k, None)
        shutil.rmtree(tmp, ignore_errors=True)


# ---- the result record ------------------------------------------------------

def key(slice_hash: str, options: Sequence[str], seed: Optional[int]) -> Dict:
    """What a recorded pass must match to stand for a run now: the slice
    (which covers the engine version), the verdict-changing options (the
    platform among them) and the seed, when one was given."""
    return {"slice": slice_hash, "options": list(options), "seed": seed}


def read_results(path: Optional[str] = None) -> Dict[str, Dict]:
    """``{test ID: {"key": ..., "status": ...}}``; empty when there is none."""
    try:
        with open(path or RESULTS) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    tests = data.get("tests") if isinstance(data, dict) else None
    return tests if isinstance(tests, dict) else {}


def write_results(entries: Dict[str, Dict], path: Optional[str] = None) -> None:
    """Merge ``entries`` into the record; a test not in them keeps its own."""
    path = path or RESULTS
    tests = read_results(path)
    tests.update(entries)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump({"schema": RESULTS_SCHEMA, "tests": tests}, f, indent=1,
                  sort_keys=True)
        f.write("\n")


def unchanged(record: Dict[str, Dict], qname: str, k: Dict) -> bool:
    """True when ``qname`` last passed under key ``k``. Without a ``--seed``
    (``k["seed"]`` None) a pass under any seed counts."""
    r = record.get(qname)
    if not isinstance(r, dict) or r.get("status") not in ("pass", "skip", "xfail"):
        return False
    rk = r.get("key") or {}
    return (rk.get("slice") == k["slice"] and rk.get("options") == k["options"]
            and (k["seed"] is None or rk.get("seed") == k["seed"]))
