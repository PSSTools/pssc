"""The static test catalog: every test in a translated model, and what it says.

A test is one of:

* a concrete action whose ``super`` chain reaches ``pssunit_pkg::test_a``,
  declared in a component -- **or inherited by one**. A fixture derived from
  another fixture runs every test of its base under its own root, so
  ``derived_f`` declaring nothing still has ``derived_f::t1`` when ``base_f``
  declares ``t1``;
* a component function marked ``@pssunit_pkg::test``.

The roots are the components that have at least one test, own or inherited.
That covers both shapes a suite may take: a component derived from
``pssunit_pkg::fixture_c`` (the fixture pattern), and the earlier "a test runs
in the component that declares it", which keeps working unchanged (R-2).

IDs are ``<root>::<test>``. The file is metadata, never part of the ID, so
moving a file keeps every ID. A test's location *span* -- from its declaration
to its closing brace -- is what ``pssc test FILE:LINE`` selects by.

The slice (which operations a test reaches, for ``--changed``) is computed by
:mod:`.slice`; :func:`to_json` takes it as ``slicer``.

Like :mod:`.discover`, this module shouts: a base type that does not resolve is
a located :class:`DiscoveryError` (exit 2), never a quietly missing test.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple

from .discover import (DiscoveryError, TestItem, TestMeta, TEST_ANNOTATION,
                       TEST_BASES, meta_of, resolve_type_name)

#: The base of every fixture component.
FIXTURE_BASE = "pssunit_pkg::fixture_c"

#: Fixture functions run before and after each test.
SETUP_ANNOTATION = "pssunit_pkg::setup"
TEARDOWN_ANNOTATION = "pssunit_pkg::teardown"
#: Fixture functions run once per simulation (boot), before any test.
SESSION_ANNOTATION = "pssunit_pkg::session"

#: The ``--collect-only --json`` schema; see docs/schemas/pssunit.catalog-1.json.
SCHEMA = "pssunit.catalog/1"


def _where(dt) -> str:
    loc = getattr(dt, "loc", None)
    if loc is None or getattr(loc, "file", None) is None:
        return ""
    return f"{loc.file}:{loc.line}:{getattr(loc, 'pos', 0) or 0}: "


def base_chain(ctx, dt) -> List[str]:
    """``dt``'s ``super`` chain, nearest first, as resolved ``type_map`` keys.

    An unresolvable link raises a :class:`DiscoveryError` prefixed with the
    location of the type whose base it is. Cycle-guarded.
    """
    chain: List[str] = []
    while dt is not None:
        ref = getattr(dt, "super", None)
        ref_name = getattr(ref, "ref_name", None) if ref is not None else None
        if ref_name is None:
            break
        try:
            name = resolve_type_name(ctx, ref_name)
        except DiscoveryError as e:
            raise DiscoveryError(_where(dt) + str(e)) from None
        if name in chain:
            break
        chain.append(name)
        dt = ctx.type_map.get(name)
    return chain


# ---- location spans ---------------------------------------------------------

_LINES: Dict[str, List[str]] = {}


def _source_lines(path: str) -> Optional[List[str]]:
    if path not in _LINES:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                _LINES[path] = f.read().splitlines()
        except OSError:
            return None
    return _LINES[path]


def span_end(loc) -> Optional[int]:
    """The line of the brace that closes the declaration starting at ``loc``.

    The IR records only where a declaration starts, so the end is found in the
    source: the first ``{`` from ``loc`` and its match, skipping comments and
    string literals. ``None`` when the file cannot be read or has no body.
    """
    if loc is None or not getattr(loc, "file", None):
        return None
    lines = _source_lines(loc.file)
    if not lines:
        return None
    depth = 0
    in_block = False
    for ln in range(max(loc.line, 1) - 1, len(lines)):
        text = lines[ln]
        i = (max((getattr(loc, "pos", 1) or 1) - 1, 0)
             if ln == loc.line - 1 else 0)
        while i < len(text):
            c = text[i]
            if in_block:
                if text.startswith("*/", i):
                    in_block = False
                    i += 1
            elif text.startswith("//", i):
                break
            elif text.startswith("/*", i):
                in_block = True
                i += 1
            elif c == '"':
                j = i + 1
                while j < len(text) and text[j] != '"':
                    j += 2 if text[j] == "\\" else 1
                i = j
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return ln + 1
            i += 1
    return None


# ---- building the catalog ---------------------------------------------------

def _tags(node, meta: TestMeta) -> Tuple[str, ...]:
    from .. import annotations

    tags = [t.strip() for t in meta.tags.split(",") if t.strip()]
    for a in annotations.all_of(node, "pssunit_pkg::tag"):
        name = a.get("name")
        if isinstance(name, str) and name and name not in tags:
            tags.append(name)
    return tuple(tags)


def _one(node, qname: str, attr: str, kind):
    from .. import annotations

    p = annotations.of(node, f"pssunit_pkg::{qname}")
    if p is None:
        return None
    v = p.get(attr)
    return v if isinstance(v, kind) and not (
        kind is int and isinstance(v, bool)) else None


def _item(root: str, name: str, kind: str, decl: str, node, *,
          bases: Tuple[str, ...], is_fixture: bool) -> TestItem:
    meta = meta_of(node)
    loc = getattr(node, "loc", None)
    from .doctest import item_loc
    loc, end = item_loc(loc) or (loc, span_end(loc))  # a doc-comment example
    return TestItem(
        qname=f"{root}::{name}", name=name, comp=root, loc=loc, meta=meta,
        kind=kind, decl=decl, end_line=end,
        tags=_tags(node, meta),
        property_runs=_one(node, "property", "runs", int),
        expect_error=_one(node, "expect_error", "text", str),
        platform_min=_one(node, "platform", "min", str),
        bases=bases, is_fixture=is_fixture)


def build(ctx) -> List[TestItem]:
    """Every test in ``ctx``, grouped by root, in declaration order.

    Within a root, inherited tests come first (base before derived), then the
    root's own -- the order a reader meets them walking down the hierarchy.
    """
    parents: Dict[str, str] = getattr(ctx, "parent_comp_names", None) or {}
    type_map: Dict[str, Any] = ctx.type_map

    # Actions that are tests, by declaring component, in declaration order.
    by_comp: Dict[str, List[Tuple[str, Any, Tuple[str, ...]]]] = {}
    for qname, comp in parents.items():
        dt = type_map.get(qname)
        if dt is None or getattr(dt, "is_abstract", False):
            continue
        chain = base_chain(ctx, dt)
        if not any(b in TEST_BASES for b in chain):
            continue
        by_comp.setdefault(comp, []).append((qname, dt, tuple(chain)))

    items: List[TestItem] = []
    for cname, cdt in type_map.items():
        if (cname not in by_comp
                and type(cdt).__name__ != "DataTypeComponent"):
            continue
        if getattr(cdt, "is_abstract", False):
            continue
        chain = base_chain(ctx, cdt)
        is_fixture = FIXTURE_BASE in chain
        lineage = [c for c in reversed(chain) if c in type_map] + [cname]

        seen = set()
        for decl_comp in lineage:
            for qname, dt, abases in by_comp.get(decl_comp, ()):
                name = qname.rsplit("::", 1)[-1]
                if name in seen:
                    continue
                seen.add(name)
                items.append(_item(cname, name, "action", qname, dt,
                                   bases=abases, is_fixture=is_fixture))
            for fn in getattr(type_map.get(decl_comp), "functions", None) or []:
                if not any(getattr(a, "type_qname", None) == TEST_ANNOTATION
                           for a in getattr(fn, "annotations", None) or []):
                    continue
                if fn.name in seen:
                    continue
                seen.add(fn.name)
                items.append(_item(cname, fn.name, "function",
                                   f"{decl_comp}::{fn.name}", fn,
                                   bases=(), is_fixture=is_fixture))
    return items


def hooks(ctx, comp: str) -> Tuple[List[str], List[str]]:
    """``(setup, teardown)``: the ``@pssunit_pkg::setup`` and
    ``@pssunit_pkg::teardown`` functions of root ``comp`` and its bases, as
    ``<declaring comp>::<fn>`` symbols.

    Setup runs base first and teardown derived first, as constructors and
    destructors do, so a derived fixture's set-up can rely on its base's. A
    function a derived component redeclares runs once, as the derived one.

    Every test has a fresh tree on pyvm, so the ``@pssunit_pkg::session``
    functions (``session_hooks``) lead the set-up: each test boots first.
    """
    found = _annotated(ctx, comp, (SESSION_ANNOTATION, SETUP_ANNOTATION,
                                   TEARDOWN_ANNOTATION))
    return (found[SESSION_ANNOTATION] + found[SETUP_ANNOTATION],
            list(reversed(found[TEARDOWN_ANNOTATION])))


def session_hooks(ctx, comp: str) -> List[str]:
    """The ``@pssunit_pkg::session`` functions of root ``comp`` and its
    bases, base first: a simulation runs them once, before its first test,
    and then runs the tests with no platform reset between them."""
    return _annotated(ctx, comp, (SESSION_ANNOTATION,))[SESSION_ANNOTATION]


def _annotated(ctx, comp: str, kinds) -> Dict[str, List[str]]:
    """Annotation qname -> the functions of root ``comp`` and its bases it
    marks, base first, as ``<declaring comp>::<fn>``."""
    type_map: Dict[str, Any] = ctx.type_map
    found: Dict[str, Dict[str, str]] = {k: {} for k in kinds}
    cdt = type_map.get(comp)
    if cdt is None:
        return {k: [] for k in kinds}
    lineage = [c for c in reversed(base_chain(ctx, cdt)) if c in type_map]
    lineage.append(comp)
    for decl_comp in lineage:
        for fn in getattr(type_map.get(decl_comp), "functions", None) or []:
            for a in getattr(fn, "annotations", None) or []:
                by_name = found.get(getattr(a, "type_qname", None))
                if by_name is not None:
                    # Re-inserting moves nothing: dict order is first seen.
                    by_name[fn.name] = f"{decl_comp}::{fn.name}"
    return {k: list(v.values()) for k, v in found.items()}


def function_of(ctx, comp: str, name: str) -> Optional[str]:
    """``<declaring comp>::<name>``: function ``name`` of root ``comp`` or
    its nearest base that declares it, or None."""
    type_map: Dict[str, Any] = ctx.type_map
    cdt = type_map.get(comp)
    if cdt is None:
        return None
    lineage = [comp] + [c for c in base_chain(ctx, cdt) if c in type_map]
    for decl_comp in lineage:
        for fn in getattr(type_map.get(decl_comp), "functions", None) or []:
            if fn.name == name:
                return f"{decl_comp}::{name}"
    return None


def select_lines(items: List[TestItem],
                 where: List[Tuple[str, int]]) -> List[TestItem]:
    """The tests whose span contains any ``(file, line)`` in ``where``."""
    def _same(a: str, b: str) -> bool:
        try:
            return os.path.samefile(a, b)
        except OSError:
            return os.path.abspath(a) == os.path.abspath(b)

    out = []
    for it in items:
        loc = it.loc
        if loc is None or not getattr(loc, "file", None):
            continue
        end = it.end_line or loc.line
        if any(loc.line <= line <= end and _same(loc.file, f)
               for f, line in where):
            out.append(it)
    return out


def to_json(items: List[TestItem], slicer=None) -> Dict[str, Any]:
    """The ``pssunit.catalog/1`` document for ``items``; ``calls`` is each
    test's slice when a :class:`.slice.Slicer` is given, else ``None``."""
    roots: List[Dict[str, Any]] = []
    for it in items:
        if not any(r["name"] == it.comp for r in roots):
            roots.append({"name": it.comp, "fixture": it.is_fixture})
    tests = []
    for it in items:
        loc = it.loc
        tests.append({
            "id": it.qname,
            "name": it.name,
            "kind": it.kind,
            "fixture": it.comp,
            "decl": it.decl,
            "file": getattr(loc, "file", None),
            "line": getattr(loc, "line", None),
            "end_line": it.end_line,
            "group": it.meta.group,
            "tags": list(it.tags),
            "skip": it.meta.skip,
            "xfail": it.meta.xfail,
            "reason": it.meta.reason,
            "samples": it.meta.samples,
            "property_runs": it.property_runs,
            "expect_error": it.expect_error,
            "platform_min": it.platform_min,
            "bases": list(it.bases),
            "calls": None if slicer is None else {
                "units": slicer.calls(it), "slice": slicer.slice_hash(it)},
        })
    return {"schema": SCHEMA, "fixtures": roots, "tests": tests}
