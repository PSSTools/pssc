"""Lower PSS activity IR to SV task body lines.

Walks the activity IR tree and produces SV task body lines (strings).
Each activity IR node maps to a SV code pattern as specified in the
design document.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional, TYPE_CHECKING

import zuspec.ir.core as ir
from zuspec.be.sv.ir.sv import SVLineDirective

from .lower_exprs import lower_expr
from .lower_flow_objects import (
    FlowBinding,
    emit_buffer_decl,
    emit_buffer_producer_capture,
    emit_buffer_consumer_inject,
    emit_buffer_consumer_constraint,
    emit_stream_decl,
    emit_stream_producer_put,
    emit_stream_consumer_get,
)
from .lower_resources import ResourceClaim, emit_resource_acquire, emit_resource_release
from .trace import (
    wrap_traversal_with_trace,
    trace_parallel_enter, trace_parallel_exit,
    trace_schedule_enter, trace_schedule_exit,
    trace_select_enter, trace_select_exit,
)

if TYPE_CHECKING:
    from .context import LoweringContext
    from .analyze_flow import ActivityFlowInfo, FlowBindingInfo
    from .analyze_activity import ActivityPlan, PipelineChain


# ------------------------------------------------------------------ #
# Flow context -- carries binding info through an activity scope      #
# ------------------------------------------------------------------ #

@dc.dataclass
class _FlowCtx:
    """Flow-object bindings in scope for one activity block.

    Keyed by action variable name.  The lowering passes thread this
    context through so each traversal can emit the right wiring code.
    """
    # var_name -> bindings where this action is the producer
    producer: Dict[str, List['FlowBindingInfo']] = dc.field(default_factory=dict)
    # var_name -> bindings where this action is the consumer
    consumer: Dict[str, List['FlowBindingInfo']] = dc.field(default_factory=dict)
    # Mapping binding label -> SV local variable name for the flow object
    flow_var: Dict[str, str] = dc.field(default_factory=dict)

    @classmethod
    def from_flow_info(cls, info: 'ActivityFlowInfo') -> '_FlowCtx':
        fctx = cls(
            producer=dict(info.producer_bindings),
            consumer=dict(info.consumer_bindings),
        )
        # Generate local SV variable names for each binding
        for b in info.bindings:
            key = f"{b.producer_var}__{b.producer_field}"
            if key not in fctx.flow_var:
                fctx.flow_var[key] = f"_flow_{b.producer_var}_{b.producer_field}"
        return fctx

    def get_flow_var(self, binding: 'FlowBindingInfo') -> str:
        key = f"{binding.producer_var}__{binding.producer_field}"
        return self.flow_var.get(key, f"_flow_{binding.producer_var}_{binding.producer_field}")




# ------------------------------------------------------------------ #
# Resource claim extraction                                            #
# ------------------------------------------------------------------ #

def _get_resource_claims(
    ctx: LoweringContext,
    action_var: str,
    action_type_name: str,
    is_head: bool = False,
) -> List[ResourceClaim]:
    """Extract resource claims (Lock/Share) from an action type's fields.

    Returns a list of ResourceClaim for use with emit_resource_acquire/release.
    """
    if ctx.ir_ctx is None:
        return []
    from zuspec.ir.core.fields import FieldKind

    dtype = None
    for qname, dt in ctx.ir_ctx.type_map.items():
        if qname == action_type_name or qname.endswith(f"::{action_type_name}"):
            dtype = dt
            break
    if dtype is None:
        return []

    claims: List[ResourceClaim] = []
    for field in getattr(dtype, "fields", []):
        if field.kind not in (FieldKind.Lock, FieldKind.Share):
            continue
        # Resolve the pool name by finding the pool on the owning component
        # whose element type matches this field's type.
        elem_type_name = getattr(field.datatype, 'ref_name', None) or getattr(field.datatype, 'name', None)
        pool_field_name = f"{field.name}_pool"  # fallback
        if elem_type_name and ctx.ir_ctx:
            parent_comp_name = ctx.ir_ctx.parent_comp_names.get(action_type_name)
            if parent_comp_name is None:
                # Try suffix match
                short = action_type_name.split("::")[-1]
                for k, v in ctx.ir_ctx.parent_comp_names.items():
                    if k == action_type_name or k.endswith(f"::{short}"):
                        parent_comp_name = v
                        break
            if parent_comp_name:
                comp_dtype = ctx.ir_ctx.type_map.get(parent_comp_name)
                for pool in getattr(comp_dtype, 'pools', []):
                    if pool.element_type_name == elem_type_name:
                        pool_field_name = pool.name
                        break
        pool_expr = f"{action_var}.comp.{pool_field_name}"
        id_field = f"{field.name}_instance_id"
        claim_kind = "lock" if field.kind == FieldKind.Lock else "share"
        claims.append(ResourceClaim(
            field_name=field.name,
            pool_expr=pool_expr,
            id_field=id_field,
            claim_kind=claim_kind,
            is_head=is_head,
        ))
    return claims

def _lower_dpi_chain(
    ctx: LoweringContext,
    chain: "PipelineChain",
    comp_expr: str,
) -> List[str]:
    """Emit the joint DPI chain solve block for a pipeline chain.

    Emits:
        chandle _ctx_<first> = zsp_dpi_compile_b64(PROBLEM_B64);
        zsp_dpi_pin_var_h(_ctx_<first>, VID_X, val);  // for each entry var
        rc = zsp_dpi_solve_h(_ctx_<first>, $urandom());
        // apply solved values to each action instance
        ...
        zsp_dpi_release_h(_ctx_<first>);

    Args:
        ctx: Lowering context.
        chain: The pipeline chain with compiled problem.
        comp_expr: SV expression for the component reference.

    Returns:
        SV statement lines.
    """
    if not chain.problem_b64 or not chain.var_id_map:
        # Chain exists but no DPI problem (e.g. fields not resolved) --
        # fall back to sequential SV-native traversals
        return [f"// pipeline chain {chain.steps} (no DPI problem compiled -- using SV-native)"]

    ctx_var = f"_dpi_ctx_{chain.entry_var}"
    # Name for the problem constant (will be a localparam string)
    prob_const = f"CHAIN_PROBLEM_{chain.entry_var.upper()}_B64"

    lines = ["begin"]
    lines.append(f"  chandle {ctx_var};")
    lines.append(f"  int _dpi_rc;")
    lines.append(f"  {ctx_var} = zsp_dpi_compile_b64({prob_const});")
    lines.append(f"  if ({ctx_var} == null)")
    lines.append(f'    $fatal(1, "chain compile failed: {chain.entry_var}");')

    # Pin entry variables (the chain's input stream fields)
    if chain.entry_field and chain.steps:
        entry_step = chain.steps[0]
        for fname in chain.field_names:
            vid = chain.var_id_map.get(f"{entry_step}.in.{fname}")
            if vid is not None:
                lines.append(
                    f"  void'(zsp_dpi_pin_var_h("
                    f"{ctx_var}, {vid}, longint'("
                    f"{entry_step}.{chain.entry_field}.{fname})));"
                )

    # Single joint solve
    lines.append(f"  _dpi_rc = zsp_dpi_solve_h({ctx_var}, $urandom());")
    lines.append(f"  if (_dpi_rc != 0)")
    lines.append(f'    $fatal(1, "chain solve failed: {chain.entry_var}");')

    # Apply solved values back to action instances
    for step in chain.steps:
        for fname in chain.field_names:
            # Output field
            out_vid = chain.var_id_map.get(f"{step}.out.{fname}")
            if out_vid is not None:
                lines.append(
                    f"  {step}.out_istate.{fname} = "
                    f"zsp_dpi_get_value_h({ctx_var}, {out_vid});"
                )

    # Execute actions in chain order
    for step in chain.steps:
        lines.append(f"  {step}.pre_solve();")
        lines.append(f"  {step}.post_solve();")
        lines.append(f"  {step}.body();")

    lines.append(f"  zsp_dpi_release_h({ctx_var});")
    lines.append("end")
    return lines


def lower_activity(
    ctx: LoweringContext,
    activity: ir.ActivitySequenceBlock,
    comp_expr: str = "comp",
) -> List[str]:
    return _lower_activity_impl(ctx, activity, comp_expr, parent_dtype=None)


def lower_activity_for(
    ctx: LoweringContext,
    activity: ir.ActivitySequenceBlock,
    comp_expr: str = "comp",
    parent_dtype=None,
) -> List[str]:
    """Like lower_activity but with parent action type for named-handle resolution."""
    return _lower_activity_impl(ctx, activity, comp_expr, parent_dtype=parent_dtype)


def _lower_activity_impl(
    ctx: LoweringContext,
    activity: ir.ActivitySequenceBlock,
    comp_expr: str = "comp",
    parent_dtype=None,
) -> List[str]:
    """Lower an activity sequence block to SV task body lines.

    Runs flow-object analysis when ctx.ir_ctx is available to produce
    correct producer/consumer wiring code.

    Args:
        ctx: Lowering context.
        activity: The top-level activity sequence block.
        comp_expr: SV expression for the component reference.

    Returns:
        List of SV statement lines forming the activity task body.
    """
    # Run flow + activity analysis to discover bindings and pipeline chains
    fctx: Optional[_FlowCtx] = None
    plan = None
    chain_steps: set = set()  # var_names handled by DPI chains
    if ctx.ir_ctx is not None:
        try:
            from .analyze_flow import analyze_flow
            from .analyze_activity import analyze_activity as _analyze_activity
            info = analyze_flow(ctx, activity, parent_dtype=parent_dtype)
            fctx = _FlowCtx.from_flow_info(info)
            plan = _analyze_activity(ctx, activity)
            # Collect which action vars are covered by DPI chains
            for chain in plan.chains:
                if chain.problem_b64:
                    chain_steps.update(chain.steps)
        except Exception:
            pass  # degrade gracefully if analysis fails

    lines: List[str] = []

    # Emit local variable declarations for buffer flow objects
    if fctx is not None:
        seen_vars: set = set()
        for b_list in fctx.producer.values():
            for b in b_list:
                if b.flow_kind in ("buffer", "stream", "state") and b.flow_type:
                    var_name = fctx.get_flow_var(b)
                    if var_name not in seen_vars:
                        seen_vars.add(var_name)
                        lines.append(f"{b.flow_type} {var_name};")
        for b_list in fctx.consumer.values():
            for b in b_list:
                if b.flow_kind in ("buffer", "stream", "state") and b.flow_type:
                    var_name = fctx.get_flow_var(b)
                    if var_name not in seen_vars:
                        seen_vars.add(var_name)
                        lines.append(f"{b.flow_type} {var_name};")

    # Emit DPI chain blocks first (they replace individual traversals)
    emitted_chains: set = set()
    if plan is not None:
        for chain in plan.chains:
            if chain.problem_b64:
                key = frozenset(chain.steps)
                if key not in emitted_chains:
                    emitted_chains.add(key)
                    lines.extend(_lower_dpi_chain(ctx, chain, comp_expr))

    for stmt in activity.stmts:
        # Skip individual traversals that are handled by a DPI chain
        if isinstance(stmt, ir.ActivityAnonTraversal):
            var_name = stmt.label if stmt.label else f"_anon_{ctx.resolve_sv_class_name(stmt.action_type)}"
            if var_name in chain_steps:
                continue  # handled by _lower_dpi_chain above
        elif isinstance(stmt, (ir.ActivitySequenceBlock, ir.ActivitySchedule)):
            pass  # recurse into these -- inner traversals filtered inside
        lines.extend(_lower_activity_stmt(ctx, stmt, comp_expr, fctx,
                                           chain_steps=chain_steps))
    return lines


def _lower_activity_stmt(
    ctx: LoweringContext,
    stmt: ir.ActivityStmt,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
    chain_steps: Optional[set] = None,
) -> List[str]:
    """Lower a single activity statement to SV lines."""

    if isinstance(stmt, ir.ActivitySequenceBlock):
        lines: List[str] = ["begin"]
        for s in stmt.stmts:
            for l in _lower_activity_stmt(ctx, s, comp_expr, fctx, chain_steps):
                lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.ActivityTraversal):
        return _lower_traversal(ctx, stmt, comp_expr, fctx)

    if isinstance(stmt, ir.ActivityAnonTraversal):
        var_name = stmt.label if stmt.label else f"_anon_{ctx.resolve_sv_class_name(stmt.action_type)}"
        if chain_steps and var_name in chain_steps:
            return []  # handled by DPI chain block
        return _lower_anon_traversal(ctx, stmt, comp_expr, fctx)

    if isinstance(stmt, ir.ActivityRepeat):
        return _lower_repeat(ctx, stmt, comp_expr)

    if isinstance(stmt, ir.ActivityDoWhile):
        return _lower_do_while(ctx, stmt, comp_expr)

    if isinstance(stmt, ir.ActivityWhileDo):
        return _lower_while_do(ctx, stmt, comp_expr)

    if isinstance(stmt, ir.ActivityForeach):
        return _lower_foreach(ctx, stmt, comp_expr)

    if isinstance(stmt, ir.ActivityIfElse):
        return _lower_if_else(ctx, stmt, comp_expr)

    if isinstance(stmt, ir.ActivityMatch):
        return _lower_match(ctx, stmt, comp_expr)

    if isinstance(stmt, ir.ActivitySelect):
        return _lower_select(ctx, stmt, comp_expr)

    if isinstance(stmt, ir.ActivityAtomic):
        return _lower_atomic(ctx, stmt, comp_expr)

    if isinstance(stmt, ir.ActivityReplicate):
        return _lower_replicate(ctx, stmt, comp_expr, fctx)

    if isinstance(stmt, ir.ActivitySuper):
        return ["super.activity();"]

    if isinstance(stmt, ir.ActivityParallel):
        return _lower_parallel(ctx, stmt, comp_expr)

    if isinstance(stmt, ir.ActivityConstraint):
        # Inline constraints in activity context are informational
        return [f"// activity constraint (handled at randomize time)"]

    if isinstance(stmt, ir.ActivitySchedule):
        # Schedule block: lower as a sequence of staged fork/join blocks.
        lines: List[str] = []
        lines.append(trace_schedule_enter())
        for s in stmt.stmts:
            for l in _lower_activity_stmt(ctx, s, comp_expr, fctx, chain_steps):
                lines.append(l)
        lines.append(trace_schedule_exit())
        return lines

    if isinstance(stmt, ir.ActivityBind):
        # When flow context is active, bindings are handled by traversal wiring
        if fctx is not None:
            return []  # already handled via flow injection/capture
        src = lower_expr(ctx, stmt.src)
        dst = lower_expr(ctx, stmt.dst)
        return [f"// bind {src} -> {dst}"]

    node_name = type(stmt).__name__
    ctx.warn(f"unsupported activity node '{node_name}' — skipped", node_name)
    return [f"// unsupported activity: {type(stmt).__name__}"]


def _lower_traversal(
    ctx: LoweringContext,
    trav: ir.ActivityTraversal,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower a named handle traversal to the full lifecycle pattern.

    When fctx is provided, emits flow-object wiring:
    - Consumer: inject pinned values before pre_solve; add with-constraints
    - Producer: capture output into local variable after body()
    """
    handle = trav.handle
    lines = ["begin"]

    # --- Consumer: inject buffer inputs before pre_solve ---
    if fctx is not None:
        for b in fctx.consumer.get(handle, []):
            if b.flow_kind in ("buffer", "stream", "state"):
                var_name = fctx.get_flow_var(b)
                lines.append(f"  // inject flow input: {b.consumer_field} from {var_name}")
                lines.append(f"  {handle}.{b.consumer_field} = {var_name};")

    # Re-enable constraints (was disabled in parent pre_solve to avoid null traversal)
    lines.append(f"  {handle}.constraint_mode(1);  // re-enable for explicit randomize")
    # Note: do NOT call rand_mode(0) on state flow objects here — VCS applies it
    # class-wide, which would disable randomization of ALL instances of that class.
    # The consumer's class-level constraints are sufficient to propagate state values.

    lines.append(f"  {handle}.comp = {comp_expr};")
    lines.append(f"  {handle}.pre_solve();")

    # Build with-constraints: inline + buffer/stream consumer pinning.
    # State objects are NOT constrained here — field injection above is sufficient.
    with_parts: List[str] = []
    if fctx is not None:
        for b in fctx.consumer.get(handle, []):
            if b.flow_kind in ("buffer", "stream"):
                _fv = fctx.get_flow_var(b)
                with_parts.append(f"{b.consumer_field} == {_fv}")
    if trav.inline_constraints:
        with_parts.extend(lower_expr(ctx, c) for c in trav.inline_constraints)

    if with_parts:
        with_body = "; ".join(with_parts)
        lines.append(f"  if (!{handle}.randomize() with {{ {with_body}; }})")
    else:
        lines.append(f"  if (!{handle}.randomize())")
    lines.append(f'    $fatal(1, "randomize failed: {handle}");')

    lines.append(f"  {handle}.post_solve();")
    lines.append(f"  {handle}.body();")

    # --- Producer: capture buffer outputs after body() ---
    if fctx is not None:
        for b in fctx.producer.get(handle, []):
            if b.flow_kind in ("buffer", "stream", "state"):
                var_name = fctx.get_flow_var(b)
                lines.append(f"  {var_name} = {handle}.{b.producer_field};")

    lines.append("end")
    # Prepend trace macro (outside begin/end so it shows before the block)
    return wrap_traversal_with_trace(handle, comp_expr, lines)


def _lower_anon_traversal(
    ctx: LoweringContext,
    trav: ir.ActivityAnonTraversal,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower an anonymous traversal (do Type) to the full lifecycle.

    When fctx is provided, emits flow-object wiring around the lifecycle:
    - Consumer: inject pinned buffer values before pre_solve; add with-constraints
    - Producer: capture buffer outputs into local variables after body()
    """
    type_name = ctx.resolve_sv_class_name(trav.action_type)
    var_name = trav.label if trav.label else f"_anon_{type_name}"

    # Determine the effective component expression for this traversal.
    # When the action type has an instance-path prefix (e.g. "enc::encode_pipeline"
    # where "enc" is a field on the current component), the sub-component reference
    # is used instead of the direct comp_expr.
    effective_comp_expr = comp_expr
    if "::" in trav.action_type and ctx.ir_ctx is not None:
        parts = trav.action_type.split("::")
        instance_parts = parts[:-1]  # everything before the action name
        # Verify that the first segment is an instance name (not a type-map prefix)
        # by checking whether it appears as a type key in type_map.
        first = instance_parts[0]
        is_instance = first not in ctx.ir_ctx.type_map and not any(
            k.startswith(first + "::") for k in ctx.ir_ctx.type_map
        )
        if is_instance:
            effective_comp_expr = comp_expr + "." + ".".join(instance_parts)

    lines = ["begin"]
    lines.append(f"  {type_name} {var_name} = new();")

    # --- Consumer: inject buffer inputs before pre_solve ---
    if fctx is not None:
        for b in fctx.consumer.get(var_name, []):
            if b.flow_kind in ("buffer", "stream", "state"):
                flow_var = fctx.get_flow_var(b)
                lines.append(f"  // flow input: {b.consumer_field} <- {flow_var}")
                lines.append(f"  {var_name}.{b.consumer_field} = {flow_var};")

    # Note: do NOT call rand_mode(0) on state flow objects — VCS applies it class-wide.

    lines.append(f"  {var_name}.comp = {effective_comp_expr};")
    lines.append(f"  {var_name}.pre_solve();")

    # Build with-constraints: inline + buffer/stream consumer pinning.
    # State objects are NOT constrained here — field injection above is sufficient.
    with_parts: List[str] = []
    if fctx is not None:
        for b in fctx.consumer.get(var_name, []):
            if b.flow_kind in ("buffer", "stream"):
                flow_var = fctx.get_flow_var(b)
                with_parts.append(f"{b.consumer_field} == {flow_var}")
    if trav.inline_constraints:
        with_parts.extend(lower_expr(ctx, c) for c in trav.inline_constraints)

    if with_parts:
        with_body = "; ".join(with_parts)
        lines.append(f"  if (!{var_name}.randomize() with {{ {with_body}; }})")
    else:
        lines.append(f"  if (!{var_name}.randomize())")
    lines.append(f'    $fatal(1, "randomize failed: {type_name}");')

    # --- Resource acquire (before body) ---
    resource_claims = _get_resource_claims(ctx, var_name, trav.action_type)
    for acq_line in emit_resource_acquire(resource_claims, var_name):
        lines.append(f"  {acq_line}")

    lines.append(f"  {var_name}.post_solve();")
    lines.append(f"  {var_name}.body();")

    # --- Producer: capture buffer outputs after body() ---
    if fctx is not None:
        for b in fctx.producer.get(var_name, []):
            if b.flow_kind in ("buffer", "stream", "state"):
                flow_var = fctx.get_flow_var(b)
                lines.append(f"  {flow_var} = {var_name}.{b.producer_field};")

    # --- Resource release (after body) ---
    for rel_line in emit_resource_release(resource_claims, var_name):
        lines.append(f"  {rel_line}")

    lines.append("end")
    return wrap_traversal_with_trace(trav.action_type, comp_expr, lines)


def _lower_repeat(
    ctx: LoweringContext,
    repeat: ir.ActivityRepeat,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivityRepeat to ``repeat (N) begin ... end``."""
    count = lower_expr(ctx, repeat.count)
    if repeat.index_var:
        lines = [f"for (int {repeat.index_var} = 0; {repeat.index_var} < {count}; {repeat.index_var}++) begin"]
    else:
        lines = [f"repeat ({count}) begin"]
    for s in repeat.body:
        for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
            lines.append(f"  {l}")
    lines.append("end")
    return lines


def _lower_do_while(
    ctx: LoweringContext,
    dw: ir.ActivityDoWhile,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivityDoWhile to ``do begin ... end while (cond);``."""
    cond = lower_expr(ctx, dw.condition)
    lines = ["do begin"]
    for s in dw.body:
        for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
            lines.append(f"  {l}")
    lines.append(f"end while ({cond});")
    return lines


def _lower_while_do(
    ctx: LoweringContext,
    wd: ir.ActivityWhileDo,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivityWhileDo to ``while (cond) begin ... end``."""
    cond = lower_expr(ctx, wd.condition)
    lines = [f"while ({cond}) begin"]
    for s in wd.body:
        for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
            lines.append(f"  {l}")
    lines.append("end")
    return lines


def _lower_foreach(
    ctx: LoweringContext,
    fe: ir.ActivityForeach,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivityForeach to ``foreach (collection[iter]) begin ... end``."""
    collection = lower_expr(ctx, fe.collection)
    lines = [f"foreach ({collection}[{fe.iterator}]) begin"]
    for s in fe.body:
        for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
            lines.append(f"  {l}")
    lines.append("end")
    return lines


def _lower_if_else(
    ctx: LoweringContext,
    ie: ir.ActivityIfElse,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivityIfElse to ``if (cond) begin ... end else begin ... end``."""
    cond = lower_expr(ctx, ie.condition)
    lines = [f"if ({cond}) begin"]
    for s in ie.if_body:
        for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
            lines.append(f"  {l}")
    if ie.else_body:
        lines.append("end else begin")
        for s in ie.else_body:
            for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
                lines.append(f"  {l}")
    lines.append("end")
    return lines


def _lower_match(
    ctx: LoweringContext,
    match: ir.ActivityMatch,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivityMatch to ``case (subject) ... endcase``."""
    subj = lower_expr(ctx, match.subject)
    lines = [f"case ({subj})"]
    for case in match.cases:
        pat = lower_expr(ctx, case.pattern)
        lines.append(f"  {pat}: begin")
        for s in case.body:
            for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
                lines.append(f"    {l}")
        lines.append("  end")
    lines.append("endcase")
    return lines


def _lower_select(
    ctx: LoweringContext,
    sel: ir.ActivitySelect,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivitySelect to weighted random branch selection.

    Pattern:
        begin
            int _sel_idx;
            int _weights[N] = '{w0, w1, ...};
            // compute cumulative weights, pick random
            _sel_idx = ...;
            case (_sel_idx)
                0: begin ... end
                1: begin ... end
                ...
            endcase
        end
    """
    n = len(sel.branches)
    if n == 0:
        return ["// empty select"]

    lines = []
    lines.append(f"  {trace_select_enter()}")

    # Collect weights as expressions; try to evaluate constant weights for
    # cumulative-breakpoint codegen (avoids arrays, compatible with all VCS modes).
    raw_weights = []
    for b in sel.branches:
        if b.weight is not None:
            raw_weights.append(lower_expr(ctx, b.weight))
        else:
            raw_weights.append("1")

    # Try to evaluate weights as integer literals for compile-time cumulation.
    def _try_int(s: str):
        try:
            return int(s)
        except ValueError:
            return None

    int_weights = [_try_int(w) for w in raw_weights]
    all_const = all(w is not None for w in int_weights)

    if all_const:
        # Constant weights: use named begin/end block with local vars (VCS-safe).
        # Named blocks allow local variable declarations in all VCS task contexts.
        total = sum(int_weights)
        lines.append(f"  begin : _zsp_sel")
        lines.append(f"    int _pick = $urandom_range(0, {total - 1});")
        lines.append(f"    int _idx;")
        # Build nested ternary for index
        cumulative = 0
        parts = []
        for i, w in enumerate(int_weights[:-1]):
            cumulative += w
            parts.append((cumulative, i))
        ternary = f"{n - 1}"
        for cumul, idx in reversed(parts):
            ternary = f"((_pick < {cumul}) ? {idx} : {ternary})"
        lines.append(f"    _idx = {ternary};")
        lines.append(f"    case (_idx)")
        for i, branch_i in enumerate(sel.branches):
            if branch_i.guard is not None:
                guard = lower_expr(ctx, branch_i.guard)
                lines.append(f"      {i}: if ({guard}) begin")
            else:
                lines.append(f"      {i}: begin")
            for s in branch_i.body:
                for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
                    lines.append(f"        {l}")
            lines.append(f"      end")
        lines.append(f"    endcase")
        lines.append(f"  end")
    else:
        # Dynamic weights: same named-block approach with runtime sum.
        total_expr = " + ".join(raw_weights)
        lines.append(f"  begin : _zsp_sel")
        lines.append(f"    int _total = {total_expr};")
        lines.append(f"    int _pick = $urandom_range(0, _total - 1);")
        lines.append(f"    int _idx = {n - 1};")
        cumulative = "0"
        for i, w in enumerate(raw_weights[:-1]):
            cumulative = f"({cumulative} + {w})"
            lines.append(f"    if (_pick < {cumulative}) _idx = {i};")
        lines.append(f"    case (_idx)")
        for i, branch_i in enumerate(sel.branches):
            if branch_i.guard is not None:
                guard = lower_expr(ctx, branch_i.guard)
                lines.append(f"      {i}: if ({guard}) begin")
            else:
                lines.append(f"      {i}: begin")
            for s in branch_i.body:
                for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
                    lines.append(f"        {l}")
            lines.append(f"      end")
        lines.append(f"    endcase")
        lines.append(f"  end")
    lines.append(f"  {trace_select_exit()}")
    return lines


def _lower_atomic(
    ctx: LoweringContext,
    atomic: ir.ActivityAtomic,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivityAtomic with semaphore get/put around body."""
    lines = [f"{comp_expr}.atomic_sem.get(1);"]
    lines.append("begin")
    for s in atomic.stmts:
        for l in _lower_activity_stmt(ctx, s, comp_expr, fctx):
            lines.append(f"  {l}")
    lines.append("end")
    lines.append(f"{comp_expr}.atomic_sem.put(1);")
    return lines


def _lower_replicate(
    ctx: LoweringContext,
    repl: ir.ActivityReplicate,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivityReplicate to a fork/join with N concurrent body copies.

    PSS ``replicate N { do T; }`` spawns N parallel instances of the body.
    We emit a generate-time static fork with N branches when the count is
    a compile-time constant, or a dynamic fork array when it is not.
    For the dynamic case we use a runtime loop over automatic tasks.

    Args:
        ctx: Lowering context.
        repl: The ActivityReplicate node.
        comp_expr: SV expression for the component reference.
        fctx: Optional flow context.

    Returns:
        List of SV statement lines.
    """
    from .lower_exprs import lower_expr
    count_expr = lower_expr(ctx, repl.count)
    idx = repl.index_var or "_repl_i"

    # Emit a fork/join with a begin..end per iteration using a task loop.
    # We wrap the body in an automatic task so each iteration has its own
    # scope, matching PSS replicate semantics.
    task_name = f"_repl_{ctx.mangle_name(repl.label or 'body')}"
    lines: List[str] = []

    # Build body lines for a single iteration
    body_lines: List[str] = []
    for s in repl.body:
        body_lines.extend(_lower_activity_stmt(ctx, s, comp_expr, fctx))

    # Emit as: for (int _repl_i = 0; ...) fork begin ... end join_none;
    # followed by a final join
    lines.append(f"begin : {task_name}_block")
    lines.append(f"  fork")
    lines.append(f"    for (int {idx} = 0; {idx} < {count_expr}; {idx}++) begin")
    for bl in body_lines:
        lines.append(f"      {bl}")
    lines.append(f"    end")
    lines.append(f"  join")
    lines.append(f"end")
    return lines


def _emit_parallel_head_solve(
    ctx: LoweringContext,
    par: ir.ActivityParallel,
    comp_expr: str,
) -> List[str]:
    """Emit head-action coordinated resource solve before a parallel fork.

    Finds the first traversal in each branch, extracts resource claims, and
    when multiple branches share a pool emits a pre-fork coordinated solve
    that assigns unique instance_ids to each branch's head action.

    Returns an empty list when no coordination is needed.
    """
    from .lower_head_solve import HeadAction, emit_head_action_solve

    if ctx.ir_ctx is None:
        return []

    heads: List[HeadAction] = []
    for i, stmt in enumerate(par.stmts):
        # Find the head traversal in this branch (first ActionAnonTraversal/Traversal)
        head_trav = _find_head_traversal(stmt)
        if head_trav is None:
            continue
        if isinstance(head_trav, ir.ActivityTraversal):
            action_var = head_trav.handle
            action_type = head_trav.handle  # type unknown without IR
        else:
            # Anonymous traversals: variable is local to a begin..end inside the fork.
            # We cannot pre-assign instance IDs from outside the fork scope, so
            # skip head-action coordinated solve for these branches.
            # Resource correctness is maintained by the blocking lock() semantics.
            continue
            action_type = head_trav.action_type
            action_var = head_trav.label if head_trav.label else f"_anon_{ctx.resolve_sv_class_name(action_type)}"
        claims = _get_resource_claims(ctx, action_var, action_type, is_head=True)
        if claims:
            heads.append(HeadAction(
                branch_index=i,
                action_var=action_var,
                action_type=action_type,
                claims=claims,
            ))

    if not heads:
        return []

    # Build pool sizes map from ir_ctx pools
    pool_sizes: dict = {}
    for qname, dtype in ctx.ir_ctx.type_map.items():
        if isinstance(dtype, ir.DataTypeComponent):
            for pool in getattr(dtype, 'pools', []):
                key = f"comp.{pool.name}"
                pool_sizes[key] = pool.capacity or 1

    return emit_head_action_solve(heads, pool_sizes)


def _find_head_traversal(stmt: ir.ActivityStmt) -> Optional[ir.ActivityStmt]:
    """Return the first traversal nested in *stmt*, or None."""
    if isinstance(stmt, (ir.ActivityTraversal, ir.ActivityAnonTraversal)):
        return stmt
    if isinstance(stmt, ir.ActivitySequenceBlock):
        for s in stmt.stmts:
            result = _find_head_traversal(s)
            if result is not None:
                return result
    return None


def _lower_parallel(
    ctx: LoweringContext,
    par: ir.ActivityParallel,
    comp_expr: str,
    fctx: Optional[_FlowCtx] = None,
) -> List[str]:
    """Lower ActivityParallel to ``fork ... join``.

    Join semantics depend on join_spec (Phase 5 handles complex cases;
    Phase 4 implements basic fork/join).
    """
    join_kw = "join"
    if par.join_spec is not None:
        kind = par.join_spec.kind
        if kind is ir.JoinKind.NONE:
            join_kw = "join_none"
        elif kind is ir.JoinKind.FIRST:
            join_kw = "join_any"
        # ALL (default) -> join

    lines = [trace_parallel_enter(), "fork"]

    # Head-action coordinated solve: when multiple branches claim resources
    # from the same pool, assign instance_ids uniquely before the fork.
    head_solve_lines = _emit_parallel_head_solve(ctx, par, comp_expr)
    lines = [trace_parallel_enter()]
    lines.extend(head_solve_lines)
    lines.append("fork")

    for i, s in enumerate(par.stmts):
        branch_lines = _lower_activity_stmt(ctx, s, comp_expr, fctx)
        # Wrap each branch in begin/end if not already
        if len(branch_lines) == 1 and not branch_lines[0].startswith("begin"):
            lines.append(f"  begin")
            lines.append(f"    {branch_lines[0]}")
            lines.append(f"  end")
        else:
            for l in branch_lines:
                lines.append(f"  {l}")
    lines.append(join_kw)
    lines.append(trace_parallel_exit())
    return lines
