"""Mutants of a model's PSS source, for ``pssc test --mutate``.

A *mutant* is one small edit of a model file's text that a good suite should
notice: a flipped operator, an integer off by one, a register offset moved by
a word, a field swapped for its neighbour. Mutants are found in the source
text, with comments and strings blanked out first, so each one is an exact
``old -> new`` replacement at a known place:

* ``operator``: ``==``/``!=``, ``<``/``<=``, ``>``/``>=``, ``+``/``-``,
  ``&``/``|``, ``&&``/``||``, ``<<``/``>>``. One-character operators (and
  shifts) must have white space on both sides, so ``packed_s<E>``, unary
  ``-1`` and ``a<b<c>>`` are left alone;
* ``constant``: an integer literal ``n`` becomes ``n + 1`` (``1`` becomes
  ``0``), in its own base. Widths (``bit[3]``) are not mutated;
* ``offset``: a hex literal that is a multiple of 4 moves up by 4 -- the next
  register;
* ``field``: in a packed struct, two adjacent field declarations swap lines
  (the layout changes), and a ``.f`` that names a field becomes ``.g`` for
  each sibling ``g`` declared next to ``f``. Reserved fields (``rsvd*``) are
  never used.

Only model files are mutated: files that declare no test
(:func:`model_files`). Whether a mutant compiles is not checked here; the run
drops the ones that do not.
"""
from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set, Tuple

KINDS = ("operator", "constant", "offset", "field")


@dataclass(frozen=True)
class Mutant:
    kind: str
    file: str
    line: int           # 1-based
    col: int            # 1-based
    old: str
    new: str
    pos: int            # offset of ``old`` in the file's text

    def __str__(self) -> str:
        def first(s: str) -> str:
            s = s.strip()
            return s.split("\n", 1)[0].rstrip() + " ..." if "\n" in s else s
        return f"{self.file}:{self.line}:{self.col} {self.kind} {first(self.old)!r} -> {first(self.new)!r}"


def apply(m: Mutant, text: str = None) -> str:
    """The text of ``m.file`` with ``m`` applied."""
    if text is None:
        with open(m.file) as f:
            text = f.read()
    if text[m.pos:m.pos + len(m.old)] != m.old:
        raise ValueError(f"{m}: the file no longer has {m.old!r} there")
    return text[:m.pos] + m.new + text[m.pos + len(m.old):]


def model_files(paths: Iterable[str], items: Iterable = ()) -> List[str]:
    """``paths`` minus the files that declare a test (``TestItem.loc.file``)."""
    tests: Set[str] = set()
    for it in items:
        f = getattr(getattr(it, "loc", None), "file", None)
        if f:
            tests.add(os.path.realpath(str(f)))
    return [str(p) for p in paths if os.path.realpath(str(p)) not in tests]


def mask(text: str) -> str:
    """``text`` with comments and string contents replaced by spaces; newlines
    and every other character keep their place."""
    out = list(text)
    i, n = 0, len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, b):
            if out[k] != "\n":
                out[k] = " "
    while i < n:
        c = text[i]
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            blank(i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            blank(i, j)
            i = j
        elif c == '"':
            if text.startswith('"""', i):
                j = text.find('"""', i + 3)
                j = n if j < 0 else j + 3
            else:
                j = i + 1
                while j < n and text[j] != '"' and text[j] != "\n":
                    j += 2 if text[j] == "\\" else 1
                j = min(j + 1, n)
            blank(i, j)
            i = j
        else:
            i += 1
    return "".join(out)


_SWAP = {"==": "!=", "!=": "==", "<": "<=", "<=": "<", ">": ">=", ">=": ">",
         "+": "-", "-": "+", "&": "|", "|": "&", "&&": "||", "||": "&&",
         "<<": ">>", ">>": "<<"}
_SPACED = {"<", ">", "+", "-", "&", "|", "<<", ">>"}
_OP = re.compile(r"<<=|>>=|==|!=|<=|>=|&&|\|\||<<|>>|\+\+|--|\+=|-=|&=|\|=|->|::|[<>+\-&|=!*/%^~]")
_NUM = re.compile(r"(?<![\w'.])(0[xX][0-9a-fA-F_]+|0[bB][01_]+|[0-9][0-9_]*)(?![\w'.])")
_WIDTH = re.compile(r"(?:bit|int)\s*\[\s*$")
_STRUCT = re.compile(r"\bstruct\s+(\w+)\s*:\s*packed_s\b[^{]*\{")
_FIELD = re.compile(r"[ \t]*(?:bit|int)\s*(?:\[[^\]]*\])?\s+(\w+)\s*;[^\n]*\n")
_DOT = re.compile(r"\.\s*([A-Za-z_]\w*)\b(?!\s*\()")


class _Text:
    def __init__(self, path: str, text: str):
        self.path, self.text, self.masked = path, text, mask(text)
        self._starts = [0] + [m.end() for m in re.finditer("\n", text)]

    def at(self, kind: str, pos: int, old: str, new: str) -> Mutant:
        ln = bisect.bisect_right(self._starts, pos)
        return Mutant(kind, self.path, ln, pos - self._starts[ln - 1] + 1, old, new, pos)


def _operators(t: _Text) -> Iterable[Mutant]:
    s = t.masked
    for m in _OP.finditer(s):
        op = m.group()
        if op not in _SWAP:
            continue
        a, b = m.start(), m.end()
        if op in _SPACED and not (a > 0 and s[a - 1].isspace() and b < len(s) and s[b].isspace()):
            continue
        yield t.at("operator", a, op, _SWAP[op])


def _int(lit: str) -> Tuple[int, str]:
    v = lit.replace("_", "")
    if v[:2] in ("0x", "0X"):
        return int(v[2:], 16), "x"
    if v[:2] in ("0b", "0B"):
        return int(v[2:], 2), "b"
    return int(v, 10), "d"


def _fmt(v: int, base: str, like: str) -> str:
    if base == "x":
        digits = format(v, "x")
        if any(c.isupper() for c in like[2:]):
            digits = digits.upper()
        return like[:2] + digits
    if base == "b":
        return like[:2] + format(v, "b")
    return str(v)


def _constants(t: _Text) -> Iterable[Mutant]:
    s = t.masked
    for m in _NUM.finditer(s):
        if _WIDTH.search(s, max(0, m.start() - 16), m.start()):
            continue
        lit = m.group()
        v, base = _int(lit)
        yield t.at("constant", m.start(), lit, _fmt(0 if v == 1 else v + 1, base, lit))
        if base == "x" and v % 4 == 0:
            yield t.at("offset", m.start(), lit, _fmt(v + 4, base, lit))


def _close(s: str, i: int) -> int:
    """Index of the ``}`` that closes the ``{`` before ``i``."""
    depth = 1
    while i < len(s) and depth:
        depth += {"{": 1, "}": -1}.get(s[i], 0)
        i += 1
    return i


def _structs(t: _Text) -> List[List[Tuple[str, int, int]]]:
    """Each packed struct's fields as (name, start, end) of the declaration line."""
    out = []
    for m in _STRUCT.finditer(t.masked):
        end = _close(t.masked, m.end())
        fields = []
        i = m.end()
        nl = t.masked.find("\n", i)
        i = end if nl < 0 else nl + 1
        while i < end:
            f = _FIELD.match(t.masked, i)
            if f is None:
                nl = t.masked.find("\n", i)
                if nl < 0 or nl >= end:
                    break
                i = nl + 1
                continue
            fields.append((f.group(1), f.start(), f.end()))
            i = f.end()
        out.append(fields)
    return out


def _field_swaps(t: _Text, structs) -> Iterable[Mutant]:
    for fields in structs:
        for (_, a0, a1), (_, b0, b1) in zip(fields, fields[1:]):
            if a1 != b0:
                continue
            first, second = t.text[a0:a1], t.text[b0:b1]
            yield t.at("field", a0, first + second, second + first)


def _siblings(structs_by_file) -> Dict[str, List[str]]:
    sib: Dict[str, List[str]] = {}
    for structs in structs_by_file:
        for fields in structs:
            names = [f[0] for f in fields]
            for k, name in enumerate(names):
                for j in (k - 1, k + 1):
                    if 0 <= j < len(names) and not names[j].startswith("rsvd"):
                        lst = sib.setdefault(name, [])
                        if names[j] not in lst and names[j] != name:
                            lst.append(names[j])
    return sib


def _field_refs(t: _Text, sib: Dict[str, List[str]]) -> Iterable[Mutant]:
    for m in _DOT.finditer(t.masked):
        name = m.group(1)
        for other in sib.get(name, ()):
            yield t.at("field", m.start(1), name, other)


def mutants(paths: Sequence[str], test_files: Iterable[str] = (),
            kinds: Iterable[str] = KINDS) -> List[Mutant]:
    """Every mutant of the files in ``paths`` that are not in ``test_files``,
    in file order, then by place."""
    skip = {os.path.realpath(str(p)) for p in test_files}
    texts: List[_Text] = []
    for p in paths:
        p = str(p)
        if os.path.realpath(p) in skip or not os.path.isfile(p):
            continue
        with open(p) as f:
            texts.append(_Text(p, f.read()))
    kinds = set(kinds)
    structs = [_structs(t) for t in texts]
    sib = _siblings(structs)
    out: List[Mutant] = []
    seen = set()
    for t, st in zip(texts, structs):
        found: List[Mutant] = []
        if "operator" in kinds:
            found += _operators(t)
        if kinds & {"constant", "offset"}:
            found += [m for m in _constants(t) if m.kind in kinds]
        if "field" in kinds:
            found += _field_swaps(t, st)
            found += _field_refs(t, sib)
        for m in sorted(found, key=lambda m: (m.pos, m.kind, m.new)):
            key = (m.file, m.pos, m.old, m.new)
            if key not in seen:
                seen.add(key)
                out.append(m)
    return out


# --- running them (``pssc test --mutate``) -----------------------------------

#: verdicts of one mutant's run
KILLED, SURVIVED, INVALID, UNCOVERED = "killed", "survived", "invalid", "not covered"


@dataclass
class Outcome:
    mutant: Mutant
    status: str
    by: str = ""            # the test that killed it, or "timeout"
    tests: int = 0          # how many tests ran on it


def tests_for(m: Mutant, items, slicer) -> List:
    """The tests that reach ``m``'s line: those whose own declaration, or a
    unit they call (``Slicer.calls``), spans it. A line in no unit (a type,
    a constant) may change any test, so all of them."""
    f = os.path.realpath(m.file)
    hit = {u.qname for u in slicer.units
           if os.path.realpath(u.file) == f and u.line <= m.line <= u.end_line}
    if not hit:
        return list(items)
    return [it for it in items
            if it.decl in hit or hit.intersection(slicer.calls(it))]


def _child_argv(args) -> List[str]:
    from .cmd import replay_options
    out = replay_options(args)
    for i, o in enumerate(out[:-1]):
        # the child runs in its own directory: paths it is given are absolute
        if o == "--reg-semantics":
            out[i + 1] = os.path.abspath(out[i + 1])
        elif o == "--platform":
            spec = out[i + 1]
            head = spec.split(":", 1)[0]
            if os.path.exists(head):
                out[i + 1] = os.path.abspath(head) + spec[len(head):]
    if getattr(args, "seed", None) is not None:
        out += ["--seed", str(args.seed)]
    if not getattr(args, "doctest", True):
        out.append("--no-doctest")
    return out


def run_one(m: Mutant, files: Sequence[str], ids: Sequence[str],
            options: Sequence[str], timeout: float) -> Outcome:
    """Run the tests ``ids`` on the sources ``files`` with ``m`` applied, in
    a child ``pssc test`` (its own directory, no daemon)."""
    import json
    import subprocess
    import sys
    import tempfile
    pkg_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    env = dict(os.environ, PSSC_NO_DAEMON="1", PYTHONPATH=os.pathsep.join(
        p for p in (pkg_root, os.environ.get("PYTHONPATH", "")) if p))
    target = os.path.realpath(m.file)
    with tempfile.TemporaryDirectory(prefix="pssc-mutant-") as tmp:
        # Same base name, so diagnostics still name the file; PSS imports are
        # by package, not by path, so the file may move.
        mutated = os.path.join(tmp, "src", os.path.basename(m.file))
        os.makedirs(os.path.dirname(mutated))
        with open(mutated, "w") as f:
            f.write(apply(m))
        srcs = [mutated if os.path.realpath(p) == target else os.path.abspath(p)
                for p in files]
        argv = [sys.executable, "-m", "pssc", "test", *srcs, *ids, "--json",
                "-x", "--no-ctrf", *options]
        try:
            proc = subprocess.run(argv, cwd=tmp, env=env, capture_output=True,
                                  text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return Outcome(m, KILLED, "timeout", len(ids))
    if proc.returncode == 0:
        return Outcome(m, SURVIVED, tests=len(ids))
    if proc.returncode == 1:
        by = ""
        for line in proc.stdout.splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") == "test.end" and e.get("status") in ("fail", "error"):
                by = e.get("id", "")
                break
        return Outcome(m, KILLED, by, len(ids))
    return Outcome(m, INVALID, f"exit {proc.returncode}", len(ids))


def run_mutants(args, coll, slicer, baseline: float) -> int:
    """``pssc test --mutate`` after a green run: each mutant of the model
    runs on the tests that reach it; the survivors are listed. Exit 1 if any
    survive, else 0."""
    import sys
    from concurrent.futures import ThreadPoolExecutor
    from .report import emit_event
    from .cmd import split_id_specs, split_line_specs
    from .sources import resolve_sources
    specs = split_line_specs(split_id_specs(args.sources)[0])[0]
    files = [str(p) for p in resolve_sources(specs)]
    ms = mutants(model_files(files, coll.found), [],
                 kinds=getattr(args, "mutate_kinds", None) or KINDS)
    ms = sample(ms, getattr(args, "mutate_max", None), getattr(args, "seed", None))
    options = _child_argv(args)
    timeout = max(10.0, 10 * baseline)
    as_json = getattr(args, "json", False)
    out = sys.stderr if as_json else sys.stdout
    plan = []
    outcomes: List[Outcome] = []
    for m in ms:
        items = tests_for(m, coll.items, slicer)
        if items:
            plan.append((m, [it.qname for it in items]))
        else:
            outcomes.append(Outcome(m, UNCOVERED))
    jobs = max(getattr(args, "jobs", 1) or 1, 1)
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        outcomes += pool.map(
            lambda p: run_one(p[0], files, p[1], options, timeout), plan)
    outcomes.sort(key=lambda o: (o.mutant.file, o.mutant.pos, o.mutant.new))
    count = {s: sum(o.status == s for o in outcomes)
             for s in (KILLED, SURVIVED, INVALID, UNCOVERED)}
    for o in outcomes:
        if as_json:
            emit_event(event_mutant_end(o))
        if o.status in (SURVIVED, UNCOVERED):
            print(f"{o.status.upper()}: {o.mutant}", file=out)
        elif getattr(args, "verbose", False):
            print(f"{o.status}: {o.mutant}" + (f" ({o.by})" if o.by else ""),
                  file=out)
    scored = count[KILLED] + count[SURVIVED]
    pct = round(100.0 * count[KILLED] / scored, 1) if scored else None
    score = f"{pct:.1f}%" if scored else "n/a"
    print(f"mutation score {score}: {count[KILLED]} killed, "
          f"{count[SURVIVED]} survived, {count[UNCOVERED]} not covered, "
          f"{count[INVALID]} invalid (did not compile) of {len(outcomes)}",
          file=out)
    rc = 1 if count[SURVIVED] or count[UNCOVERED] else 0
    if as_json:
        emit_event({"event": "mutate.end", "killed": count[KILLED],
                    "survived": count[SURVIVED],
                    "uncovered": count[UNCOVERED],
                    "invalid": count[INVALID], "score": pct, "exit": rc})
    return rc


def sample(ms: List[Mutant], n, seed=None) -> List[Mutant]:
    """At most ``n`` of ``ms``, picked by ``seed``, in their original order
    (all of them when ``n`` is None or not smaller)."""
    import random
    if n is None or n >= len(ms):
        return list(ms)
    keep = set(random.Random(seed or 0).sample(range(len(ms)), max(n, 0)))
    return [m for i, m in enumerate(ms) if i in keep]


def event_mutant_end(o: Outcome) -> Dict:
    """The ``mutant.end`` event of ``pssc test --mutate --json``."""
    m = o.mutant
    return {"event": "mutant.end", "file": m.file, "line": m.line,
            "col": m.col, "kind": m.kind, "old": m.old, "new": m.new,
            "status": o.status, "by": o.by, "tests": o.tests}
