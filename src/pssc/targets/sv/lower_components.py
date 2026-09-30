"""Lower PSS components to SV IR nodes.

Each ``DataTypeComponent`` becomes an ``SVClass extends zsp_component``
with a constructor, sub-component fields, resource pool fields, and
an import interface reference when the component declares import functions.
"""
from __future__ import annotations

from typing import List

import zuspec.ir.core as ir
from zuspec.be.sv.ir.sv import (
    SVArg,
    SVClass,
    SVClassField,
    SVFunctionDecl,
)

from .context import LoweringContext
from .lower_types import _resolve_super


def lower_component(ctx: LoweringContext, dtype: ir.DataTypeComponent) -> SVClass:
    """Lower a PSS component to an SVClass extending zsp_component.

    Resource pools (``Pool`` nodes on the component) are emitted as
    ``zsp_resource_pool #(ElemType, capacity) pool_name;`` fields and
    constructed in the ``new`` function.
    """
    sv_name = ctx.mangle_name(dtype.name) if dtype.name else "unnamed_comp"

    extends = "zsp_component"
    if dtype.super:
        if isinstance(dtype.super, ir.DataTypeRef):
            extends = _resolve_super(ctx, dtype.super.ref_name)
        elif hasattr(dtype.super, 'name') and dtype.super.name:
            extends = _resolve_super(ctx, dtype.super.name)

    # Fields
    fields: List[SVClassField] = []
    ctor_body: List[str] = ["super.new(name, parent);"]

    # Import interface field (if the component has import functions)
    import_funcs = [f for f in dtype.functions if getattr(f, 'is_import', False)]
    if import_funcs:
        import_if_name = f"{sv_name}_import_if"
        fields.append(SVClassField(name="import_if", dtype=import_if_name))

    for f in dtype.fields:
        sv_dtype = ctx.pss_type_to_sv_type_str(f.datatype)
        fields.append(SVClassField(name=f.name, dtype=sv_dtype))

        # Auto-construct sub-components and resource pools
        if isinstance(f.datatype, ir.DataTypeComponent):
            ctor_body.append(f'{f.name} = new("{f.name}", this);')
        elif isinstance(f.datatype, ir.DataTypeRef):
            ref_name = f.datatype.ref_name
            resolved = ctx.ir_ctx.type_map.get(ref_name) if ctx.ir_ctx else None
            if resolved and isinstance(resolved, ir.DataTypeComponent):
                ctor_body.append(f'{f.name} = new("{f.name}", this);')

    # Resource pool fields from Pool nodes
    # Only emit zsp_resource_pool for resource-type pools.
    # Buffer, stream, and state pools are binding declarations for the PSS
    # solver; in SV they are managed as local variables in the activity task
    # and do not need a pool object on the component.
    for pool in getattr(dtype, 'pools', []):
        # Determine the flow kind of the element type
        elem_type = ctx.ir_ctx.type_map.get(pool.element_type_name) if ctx.ir_ctx else None
        flow_kind = getattr(elem_type, 'flow_kind', None)
        if flow_kind is not ir.FlowKind.RESOURCE:
            # Skip pools with a known non-resource flow kind.
            # When flow_kind is None (unknown type or no ir_ctx), emit conservatively.
            if flow_kind in (ir.FlowKind.BUFFER, ir.FlowKind.STREAM, ir.FlowKind.STATE):
                continue  # managed as local variables in activity lowering
        elem_sv = ctx.mangle_name(pool.element_type_name) if pool.element_type_name else "zsp_resource"
        capacity = pool.capacity if pool.capacity and pool.capacity > 0 else 1
        pool_type = f"zsp_resource_pool #({elem_sv})"
        fields.append(SVClassField(name=pool.name, dtype=pool_type))
        ctor_body.append(f'{pool.name} = new({capacity});')

    # Constructor
    ctor = SVFunctionDecl(
        name="new",
        args=[
            SVArg(name="name", dtype="string"),
            SVArg(name="parent", dtype="zsp_component"),
        ],
        return_type="",
        body_lines=ctor_body,
    )

    return SVClass(
        name=sv_name,
        extends_name=extends,
        fields=fields,
        functions=[ctor],
    )
