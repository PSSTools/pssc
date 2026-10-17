"""Reject calls the backend cannot lower, before anything is written.

A PASS, not an exception inside the emitter, for three reasons (docs §5.1):

  1. It reports EVERY illegal call in one run. An exception raised from
     expression rendering aborts at the first, so a model with six unmapped
     calls would take six compiles to find out.
  2. It runs before any output file is opened. A failure part-way through
     emission leaves a truncated artifact on disk that a later dv-flow run may
     treat as up-to-date.
  3. It is testable without a backend.

The pass and the emitters share ONE registry (`call_legality`), so they cannot
drift into two independently maintained lists -- which is the failure mode this
design would otherwise introduce. The pass produces the user-facing diagnostic;
an emitter that nevertheless meets an unclassified call raises, because by then
the pass has vouched for it and arriving there is a compiler bug.
"""
from __future__ import annotations

import dataclasses as dc

from typing import Dict, FrozenSet, List, Optional, Set

from ..diag import at
from . import pkg_functions as pf
from .call_legality import Ctx, Outcome, classify, core_function
from .progseq_model import (FuncKind, INIT_EXEC_KINDS, exec_kind, func_kind,
                            is_reg_group, _dt_name, sub_components)


def _site(comp, fn) -> str:
    """``comp::fn: ``: what a diagnostic names besides its location (`at`),
    so a reader holding the generated API finds the function too."""
    if comp is None:
        return f"{pf.qualified_name(fn)}: "
    return f"{_name_of(comp)}::{fn.name}: "


def _name_of(dtype) -> str:
    nm = getattr(dtype, "name", None) or _dt_name(dtype)
    return nm.split("::")[-1]


def callee_name(func) -> Optional[str]:
    """The name a call is classified by, however the IR spells the reference.

    A call qualified by a core-library package is classified by its short
    name: `addr_reg_pkg::write32` is `write32` (`call_legality.core_function`).
    Any other qualified name stays qualified, so the registry finds nothing
    under it and the call is refused rather than mistaken for a built-in.
    """
    for attr in ("attr", "name"):
        v = getattr(func, attr, None)
        if isinstance(v, str):
            return core_function(v) or v
    return None


def is_core_call(func) -> bool:
    """True if the call was WRITTEN qualified by a core-library package.

    The spelling changes nothing about which function it is:
    `addr_reg_pkg::write32(h, d)` is delegated to the active executor exactly
    as `write32(h, d)` is (LRM 21.13.9.5), so inside that executor's own
    `write32` it is a call to itself. The default implementation is
    `super.write32(...)` (`is_super_call`).
    """
    return (_dt_name(func) == "ExprRefUnresolved"
            and core_function(getattr(func, "name", "") or "") is not None)


def is_super_call(func) -> bool:
    """True for `super.f(...)`: the base type's `f`, never an override."""
    return (_dt_name(func) == "ExprAttribute"
            and _dt_name(getattr(func, "value", None)) == "TypeExprRefSuper")


#: Function kinds a call on the model reaches as an operation: a `target`
#: (or unqualified) function, and a `solve` function that is not the
#: constructor -- the second only from solve context (`solve_ops`).
_OP_KINDS = (FuncKind.EXPORT_OP, FuncKind.EXPORT_SOLVE)


def own_ops(comp, ctor_names=None) -> FrozenSet[str]:
    """The operations ``comp`` itself declares, solve functions included."""
    return frozenset(fn.name for fn in (getattr(comp, "functions", None) or [])
                     if func_kind(fn, ctor_names) in _OP_KINDS)


def solve_ops(root, ctor_names=None) -> FrozenSet[str]:
    """The model's solve functions other than constructors, by name: the
    operations a constructor and another solve function may call, and a
    target function may not (LRM 22.2.3)."""
    return frozenset(fn.name for comp in _components(root)
                     for fn in (getattr(comp, "functions", None) or [])
                     if func_kind(fn, ctor_names) is FuncKind.EXPORT_SOLVE)


def ops_for_call(comp, func, ops: FrozenSet[str],
                 ctor_names=None, super_base=None) -> FrozenSet[str]:
    """The operation names a call of this FORM may be classified against.

    ``ops`` is every operation in the model, by name (`model_names`). That is
    the right set for a call through a sub-component (`self.ch.start()`), whose
    receiver this pass does not type. It is the WRONG set for `self.f()`: the
    front end writes a call to a core-library function that way too, so an
    executor anywhere in the model that overrides `read32` (LRM 21.13.9.5)
    would turn every other component's `read32(h)` into a call to a method it
    does not have. `self.f()` is an operation only if ``comp`` declares `f`,
    and neither is a call written `addr_reg_pkg::read32(...)` or
    `super.read32(...)`. (A base component's operations are not inherited by
    the lowered component, so `super.f()` has no operation to name.)
    """
    if is_super_call(func):
        # The base's operation, where the body belongs to a class rendered as
        # derived from its base's (`super_base`); nothing otherwise.
        return (own_ops(super_base, ctor_names) if super_base is not None
                else frozenset())
    if is_core_call(func):
        return frozenset()
    if (_dt_name(func) == "ExprAttribute"
            and _dt_name(getattr(func, "value", None)) == "TypeExprRefSelf"):
        return own_ops(comp, ctor_names) if comp is not None else frozenset()
    return ops


def _walk_calls(node, out: List[object]) -> None:
    """Collect every ExprCall reachable from ``node``.

    Traverses by DATACLASS FIELDS, not by a hand-written list of child
    attribute names. The list version of this shipped for one iteration and
    missed `StmtExpr.expr` -- so a bare `write_bytes(h)` statement, the most
    ordinary call shape there is, was walked straight past and the whole gate
    silently passed the model. A gate whose traversal has holes is worse than no
    gate: it reports "no problems" with authority.

    The field walk cannot develop that hole. An IR node added later, or a field
    added to an existing one, is followed because it exists, not because someone
    remembered to add its name here.
    """
    if node is None or isinstance(node, (str, int, float, bool)):
        return
    if isinstance(node, (list, tuple, set)):
        for n in node:
            _walk_calls(n, out)
        return
    if isinstance(node, dict):
        for n in node.values():
            _walk_calls(n, out)
        return

    fields = getattr(type(node), "__dataclass_fields__", None)
    if fields is None:
        return                      # a leaf: enum, loc record, or a plain value
    if _dt_name(node) == "ExprCall":
        out.append(node)
    for fname in fields:
        child = getattr(node, fname, None)
        if child is not node:
            _walk_calls(child, out)


def _context_of(fn, ctor_names=None) -> Optional[Ctx]:
    """Which context ``fn``'s body is lowered into, or None if it is not
    lowered at all (a declaration, or an offset function that is evaluated)."""
    kind = func_kind(fn, ctor_names)
    if kind is FuncKind.EXPORT_OP:
        return Ctx.TARGET
    if kind in (FuncKind.CONSTRUCTOR, FuncKind.EXPORT_SOLVE):
        return Ctx.SOLVE
    if kind is FuncKind.EXEC and exec_kind(fn) in INIT_EXEC_KINDS:
        # Solve context: the tree is built before any scenario runs (20.1.2).
        # Whether the target lowers them at all is `exec_blocks`' question.
        return Ctx.SOLVE
    return None


def exec_blocks(root, ctx, target: str, init_blocks: bool) -> List[str]:
    """Refuse every component exec block the target would not run.

    An exec block the backend does not lower is a statement the model makes
    that the generated code silently does not: `exec init_down { a = 10; }`
    dropped leaves `a` at its initializer, and nothing says so. So a target
    either lowers init_down/init_up (``init_blocks``) or is refused them here,
    and every other component exec kind is refused everywhere.

    Inherited blocks arrive with the component (`comp_inherit`): a kind the
    derived component does not declare is the base's, and one it declares
    shadows the base's. `super;` in a derived block is the backend's to resolve
    or refuse (op-model-py's `stmt_super` refuses it outside an entry).
    """
    msgs: List[str] = []
    for comp in _components(root):
        kinds = {}
        for fn in getattr(comp, "functions", None) or []:
            if exec_kind(fn) is not None:
                kinds.setdefault(exec_kind(fn), fn)
        for k, fn in kinds.items():
            if k not in INIT_EXEC_KINDS:
                msgs.append(at((fn, comp), f"{_name_of(comp)}: `exec {k}` is "
                               f"not lowered by '{target}'"))
            elif not init_blocks:
                msgs.append(at((fn, comp), f"{_name_of(comp)}: `exec {k}` is "
                               f"not lowered by '{target}' yet"))
    return msgs


def _components(root) -> List[object]:
    """Every regular component reachable from ``root``, root first.

    A local walk rather than `walk_tree`, which needs a type resolver this pass
    has no reason to carry. Register groups are not visited: their only
    functions are the offset accessors, which are evaluated rather than lowered.
    """
    out: List[object] = []
    seen: Set[int] = set()

    def visit(comp):
        if id(comp) in seen:
            return
        seen.add(id(comp))
        out.append(comp)
        for sub in sub_components(comp):
            visit(sub.dtype)

    visit(root)
    return out


def model_names(root, ctor_names=None):
    """Names the model itself supplies: operations, and sub-component ctors.

    Public because an emitter dispatching on `Disposition` has to classify a
    call the same way this pass did (P7.T4). Two implementations of "what
    counts as an operation" would disagree exactly where it matters -- the pass
    vouches for a call and the emitter then fails to place it.
    """
    ops: Set[str] = set()
    ctors: Set[str] = set()
    comps = _components(root)
    for comp in comps:
        for fn in (getattr(comp, "functions", None) or []):
            kind = func_kind(fn, ctor_names)
            if kind in _OP_KINDS:
                ops.add(fn.name)
            elif kind is FuncKind.CONSTRUCTOR:
                ctors.add(fn.name)
    return comps, frozenset(ops), frozenset(ctors)


def _with_extra(comps, extra) -> List[object]:
    """``comps`` plus the ``extra`` ones not already in it."""
    have = {id(c) for c in comps}
    return list(comps) + [c for c in extra or () if id(c) not in have]


def package_bodies(root, ctx, ctor_names=None, entries=(),
                   extra_components=()):
    """`pkg_functions.reach` from everything the tree rooted at ``root``
    lowers: ``[(function, context)]``. The ONE answer to "which package
    functions does this model carry", used by the gate and by `OpModel`.

    ``extra_components`` are rendered too though not in the tree: the base
    classes of a target that renders inheritance natively."""
    comps, ops, ctors = model_names(root, ctor_names)
    comps = _with_extra(comps, extra_components)
    bodies = [(fn, _context_of(fn, ctor_names))
              for comp in comps
              for fn in (getattr(comp, "functions", None) or [])]
    bodies += [(fn, Ctx.TARGET) for _, fn in entries]
    return pf.reach(ctx, bodies)


def rendered_bodies(root, ctx, *, ctor_names=None, entries=(),
                    pkg_functions: bool = False, init_blocks: bool = False,
                    extra_components=(), native: bool = False):
    """``[(component, function, context, super_base)]``: every body the
    target renders, and the context it runs in. The ONE list the gate's
    passes over bodies walk (this one, `body_kinds`).

    A target rendering inheritance natively (``native``) renders a derived
    component's DECLARED functions, `super` intact, and reaches the rest
    through its base class (``super_base``); a flattening one renders
    `comp_inherit`'s completed view. A package function has no component.
    A body that is not lowered at all (a declaration, an offset function,
    which is evaluated) is left out."""
    from .comp_inherit import declared as _declared_members
    comps, _ops, _ctors = model_names(root, ctor_names)
    comps = _with_extra(comps, extra_components)
    bodies = []
    for comp in comps:
        dec = _declared_members(ctx, comp) if native else None
        fns = dec.functions if dec is not None else (
            getattr(comp, "functions", None) or [])
        sb = dec.base if dec is not None else None
        bodies += [(comp, fn, _context_of(fn, ctor_names), sb) for fn in fns
                   if init_blocks
                   or func_kind(fn, ctor_names) is not FuncKind.EXEC]
    bodies += [(comp, fn, Ctx.TARGET, None) for comp, fn in entries]
    if pkg_functions:
        bodies += [(None, fn, c, None) for fn, c in
                   package_bodies(root, ctx, ctor_names, entries,
                                  extra_components)]
    return [b for b in bodies if b[2] is not None]


def validate_calls(root, ctx, target: str, *, report_only: bool = False,
                   ctor_names=None, entries=(),
                   pkg_functions: bool = False,
                   init_blocks: bool = False,
                   extra_components=(), native: bool = False,
                   string_methods: bool = False) -> List[str]:
    """Classify every call in the component tree rooted at ``root``.

    ``entries`` is ``[(component, function)]`` for the exported actions
    (`export_action.EntryPoint`): target-context code that runs in
    ``component`` but is not one of its declared functions.

    ``pkg_functions`` says whether the target lowers package-scope functions.
    If it does, each one the model reaches is checked too, in the context it
    runs in (`pkg_functions.py`); if not, a call to one is refused by name.

    ``string_methods`` says whether it lowers the string methods
    (`string_methods.RESULTS`). A call to one is recognised by its
    receiver's type, before its name is classified (E1).

    ``init_blocks`` says whether it lowers `exec init_down`/`init_up`. If it
    does, their bodies are checked in solve context; if not, each is refused
    (:func:`exec_blocks`).

    Returns the diagnostics. Unless ``report_only``, they are also pushed onto
    ``ctx.errors`` via ``add_error``, which is what stops the build --
    ``driver.compile`` checks ``ctx.errors`` only BEFORE the target runs, so
    the target has to check for itself (docs §5.3).
    """
    _comps, ops, ctors = model_names(root, ctor_names)
    solve_names = solve_ops(root, ctor_names)
    import_fns = getattr(ctx, "import_functions", None) or []
    imports: FrozenSet[str] = frozenset(f.name for f in import_fns)
    # Only the `target` qualifier is enforced on imports: a target import
    # cannot run while the tree is built. The converse -- a `solve` import
    # called from a target function -- is also illegal (20.2.1.3), but models
    # and tests in this repo do it (`test_c_imports.py`), so refusing it is a
    # separate decision.
    import_contexts = {f.name: pf.contexts_of(f) for f in import_fns
                       if getattr(f, "is_target", False)}
    # An `output`/`inout` parameter (20.4.1) of an import: every op-model
    # target passes a scalar by value, so the callee's write would be lost
    # (W18). Refused until a target carries the direction.
    import_dirs: Dict[str, Dict[str, str]] = {}
    for f in import_fns:
        dirs = (getattr(f, "metadata", None) or {}).get("param_dirs")
        if dirs:
            import_dirs[f.name] = dirs
            qn = f.metadata.get("qualified_name")
            if qn:
                import_dirs[qn] = dirs

    msgs: List[str] = exec_blocks(root, ctx, target, init_blocks)
    declared = pf.declared(ctx)
    for comp, fn, context, super_base in rendered_bodies(
            root, ctx, ctor_names=ctor_names, entries=entries,
            pkg_functions=pkg_functions, init_blocks=init_blocks,
            extra_components=extra_components, native=native):
        calls: List[object] = []
        _walk_calls(getattr(fn, "body", None), calls)
        in_pkg = comp is None
        types = None
        for call in calls:
            def say(text, call=call):
                msgs.append(at((call, fn), f"{_site(comp, fn)}{text}"))
            func = getattr(call, "func", None)
            name = callee_name(func)
            if name is None:
                continue
            if _dt_name(func) == "ExprAttribute":
                from .expr_types import ExprTypes
                from .string_methods import LISTS, method_of
                types = types or ExprTypes(fn, comp, ctx)
                m = method_of(call, types)
                if m in LISTS:
                    say(
                        f"cannot lower call: the string "
                        f"method '{m}' returns a list, which '{target}' does "
                        f"not hold (ruling R4)")
                    continue
                if m is not None:
                    if not string_methods:
                        say(
                            f"cannot lower call: "
                            f"'{m}' is a string method (PSS 7.6.3), which "
                            f"'{target}' does not lower")
                    continue
            dirs = import_dirs.get(name)
            if dirs:
                p, d = next(iter(dirs.items()))
                say(
                    f"cannot lower call: '{name}' has "
                    f"an {d} parameter '{p}', which '{target}' does not "
                    f"lower yet (only input parameters are supported)")
                continue
            callee = pf.callee(call, declared)
            if callee is not None and not pkg_functions:
                say(
                    f"cannot lower call: '{name}' is a "
                    f"package-scope function, which '{target}' does not "
                    f"lower yet")
                continue
            if callee is not None:
                # The front end says which function this is; the model's own
                # names do not enter into it.
                res = classify(name, context=context, target=target,
                               pkg_funcs={name: pf.contexts_of(callee)})
            else:
                res = classify(name, context=context, target=target,
                               model_ops=(frozenset() if in_pkg else
                                          ops_for_call(comp, func, ops,
                                                       ctor_names,
                                                       super_base)),
                               imports=imports,
                               import_contexts=import_contexts,
                               subcomps=frozenset() if in_pkg else ctors,
                               solve_ops=solve_names)
            if res.outcome is Outcome.SUPPORTED:
                continue
            say(f"cannot lower call: {res.message}")

    if not report_only:
        for m in msgs:
            ctx.add_error(m)
    return msgs


def array_signatures(comps) -> List[str]:
    """Each function with an array parameter or result, as
    ``"<comp>::<fn>: an array parameter 'v'"`` / ``"...: an array result"``.

    PSS passes an aggregate by handle (LRM 20.3.2). A target that cannot
    refuses it here: C and SV failed with an internal error, and C++'s
    `std::array` is a value, so a callee's writes would be lost."""
    out: List[str] = []
    for comp in comps:
        cname = _name_of(comp)
        for fn in getattr(comp, "functions", None) or []:
            for a in (getattr(getattr(fn, "args", None), "args", None)
                      or []):
                if _dt_name(getattr(a, "annotation", None)) \
                        == "DataTypeArray":
                    out.append(at((a, fn), f"{cname}::{fn.name}: an array "
                                  f"parameter '{a.arg}' is not supported "
                                  f"yet"))
            if _dt_name(getattr(fn, "returns", None)) == "DataTypeArray":
                out.append(at(fn, f"{cname}::{fn.name}: an array result is "
                                  f"not supported yet"))
    return out


def gate(root, ctx, target: str, language: str, ctor_names=None,
         entries=(), pkg_functions: bool = False,
         init_blocks: bool = False, extra_components=(),
         native: bool = False, array_params: bool = False,
         string_methods: bool = False) -> None:
    """Run :func:`validate_calls` and raise if anything is unlowerable.

    Every operation-model backend calls this as its first act, BEFORE any file
    is opened -- see `progseq_gen.generate` for the two reasons that matters
    (a truncated artifact looks up-to-date to dv-flow, and `driver.compile`
    inspects `ctx.errors` only before the target runs).

    One function rather than three copies of the same six lines, because the
    three copies is how the gate came to run for SystemVerilog only: it was
    written where the SV backend needed it and never propagated to the two
    backends with the NARROWEST lowering, which are the ones most likely to
    meet a call they cannot render. `OpModelTarget.check()` absorbs this in
    P2 of docs/design/generator-style-extensions-plan.md.
    """
    from ..driver import CompileError
    from .array_literal import literal_errors
    lists = literal_errors(_with_extra(_components(root), extra_components),
                           ctx)
    if lists:
        raise CompileError(
            f"{len(lists)} array initializer(s) do not fit their array",
            lists)
    if not array_params:
        sigs = array_signatures(_with_extra(_components(root),
                                            extra_components))
        if sigs:
            raise CompileError(
                f"{len(sigs)} array parameter(s) or result(s) cannot be "
                f"lowered to {language} yet", sigs)
    from .field_writes import refusals as field_write_refusals
    writes = field_write_refusals(
        _with_extra(_components(root), extra_components), ctx,
        entries=entries, ctor_names=ctor_names)
    if writes:
        raise CompileError(
            f"{len(writes)} function(s) write a component field from target "
            f"code", writes)
    from .error_strings import refusals as error_string_refusals
    strings = error_string_refusals(
        _with_extra(_components(root), extra_components), ctx,
        entries=entries, ctor_names=ctor_names)
    if strings:
        raise CompileError(
            f"{len(strings)} error()/fatal() argument(s) are not known at "
            f"solve time", strings)
    bad = validate_calls(root, ctx, target, ctor_names=ctor_names,
                         entries=entries, pkg_functions=pkg_functions,
                         init_blocks=init_blocks,
                         extra_components=extra_components, native=native,
                         string_methods=string_methods)
    if bad:
        raise CompileError(
            f"{len(bad)} call(s) cannot be lowered to {language}", bad)
