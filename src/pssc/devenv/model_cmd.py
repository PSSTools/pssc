"""`pssc model api|regs|explain <addr|path>|tests` (P1.17).

The elaborated model, asked questions. Every answer comes from the
op-model manifest document (`targets/manifest.py` ``build``) -- the same
registers, offsets and operations a generator emits -- so what this prints
cannot disagree with what was generated. Two things are added to it here:

* each operation's register uses (`OpModel.state_uses`): ``reads`` and
  ``writes``, register paths from the operation's component;
* the register INSTANCES of the tree: every array element expanded, with an
  address from ``--base`` (default 0) for the root's registers.

A sub-component's register group is bound at run time
(``regs.set_handle(bank)``), so its address is not in the model. Where the
group has exactly the layout of an array of groups in the root, element *i*
of a sub-component array is taken to be bound to element *i* of that array
(WB DMA's ``ch[i].regs`` is ``regs.bank[i]``); the instance then has the
root's address and the two are listed as ``aliases``. That is an inference
from the layout, and ``via`` says so. A group with no such match has no
address.

``--json`` prints one JSON document for every subcommand.
"""
from __future__ import annotations

import argparse
import itertools
import json
import re
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .. import driver

SCHEMA = "pssc.model/1"


# --- loading -----------------------------------------------------------------

def load(specs: Sequence[str], root: str, target: str = "op-model-c"):
    """``(ctx, model, doc)`` for *specs*: the translation, the op-model and its
    manifest document, operations with ``reads``/``writes``."""
    from .. import targets as _targets
    from ..targets import manifest
    from ..testing import sources as _sources

    tgt = _targets.get(target)
    paths = [str(p) for p in _sources.all_sources(list(specs) or ["."])]
    ctx = driver.translate(paths, prelude=tgt.prelude(argparse.Namespace()))
    if getattr(ctx, "errors", None):
        raise driver.CompileError(
            f"PSS translation failed with {len(ctx.errors)} error(s)",
            ctx.errors)
    model = tgt.build_model(ctx, argparse.Namespace(
        progseq_root=root, output_dir="."))
    doc = manifest.build(model, target=target)
    for node, comp in zip(model.components, doc["components"]):
        for fn, op in zip(model.operations(node.dtype), comp["operations"]):
            u = model.state_uses(fn)
            op["reads"] = sorted(".".join(p) for p in u.regs_read)
            op["writes"] = sorted(".".join(p) for p in u.regs_written)
    return ctx, model, doc


# --- register instances ------------------------------------------------------

def _indexed(path: Sequence[str], at: Sequence[int], idx: Sequence[int]) -> str:
    segs = list(path)
    for k, i in zip(at, idx):
        segs[k] = f"{segs[k]}[{i}]"
    return ".".join(segs)


def _elements(reg) -> List[Tuple[Tuple[int, ...], int]]:
    """Each element of *reg* (manifest register): its indices and offset."""
    counts = reg.get("counts") or []
    return [(idx, reg["offset"] + sum(i * s for i, s in zip(idx, reg["strides"])))
            for idx in itertools.product(*(range(n) for n in counts))]


def _bank(root_regs, sub_regs, group: str, count: int):
    """The root array of groups whose layout is *sub_regs*' (the
    sub-component's group *group*), as ``(prefix, base, stride)``, or None.
    Only an outermost-array prefix whose element count is *count* is a
    match."""
    mine = {(tuple(r["path"][1:]), r["offset"], r["value_struct"], r["access"])
            for r in sub_regs if r["path"][0] == group and not r["strides"]}
    if not mine or len(mine) != len([r for r in sub_regs if r["path"][0] == group]):
        return None
    lo = min(o for _, o, _, _ in mine)
    mine = {(p, o - lo, v, a) for p, o, v, a in mine}
    for r in root_regs:
        if len(r["strides"]) != 1 or r["counts"][0] != count:
            continue
        k = r["array_at"][0]
        prefix = tuple(r["path"][:k + 1])
        under = [x for x in root_regs if tuple(x["path"][:k + 1]) == prefix]
        if any(len(x["strides"]) != 1 or x["array_at"][0] != k for x in under):
            continue
        base = min(x["offset"] for x in under)
        theirs = {(tuple(x["path"][k + 1:]), x["offset"] - base,
                   x["value_struct"], x["access"]) for x in under}
        if theirs == mine:
            return prefix, base, r["strides"][0], k
    return None


def instances(doc, base: int = 0) -> List[Dict[str, Any]]:
    """Every register instance in the tree, root first: ``path`` (from the
    root, indices included), ``component`` (the instance holding it, ``""``
    for the root), ``type``, ``reg`` (the path within the component, no
    indices), ``addr`` (or None), ``aliases``, ``via``, and the manifest
    register's fields."""
    comps = {c["name"]: c for c in doc["components"]}
    root = comps[doc["root"]]
    out: List[Dict[str, Any]] = []
    by_root: Dict[Tuple[str, ...], Dict[str, Any]] = {}

    def add(comp, prefix: str, reg, idx, off, addr, via=None):
        path = _indexed(reg["path"], reg["array_at"], idx)
        inst = dict(reg, path=(prefix + "." if prefix else "") + path,
                    component=prefix, type=comp["name"], reg=".".join(reg["path"]),
                    addr=addr, offset=off, aliases=[], via=via)
        out.append(inst)
        return inst

    for reg in root["registers"]:
        for idx, off in _elements(reg):
            inst = add(root, "", reg, idx, off, base + off)
            by_root[(".".join(reg["path"]),) + tuple(idx)] = inst

    def walk(comp, prefix: str, top: bool):
        for sub in comp["sub_components"]:
            sc = comps.get(sub["type"])
            if sc is None:
                continue
            n = sub["count"] or 0
            names = ([f"{sub['name']}[{i}]" for i in range(n)] if n
                     else [sub["name"]])
            groups = {r["path"][0] for r in sc["registers"]}
            banks = {g: (_bank(root["registers"], sc["registers"], g, n)
                         if top and n else None) for g in groups}
            for i, name in enumerate(names):
                ip = (prefix + "." if prefix else "") + name
                for reg in sc["registers"]:
                    b = banks.get(reg["path"][0])
                    for idx, off in _elements(reg):
                        addr = via = None
                        twin = None
                        if b is not None:
                            prefix_path, bbase, stride, k = b
                            lo = min(x["offset"] for x in sc["registers"]
                                     if x["path"][0] == reg["path"][0])
                            addr = base + bbase + i * stride + off - lo
                            via = "layout"
                            rp = ".".join(prefix_path + tuple(reg["path"][1:]))
                            twin = by_root.get((rp, i))
                        inst = add(sc, ip, reg, idx, off, addr, via)
                        if twin is not None:
                            inst["aliases"].append(twin["path"])
                            twin["aliases"].append(inst["path"])
                walk(sc, ip, False)

    walk(root, "", True)
    return out


def _fields(doc, inst) -> List[Dict[str, Any]]:
    vs = next((v for v in doc["value_structs"]
               if v["name"] == inst["value_struct"]), None)
    return list(vs["fields"]) if vs else []


def _touching(doc, inst) -> List[Dict[str, Any]]:
    """The operations that read or write *inst*, as ``{op, reads, writes}``
    with ``op`` the instance path of the operation (``ch[2].configure``)."""
    comps = {c["name"]: c for c in doc["components"]}
    out = []
    for ip, tname, reg in [(inst["component"], inst["type"], inst["reg"])] + [
            (a["component"], a["type"], a["reg"]) for a in inst["_alias_insts"]]:
        for op in comps[tname]["operations"]:
            r, w = reg in op.get("reads", ()), reg in op.get("writes", ())
            if r or w:
                out.append({"op": (ip + "." if ip else "") + op["name"],
                            "reads": r, "writes": w})
    return out


def explain(doc, what: str, base: int = 0) -> List[Dict[str, Any]]:
    """The register instances *what* names -- an address (``0x88``; any
    byte of the register) or a path from the root (``regs.bank[2].csr``,
    ``ch[2].regs.csr``; without indices, every element) -- each with its
    fields and the operations that touch it."""
    insts = instances(doc, base)
    by_path = {i["path"]: i for i in insts}
    addr = _number(what)
    if addr is not None:
        hits = [i for i in insts if i["addr"] is not None and not i["component"]
                and i["addr"] <= addr < i["addr"] + i["access_width"] // 8]
        if not hits:   # no root register: a sub-instance with an address
            hits = [i for i in insts if i["addr"] is not None
                    and i["addr"] <= addr < i["addr"] + i["access_width"] // 8]
    else:
        hits = ([by_path[what]] if what in by_path else
                [i for i in insts if re.sub(r"\[\d+\]", "", i["path"]) == what])
    out = []
    for h in hits:
        h = dict(h, _alias_insts=[by_path[a] for a in h["aliases"]])
        h["fields"] = _fields(doc, h)
        h["operations"] = _touching(doc, h)
        del h["_alias_insts"]
        out.append(h)
    return out


def _number(s: str) -> Optional[int]:
    try:
        return int(s.replace("_", ""), 0)
    except ValueError:
        return None


# --- the subcommands ---------------------------------------------------------

def _hex(a: Optional[int]) -> str:
    return "-" if a is None else f"0x{a:08x}"


def _sig(fn) -> str:
    ps = ", ".join(f"{p['type']} {p['name']}" for p in fn["params"])
    return f"{fn['returns']} {fn['name']}({ps})"


def _api(doc, args) -> int:
    if args.json:
        _dump({"schema": SCHEMA, "command": "api", "root": doc["root"],
               "imports": doc["imports"], "exports": doc["exports"],
               "components": [{k: c[k] for k in (
                   "name", "is_root", "constructor", "operations",
                   "sub_components", "groups")} for c in doc["components"]]})
        return 0
    for c in doc["components"]:
        print(f"component {c['name']}" + ("  (root)" if c["is_root"] else ""))
        if c["constructor"]:
            print(f"  ctor  {_sig(c['constructor'])}")
        for s in c["sub_components"]:
            print(f"  sub   {s['type']} {s['name']}"
                  + (f"[{s['count']}]" if s["count"] else ""))
        for op in c["operations"]:
            print(f"  op    {_sig(op)}")
    for fn in doc["imports"]:
        print(f"import {_sig(fn)}")
    return 0


def _regs(doc, args) -> int:
    insts = sorted(instances(doc, args.base),
                   key=lambda i: (i["addr"] is None, i["addr"] or 0, i["path"]))
    if args.json:
        _dump({"schema": SCHEMA, "command": "regs", "root": doc["root"],
               "base": args.base, "registers": insts})
        return 0
    for i in insts:
        alias = f"  = {', '.join(i['aliases'])}" if i["aliases"] else ""
        print(f"{_hex(i['addr'])}  {i['path']:<28} {i['access']:<9} "
              f"{i['value_struct'] or i['bits']}{alias}")
    return 0


def _explain(doc, args) -> int:
    hits = explain(doc, args.what, args.base)
    if args.json:
        _dump({"schema": SCHEMA, "command": "explain", "what": args.what,
               "base": args.base, "registers": hits})
        return 0 if hits else 1
    if not hits:
        print(f"pssc: no register at {args.what}", file=sys.stderr)
        return 1
    for h in hits:
        print(f"{_hex(h['addr'])}  {h['path']}  ({h['value_struct'] or 'scalar'}, "
              f"{h['bits']} bits, {h['access']})")
        for a in h["aliases"]:
            print(f"  alias       {a}  (bound by layout)")
        if h["fields"]:
            print("  fields      " + " ".join(
                f"{f['name']}[{f['lsb'] + f['width'] - 1}:{f['lsb']}]"
                if f["width"] > 1 else f"{f['name']}[{f['lsb']}]"
                for f in h["fields"]))
        rd = [o["op"] for o in h["operations"] if o["reads"]]
        wr = [o["op"] for o in h["operations"] if o["writes"]]
        print("  read by     " + (", ".join(rd) or "-"))
        print("  written by  " + (", ".join(wr) or "-"))
    return 0


def _tests(ctx, args) -> int:
    from ..testing.catalog import to_json
    from ..testing.discover import discover

    items = discover(ctx)
    if args.json:
        _dump(dict(to_json(items), command="tests"))
        return 0
    for it in items:
        loc = it.loc
        where = (f"  {getattr(loc, 'file', '')}:{getattr(loc, 'line', '')}"
                 if loc is not None else "")
        print(f"{it.qname}{where}")
    return 0


def _dump(obj) -> None:
    print(json.dumps(obj, indent=2, sort_keys=True))


def add_parser(sub) -> None:
    """Register `pssc model` on the top-level subparsers *sub*."""
    mo = sub.add_parser("model", help="query the elaborated model (from the "
                        "op-model manifest)")
    msub = mo.add_subparsers(dest="model_cmd", metavar="QUERY")
    msub.required = True

    def common(p, *, root=True):
        p.add_argument("sources", nargs="*", metavar="SRC-SPEC",
                       help="PSS file, directory, glob or @filelist "
                       "(default: the current directory)")
        if root:
            p.add_argument("--root", required=True, metavar="COMP",
                           help="the root component")
            p.add_argument("--base", type=lambda s: int(s, 0), default=0,
                           metavar="ADDR", help="the root register group's "
                           "address (default 0)")
        p.add_argument("--json", action="store_true",
                       help="print one JSON document (schema pssc.model/1)")

    common(msub.add_parser("api", help="components, constructors, operations "
                           "(with the registers each reads and writes), imports"))
    common(msub.add_parser("regs", help="every register instance and its "
                           "address"))
    ex = msub.add_parser("explain", help="the register at an address or path: "
                         "its fields and the operations that touch it")
    ex.add_argument("what", metavar="ADDR|PATH")
    common(ex)
    common(msub.add_parser("tests", help="the pssunit test catalog"),
           root=False)
    mo.set_defaults(func=cmd_model)


def cmd_model(args: argparse.Namespace) -> int:
    from ..testing.sources import SourceSpecError
    try:
        return _cmd_model(args)
    except (SourceSpecError, ValueError) as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        return 2


def _cmd_model(args: argparse.Namespace) -> int:
    if args.model_cmd == "tests":
        from ..testing import sources as _sources
        paths = [str(p) for p in _sources.all_sources(args.sources or ["."])]
        ctx = driver.translate(paths)
        if getattr(ctx, "errors", None):
            raise driver.CompileError(
                f"PSS translation failed with {len(ctx.errors)} error(s)",
                ctx.errors)
        return _tests(ctx, args)
    _, _, doc = load(args.sources, args.root)
    return {"api": _api, "regs": _regs, "explain": _explain}[
        args.model_cmd](doc, args)
