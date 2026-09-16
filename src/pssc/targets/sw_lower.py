"""IR lowering for the C (software) targets.

`zuspec-be-sw`'s ``CGenerator`` emits C only for ``DataTypeComponent`` (and
``DataTypeProtocol``). PSS **actions** are ``DataTypeClass`` (a ``DataTypeStruct``
subtype), so their coroutine ``body`` is skipped by the component walk.

``to_sw_context`` produces a *new* ``Context`` in which each PSS action is
re-expressed as a ``DataTypeComponent`` carrying the same fields/functions, so the
backend lowers its ``is_async`` body to a C coroutine. The original context (used
by the SV/Python paths) is left untouched.

It also maps the PSS reporting builtins ``print`` / ``message`` onto the backend's
``print()`` → ``fprintf`` path (see ``_lower_builtin_call``).

Scope: this slice handles the action -> component bridge and print/message.
Activities, flow-object scheduling, and constraint solving are future work tracked
in the implementation plan (Phase 3).
"""
from __future__ import annotations

import dataclasses as _dc

import zuspec.ir.core as ir

from .sw_solve import solve_action, problem_bytes, seed_for


# ---------------------------------------------------------------------------
# PSS builtin -> C mapping
#
# pssc emits PSS reporting builtins as method calls on self:
#   print(x)                -> ExprCall(ExprAttribute(self, 'print'),   [x])
#   message(VERB, fmt, ...) -> ExprCall(ExprAttribute(self, 'message'), [VERB, fmt, ...])
# zuspec-be-sw recognises print() only as a *bare* call (ExprRefUnresolved
# 'print') and renders it to fprintf; it supports a single %-format value
# (print(fmt % value)). We rewrite the PSS forms into that bare-print form.
# ---------------------------------------------------------------------------

def _lower_builtin_call(expr):
    """Map a ``self.print(...)`` / ``self.message(...)`` call to zuspec-be-sw's
    bare ``print()`` form. Return the rewritten ``ExprCall`` or ``None`` if this
    expression is not a mappable PSS builtin."""
    if not (isinstance(expr, ir.ExprCall)
            and isinstance(expr.func, ir.ExprAttribute)
            and isinstance(expr.func.value, ir.TypeExprRefSelf)):
        return None
    name = expr.func.attr
    if name == "print":
        # print(fmt, *values)
        args = list(expr.args)
    elif name == "message":
        # message(verbosity, fmt, *values): drop the verbosity level.
        args = list(expr.args[1:])
    else:
        return None
    if not args:
        return None
    # Emit bare print(fmt, v1, v2, ...); the backend renders
    # fprintf(stdout, "fmt\n", v1, v2, ...) (any number of format values).
    return ir.ExprCall(func=ir.ExprRefUnresolved(name="print"), args=args)


def _rewrite_stmt(stmt):
    """Return ``stmt`` with PSS builtin calls lowered, rebuilding only the nodes
    that change (originals are never mutated, so the shared SV/Python IR is safe)."""
    if isinstance(stmt, ir.StmtExpr):
        lowered = _lower_builtin_call(stmt.expr)
        return _dc.replace(stmt, expr=lowered) if lowered is not None else stmt

    changes = {}
    for f in _dc.fields(stmt):
        val = getattr(stmt, f.name)
        if isinstance(val, list) and val and all(isinstance(x, ir.Stmt) for x in val):
            new_list = [_rewrite_stmt(x) for x in val]
            if any(a is not b for a, b in zip(new_list, val)):
                changes[f.name] = new_list
        elif isinstance(val, ir.Stmt):
            new_val = _rewrite_stmt(val)
            if new_val is not val:
                changes[f.name] = new_val
    return _dc.replace(stmt, **changes) if changes else stmt


def _lower_builtins_in_function(func: "ir.Function") -> "ir.Function":
    new_body = [_rewrite_stmt(s) for s in func.body]
    if any(a is not b for a, b in zip(new_body, func.body)):
        return _dc.replace(func, body=new_body)
    return func


def _lower_builtins_in_type(dt):
    """Return ``dt`` with builtin calls lowered in its functions (new node only if
    something changed)."""
    funcs = getattr(dt, "functions", None)
    if not funcs:
        return dt
    new_funcs = [_lower_builtins_in_function(f) for f in funcs]
    if any(a is not b for a, b in zip(new_funcs, funcs)):
        return _dc.replace(dt, functions=new_funcs)
    return dt


def _is_action(dt) -> bool:
    """A PSS action: a DataTypeClass that is not itself a component.

    Flow objects are ``DataTypeStruct`` (not ``DataTypeClass``) and components are
    ``DataTypeComponent``, so neither is matched.
    """
    return isinstance(dt, ir.DataTypeClass) and not isinstance(dt, ir.DataTypeComponent)


# ---------------------------------------------------------------------------
# Activity lowering (sequential, with field hoisting)
#
# zuspec-be-sw emits C only for action *bodies*; PSS activity scheduling
# (`activity { a; b; }`) is not lowered by the Context-first path, and the
# zdc-only action-inlining pass is unavailable here. We inline a sequential
# activity into the root action's synthesized ``body`` coroutine:
#   * the root action stays a real component (its own fields are ``self->f``);
#   * each *traversed* sub-action is transient — its data fields are hoisted into
#     the coroutine as path-prefixed locals (``<handle-path>__<field>``, avoiding
#     collisions between sibling traversals), and ``self.<field>`` in its body is
#     rebound to that local. Nested activities recurse, accumulating the prefix.
#
# Scope: flat/nested ``ActivitySequenceBlock`` of ``ActivityTraversal``s.
# Parallel/select/repeat are not yet handled (skipped). Hoisted rand fields are
# zero-initialized (no constraint solver yet).
# ---------------------------------------------------------------------------

def _scope_of(qual_name: str) -> str:
    """Enclosing scope of a qualified name (``pss_top::Root`` -> ``pss_top``)."""
    return qual_name.rsplit("::", 1)[0] if "::" in qual_name else ""


def _resolve_sub_action(action, handle, type_m, scope):
    """Resolve a traversal ``handle`` to ``(qualified_key, sub_action)``.

    Field refs use the simple type name (``Leaf``), but ``type_m`` is keyed by the
    qualified name (``pss_top::Leaf``), so try the scope-qualified key, then a
    unique simple-name match.
    """
    for fld in getattr(action, "fields", []):
        if fld.name != handle:
            continue
        ref = getattr(fld.datatype, "ref_name", None)
        if ref is None:
            return None, None
        for cand in (ref, f"{scope}::{ref}" if scope else None):
            if cand and cand in type_m:
                return cand, type_m[cand]
        matches = [(k, v) for k, v in type_m.items() if k.split("::")[-1] == ref]
        return matches[0] if len(matches) == 1 else (None, None)
    return None, None


def _resolve_action_type(ref, type_m, scope):
    """Resolve an action *type name* to ``(qualified_key, action)``.

    Same fallback chain as :func:`_resolve_sub_action` -- exact key, then
    scope-qualified, then a unique simple-name match -- but keyed off a type
    name rather than a field handle, because an anonymous traversal (``do A;``)
    names its action type directly and owns no field to look through.
    """
    if ref is None:
        return None, None
    for cand in (ref, f"{scope}::{ref}" if scope else None):
        if cand and cand in type_m:
            return cand, type_m[cand]
    matches = [(k, v) for k, v in type_m.items() if k.split("::")[-1] == ref]
    return matches[0] if len(matches) == 1 else (None, None)


def _subscript_index(node, bindings):
    """Constant index of a subscript slice: a literal or a bound loop var."""
    if isinstance(node, ir.ExprConstant):
        return node.value
    if isinstance(node, ir.ExprRefLocal) and node.name in bindings:
        return bindings[node.name]
    return None


def _self_path(node, bindings):
    """Path tuple of a self-rooted ref (``self.pts[0].x`` -> ``("pts", 0, "x")``),
    or ``None``. Subscripts must resolve to a constant (literal or bound loop var)."""
    if isinstance(node, ir.TypeExprRefSelf):
        return ()
    if isinstance(node, ir.ExprAttribute):
        base = _self_path(node.value, bindings)
        return base + (node.attr,) if base is not None else None
    if isinstance(node, ir.ExprSubscript):
        base = _self_path(node.value, bindings)
        idx = _subscript_index(node.slice, bindings)
        return base + (idx,) if (base is not None and idx is not None) else None
    return None


def _rebind_fields(node, prefix, slot_map, bindings):
    """Rewrite a sub-action's own-field refs to hoisted locals, and bound ``foreach``
    loop vars to their constant: a self-rooted path in ``slot_map`` becomes local
    ``<prefix><cname>``; a bound loop var becomes a constant."""
    path = _self_path(node, bindings)
    if path and path in slot_map:
        return ir.ExprRefLocal(name=prefix + slot_map[path])
    if isinstance(node, ir.ExprRefLocal) and node.name in bindings:
        return ir.ExprConstant(value=bindings[node.name])
    if not _dc.is_dataclass(node):
        return node
    changes = {}
    for f in _dc.fields(node):
        v = getattr(node, f.name)
        if isinstance(v, ir.Base):
            nv = _rebind_fields(v, prefix, slot_map, bindings)
            if nv is not v:
                changes[f.name] = nv
        elif isinstance(v, list) and v:
            nl = [_rebind_fields(x, prefix, slot_map, bindings) if isinstance(x, ir.Base) else x
                  for x in v]
            if any(a is not b for a, b in zip(nl, v)):
                changes[f.name] = nl
    return _dc.replace(node, **changes) if changes else node


def _lower_body(stmts, prefix, slot_map, bindings):
    """Lower exec-body statements: unroll ``foreach`` over a (flattened) array, then
    rebind field refs / loop vars. Other statements (incl. if/while) recurse so a
    nested ``foreach`` still unrolls."""
    out = []
    for stmt in stmts:
        if isinstance(stmt, ir.StmtForeach) and isinstance(stmt.target, ir.ExprRefLocal):
            arr_path = _self_path(stmt.iter, bindings)
            if arr_path is not None:
                idx_var = stmt.target.name
                indices = sorted({p[len(arr_path)] for p in slot_map
                                  if len(p) > len(arr_path)
                                  and p[:len(arr_path)] == arr_path
                                  and isinstance(p[len(arr_path)], int)})
                for k in indices:
                    out += _lower_body(stmt.body, prefix, slot_map, {**bindings, idx_var: k})
                continue
        if isinstance(stmt, (ir.StmtIf, ir.StmtWhile)):
            ch = {}
            for attr in ("body", "orelse"):
                sub = getattr(stmt, attr, None)
                if isinstance(sub, list):
                    ch[attr] = _lower_body(sub, prefix, slot_map, bindings)
            test = _rebind_fields(stmt.test, prefix, slot_map, bindings) if getattr(stmt, "test", None) else None
            if test is not None:
                ch["test"] = test
            out.append(_dc.replace(stmt, **ch) if ch else stmt)
            continue
        out.append(_rebind_fields(stmt, prefix, slot_map, bindings))
    return out


def _field_locals(fld):
    """Hoistable locals for a data field as ``[(cname, path, elem_dt)]`` — one per
    scalar-int leaf, recursing through arrays and structs (``path`` is the
    ``slot_map`` key; ``cname`` its ``_``-joined form). Non-int leaves are skipped."""
    return _type_locals((fld.name,), fld.datatype)


def _type_locals(path, dt):
    cname = "_".join(str(s) for s in path)
    if isinstance(dt, ir.DataTypeInt):
        return [(cname, path, dt)]
    if (isinstance(dt, ir.DataTypeArray) and isinstance(dt.size, int)):
        out = []
        for i in range(dt.size):
            out += _type_locals(path + (i,), dt.element_type)
        return out
    if isinstance(dt, ir.DataTypeStruct) and not isinstance(dt, ir.DataTypeClass):
        out = []
        for sf in dt.fields:
            out += _type_locals(path + (sf.name,), sf.datatype)
        return out
    return []


def solve_global_name(prefix, cname):
    """C global the runtime-solve harness fills and the coroutine reads."""
    return "g_" + prefix + cname


def solve_global_name(prefix, cname):
    """C global the runtime-solve harness fills and the coroutine reads."""
    return "g_" + prefix + cname


def _inline_sub_action(qual_name, action, type_m, seen, prefix, solve_plan):
    """Inline a *traversed* sub-action: hoist its data fields as locals, then its
    (builtin-lowered) body or nested activity, rebinding self-field refs.

    ``rand`` field initialization depends on the solve mode:
      * pre-solve (``solve_plan is None``, style 5): constraint-satisfying value
        solved at compile time and baked in;
      * runtime-solve (style 4): the hoisted local reads a C global the harness
        fills by solving at startup; the per-traversal problem is recorded in
        ``solve_plan``.
    Non-rand / unsolved fields default to 0.
    """
    out = []
    if solve_plan is None:
        solved, runtime_cnames = solve_action(action, seed=seed_for(prefix)), set()
    else:
        pbytes, slots = problem_bytes(action)
        solved, runtime_cnames = {}, {s.cname for s in slots}
        if pbytes is not None:
            solve_plan.append({"prefix": prefix, "bytes": pbytes, "slots": slots})

    slot_map = {}
    for fld in getattr(action, "fields", []):
        for cname, path, elem_dt in _field_locals(fld):
            slot_map[path] = cname
            if cname in runtime_cnames:
                value = ir.ExprRefUnresolved(name=solve_global_name(prefix, cname))
            else:
                value = ir.ExprConstant(value=solved.get(cname, 0))
            out.append(ir.StmtAnnAssign(
                target=ir.ExprRefLocal(name=prefix + cname),
                annotation=elem_dt,
                value=value,
            ))

    body_fn = next((fn for fn in getattr(action, "functions", []) if fn.name == "body"), None)
    if body_fn is not None:
        out.extend(_lower_body(body_fn.body, prefix, slot_map, {}))
        return out
    if getattr(action, "activity_ir", None) is not None:
        out.extend(_inline_activity(qual_name, action, type_m, seen, prefix, solve_plan))
    return out


def _inline_activity(qual_name, action, type_m, seen, prefix, solve_plan):
    """Inline ``action``'s activity, accumulating the handle ``prefix`` so nested
    locals never collide. ``action`` owns the traversed sub-action fields, so it
    (and its scope) stay fixed across the whole activity tree."""
    act_ir = getattr(action, "activity_ir", None)
    if act_ir is None:
        return []
    return _lower_activity_node(act_ir, qual_name, action, type_m, seen, prefix, solve_plan)


def _lower_activity_node(node, qual_name, action, type_m, seen, prefix, solve_plan, idx=0):
    """Lower one activity node to coroutine statements.

    First slice: ``parallel`` runs its branches in order (observationally
    equivalent for atomic, non-timed actions; true fork/join is future work);
    ``select`` takes the first branch (deterministic; weighted/guarded selection
    needs the solver). Repeat/schedule are not yet handled.

    ``idx`` is the node's position among its siblings. It is used only to name
    anonymous traversals, which -- unlike named ones -- have no handle to
    prefix their hoisted locals with, so two sibling ``do A;`` statements would
    otherwise collide on identical local names.
    """
    scope = _scope_of(qual_name)
    # sequence / parallel / schedule all run their children; for atomic, non-timed
    # actions a sequential order is a valid schedule (true concurrency is future work).
    if isinstance(node, (ir.ActivitySequenceBlock, ir.ActivityParallel, ir.ActivitySchedule)):
        out = []
        for i, s in enumerate(node.stmts):
            out.extend(_lower_activity_node(s, qual_name, action, type_m, seen, prefix, solve_plan, i))
        return out
    if isinstance(node, ir.ActivityRepeat):
        # repeat (N) { body } — unroll a constant count; each iteration gets its own
        # prefix so hoisted locals / per-instance solves stay distinct.
        count = node.count
        if not (isinstance(count, ir.ExprConstant) and isinstance(count.value, int)):
            return []  # non-constant repeat count not yet supported
        out = []
        for i in range(max(0, count.value)):
            for j, s in enumerate(node.body):
                out.extend(_lower_activity_node(
                    s, qual_name, action, type_m, seen, prefix + f"r{i}__", solve_plan, j))
        return out
    if isinstance(node, ir.ActivitySelect):
        if node.branches:
            out = []
            for i, s in enumerate(node.branches[0].body):
                out.extend(_lower_activity_node(s, qual_name, action, type_m, seen, prefix, solve_plan, i))
            return out
        return []
    if isinstance(node, ir.ActivityTraversal):
        sub_key, sub = _resolve_sub_action(action, node.handle, type_m, scope)
        if sub is None or sub_key in seen:
            return []
        return _inline_sub_action(
            sub_key, sub, type_m, seen | {sub_key}, prefix + node.handle + "__", solve_plan)
    if isinstance(node, ir.ActivityAnonTraversal):
        # ``do A;`` -- names its action type directly and declares no field, so
        # resolve the type rather than a handle. Without this case the node fell
        # through to "unsupported" below and lowered to nothing, which meant an
        # activity built entirely from ``do`` statements produced an EMPTY body;
        # _synthesize_activity_body then declined to synthesize one at all while
        # the generated main.c still called it, so the C failed to link on an
        # undefined ``<Root>_body``.
        sub_key, sub = _resolve_action_type(node.action_type, type_m, scope)
        if sub is None or sub_key in seen:
            return []
        # Named traversals prefix hoisted locals with the handle. An anonymous
        # one has no handle: use its label if it was given one, else the simple
        # type name plus the sibling index, so `do A; do A;` gets two distinct
        # sets of locals rather than one silently shared set.
        tag = node.label or f"{sub_key.split('::')[-1].lower()}{idx}"
        return _inline_sub_action(
            sub_key, sub, type_m, seen | {sub_key}, prefix + tag + "__", solve_plan)
    return []  # unsupported activity node


def _synthesize_activity_body(qual_name, action, type_m, solve_plan):
    """If ``action`` has an activity but no explicit body, synthesize a ``body``
    coroutine that inlines the activity (the root action stays a real instance)."""
    if any(fn.name == "body" for fn in getattr(action, "functions", [])):
        return action
    if getattr(action, "activity_ir", None) is None:
        return action
    stmts = _inline_activity(qual_name, action, type_m, frozenset({qual_name}), "", solve_plan)
    if not stmts:
        return action
    body = ir.Function(name="body", is_async=True, body=stmts)
    return _dc.replace(action, functions=list(action.functions) + [body])


def _action_to_component(act: "ir.DataTypeClass", name: str) -> "ir.DataTypeComponent":
    return ir.DataTypeComponent(
        loc=act.loc,
        name=name,
        py_type=act.py_type,
        super=act.super,
        fields=list(act.fields),
        functions=list(act.functions),
        is_abstract=act.is_abstract,
        flow_kind=act.flow_kind,
        has_initial_constraint=act.has_initial_constraint,
        covergroups=list(act.covergroups),
        activity_ir=act.activity_ir,
    )


def _iter_traversals(act_ir):
    """Yield ``ActivityTraversal`` nodes from a (possibly nested) activity."""
    if isinstance(act_ir, (ir.ActivitySequenceBlock, ir.ActivityParallel,
                           ir.ActivitySchedule)):
        for s in act_ir.stmts:
            yield from _iter_traversals(s)
    elif isinstance(act_ir, ir.ActivityRepeat):
        for s in act_ir.body:
            yield from _iter_traversals(s)
    elif isinstance(act_ir, ir.ActivitySelect):
        for br in act_ir.branches:
            for s in br.body:
                yield from _iter_traversals(s)
    elif isinstance(act_ir, ir.ActivityTraversal):
        yield act_ir


def _traversed_keys(type_m) -> set:
    """Qualified keys of all sub-actions reached by some activity (i.e. inlined)."""
    traversed = set()
    for name, dt in type_m.items():
        if not (_is_action(dt) and getattr(dt, "activity_ir", None) is not None):
            continue
        for trav in _iter_traversals(dt.activity_ir):
            key, _ = _resolve_sub_action(dt, trav.handle, type_m, _scope_of(name))
            if key:
                traversed.add(key)
    return traversed


def find_root_action(core: "ir.Context"):
    """Return the qualified name of the single top-level activity-bearing action
    (one with an ``activity_ir`` that no other activity traverses), or ``None``."""
    activity_actions = {
        name for name, dt in core.type_m.items()
        if _is_action(dt) and getattr(dt, "activity_ir", None) is not None
    }
    roots = activity_actions - _traversed_keys(core.type_m)
    return next(iter(roots)) if len(roots) == 1 else None


def _drop_subaction_ref_fields(dt, traversed, type_m, scope):
    """Drop ``DataTypeRef`` fields pointing at inlined sub-actions — after inlining
    they are dead (their behaviour lives in the synthesized body as locals), and
    keeping them would reference components we no longer emit."""
    def is_subaction_ref(fld):
        ref = getattr(fld.datatype, "ref_name", None)
        if ref is None:
            return False
        for cand in (ref, f"{scope}::{ref}" if scope else None):
            if cand in traversed:
                return True
        return any(k in traversed for k, _v in type_m.items()
                   if k.split("::")[-1] == ref)
    kept = [f for f in dt.fields if not is_subaction_ref(f)]
    return _dc.replace(dt, fields=kept) if len(kept) != len(dt.fields) else dt


def to_sw_context(core: "ir.Context", solve_plan=None) -> "ir.Context":
    """Return a new ``Context`` with PSS actions lowered to components.

    Node objects for non-action types are shared (identity preserved); only
    actions are wrapped into fresh ``DataTypeComponent`` instances.

    ``solve_plan``: pass a list to enable **runtime** constraint solving (PSS
    style 4) — each traversed sub-action's serialized dv-solve problem is appended
    as ``{"prefix", "bytes", "var_map"}`` and its rand fields read C globals the
    harness fills. ``None`` (default) keeps **compile-time** pre-solve (style 5).
    """
    # Phase 1: lower PSS builtins (print/message) in every type's functions, so
    # that activity inlining below splices already-lowered sub-action bodies.
    lowered = {name: _lower_builtins_in_type(dt) for name, dt in core.type_m.items()}

    # Sub-actions reached by an activity are inlined into their parent's body, so
    # their standalone components are dead — and emitting them would hit gaps in
    # the backend's array/constraint codegen. Drop them (and the now-dead
    # sub-action-ref fields that pointed at them).
    traversed = _traversed_keys(lowered)

    # Phase 2: synthesize bodies for activity-only actions, then lower actions to
    # components (named by their qualified type_m key to avoid file collisions).
    new_type_m = {}
    for name, dt in lowered.items():
        if name in traversed:
            continue
        if _is_action(dt):
            dt = _synthesize_activity_body(name, dt, lowered, solve_plan)
            dt = _drop_subaction_ref_fields(dt, traversed, lowered, _scope_of(name))
            dt = _action_to_component(dt, name)
        new_type_m[name] = dt
    return ir.Context(type_m=new_type_m)
