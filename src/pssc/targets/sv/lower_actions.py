"""Lower PSS actions to SV IR nodes.

Each ``DataTypeClass`` (action) becomes an ``SVClass extends zsp_action``
with rand fields, constraints, body task, and component reference.
"""
from __future__ import annotations

from typing import List, Optional

import zuspec.ir.core as ir
from zuspec.be.sv.ir.sv import (
    SVArg,
    SVClass,
    SVClassField,
    SVConstraintBlock,
    SVFunctionDecl,
    SVTaskDecl,
)

from .context import LoweringContext
from .lower_constraints import lower_constraint_func
from .lower_stmts import lower_stmt
from .lower_coverage import lower_action_covergroups, lower_covergroup_sample_call


def _resolve_super(ctx: LoweringContext, ref_name: str, comp_name: Optional[str] = None) -> str:
    """Resolve an unqualified super-type reference to its canonical SV class name.

    The IR type_map has alias entries (short name → same object as long qualified
    name).  The class was emitted under exactly one key; that key's mangled name
    is already recorded in ``ctx.sv_name_map``.  We find the target type object,
    then look up the mangled name that was actually used for it.

    Resolution order:
      1. ``comp_name::ref_name``  (action within the same component)
      2. Exact key in type_map (qualified or package-level alias)
      3. Suffix scan for ``*::ref_name``
    Then cross-reference sv_name_map to return the canonical mangled name.
    """
    if ctx.ir_ctx is None:
        return ctx.mangle_name(ref_name)

    type_map = ctx.ir_ctx.type_map

    # Find the target type object
    target = None
    if comp_name:
        target = type_map.get(f"{comp_name}::{ref_name}")
    if target is None:
        target = type_map.get(ref_name)
    if target is None:
        for key, dtype in type_map.items():
            if key.endswith(f"::{ref_name}"):
                target = dtype
                break

    if target is None:
        return ctx.mangle_name(ref_name)

    # Cross-reference sv_name_map: find the mangled name already assigned
    # to this exact type object (handles alias keys mapping to the same obj).
    target_id = id(target)
    for key, mangled in ctx.sv_name_map.items():
        if id(type_map.get(key)) == target_id:
            return mangled

    # Not yet mangled: pick the most-qualified key and mangle it
    best = ref_name
    for key, dtype in type_map.items():
        if id(dtype) == target_id:
            if key.count("::") > best.count("::") or (
                key.count("::") == best.count("::") and len(key) > len(best)
            ):
                best = key
    return ctx.mangle_name(best)


def _resource_instance_id_field(field_name: str) -> SVClassField:
    """Return a rand ``int unsigned`` instance_id field for a resource claim."""
    return SVClassField(
        name=f"{field_name}_instance_id",
        dtype="int unsigned",
        is_rand=True,
    )


def _is_action_class_field(ctx: LoweringContext, field: ir.Field) -> bool:
    """Return True if *field* is a named action-handle field (plain Field whose datatype
    resolves to a DataTypeClass / action type).  These fields need to be constructed
    in pre_solve() before randomize() is called."""
    from zuspec.ir.core.fields import FieldKind
    if field.kind != FieldKind.Field:
        return False
    dtype = field.datatype
    # Direct reference to an action class
    if isinstance(dtype, ir.DataTypeClass):
        return True
    # Indirect reference via DataTypeRef
    if isinstance(dtype, ir.DataTypeRef) and ctx.ir_ctx is not None:
        ref_name = getattr(dtype, 'ref_name', '')
        target = ctx.ir_ctx.type_map.get(ref_name)
        if target is None:
            for k, v in ctx.ir_ctx.type_map.items():
                if k.endswith(f"::{ref_name}"):
                    target = v
                    break
        return isinstance(target, ir.DataTypeClass)
    return False


def _find_pool_capacity(ctx: LoweringContext, resource_dt, comp_type_name: Optional[str]) -> int:
    """Return the pool capacity for a resource datatype in the given component context.

    Walks the parent component (and its super-components) to find a Pool whose
    element_type_name matches the resource type.  Falls back to 16 when not found.
    """
    if ctx.ir_ctx is None or comp_type_name is None:
        return 16
    # Resolve the resource type name
    elem_name = None
    if isinstance(resource_dt, ir.DataTypeStruct) and resource_dt.name:
        elem_name = resource_dt.name
    elif isinstance(resource_dt, ir.DataTypeRef):
        elem_name = getattr(resource_dt, 'ref_name', None)
    if elem_name is None:
        return 16
    # Walk type_map looking for a component that has this comp_type_name and a matching pool
    comp_dt = ctx.ir_ctx.type_map.get(comp_type_name)
    if comp_dt is None:
        # Try unqualified match
        for k, v in ctx.ir_ctx.type_map.items():
            if k == comp_type_name or k.endswith(f"::{comp_type_name}"):
                comp_dt = v
                break
    if comp_dt is None:
        return 16
    for pool in getattr(comp_dt, 'pools', []):
        # Match on element type name (may be unqualified or qualified)
        pname = pool.element_type_name or ''
        if pname == elem_name or pname.endswith(f"::{elem_name}") or elem_name.endswith(f"::{pname}"):
            return pool.capacity if pool.capacity and pool.capacity > 0 else 16
    return 16


def lower_action(
    ctx: LoweringContext,
    dtype: ir.DataTypeClass,
    comp_type_name: Optional[str] = None,
) -> SVClass:
    """Lower a PSS action to an SVClass extending zsp_action.

    Args:
        ctx: Lowering context.
        dtype: Action IR type (DataTypeClass or DataTypeAction).
        comp_type_name: Name of owning component (for comp field type).
    """
    sv_name = ctx.mangle_name(dtype.name) if dtype.name else "unnamed_action"

    extends = "zsp_action"
    if dtype.super:
        if isinstance(dtype.super, ir.DataTypeRef):
            extends = _resolve_super(ctx, dtype.super.ref_name, comp_type_name)
        elif hasattr(dtype.super, 'name') and dtype.super.name:
            extends = _resolve_super(ctx, dtype.super.name, comp_type_name)

    # Fields
    fields: List[SVClassField] = []
    # Resource instance_id rand fields (one per lock/share field)
    resource_fields: List[SVClassField] = []
    resource_id_constraints: List[str] = []

    # Component context field
    if comp_type_name:
        comp_sv_type = ctx.mangle_name(comp_type_name)
        fields.append(SVClassField(name="comp", dtype=comp_sv_type))

    for f in dtype.fields:
        sv_dtype = ctx.pss_type_to_sv_type_str(f.datatype)
        is_rand = f.rand_kind == "rand"
        is_randc = f.rand_kind == "randc"
        # Output flow-object fields (buffers, streams, states) must be declared
        # as `rand` handles in SV so VCS includes their sub-fields in the
        # randomization scope.  Without `rand`, constraints like
        # `next.established == 1` are treated as constant-variable conflicts
        # and cause CNST-CIF.  Input flow-object fields stay non-rand (the
        # injected value is fixed before randomize).
        from zuspec.ir.core.fields import FieldKind as _FOK
        if f.kind == _FOK.Output and isinstance(f.datatype, ir.DataTypeStruct):
            is_rand = True
        fields.append(SVClassField(
            name=ctx.safe_field_name(f.name),
            dtype=sv_dtype,
            is_rand=is_rand,
            is_randc=is_randc,
        ))
        # For Lock/Share fields, add a rand instance_id field
        from zuspec.ir.core.fields import FieldKind
        if f.kind in (FieldKind.Lock, FieldKind.Share):
            resource_fields.append(_resource_instance_id_field(f.name))
            # Determine pool capacity so we can constrain instance_id to [0:cap-1]
            cap = _find_pool_capacity(ctx, f.datatype, comp_type_name)
            resource_id_constraints.append((f"{f.name}_instance_id", cap))

    # Constraints
    constraints: List[SVConstraintBlock] = []
    functions: List[SVFunctionDecl] = []
    tasks: List[SVTaskDecl] = []

    # Add resource instance_id fields before constraints
    fields.extend(resource_fields)

    # Generate bounds constraints for each resource instance_id: [0:capacity-1]
    for _id_field, _cap in resource_id_constraints:
        constraints.append(SVConstraintBlock(
            name=f"_c_{_id_field}_bounds",
            exprs=[f"{_id_field} inside {{[0:{_cap - 1}]}}"],
        ))

    # Coverage: generate covergroup declaration if the action has covergroups
    cg_lines = lower_action_covergroups(ctx, dtype.name or sv_name, dtype)
    has_covergroup = bool(cg_lines)

    # Build known field name set for constraint validation
    _known_fields = {f.name for f in dtype.fields}
    _known_fields.update(f.name for f in resource_fields)

    for func in dtype.functions:
        # Constraint functions
        exprs = lower_constraint_func(ctx, func, known_field_names=list(_known_fields))
        if exprs is not None:
            constraints.append(SVConstraintBlock(name=func.name, exprs=exprs))
            continue

        # body -> virtual task; lower exec block body stmts if present
        if func.name == "body":
            body_lines: List[str] = []
            if func.body:
                for stmt in func.body:
                    body_lines.extend(lower_stmt(ctx, stmt))
            if not body_lines:
                body_lines = ["// empty body"]
            tasks.append(SVTaskDecl(
                name="body",
                is_virtual=True,
                body_lines=body_lines,
            ))

        # pre_solve / post_solve -> virtual functions; lower exec block if present
        elif func.name in ("pre_solve", "post_solve"):
            func_lines: List[str] = []
            if func.body:
                for stmt in func.body:
                    func_lines.extend(lower_stmt(ctx, stmt))
            # For pre_solve: construct any output flow-object fields so that
            # their sub-field constraints don't raise a null-pointer error.
            if func.name == "pre_solve":
                from zuspec.ir.core.fields import FieldKind
                for f in dtype.fields:
                    if f.kind == FieldKind.Output:
                        fname = ctx.safe_field_name(f.name)
                        func_lines.append(f"if ({fname} == null) {fname} = new();")
                    elif _is_action_class_field(ctx, f):
                        fname = ctx.safe_field_name(f.name)
                        func_lines.append(f"if ({fname} == null) {fname} = new();")
                        # Disable constraints so parent randomize() skips this
                        # handle's constraint graph (inputs may be null until body).
                        func_lines.append(f"{fname}.constraint_mode(0);")
            if not func_lines:
                func_lines = ["// no-op"]
            # Sample covergroup in post_solve
            if func.name == "post_solve" and has_covergroup:
                func_lines.extend(lower_covergroup_sample_call(dtype.name or sv_name))
            functions.append(SVFunctionDecl(
                name=func.name,
                return_type="void",
                is_virtual=True,
                body_lines=func_lines,
            ))

    # Generate pre_solve() to construct output flow-object fields if not
    # already generated from an exec block.  This ensures that constraints
    # referencing output flow-object sub-fields do not NPE at randomize().
    if not any(f.name == "pre_solve" for f in functions):
        from zuspec.ir.core.fields import FieldKind as _FK
        output_fields = [
            ctx.safe_field_name(f.name)
            for f in dtype.fields if f.kind == _FK.Output
        ]
        handle_fields = [
            ctx.safe_field_name(f.name)
            for f in dtype.fields if _is_action_class_field(ctx, f)
        ]
        if output_fields or handle_fields:
            ctor_lines = []
            for fname in output_fields:
                ctor_lines.append(f"if ({fname} == null) {fname} = new();")
            for fname in handle_fields:
                ctor_lines.append(f"if ({fname} == null) {fname} = new();")
                # Disable constraints so parent randomize() won't fail on null inputs
                # (flow-object inputs like 'prev' are only set later in body()).
                ctor_lines.append(f"{fname}.constraint_mode(0);")
        fields_to_construct = output_fields + handle_fields  # for empty check
        if fields_to_construct:
            pass  # ctor_lines already built above
        if fields_to_construct:
            functions.append(SVFunctionDecl(
                name="pre_solve",
                return_type="void",
                is_virtual=True,
                body_lines=ctor_lines,
            ))

    # If the action declares an activity, generate a body() task from it.
    # This covers compound actions (actions with 'activity { ... }' blocks)
    # whose body is defined by the activity IR rather than an exec block.
    _activity_ir = getattr(dtype, 'activity_ir', None)
    if _activity_ir is not None and not any(t.name == "body" for t in tasks):
        try:
            from .lower_activities import lower_activity
            from .lower_activities import lower_activity_for
            _body_lines = lower_activity_for(ctx, _activity_ir, comp_expr="comp",
                                             parent_dtype=dtype)
            if _body_lines:
                tasks.append(SVTaskDecl(
                    name="body",
                    is_virtual=True,
                    body_lines=_body_lines,
                ))
        except Exception:
            pass  # graceful degradation; leave body() as inherited no-op

    _lower_addr_claims(ctx, dtype, comp_type_name, sv_name, functions, tasks)

    return SVClass(
        name=sv_name,
        extends_name=extends,
        fields=fields,
        constraints=constraints,
        functions=functions,
        tasks=tasks,
    )


def _lower_addr_claims(ctx, dtype, comp_type_name, sv_name, functions, tasks):
    """Allocate transparent_addr_claim_s fields from the component's address
    space in post_solve() (after the solver fixed size/alignment) and free
    them at the end of body().  Uses the component's first
    transparent_addr_space_c field."""
    from zuspec.ir.core.fields import FieldKind
    def _is_claim(f):
        return ctx.pss_type_to_sv_type_str(f.datatype) == "transparent_addr_claim_s"

    def _flow_claims(kind):
        # Claim fields inside flow objects (buffer { rand claim_s c; }).
        ret = []
        for f in dtype.fields:
            if f.kind != kind or not isinstance(f.datatype, ir.DataTypeStruct):
                continue
            for sf in getattr(f.datatype, "fields", None) or []:
                if _is_claim(sf):
                    ret.append(f"{ctx.safe_field_name(f.name)}."
                               f"{ctx.safe_field_name(sf.name)}")
        return ret

    claims = [ctx.safe_field_name(f.name) for f in dtype.fields if _is_claim(f)]
    # The producer claims an output flow object's address; the consumer
    # frees it once its body completes (free of an unknown addr is a no-op).
    out_claims = _flow_claims(FieldKind.Output)
    in_claims = _flow_claims(FieldKind.Input)
    if not (claims or out_claims or in_claims):
        return
    comp_t = None
    if comp_type_name and ctx.ir_ctx:
        comp_t = ctx.ir_ctx.type_map.get(comp_type_name)
    aspace = None
    for f in getattr(comp_t, "fields", None) or []:
        if ctx.pss_type_to_sv_type_str(f.datatype) == "transparent_addr_space_c":
            aspace = f.name
            break
    if aspace is None:
        ctx.warn(f"action {sv_name}: address claims but component has no "
                 f"transparent_addr_space_c field; claims not allocated")
        return
    alloc = [f"comp.{aspace}.claim({c});" for c in claims + out_claims]
    free = [f"comp.{aspace}.free({c}.addr);" for c in claims + in_claims]
    ps = next((f for f in functions if f.name == "post_solve"), None)
    if ps is None:
        functions.append(SVFunctionDecl(
            name="post_solve", return_type="void", is_virtual=True,
            body_lines=(["super.post_solve();"] if dtype.super else []) + alloc))
    else:
        ps.body_lines[:0] = alloc
    body = next((t for t in tasks if t.name == "body"), None)
    if body is None:
        tasks.append(SVTaskDecl(name="body", is_virtual=True,
                                body_lines=(["super.body();"] if dtype.super else []) + free))
    else:
        body.body_lines.extend(free)
