"""`pssc build --image OUT`: write a root component's model image (P3.2).

The image is the debug-profile ``.zbc`` ``pssc test`` lowers in memory: every
test, set-up, tear-down, interrupt-handler and mock-hook entry keyed by stable
symbol, the line table and provenance, the API hash and ``--generation``, and
the PSS sources (``--no-sources`` leaves them out). What pssc needs beside
the bytecode to list and run the tests rides in the image's ``META`` section
(``ZbcModel.meta``), one JSON document each:

- ``pssc.catalog``: the root's tests (``pssunit.catalog/1``, as
  ``pssc test --collect-only --json`` prints it);
- ``pssc.image``: the root, its set-up / tear-down / interrupt-handler
  symbols and the ``@pssunit_pkg::allow`` table;
- ``pssc.registers``: the register map and field semantics (reg-id ->
  register), the mock hooks and the check sites;
- ``pssc.pyvm``: what pyvm needs to run the image that has no be-bc
  encoding yet (solve problems, activation and component tables, the
  per-coroutine debug tables, the register objects) and the test items,
  pickled. Interim, pyvm only, until the native engine (plan P4.3)
  specifies an encoding; :func:`read` refuses one from another be-bc ABI.
  An image file is a trusted build output: reading one unpickles it.

One image holds one root component; with several in the sources,
``--root`` picks one.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses as dc
import json
import os
import pickle
import sys
from typing import Any, Dict, List, Optional

#: schema of ``pssc build --json`` and of the ``pssc.image`` document
SCHEMA = "pssc.image/1"
CATALOG = "pssc.catalog"
IMAGE = "pssc.image"
#: plain-text test list for the C engine over DPI (:func:`_native_doc`)
NATIVE = "pssc.native"
REGISTERS = "pssc.registers"
PYVM = "pssc.pyvm"
PYVM_SCHEMA = "pssc.pyvm/1"
#: ``ZbcModel`` side channels the image's sections already carry
_SERIALIZED = ("entries", "sources", "meta")


class BuildError(Exception):
    """A build that cannot go ahead (no tests, an ambiguous root)."""


def _dump(doc: Any) -> str:
    return json.dumps(doc, sort_keys=True, separators=(",", ":"),
                      default=_jsonable)


def _jsonable(v):
    if isinstance(v, (set, frozenset)):
        return sorted(v)
    if dc.is_dataclass(v) and not isinstance(v, type):
        return dc.asdict(v)
    raise TypeError(f"not JSON-serializable: {type(v).__name__}")


def build(coll, root: Optional[str] = None, generation: int = 0,
          sources: bool = True, api: bool = False):
    """Lower the root of ``coll`` (``testing.cmd.Collection``) to a debug
    model carrying its catalog, register map and identity; returns
    ``(model, root)``. Raises :class:`BuildError`, or what the lowering
    raised.

    ``api``: ``root`` is any component (an op-model root, say) and every
    function in its tree gets an entry, for a facade that calls them by
    symbol (target ``op-model-sv-vm``); it need not hold tests."""
    from pssc.testing import catalog, runner
    from pssc.testing import vm as _vm

    roots: List[str] = []
    for it in coll.items:
        if it.comp not in roots:
            roots.append(it.comp)
    if api:
        if root is None:
            raise BuildError("--api needs --root")
    elif root is not None:
        if root not in roots:
            raise BuildError(f"--root {root}: no tests under it; roots: "
                             + (", ".join(roots) or "none"))
    elif len(roots) == 1:
        root = roots[0]
    elif not roots:
        raise BuildError("no tests found")
    else:
        raise BuildError("several roots, pick one with --root: "
                         + ", ".join(roots))
    items = [it for it in coll.items if it.comp == root]
    functions = [it.decl for it in items if it.kind == "function"]
    if api:
        functions = ["*"] + functions
    image = runner._build_image(coll.ctx, root, functions, debug=True)
    if image.error is not None:
        raise image.error
    model = image.model
    model.generation = generation
    if not sources:
        model.sources = {}
    setup, teardown = catalog.hooks(coll.ctx, root)
    # "setup" leaves the session functions out: a simulation runs them
    # once (pssvm_runner), pyvm before each test (``load``)
    session = catalog.session_hooks(coll.ctx, root)
    setup = setup[len(session):]
    model.meta = {
        CATALOG: _dump(catalog.to_json(items)),
        IMAGE: _dump({
            "schema": SCHEMA, "root": root, "session": session,
            "setup": setup, "teardown": teardown,
            "irq": catalog.function_of(coll.ctx, root, _vm.IRQ_HANDLER),
            "allows": getattr(model, "allows", {}) or {},
        }),
        REGISTERS: _dump({
            "registers": {str(k): v for k, v in
                          (getattr(model, "registers", None) or {}).items()},
            "hooks": list(getattr(model, "hooks", None) or []),
            "checks": {str(k): v for k, v in
                       (getattr(model, "checks", None) or {}).items()},
        }),
    }
    fields = {it.type_name: runner._action_fields(coll.ctx, it.type_name)
              for it in items if it.kind != "function"}
    model.meta[PYVM] = _pyvm_doc(model, items, fields)
    model.meta[NATIVE] = _native_doc(model, items, session, setup, teardown)
    return model, root


def _native_doc(model, items, session, setup, teardown) -> str:
    """The test list for the C engine's DPI adapter, which reads no JSON:
    tab-separated lines ``test <comp::fn> <expected error>``, then
    ``session``/``setup``/``teardown <comp::fn>``, ``inst <path> <type>
    <base> <end> <sig>`` for each component instance in the image's order
    (the C engine's call-by-path lookup and its reload's migration by path),
    and ``fail <slot>``, the component-object slot of each pssunit
    ledger's ``n_fail`` (as :func:`pssc.testing.bc_session.ledger` finds
    them)."""
    from zuspec.be.bc.interp.generations import native_inst_lines

    lines = [f"test\t{it.qname}\t{it.expect_error or ''}" for it in items
             if it.kind == "function" and not it.meta.skip]
    for kind, fns in (("session", session), ("setup", setup), ("teardown", teardown)):
        lines += [f"{kind}\t{fn}" for fn in fns]
    comps = getattr(model, "components", None)
    if comps is not None:
        names = list(comps.names)
        lines += native_inst_lines(model)
        for path in comps.paths:
            pre = f"{path}." if path else ""
            if all(pre + f in names for f in ("n_pass", "n_fail", "n_skip")):
                lines.append(f"fail\t{names.index(pre + 'n_fail')}")
    return "\n".join(lines) + "\n"


def native_doubles(model, hosted) -> List[str]:
    """Why ``model`` cannot run on the C engine in a simulation, which has
    no Python (``pssc build --native``, plan O14): one line per Python
    double of ``hosted`` (``vm.Hosted``, the run's platform) that names a
    function or import of the image, with the double's ``file:line`` and
    the PSS function's. Empty: nothing in the image needs Python."""
    from pssc.testing import vm as _vm

    reg = _vm._MODULES.get(getattr(getattr(hosted, "module", None),
                                   "__name__", None))
    if reg is None:
        return []
    where = {}
    for c in model.coros:
        q = getattr(c, "fn_qname", "")
        if q and q not in where:
            # a function's coroutine has no src_ref of its own: its first
            # statement's
            refs = [c.src_ref] + [i.src_ref for i in c.code]
            where[q] = next((w for w in (_vm._where(model, r)
                                         for r in refs if r) if w), "?")
    imports = getattr(model, "imports", None) or {}

    def at(fn) -> str:
        code = getattr(fn, "__code__", None)
        return (f"{code.co_filename}:{code.co_firstlineno}" if code
                else repr(fn))

    out = []
    doubles = [("mock", q, [fn]) for q, fn in reg.mocks.items()]
    doubles += [("spy", q, fns) for q, fns in reg.spies.items()]
    for kind, q, fns in doubles:
        if q in where:
            for fn in fns:
                out.append(f"{at(fn)}: @pv.{kind}({q!r}) is Python; "
                           f"{q} ({where[q]}) cannot run without it")
    for name, fn in reg.implements_.items():
        hit = [k for k in imports
               if k == name or k.rpartition("::")[2] == name]
        if hit:
            out.append(f"{at(fn)}: @pv.implements({name!r}) is Python; "
                       f"import {hit[0]} has no other implementation")
    return out


def _side_channels(model) -> Dict[str, Any]:
    """The model's in-memory state: dataclass fields excluded from ``==``
    (bar :data:`_SERIALIZED`), attributes pssc set beside them, and each
    coroutine's in-memory fields."""
    names = {f.name for f in dc.fields(model)}
    out = {f.name: getattr(model, f.name) for f in dc.fields(model)
           if not f.compare and f.name not in _SERIALIZED}
    out.update({k: v for k, v in vars(model).items() if k not in names})
    out["coros"] = [{f.name: getattr(c, f.name) for f in dc.fields(c)
                     if not f.compare} for c in model.coros]
    return out


def _pyvm_doc(model, items, fields) -> str:
    from zuspec.be.bc.model import ABI_ID

    state = {"model": _side_channels(model), "items": items,
             "fields": fields}
    return _dump({"schema": PYVM_SCHEMA, "abi_id": ABI_ID,
                  "pickle": base64.b64encode(
                      pickle.dumps(state, protocol=4)).decode("ascii")})


def _restore(model, doc: Dict[str, Any]) -> Dict[str, Any]:
    from zuspec.be.bc.model import ABI_ID

    if doc.get("schema") != PYVM_SCHEMA or doc.get("abi_id") != ABI_ID:
        raise BuildError(f"image built for {doc.get('schema')} / be-bc ABI "
                         f"{doc.get('abi_id')}; this pssc reads "
                         f"{PYVM_SCHEMA} / {ABI_ID}: rebuild it")
    state = pickle.loads(base64.b64decode(doc["pickle"]))
    side = dict(state["model"])
    coros = side.pop("coros")
    if len(coros) != len(model.coros):
        raise BuildError("image's pyvm tables do not match its coroutines")
    for k, v in side.items():
        setattr(model, k, v)
    for c, extra in zip(model.coros, coros):
        for k, v in extra.items():
            setattr(c, k, v)
    return state


def read(path: str):
    """``(model, meta)``: the ``ZbcModel`` of image file ``path`` and its
    ``META`` documents, parsed."""
    from zuspec.be.bc.model import ZbcModel

    with open(path, "rb") as f:
        model = ZbcModel.from_bytes(f.read())
    meta = {k: json.loads(v) for k, v in model.meta.items() if k != NATIVE}
    if PYVM in meta:
        meta[PYVM] = _restore(model, meta[PYVM])
    return model, meta


def load(path: str):
    """``(image, items, meta)`` of image file ``path``: a runnable
    ``runner._Image``, its test items (``TestItem``) and its ``META``
    documents. Raises :class:`BuildError` for an image pyvm cannot run."""
    from pssc.testing import bc_session, runner

    model, meta = read(path)
    if PYVM not in meta:
        raise BuildError(f"{path}: no {PYVM} document (not a pssc image?)")
    info = meta[IMAGE]
    entries = bc_session.entries(model)
    state = meta[PYVM]
    image = runner._Image(model=model, entries=entries,
                          setup=[entries[s] for s in
                                 (info.get("session") or []) + info["setup"]],
                          teardown=[entries[s] for s in info["teardown"]],
                          fields=state["fields"])
    return image, state["items"], meta


def summary(model, root: str, path: str) -> Dict[str, Any]:
    tests = json.loads(model.meta[CATALOG])["tests"]
    return {"schema": SCHEMA, "image": path, "root": root,
            "tests": [t["id"] for t in tests],
            "entries": sorted(model.entries),
            "api_hash": f"{model.api_hash:016x}",
            "generation": model.generation,
            "sources": sorted(model.sources)}


def cmd_build(args: argparse.Namespace) -> int:
    from pssc import diag, driver
    from pssc.testing import cmd as _cmd

    try:
        coll = _cmd.collect(args.sources or ["."])
        model, root = build(coll, root=args.root,
                            generation=args.generation,
                            sources=args.sources_in_image, api=args.api)
    except BuildError as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        return 2
    except driver.CompileError as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        for err in getattr(e, "errors", None) or []:
            print(diag.indent_block(str(err)), file=sys.stderr)
        return 2
    except _cmd._compile_errors() as e:
        print(f"pssc: error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"pssc: error: cannot lower {args.root or 'the model'}: "
              f"{type(e).__name__}: {e}", file=sys.stderr)
        return 1
    if args.native:
        from pssc.testing import vm as _vm
        spec = _vm.declared_platform(args.platform)
        try:
            hosted = (_vm.load_platform(spec)
                      if spec and spec != "fake" else None)
        except _vm.PlatformLoadError as e:
            print(f"pssc: error: --native: {e}", file=sys.stderr)
            return 2
        why = native_doubles(model, hosted)
        if why:
            print(f"pssc: error: --native: {root} needs Python doubles of "
                  f"platform {spec!r}, and a simulation on the C engine has "
                  "no Python (use a PSS mock hook, or +pss_impl=vm):",
                  file=sys.stderr)
            for line in why:
                print(f"  {line}", file=sys.stderr)
            return 2
    out = args.image
    if os.path.dirname(out):
        os.makedirs(os.path.dirname(out), exist_ok=True)
    data = model.to_bytes()
    with open(out, "wb") as f:
        f.write(data)
    s = summary(model, root, out)
    if args.json:
        print(json.dumps(s, indent=2))
    else:
        print(f"wrote {out}: {root}, {len(s['tests'])} test(s), "
              f"{len(s['entries'])} entries, api hash {s['api_hash']}, "
              f"generation {model.generation}, {len(data)} bytes")
    return 0


def add_parser(sub) -> None:
    """Register `pssc build` on the top-level subparsers *sub*."""
    b = sub.add_parser("build", help="write a model image (.zbc) a test "
                       "run or a simulation can load")
    b.add_argument("sources", nargs="*", metavar="SRC-SPEC",
                   help="PSS file, directory, glob or @filelist, as for "
                   "pssc test; default: the current directory")
    b.add_argument("--image", required=True, metavar="OUT",
                   help="the image file to write (e.g. out/model.zbc)")
    b.add_argument("--root", metavar="COMP",
                   help="the root component (suite or fixture) to build; "
                   "needed when the sources have several")
    b.add_argument("--api", action="store_true",
                   help="give every function under --root an entry, so a "
                   "generated facade (target op-model-sv-vm) can call it; "
                   "--root need not hold tests")
    b.add_argument("--generation", type=int, default=0, metavar="N",
                   help="the build number recorded in the image (default 0 "
                   "= unset, so the image is reproducible)")
    b.add_argument("--no-sources", dest="sources_in_image",
                   action="store_false",
                   help="leave the PSS sources out of the image")
    b.add_argument("--native", action="store_true",
                   help="the image is for the C engine in a simulation, "
                   "which has no Python: refuse it when the platform's "
                   "Python doubles (@pv.mock/spy/implements) name a "
                   "function or import in it, with their locations")
    b.add_argument("--platform", metavar="SPEC",
                   help="with --native: the Python platform whose doubles "
                   "to check (default: [tool.pssunit] platform)")
    b.add_argument("--json", action="store_true",
                   help=f"print one JSON document (schema {SCHEMA})")
    b.set_defaults(func=cmd_build)
