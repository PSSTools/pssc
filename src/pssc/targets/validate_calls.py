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

from typing import FrozenSet, List, Optional, Set

from .call_legality import Ctx, Outcome, classify
from .progseq_model import FuncKind, func_kind, _dt_name, sub_components


def _where(node) -> str:
    """``file:line:col: `` for a node that carries a location, else ``''``.

    Same form as `reg_rmw._where`; diagnostics that do not agree on their prefix
    are diagnostics users learn to skim past.

    IT RETURNS '' FOR EVERYTHING TODAY. `loc` is a declared field on every IR
    node and the front end populates none of them -- `ExprCall.loc`,
    `Function.loc` and `DataTypeComponent.loc` are all None on a freshly
    translated model, which means `reg_rmw`'s diagnostics have never carried a
    location either. `_site()` below supplies component::function instead, and
    this stays so that real locations appear on their own the day ast2ir fills
    them in.
    """
    loc = getattr(node, "loc", None)
    if loc is None:
        return ""
    return f"{loc.file or '<unknown>'}:{loc.line}:{loc.pos}: "


def _site(comp, fn, call) -> str:
    """The most specific location available: a real one if the IR has it, else
    the component and function that contain the call."""
    return _where(call) or f"{_name_of(comp)}::{fn.name}: "


def _name_of(dtype) -> str:
    nm = getattr(dtype, "name", None) or _dt_name(dtype)
    return nm.split("::")[-1]


def _callee_name(func) -> Optional[str]:
    """The bare name a call targets, however the IR spells the reference."""
    for attr in ("attr", "name"):
        v = getattr(func, attr, None)
        if isinstance(v, str):
            return v
    return None


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
    return None


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
            if kind is FuncKind.EXPORT_OP:
                ops.add(fn.name)
            elif kind is FuncKind.CONSTRUCTOR:
                ctors.add(fn.name)
    return comps, frozenset(ops), frozenset(ctors)


def validate_calls(root, ctx, target: str, *, report_only: bool = False,
                   ctor_names=None) -> List[str]:
    """Classify every call in the component tree rooted at ``root``.

    Returns the diagnostics. Unless ``report_only``, they are also pushed onto
    ``ctx.errors`` via ``add_error``, which is what stops the build --
    ``driver.compile`` checks ``ctx.errors`` only BEFORE the target runs, so
    the target has to check for itself (docs §5.3).
    """
    comps, ops, ctors = model_names(root, ctor_names)
    imports: FrozenSet[str] = frozenset(
        f.name for f in (getattr(ctx, "import_functions", None) or []))

    msgs: List[str] = []
    for comp in comps:
        for fn in (getattr(comp, "functions", None) or []):
            context = _context_of(fn, ctor_names)
            if context is None:
                continue
            calls: List[object] = []
            _walk_calls(getattr(fn, "body", None), calls)
            for call in calls:
                name = _callee_name(getattr(call, "func", None))
                if name is None:
                    continue
                res = classify(name, context=context, target=target,
                               model_ops=ops, imports=imports, subcomps=ctors)
                if res.outcome is Outcome.SUPPORTED:
                    continue
                msgs.append(f"{_site(comp, fn, call)}cannot lower call: "
                            f"{res.message}")

    if not report_only:
        for m in msgs:
            ctx.add_error(m)
    return msgs


def gate(root, ctx, target: str, language: str, ctor_names=None) -> None:
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
    bad = validate_calls(root, ctx, target, ctor_names=ctor_names)
    if bad:
        raise CompileError(
            f"{len(bad)} call(s) cannot be lowered to {language}", bad)
