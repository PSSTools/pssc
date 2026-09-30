"""Pure-SV lowering for the atomic-action subset (plan tasks C5b/E2).

This is the first real slice of the ``sv-pure`` target: it lowers PSS atomic
actions to SystemVerilog entirely through the structured IR path

    core DataType + constraints + body  ->  (be-sv) core_to_sv / structured
    constraint + statement translators  ->  SVClass  ->  SVEmitter  ->  *.sv

with no DPI and no string-rendered constraints. Components, activities, flow
objects, resources, and inference are handled by later milestones (M3+); models
using them fall back to the ``sv-native`` path via :func:`is_pure_supported`.
"""
from __future__ import annotations

import dataclasses as dc
from pathlib import Path
from typing import Any, List, Optional

import zuspec.ir.core as ir
from zuspec.ir.core.fields import FieldKind

from zuspec.be.sv.ir.sv import (
    SVClass, SVClassField, SVTaskDecl, SVFunctionDecl, SVModuleDecl)
from zuspec.be.sv.ir import stmt as svs
from zuspec.be.sv.ir.core_to_sv import translate_class, translate_stmts

from .context import LoweringContext
from .lower_constraints import lower_constraint_func_ir
from .emit_files import emit_files

_FLOW_RESOURCE_KINDS = (FieldKind.Input, FieldKind.Output,
                        FieldKind.Lock, FieldKind.Share)


def _runtime_lib_path() -> Optional[Path]:
    import pssc
    rt = Path(pssc.__file__).resolve().parent / "share" / "sv" / "zsp_rt_pkg.sv"
    return rt if rt.exists() else None


def _is_action(name, dt) -> bool:
    """An action is a ``DataTypeClass`` that is not a component (structs are
    plain ``DataTypeStruct``; actions/components are ``DataTypeClass``)."""
    from .pss_to_sv import _is_stdlib
    return (isinstance(dt, ir.DataTypeClass)
            and not isinstance(dt, ir.DataTypeComponent)
            and not _is_stdlib(name))


def _action_types(ir_ctx) -> List[tuple]:
    """Return (pss_name, action DataType) pairs for all actions (atomic and
    compound), deduped by identity (type_map registers types under both
    qualified and bare names)."""
    out = []
    seen = set()
    for name, dt in ir_ctx.type_map.items():
        if id(dt) in seen:
            continue
        if _is_action(name, dt):
            seen.add(id(dt))
            out.append((name, dt))
    return out


# Supported flow-object kinds -> their SV runtime base class.
_FLOW_BASE = {ir.FlowKind.BUFFER: "zsp_buffer", ir.FlowKind.STATE: "zsp_state",
              ir.FlowKind.STREAM: "zsp_stream"}


def _flow_types(ir_ctx) -> List[tuple]:
    """Return (name, DataTypeStruct) for supported flow-object types."""
    from .pss_to_sv import _is_stdlib
    out = []
    seen = set()
    for name, dt in ir_ctx.type_map.items():
        if id(dt) in seen or _is_stdlib(name):
            continue
        if isinstance(dt, ir.DataTypeStruct) and getattr(dt, "flow_kind", None) in _FLOW_BASE:
            seen.add(id(dt))
            out.append((name, dt))
    return out


def _producers_of(ir_ctx, flow_type_name) -> List[tuple]:
    """Return (type_map_name, action_dtype, output_field_name) for every action
    that produces a flow object of *flow_type_name* (the inference candidate
    list, design §6.4). The name is the type_map key, so it mangles to the same
    SV class name used when the class is emitted."""
    out = []
    seen = set()
    for name, dt in ir_ctx.type_map.items():
        if id(dt) in seen or not _is_action(name, dt):
            continue
        seen.add(id(dt))
        for f in dt.fields:
            if f.kind == FieldKind.Output and getattr(f.datatype, "name", None) == flow_type_name:
                out.append((name, dt, f.name))
    return out


def _struct_types(ir_ctx) -> List[tuple]:
    """Return (name, DataTypeStruct) for plain structs (non-flow, non-action)."""
    from .pss_to_sv import _is_stdlib
    out, seen = [], set()
    for name, dt in ir_ctx.type_map.items():
        if id(dt) in seen or _is_stdlib(name):
            continue
        if (isinstance(dt, ir.DataTypeStruct)
                and not isinstance(dt, ir.DataTypeClass)  # excludes actions/components
                and getattr(dt, "flow_kind", None) is None):
            seen.add(id(dt))
            out.append((name, dt))
    return out


def _resource_types(ir_ctx) -> List[tuple]:
    """Return (name, DataTypeStruct) for resource types (claimed from pools)."""
    from .pss_to_sv import _is_stdlib
    out, seen = [], set()
    for name, dt in ir_ctx.type_map.items():
        if id(dt) in seen or _is_stdlib(name):
            continue
        if isinstance(dt, ir.DataTypeStruct) and getattr(dt, "flow_kind", None) is ir.FlowKind.RESOURCE:
            seen.add(id(dt))
            out.append((name, dt))
    return out


def _pool_capacity(ir_ctx, res_type_name, default=16) -> int:
    """Pool capacity for a resource type (matched by element type across the
    component pools); falls back to *default* when not found/unbounded."""
    for _n, dt in ir_ctx.type_map.items():
        if isinstance(dt, ir.DataTypeComponent):
            for p in getattr(dt, "pools", []):
                if p.element_type_name == res_type_name and p.capacity:
                    return p.capacity
    return default


def _is_rand_field(f) -> bool:
    rk = getattr(f, "rand_kind", None)
    if rk is None:
        return False
    if isinstance(rk, str):
        return rk.lower() in ("rand", "randc")
    return True  # RandKind enum


def _couples_input_to_local_rand(dtype) -> bool:
    """True if a constraint couples an input flow field to one of the action's
    own rand data fields (``in.x == k``). This is the residual joint case the
    forwarding model can't express in pure SV (it would forward ``k`` into the
    producer's scope); such models fall back to sv-native (design §6.3 row 3,
    §10 solve-groups — not yet realized in the pure path)."""
    input_names = {f.name for f in dtype.fields if f.kind == FieldKind.Input}
    if not input_names:
        return False
    local_rand = {f.name for f in dtype.fields
                  if f.kind == FieldKind.Field and _is_rand_field(f)}
    if not local_rand:
        return False
    for fn in getattr(dtype, "functions", []):
        if not fn.metadata.get("_is_constraint"):
            continue
        for st in fn.body:
            if not isinstance(st, ir.StmtExpr):
                continue
            if (any(_expr_refs_field(st.expr, n) for n in input_names)
                    and any(_expr_refs_field(st.expr, n) for n in local_rand)):
                return True
    return False


def _action_fields_supported(dtype) -> bool:
    """Allow plain data, sub-action handles, buffer/state/stream flow fields,
    and resource lock/share fields."""
    for f in dtype.fields:
        if f.kind in (FieldKind.Input, FieldKind.Output):
            if getattr(f.datatype, "flow_kind", None) not in _FLOW_BASE:
                return False
        if f.kind in (FieldKind.Lock, FieldKind.Share):
            if getattr(f.datatype, "flow_kind", None) is not ir.FlowKind.RESOURCE:
                return False
    return True


def _activity_supported(node) -> bool:
    """Whether an activity node is within the pure path's current subset:
    sequence/parallel/schedule, traversals (named + anonymous `do X`), binds,
    and repeat/replicate loops."""
    if isinstance(node, ir.ActivitySequenceBlock):
        return all(_activity_supported(s) for s in node.stmts)
    if isinstance(node, (ir.ActivityParallel, ir.ActivitySchedule)):
        return all(_activity_supported(s) for s in node.stmts)
    if isinstance(node, (ir.ActivityRepeat, ir.ActivityReplicate)):
        return all(_activity_supported(s) for s in node.body)
    if isinstance(node, (ir.ActivityTraversal, ir.ActivityAnonTraversal)):
        return True
    if isinstance(node, ir.ActivityBind):
        return True
    return False


def _walk_anon(node, fn):
    """Visit every ActivityAnonTraversal in the activity tree."""
    if isinstance(node, ir.ActivityAnonTraversal):
        fn(node)
    if dc.is_dataclass(node):
        for f in dc.fields(node):
            v = getattr(node, f.name)
            if isinstance(v, list):
                for it in v:
                    if dc.is_dataclass(it):
                        _walk_anon(it, fn)


def _resolve_action(ctx, ref_name):
    """Resolve an action handle's type ref to its DataType."""
    tm = ctx.ir_ctx.type_map
    if ref_name in tm:
        return tm[ref_name]
    for k, v in tm.items():
        if k == ref_name or k.endswith(f"::{ref_name}"):
            return v
    return None


def _split_bind(expr):
    """Split ``self.<handle>.<field>`` into ``(handle, field)``."""
    field = expr.attr
    handle = expr.value.attr  # inner ExprAttribute
    return handle, field


def _collect_binds(node) -> List[tuple]:
    """Collect raw (h1, f1, h2, f2) pairs from ActivityBind nodes (src then
    dst). PSS ``bind`` is order-agnostic, so direction is resolved later by
    field kind (see :func:`_resolve_binds`)."""
    binds = []

    def walk(n):
        if isinstance(n, ir.ActivityBind):
            h1, f1 = _split_bind(n.src)
            h2, f2 = _split_bind(n.dst)
            binds.append((h1, f1, h2, f2))
            return
        if dc.is_dataclass(n):
            for f in dc.fields(n):
                v = getattr(n, f.name)
                if isinstance(v, list):
                    for it in v:
                        if dc.is_dataclass(it):
                            walk(it)
    walk(node)
    return binds


def _field_kind(handle_type, handle, field):
    at = handle_type.get(handle)
    if at is not None:
        for f in at.fields:
            if f.name == field:
                return f.kind
    return None


def _resolve_binds(handle_type, node) -> List[tuple]:
    """Normalize binds to (producer_handle, output_field, consumer_handle,
    input_field) using field kinds (``bind`` operand order is not significant)."""
    out = []
    for (h1, f1, h2, f2) in _collect_binds(node):
        if _field_kind(handle_type, h1, f1) == FieldKind.Output:
            out.append((h1, f1, h2, f2))
        elif _field_kind(handle_type, h2, f2) == FieldKind.Output:
            out.append((h2, f2, h1, f1))
        else:
            out.append((h1, f1, h2, f2))  # fallback: first operand is producer
    return out


def _expr_refs_field(e, name) -> bool:
    if not dc.is_dataclass(e):
        return False
    if isinstance(e, ir.ExprAttribute) and e.attr == name:
        return True
    for f in dc.fields(e):
        v = getattr(e, f.name)
        if isinstance(v, list):
            if any(dc.is_dataclass(x) and _expr_refs_field(x, name) for x in v):
                return True
        elif dc.is_dataclass(v) and f.name != "loc" and _expr_refs_field(v, name):
            return True
    return False


def _strip_handle(e, handle):
    """Rewrite ``self.<handle>.<f>`` to ``self.<f>`` so a flow-field constraint
    can be applied directly inside ``<handle>.randomize() with { ... }`` (where
    the ``with`` scope is the flow object itself). This keeps the constraint
    *local* to the flow object — Verilator does not support hierarchical/global
    constraints reached through a rand sub-handle (design §3.1)."""
    if not dc.is_dataclass(e):
        return e
    if isinstance(e, ir.ExprAttribute):
        v = e.value
        if (isinstance(v, ir.ExprAttribute)
                and isinstance(v.value, ir.TypeExprRefSelf)
                and v.attr == handle):
            return dc.replace(e, value=ir.TypeExprRefSelf())
        return dc.replace(e, value=_strip_handle(v, handle))
    repl = {}
    for f in dc.fields(e):
        v = getattr(e, f.name)
        if isinstance(v, list):
            repl[f.name] = [_strip_handle(x, handle) if dc.is_dataclass(x) else x
                            for x in v]
        elif dc.is_dataclass(v) and f.name != "loc":
            repl[f.name] = _strip_handle(v, handle)
    return dc.replace(e, **repl) if repl else e


def _rewrite_output_constraint(e, output_field, action_handle):
    """Rewrite a producer's output constraint for application in
    ``<handle>.<output_field>.randomize() with { ... }``:

    * ``self.<output_field>.f`` -> ``f`` (local to the flow object), and
    * ``self.<other>.f`` -> ``<action_handle>.<other>.f`` (a coupling reference
      to another, already-solved flow field, e.g. ``next.x == prev.x``).
    """
    if not dc.is_dataclass(e):
        return e
    if isinstance(e, ir.ExprAttribute) and isinstance(e.value, ir.TypeExprRefSelf):
        if e.attr == output_field:
            return ir.TypeExprRefSelf()  # self.<of>.f collapses to bare f
        return ir.ExprAttribute(value=ir.ExprRefUnresolved(name=action_handle),
                                attr=e.attr)  # self.<other> -> handle.<other>
    repl = {}
    for f in dc.fields(e):
        v = getattr(e, f.name)
        if isinstance(v, list):
            repl[f.name] = [_rewrite_output_constraint(x, output_field, action_handle)
                            if dc.is_dataclass(x) else x for x in v]
        elif dc.is_dataclass(v) and f.name != "loc":
            repl[f.name] = _rewrite_output_constraint(v, output_field, action_handle)
    return dc.replace(e, **repl) if repl else e


def _output_own_constraints(dtype, output_field, action_handle):
    """A producer's own constraints on *output_field*, rewritten for the output
    object's ``randomize() with`` (handles coupling to other flow fields)."""
    out = []
    for fn in getattr(dtype, "functions", []):
        if not fn.metadata.get("_is_constraint"):
            continue
        for st in fn.body:
            if isinstance(st, ir.StmtExpr) and _expr_refs_field(st.expr, output_field):
                out.append(ir.ConstraintExpr(
                    expr=_rewrite_output_constraint(st.expr, output_field, action_handle)))
    return out


def _forwarded_input_constraints(dtype, input_field):
    """Consumer constraints that reference *input_field* and are forwarded (E7)
    to the producer that creates that flow object, stripped to the flow object's
    own field scope (``in.x == 5`` -> ``x == 5``).

    A constraint that *also* references one of the consumer's output flow fields
    is a coupling constraint (e.g. ``next.x == prev.x``); it is NOT forwarded —
    it belongs to that output's own solve (see :func:`_output_own_constraints`),
    where the input is read as an already-solved value."""
    output_names = {f.name for f in dtype.fields if f.kind == FieldKind.Output}
    out = []
    for fn in getattr(dtype, "functions", []):
        if not fn.metadata.get("_is_constraint"):
            continue
        for st in fn.body:
            if not (isinstance(st, ir.StmtExpr) and _expr_refs_field(st.expr, input_field)):
                continue
            if any(_expr_refs_field(st.expr, o) for o in output_names):
                continue  # coupling -> handled by the output solve, not forwarded
            out.append(ir.ConstraintExpr(expr=_strip_handle(st.expr, input_field)))
    return out


# ------------------------------------------------------------------ #
# PSS exec built-in mapping (message/print/error/fatal -> $display...) #
# ------------------------------------------------------------------ #

def _rewrite_call(e):
    """Map a PSS exec built-in call to its SV system task."""
    if not isinstance(e, ir.ExprCall):
        return e
    name = None
    if isinstance(e.func, ir.ExprAttribute):
        name = e.func.attr
    elif isinstance(e.func, ir.ExprRefUnresolved):
        name = e.func.name
    if name == "message":   # message(verbosity, fmt, args...) -> $display(fmt, args...)
        return ir.ExprCall(func=ir.ExprRefUnresolved(name="$display"), args=list(e.args[1:]))
    if name == "print":     # print(fmt, args...) -> $write (no newline)
        return ir.ExprCall(func=ir.ExprRefUnresolved(name="$write"), args=list(e.args))
    if name == "error":
        return ir.ExprCall(func=ir.ExprRefUnresolved(name="$error"), args=list(e.args))
    if name == "fatal":
        return ir.ExprCall(func=ir.ExprRefUnresolved(name="$fatal"), args=list(e.args))
    return e


def _map_exec_builtins(stmts):
    """Rewrite PSS exec built-in calls within a procedural stmt list."""
    out = []
    for s in stmts:
        if isinstance(s, ir.StmtExpr):
            out.append(dc.replace(s, expr=_rewrite_call(s.expr)))
        elif isinstance(s, ir.StmtIf):
            out.append(dc.replace(s, body=_map_exec_builtins(s.body or []),
                                  orelse=_map_exec_builtins(s.orelse or [])))
        elif isinstance(s, (ir.StmtForeach, ir.StmtWhile, ir.StmtRepeat)):
            out.append(dc.replace(s, body=_map_exec_builtins(s.body or [])))
        else:
            out.append(s)
    return out


# ------------------------------------------------------------------ #
# Activity -> structured SV (incremental traversal)                    #
# ------------------------------------------------------------------ #

@dc.dataclass
class _BindMaps:
    """Per-activity binding context threaded through activity lowering."""
    output_solves: dict   # producer-handle -> [(output_field, [constraints])]
    inject: dict          # consumer-handle -> [(in_field, prod_handle, out_field)]
    pre_stmts: dict        # consumer-handle -> stmts before traversal (inference)
    stream_gets: dict      # consumer-handle -> [(channel_var, in_field)]
    stream_puts: dict      # producer-handle -> [(channel_var, out_field)]
    resource_claims: dict  # handle -> [(field, pool_var, claim_var, mode)]
    anon_names: dict       # id(ActivityAnonTraversal) -> synthetic handle name


def _empty_binds() -> _BindMaps:
    return _BindMaps({}, {}, {}, {}, {}, {}, {})


def _output_flow(at, field):
    """Return (flow_kind, flow_type_name) for an action's output *field*."""
    for f in getattr(at, "fields", []):
        if f.name == field and f.kind == FieldKind.Output:
            return getattr(f.datatype, "flow_kind", None), getattr(f.datatype, "name", None)
    return None, None


def _traversal_stmts(handle: str, bm: "_BindMaps") -> List[svs.SVStmt]:
    """Emit one action traversal's lifecycle (design §3): create -> context ->
    pre_solve -> (stream get | bind inputs) -> solve outputs (producer +
    forwarded constraints) -> randomize own -> post_solve -> activity ->
    stream put.
    """
    href = ir.ExprRefUnresolved(name=handle)

    def _call(method):
        return svs.SVStmtExpr(expr=ir.ExprCall(
            func=ir.ExprAttribute(value=href, attr=method), args=[]))

    stmts: List[svs.SVStmt] = [
        svs.SVStmtRaw(text=f"{handle} = new();"),
        svs.SVStmtRaw(text=f"{handle}.comp_base = comp_base;"),
        _call("pre_solve"),
    ]
    # Claim resources (design §7): grab a free pool instance and bind the handle.
    for (field, pool_var, claim_var, mode) in bm.resource_claims.get(handle, []):
        claim_fn = "claim_shared" if mode == "share" else "claim"
        stmts.append(svs.SVStmtRaw(text=f"{claim_var} = {pool_var}.{claim_fn}();"))
        stmts.append(svs.SVStmtRaw(text=f"{handle}.{field} = {pool_var}.get({claim_var});"))
    # Stream inputs are received from the channel (blocks until produced).
    for (chan, cf) in bm.stream_gets.get(handle, []):
        stmts.append(svs.SVStmtRaw(text=f"{chan}.get({handle}.{cf});"))
    # Buffer/state inputs are pinned from already-traversed producers.
    for (cf, ph, pf) in bm.inject.get(handle, []):
        stmts.append(svs.SVStmtRaw(text=f"{handle}.{cf} = {ph}.{pf};"))
    # Solve each output flow object directly (local constraints only).
    for (of, cons) in bm.output_solves.get(handle, []):
        stmts.append(svs.SVStmtRandomize(
            target=ir.ExprAttribute(value=href, attr=of), constraints=list(cons),
            fail_msg=f"randomize of {handle}.{of} failed"))
    # Solve the action's own random fields.
    stmts.append(svs.SVStmtRandomize(
        target=href, fail_msg=f"randomize of {handle} failed"))
    stmts.append(_call("post_solve"))
    stmts.append(_call("activity"))
    # Stream outputs are published to the channel after the body runs.
    for (chan, pf) in bm.stream_puts.get(handle, []):
        stmts.append(svs.SVStmtRaw(text=f"{chan}.put({handle}.{pf});"))
    # Release claimed resources when the action completes (scope-based, §7).
    for (field, pool_var, claim_var, mode) in bm.resource_claims.get(handle, []):
        rel = "unshare" if mode == "share" else "unlock"
        stmts.append(svs.SVStmtRaw(text=f"{pool_var}.{rel}({claim_var});"))
    return stmts


def _lower_activity(node, bm: "_BindMaps") -> List[svs.SVStmt]:
    """Lower an activity node to structured SV statements (design §3/§8)."""
    if isinstance(node, ir.ActivitySequenceBlock):
        out: List[svs.SVStmt] = []
        for s in node.stmts:
            out.extend(_lower_activity(s, bm))
        return out
    if isinstance(node, ir.ActivityTraversal):
        out = list(bm.pre_stmts.get(node.handle, []))  # inferred producers first
        out.extend(_traversal_stmts(node.handle, bm))
        return out
    if isinstance(node, ir.ActivityAnonTraversal):
        name = bm.anon_names[id(node)]
        out = list(bm.pre_stmts.get(name, []))
        out.extend(_traversal_stmts(name, bm))
        return out
    if isinstance(node, (ir.ActivityParallel, ir.ActivitySchedule)):
        return [svs.SVStmtFork(branches=[_lower_activity(s, bm) for s in node.stmts])]
    if isinstance(node, (ir.ActivityRepeat, ir.ActivityReplicate)):
        body: List[svs.SVStmt] = []
        for s in node.body:
            body.extend(_lower_activity(s, bm))
        if node.index_var:
            return [svs.SVStmtFor(var=node.index_var, limit=node.count, body=body)]
        return [svs.SVStmtRepeat(count=node.count, body=body)]
    if isinstance(node, ir.ActivityBind):
        return []  # realized via bind maps computed up-front
    raise NotImplementedError(
        f"_lower_activity: unsupported {type(node).__name__}")


def _lower_compound_activity(ctx, dtype) -> List[svs.SVStmt]:
    """Lower a compound action's activity with flow binding + constraint
    forwarding (design §6.1/§6.3). Buffer/state binds are sequential value
    injection; stream binds use a fork + channel (put/get)."""
    handle_type = {}
    for f in dtype.fields:
        if f.kind == FieldKind.Field and isinstance(f.datatype, ir.DataTypeRef):
            at = _resolve_action(ctx, f.datatype.ref_name)
            if at is not None:
                handle_type[f.name] = at

    bm = _empty_binds()
    top_decls: List[svs.SVStmt] = []

    # Anonymous traversals (`do X`): assign a synthetic handle, register its type
    # (so the resource/flow analysis below picks it up), and declare its var.
    anon_counter = [0]

    def _assign(anon):
        at = _resolve_action(ctx, anon.action_type)
        if at is None:
            return
        name = f"_anon{anon_counter[0]}"
        anon_counter[0] += 1
        handle_type[name] = at
        bm.anon_names[id(anon)] = name
        top_decls.append(svs.SVVarDecl(
            name=name, dtype=ctx.resolve_sv_class_name(anon.action_type)))
    _walk_anon(dtype.activity_ir, _assign)

    # Resource pools (design §7): one pool per resource type used by a traversed
    # action, declared at the activity top; each lock/share field claims from it.
    pool_vars = {}  # resource type name -> pool var
    for h, at in handle_type.items():
        for f in at.fields:
            if f.kind not in (FieldKind.Lock, FieldKind.Share):
                continue
            rtype = f.datatype.name
            rcls = ctx.mangle_name(rtype)
            if rtype not in pool_vars:
                pvar = f"_rp_{ctx.mangle_name(rtype)}"
                pool_vars[rtype] = pvar
                cap = _pool_capacity(ctx.ir_ctx, rtype)
                top_decls.append(svs.SVStmtRaw(
                    text=f"zsp_resource_pool #({rcls}) {pool_vars[rtype]} = new({cap});"))
            claim_var = f"_cid_{h}_{f.name}"
            top_decls.append(svs.SVStmtRaw(text=f"int {claim_var};"))
            mode = "share" if f.kind == FieldKind.Share else "lock"
            bm.resource_claims.setdefault(h, []).append(
                (f.name, pool_vars[rtype], claim_var, mode))

    # Each producer output gets a solve carrying that action's own output
    # constraints (mutable lists so binds can append forwarded ones).
    for h, at in handle_type.items():
        for f in at.fields:
            if f.kind == FieldKind.Output:
                bm.output_solves.setdefault(h, []).append(
                    [f.name, _output_own_constraints(at, f.name, h)])

    bound_inputs = set()
    for (ph, pf, ch, cf) in _resolve_binds(handle_type, dtype.activity_ir):
        bound_inputs.add((ch, cf))
        cons = handle_type.get(ch)
        if cons is not None:
            forwarded = _forwarded_input_constraints(cons, cf)  # E7 forwarding
            for entry in bm.output_solves.get(ph, []):
                if entry[0] == pf:
                    entry[1].extend(forwarded)
        flow_kind, flow_name = _output_flow(handle_type.get(ph), pf)
        if flow_kind is ir.FlowKind.STREAM:
            chan = f"_ch_{ph}_{pf}"
            top_decls.append(svs.SVStmtRaw(
                text=f"zsp_stream_channel #({ctx.mangle_name(flow_name)}) {chan} = new();"))
            bm.stream_puts.setdefault(ph, []).append((chan, pf))
            bm.stream_gets.setdefault(ch, []).append((chan, cf))
        else:  # buffer / state: sequential value injection
            bm.inject.setdefault(ch, []).append((cf, ph, pf))

    # Inference (design §6.4): for each unbound consumer input, synthesize a
    # single-candidate producer ahead of the consumer and forward constraints.
    for h, at in handle_type.items():
        for f in at.fields:
            if f.kind != FieldKind.Input or (h, f.name) in bound_inputs:
                continue
            cands = _producers_of(ctx.ir_ctx, f.datatype.name)
            if len(cands) != 1:
                continue  # 0 or >1 candidates: out of subset (guarded upstream)
            prod_name, prod_dtype, pf = cands[0]
            infvar = f"_inf_{h}_{f.name}"
            inf_bm = _empty_binds()
            inf_bm.output_solves[infvar] = [(
                pf, _output_own_constraints(prod_dtype, pf, infvar)
                + _forwarded_input_constraints(at, f.name))]
            pre = [svs.SVVarDecl(name=infvar, dtype=ctx.mangle_name(prod_name))]
            pre.extend(_traversal_stmts(infvar, inf_bm))
            bm.pre_stmts.setdefault(h, []).extend(pre)
            bm.inject.setdefault(h, []).append((f.name, infvar, pf))

    return top_decls + _lower_activity(dtype.activity_ir, bm)


def is_pure_supported(ir_ctx, export_actions: Optional[List[str]] = None) -> bool:
    """Whether the model is within the atomic-action subset the pure path handles.

    Conservative: requires at least one atomic action, and every action in the
    model to be atomic and flat (no activities, flow objects, or resources).
    Anything else falls back to ``sv-native``.
    """
    if ir_ctx is None:
        return False
    seen = set()
    actions = []
    for name, dt in ir_ctx.type_map.items():
        if id(dt) in seen:
            continue
        if _is_action(name, dt):
            seen.add(id(dt))
            actions.append(dt)
    if not actions:
        return False
    for dt in actions:
        if not _action_fields_supported(dt):
            return False
        if _couples_input_to_local_rand(dt):
            return False  # residual joint case -> sv-native (design §10)
        act = getattr(dt, "activity_ir", None)
        if act is not None and not _activity_supported(act):
            return False
        if act is not None and not _inference_feasible(ir_ctx, dt):
            return False
    return True


def _inference_feasible(ir_ctx, dtype) -> bool:
    """Every unbound consumer input in *dtype*'s activity must have exactly one
    producer candidate (single-candidate inference); otherwise fall back."""
    handle_type = {}
    for f in dtype.fields:
        if f.kind == FieldKind.Field and isinstance(f.datatype, ir.DataTypeRef):
            at = _resolve_action_tm(ir_ctx, f.datatype.ref_name)
            if at is not None:
                handle_type[f.name] = at
    bound = {(ch, cf) for (_ph, _pf, ch, cf) in _resolve_binds(handle_type, dtype.activity_ir)}
    for h, at in handle_type.items():
        for f in at.fields:
            if f.kind == FieldKind.Input and (h, f.name) not in bound:
                if len(_producers_of(ir_ctx, f.datatype.name)) != 1:
                    return False
    return True


def _resolve_action_tm(ir_ctx, ref_name):
    tm = ir_ctx.type_map
    if ref_name in tm:
        return tm[ref_name]
    for k, v in tm.items():
        if k == ref_name or k.endswith(f"::{ref_name}"):
            return v
    return None


def lower_pure_action(ctx: LoweringContext, dtype, sv_name: str) -> SVClass:
    """Lower one action ``DataType`` to an :class:`SVClass` (structured).

    Atomic actions get a ``body()`` task from the exec body; compound actions
    additionally get an ``activity()`` task lowered from the activity graph
    (sequence/parallel of traversals).
    """
    known = [f.name for f in dtype.fields]
    # Constraints referencing flow fields are applied to the buffer object's own
    # solve at the production point (E7 forwarding), not on the action class.
    flow_field_names = {f.name for f in dtype.fields
                        if f.kind in (FieldKind.Input, FieldKind.Output,
                                      FieldKind.Lock, FieldKind.Share)}
    constraints: List[Any] = []
    body_stmts = None

    for func in getattr(dtype, "functions", []):
        blk = lower_constraint_func_ir(ctx, func, known_field_names=known)
        if blk is not None:
            if any(isinstance(st, ir.StmtExpr)
                   and any(_expr_refs_field(st.expr, n) for n in flow_field_names)
                   for st in func.body):
                continue  # handled via the buffer solve
            constraints.append(blk)
            continue
        if func.name == "body":
            body_stmts = translate_stmts(_map_exec_builtins(func.body or []))

    tasks = [SVTaskDecl(
        name="body", is_virtual=True,
        body=body_stmts if body_stmts else None,
        body_lines=[] if body_stmts else ["// empty body"])]

    activity_ir = getattr(dtype, "activity_ir", None)
    if activity_ir is not None:
        act_stmts = _lower_compound_activity(ctx, dtype)
        tasks.append(SVTaskDecl(
            name="activity", is_virtual=True,
            body=act_stmts if act_stmts else None,
            body_lines=[] if act_stmts else ["// empty activity"]))

    # Output flow objects are constructed in pre_solve() so the producer's
    # randomize() can solve their rand fields (and forwarded constraints).
    out_fields = [f for f in dtype.fields if f.kind == FieldKind.Output]
    functions = []
    if out_fields:
        functions.append(SVFunctionDecl(
            name="pre_solve", return_type="void", is_virtual=True,
            body=[svs.SVStmtRaw(text=f"{f.name} = new();") for f in out_fields]))

    cls = translate_class(
        dtype, sv_name=sv_name, extends="zsp_action",
        constraints=constraints, tasks=tasks, functions=functions,
        type_namer=ctx.pss_type_to_sv_type_str)

    # Flow/resource handle fields are non-rand handles: flow objects are solved
    # at their production point (not hierarchically); resource handles are bound
    # to the claimed pool instance at traversal.
    for f in dtype.fields:
        if f.kind in (FieldKind.Output, FieldKind.Input,
                      FieldKind.Lock, FieldKind.Share):
            cls.fields.append(SVClassField(
                name=f.name, dtype=ctx.mangle_name(f.datatype.name), is_rand=False))
    return cls


def lower_flow_type(ctx: LoweringContext, dtype, sv_name: str) -> SVClass:
    """Lower a flow-object type (buffer/state) to an SV class (rand fields +
    constraints), extending the matching runtime base."""
    known = [f.name for f in dtype.fields]
    constraints = []
    for func in getattr(dtype, "functions", []):
        blk = lower_constraint_func_ir(ctx, func, known_field_names=known)
        if blk is not None:
            constraints.append(blk)
    bases = dict(_FLOW_BASE)
    bases[ir.FlowKind.RESOURCE] = "zsp_resource"
    base = bases.get(getattr(dtype, "flow_kind", None))  # None => plain struct
    return translate_class(
        dtype, sv_name=sv_name, extends=base,
        constraints=constraints, type_namer=ctx.pss_type_to_sv_type_str)


def _standalone_top(root_sv_name: str) -> SVModuleDecl:
    """A self-contained harness that runs one atomic action's lifecycle."""
    # emit_files inserts `import <package_name>::*;` right after the
    # `import zsp_rt_pkg::*;` line, so we only emit the runtime import here.
    return SVModuleDecl(name="zsp_test_top", body_lines=[
        "import zsp_rt_pkg::*;",
        "initial begin",
        f"  {root_sv_name} root = new();",
        "  root.pre_solve();",
        '  if (!root.randomize()) $fatal(1, "randomize failed");',
        "  root.post_solve();",
        "  root.activity();",  # base activity() falls back to body() for atomic
        '  $display("ZSP_PURE_DONE");',
        "  $finish;",
        "end",
    ])


def generate_pure_sv(ir_ctx, output_dir: str,
                     export_actions: Optional[List[str]] = None,
                     package_name: str = "zsp_gen_pkg") -> List[Path]:
    """Lower the atomic-action subset to SV files (the ``sv-pure`` generator)."""
    ctx = LoweringContext(ir_ctx=ir_ctx)

    classes: List[Any] = []

    # Plain struct classes first (flow objects / actions may contain them).
    for _sname, sdt in _struct_types(ir_ctx):
        classes.append(lower_flow_type(ctx, sdt, ctx.mangle_name(sdt.name)))

    # Flow-object (buffer) classes next, so action handle fields resolve.
    # Name by the type's own ``name`` to match ``mangle_name(field.datatype.name)``
    # used for the handle fields below.
    for _fname, fdt in _flow_types(ir_ctx):
        classes.append(lower_flow_type(ctx, fdt, ctx.mangle_name(fdt.name)))
    # Resource type classes (extend zsp_resource).
    for _rname, rdt in _resource_types(ir_ctx):
        classes.append(lower_flow_type(ctx, rdt, ctx.mangle_name(rdt.name)))

    actions = _action_types(ir_ctx)
    sv_by_pss = {}
    for pss_name, dt in actions:
        sv_name = ctx.mangle_name(pss_name)
        sv_by_pss[pss_name] = sv_name
        classes.append(lower_pure_action(ctx, dt, sv_name))

    # Pick the root action: first export, else first atomic action.
    root_sv = None
    if export_actions:
        for ea in export_actions:
            # export names may be simple or qualified; match by suffix
            for pss_name, sv_name in sv_by_pss.items():
                if pss_name == ea or pss_name.endswith(f"::{ea}"):
                    root_sv = sv_name
                    break
            if root_sv:
                break
    if root_sv is None and classes:
        root_sv = sv_by_pss[actions[0][0]]

    top = _standalone_top(root_sv) if root_sv else None

    return emit_files(classes, output_dir,
                      runtime_lib_path=_runtime_lib_path(),
                      top_module_node=top,
                      package_name=package_name)
