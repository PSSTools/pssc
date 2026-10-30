"""Lower PSS type definitions to SV IR nodes.

Handles DataTypeStruct, DataTypeEnum, and flow-object base types
(resource, buffer, stream, state).
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

import zuspec.ir.core as ir
from zuspec.be.sv.ir.sv import (
    SVClass,
    SVClassField,
    SVConstraintBlock,
    SVFunctionDecl,
    SVTypedefEnum,
)

from .context import LoweringContext
from .lower_constraints import lower_constraint_func


def _resolve_super(ctx: LoweringContext, ref_name: str) -> str:
    """Resolve an unqualified super-type reference to its canonical SV class name.

    Mirrors the logic in lower_actions._resolve_super: find the type object,
    then return the mangled name recorded in sv_name_map for that object.
    """
    if ctx.ir_ctx is None:
        return ctx.mangle_name(ref_name)
    type_map = ctx.ir_ctx.type_map
    target = type_map.get(ref_name)
    if target is None:
        for key, dtype in type_map.items():
            if key.endswith(f"::{ref_name}"):
                target = dtype
                break
    if target is None:
        return ctx.mangle_name(ref_name)
    target_id = id(target)
    for key, mangled in ctx.sv_name_map.items():
        if id(type_map.get(key)) == target_id:
            return mangled
    best = ref_name
    for key, dtype in type_map.items():
        if id(dtype) == target_id:
            if key.count("::") > best.count("::") or (
                key.count("::") == best.count("::") and len(key) > len(best)
            ):
                best = key
    return ctx.mangle_name(best)


def lower_enum(ctx: LoweringContext, dtype: ir.DataTypeEnum) -> SVTypedefEnum:
    """Lower a PSS enum to an SVTypedefEnum."""
    sv_name = ctx.mangle_name(dtype.name) if dtype.name else "unnamed_enum"
    members: List[Tuple[str, int]] = list(dtype.items.items())
    return SVTypedefEnum(name=sv_name, members=members)


def lower_struct(ctx: LoweringContext, dtype: ir.DataTypeStruct) -> SVClass:
    """Lower a PSS struct to an SVClass.

    Struct fields map to SVClassField; constraint functions become
    SVConstraintBlock nodes.
    """
    sv_name = ctx.mangle_name(dtype.name) if dtype.name else "unnamed_struct"

    # Determine base class
    extends = None
    if dtype.super:
        if isinstance(dtype.super, ir.DataTypeRef):
            extends = _resolve_super(ctx, dtype.super.ref_name)
        elif hasattr(dtype.super, 'name') and dtype.super.name:
            extends = _resolve_super(ctx, dtype.super.name)
    # std_pkg::packed_s only fixes a bit layout; zsp_rt_pkg declares no class
    # for it, so a struct derived from it is a plain class here.
    if extends is not None and extends.split("__")[-1].startswith("packed_s"):
        extends = None

    # Fields
    fields: List[SVClassField] = []
    for f in dtype.fields:
        sv_dtype = ctx.pss_type_to_sv_type_str(f.datatype)
        is_rand = f.rand_kind == "rand"
        is_randc = f.rand_kind == "randc"
        fields.append(SVClassField(
            name=ctx.safe_field_name(f.name),
            dtype=sv_dtype,
            is_rand=is_rand,
            is_randc=is_randc,
        ))

    # Constraints
    constraints: List[SVConstraintBlock] = []
    for func in dtype.functions:
        exprs = lower_constraint_func(ctx, func)
        if exprs is not None:
            constraints.append(SVConstraintBlock(name=func.name, exprs=exprs))

    return SVClass(
        name=sv_name,
        extends_name=extends,
        fields=fields,
        constraints=constraints,
    )


def lower_resource(ctx: LoweringContext, dtype: ir.DataTypeStruct) -> SVClass:
    """Lower a PSS resource type to an SVClass extending zsp_resource."""
    cls = lower_struct(ctx, dtype)
    if cls.extends_name is None:
        cls.extends_name = "zsp_resource"
    return cls


def lower_buffer(ctx: LoweringContext, dtype: ir.DataTypeStruct) -> SVClass:
    """Lower a PSS buffer type to an SVClass extending zsp_buffer."""
    cls = lower_struct(ctx, dtype)
    if cls.extends_name is None:
        cls.extends_name = "zsp_buffer"
    return cls


def lower_stream(ctx: LoweringContext, dtype: ir.DataTypeStruct) -> SVClass:
    """Lower a PSS stream type to an SVClass extending zsp_stream."""
    cls = lower_struct(ctx, dtype)
    if cls.extends_name is None:
        cls.extends_name = "zsp_stream"
    return cls


def lower_state(ctx: LoweringContext, dtype: ir.DataTypeStruct) -> SVClass:
    """Lower a PSS state type to an SVClass extending zsp_state."""
    cls = lower_struct(ctx, dtype)
    if cls.extends_name is None:
        cls.extends_name = "zsp_state"
    return cls
