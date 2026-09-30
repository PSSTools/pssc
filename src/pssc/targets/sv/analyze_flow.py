"""Flow-object analysis pass (Phase 3).

Walks an activity block and extracts:
  - Traversal labels (handle -> action_type)
  - Explicit flow-object bindings from ActivityBind nodes
  - Flow-object field metadata (which fields are input vs output, and flow kind)

The output is an ActivityFlowInfo struct consumed by the activity lowering
pass to determine how to wire flow-object data between action traversals.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

import zuspec.ir.core as ir
from zuspec.ir.core import expr as ir_expr

if TYPE_CHECKING:
    from .context import LoweringContext


# ------------------------------------------------------------------ #
# Data structures                                                       #
# ------------------------------------------------------------------ #

@dc.dataclass
class TraversalMeta:
    """Metadata for one action traversal within an activity."""
    var_name: str          # SV variable name (label or generated _anon_N)
    action_type: str       # qualified PSS action type name
    is_anonymous: bool     # True for 'do Type' without explicit label


@dc.dataclass
class FlowBindingInfo:
    """One resolved flow-object binding between a producer and consumer."""
    producer_var: str      # SV variable name of the producing action
    producer_field: str    # output field name on the producer
    consumer_var: str      # SV variable name of the consuming action
    consumer_field: str    # input field name on the consumer
    flow_kind: str         # "buffer", "stream", or "state"
    flow_type: str         # mangled SV type name of the flow object


@dc.dataclass
class ActivityFlowInfo:
    """Complete flow analysis result for one activity block."""
    traversals: List[TraversalMeta]
    bindings: List[FlowBindingInfo]
    # Mapping var_name -> list of bindings where this action is the producer
    producer_bindings: Dict[str, List[FlowBindingInfo]]
    # Mapping var_name -> list of bindings where this action is the consumer
    consumer_bindings: Dict[str, List[FlowBindingInfo]]


# ------------------------------------------------------------------ #
# Expression helpers                                                    #
# ------------------------------------------------------------------ #

def _expr_attr_chain(expr: ir.Expr) -> Optional[Tuple[str, str]]:
    """Extract (base_name, field_name) from a two-level ExprAttribute chain.

    Handles:  self.label.field  ->  ('label', 'field')
              self.field        ->  ('', 'field')

    Returns None for other expression shapes.
    """
    if not isinstance(expr, ir_expr.ExprAttribute):
        return None

    attr = expr.attr
    base = expr.value

    if isinstance(base, ir_expr.TypeExprRefSelf):
        # self.field  -- self references are stripped; return ('', field)
        return ('', attr)

    if isinstance(base, ir_expr.ExprAttribute):
        inner_attr = base.attr
        inner_base = base.value
        if isinstance(inner_base, ir_expr.TypeExprRefSelf):
            # self.label.field
            return (inner_attr, attr)

    if isinstance(base, ir_expr.ExprRefLocal):
        return (base.name, attr)

    return None


# ------------------------------------------------------------------ #
# Analysis                                                              #
# ------------------------------------------------------------------ #

def _resolve_handle_type(ctx: 'LoweringContext', handle: str, parent_dtype) -> str:
    """Return the action type name for a named handle field on *parent_dtype*.

    Named action handles in PSS compound actions are class fields whose
    datatype is the action type.  We look up the field by name and return
    the most-qualified type key for it.
    """
    if parent_dtype is None or ctx.ir_ctx is None:
        return ""
    for field in getattr(parent_dtype, 'fields', []):
        if field.name != handle:
            continue
        ref = getattr(field.datatype, 'ref_name', None) or getattr(field.datatype, 'name', None)
        if not ref:
            continue
        # Find the most-qualified matching key
        target = ctx.ir_ctx.type_map.get(ref)
        if target is None:
            for k, v in ctx.ir_ctx.type_map.items():
                if k.endswith(f"::{ref}"):
                    target = v
                    ref = k
                    break
        return ref or ""
    return ""


def analyze_flow(
    ctx: 'LoweringContext',
    activity: ir.ActivitySequenceBlock,
    parent_dtype=None,
) -> ActivityFlowInfo:
    """Analyze flow-object bindings in an activity block.

    Args:
        ctx: Lowering context (for type lookup).
        activity: The activity block to analyze.
        parent_dtype: The IR type of the action that owns this activity.
            When provided, named handle field types are resolved from it.

    Returns:
        ActivityFlowInfo with traversal metadata and resolved bindings.
    """
    traversals: List[TraversalMeta] = []
    raw_binds: List[Tuple[str, str, str, str]] = []  # (prod_var, prod_field, cons_var, cons_field)

    _anon_counter = [0]

    def _collect(stmts: List[ir.ActivityStmt]) -> None:
        for stmt in stmts:
            if isinstance(stmt, (ir.ActivitySequenceBlock,
                                  ir.ActivitySchedule,
                                  ir.ActivityParallel)):
                sub = stmt.stmts if hasattr(stmt, 'stmts') else []
                _collect(sub)

            elif isinstance(stmt, ir.ActivityAnonTraversal):
                label = stmt.label
                if not label:
                    label = f"_anon_{_anon_counter[0]}"
                    _anon_counter[0] += 1
                traversals.append(TraversalMeta(
                    var_name=label,
                    action_type=stmt.action_type,
                    is_anonymous=(stmt.label is None),
                ))

            elif isinstance(stmt, ir.ActivityTraversal):
                traversals.append(TraversalMeta(
                    var_name=stmt.handle,
                    action_type=_resolve_handle_type(ctx, stmt.handle, parent_dtype),
                    is_anonymous=False,
                ))

            elif isinstance(stmt, ir.ActivityBind):
                src_parts = _expr_attr_chain(stmt.src)
                dst_parts = _expr_attr_chain(stmt.dst)
                if src_parts and dst_parts:
                    # Store both orderings; direction resolved later from field kinds
                    if src_parts[0] and dst_parts[0]:
                        raw_binds.append((src_parts[0], src_parts[1],
                                          dst_parts[0], dst_parts[1]))

    _collect(activity.stmts)

    # Resolve flow-object kind from IR type info
    var_to_meta: Dict[str, TraversalMeta] = {t.var_name: t for t in traversals}

    bindings: List[FlowBindingInfo] = []
    def _field_io_kind(var_name: str, field_name: str) -> str:
        """Return 'Output', 'Input', or '' for the field kind.

        Walks the inheritance hierarchy so inherited flow-object fields
        (e.g., from an abstract action base class) are found correctly.
        """
        from zuspec.ir.core.fields import FieldKind as _FK
        meta = var_to_meta.get(var_name)
        if meta is None or not meta.action_type or ctx.ir_ctx is None:
            return ""
        at = meta.action_type
        dtype = None
        for k, v in ctx.ir_ctx.type_map.items():
            if k == at or k.endswith(f"::{at}"):
                dtype = v
                break
        if dtype is None:
            return ""
        # Walk the inheritance chain to find inherited fields too
        _seen: set = set()
        _cur = dtype
        while _cur is not None:
            if id(_cur) in _seen:
                break
            _seen.add(id(_cur))
            for f in getattr(_cur, 'fields', []):
                if f.name == field_name:
                    if f.kind == _FK.Output:
                        return 'Output'
                    if f.kind == _FK.Input:
                        return 'Input'
            # Follow super type
            _sup = getattr(_cur, 'super', None)
            if _sup is None:
                break
            _sup_name = getattr(_sup, 'ref_name', None) or getattr(_sup, 'name', None)
            if not _sup_name:
                break
            _next = None
            for k, v in ctx.ir_ctx.type_map.items():
                if k == _sup_name or k.endswith(f"::{_sup_name}"):
                    _next = v
                    break
            _cur = _next
        return ""

    for prod_var, prod_field, cons_var, cons_field in raw_binds:
        # Determine producer/consumer from field kinds.
        # The PSS IR may store bind as (consumer, producer) or (producer, consumer)
        # depending on whether the IR came from manual construction or pssparser.
        # We use field kinds to resolve direction correctly.
        kind_a = _field_io_kind(prod_var, prod_field)
        kind_b = _field_io_kind(cons_var, cons_field)
        if kind_a == 'Input' and kind_b == 'Output':
            # First arg is consumer, second is producer — swap
            prod_var, prod_field, cons_var, cons_field = (
                cons_var, cons_field, prod_var, prod_field
            )
        # (If both are Output, Input, or unknown: keep as-is — tests use src=producer)
        flow_kind, flow_type = _resolve_flow_kind(
            ctx, var_to_meta, prod_var, prod_field
        )
        bindings.append(FlowBindingInfo(
            producer_var=prod_var,
            producer_field=prod_field,
            consumer_var=cons_var,
            consumer_field=cons_field,
            flow_kind=flow_kind,
            flow_type=flow_type,
        ))

    # Build indexed lookups
    producer_bindings: Dict[str, List[FlowBindingInfo]] = {}
    consumer_bindings: Dict[str, List[FlowBindingInfo]] = {}
    for b in bindings:
        producer_bindings.setdefault(b.producer_var, []).append(b)
        consumer_bindings.setdefault(b.consumer_var, []).append(b)

    return ActivityFlowInfo(
        traversals=traversals,
        bindings=bindings,
        producer_bindings=producer_bindings,
        consumer_bindings=consumer_bindings,
    )


def _resolve_flow_kind(
    ctx: 'LoweringContext',
    var_to_meta: Dict[str, TraversalMeta],
    prod_var: str,
    prod_field: str,
) -> Tuple[str, str]:
    """Determine the flow_kind and flow_type for a binding.

    Looks up the producer action's type, finds the field, and reads its
    flow_kind from the IR DataTypeStruct.

    Returns ("buffer", "") if the kind cannot be resolved (safe default for
    sequential buffer flow objects).
    """
    if ctx.ir_ctx is None:
        return ("buffer", "")

    meta = var_to_meta.get(prod_var)
    if meta is None or not meta.action_type:
        return ("buffer", "")

    # Look up the action type and find the output field
    action_type = meta.action_type
    # Try qualified names: action_type may be bare or qualified
    dtype = None
    for qname, dt in ctx.ir_ctx.type_map.items():
        if qname == action_type or qname.endswith(f"::{action_type}"):
            dtype = dt
            break
    if dtype is None:
        return ("buffer", "")

    # Walk the inheritance chain to find inherited output fields too
    _seen_types: set = set()
    _cur_dtype = dtype
    while _cur_dtype is not None:
        if id(_cur_dtype) in _seen_types:
            break
        _seen_types.add(id(_cur_dtype))
        for field in _cur_dtype.fields:
            if field.name != prod_field:
                continue
            ft = field.datatype
            if hasattr(ft, 'flow_kind') and ft.flow_kind:
                kind = ft.flow_kind.name.lower()   # IR FlowKind -> binding's str
                ft_name = getattr(ft, 'name', '') or ''
                sv_type = ctx.resolve_sv_class_name(ft_name) if ft_name else ""
                return (kind, sv_type)
            # DataTypeRef
            if isinstance(ft, ir.DataTypeRef):
                ref_name = ft.ref_name
                for rname, rdt in ctx.ir_ctx.type_map.items():
                    if rname == ref_name or rname.endswith(f"::{ref_name}"):
                        if hasattr(rdt, 'flow_kind') and rdt.flow_kind:
                            return (rdt.flow_kind.name.lower(),
                                    ctx.resolve_sv_class_name(rname))
            break
        # Follow super type
        _sup = getattr(_cur_dtype, 'super', None)
        if _sup is None:
            break
        _sn = getattr(_sup, 'ref_name', None) or getattr(_sup, 'name', None)
        _cur_dtype = None
        if _sn:
            for k, v in ctx.ir_ctx.type_map.items():
                if k == _sn or k.endswith(f"::{_sn}"):
                    _cur_dtype = v
                    break
    return ("buffer", "")


def get_flow_input_fields(
    ctx: 'LoweringContext',
    action_type_name: str,
) -> Set[str]:
    """Return the set of flow-object INPUT field names for an action type."""
    from zuspec.ir.core.fields import FieldKind
    if ctx.ir_ctx is None:
        return set()
    result: Set[str] = set()
    for qname, dtype in ctx.ir_ctx.type_map.items():
        if qname == action_type_name or qname.endswith(f"::{action_type_name}"):
            for field in dtype.fields:
                if field.kind == FieldKind.Input:
                    result.add(field.name)
            break
    return result


def get_flow_output_fields(
    ctx: 'LoweringContext',
    action_type_name: str,
) -> Set[str]:
    """Return the set of flow-object OUTPUT field names for an action type."""
    from zuspec.ir.core.fields import FieldKind
    if ctx.ir_ctx is None:
        return set()
    result: Set[str] = set()
    for qname, dtype in ctx.ir_ctx.type_map.items():
        if qname == action_type_name or qname.endswith(f"::{action_type_name}"):
            for field in dtype.fields:
                if field.kind == FieldKind.Output:
                    result.add(field.name)
            break
    return result
