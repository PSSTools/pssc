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
    SVTaskDecl,
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
    children: List[str] = []
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
            children.append(f.name)
        elif isinstance(f.datatype, ir.DataTypeRef):
            ref_name = f.datatype.ref_name
            resolved = ctx.ir_ctx.type_map.get(ref_name) if ctx.ir_ctx else None
            if resolved and isinstance(resolved, ir.DataTypeComponent):
                ctor_body.append(f'{f.name} = new("{f.name}", this);')
                children.append(f.name)

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

    ctx.component_children[sv_name] = children

    functions = [ctor]
    tasks: List[SVTaskDecl] = []
    for func in dtype.functions:
        if getattr(func, 'is_import', False) or not func.body:
            continue
        _lower_comp_function(ctx, func, functions, tasks)

    return SVClass(
        name=sv_name,
        extends_name=extends,
        fields=fields,
        functions=functions,
        tasks=tasks,
    )


def _lower_comp_function(ctx: LoweringContext, func, functions, tasks) -> None:
    """Lower a component function with a body to a method of its class.

    A ``target`` function may consume time (it typically calls a target
    import), so it becomes a virtual task; its return value, if any, comes
    back through a leading ``output`` argument. Any other function becomes a
    virtual function. Import calls in the body go through the component's
    own ``import_if`` handle (wired from the root by the OO projection).
    """
    from .lower_stmts import lower_stmts

    args: List[SVArg] = []
    for a in (func.args.args if getattr(func, 'args', None) else []):
        dtype = (ctx.pss_type_to_sv_type_str(a.annotation)
                 if a.annotation is not None else "int")
        args.append(SVArg(name=a.arg, dtype=dtype))
    ret_type = (ctx.pss_type_to_sv_type_str(func.returns)
                if func.returns is not None else None)
    is_task = func.name in ctx.task_funcs()

    saved = (ctx.import_if_ref, ctx.ret_out_name)
    ctx.import_if_ref = "import_if"
    ret_out = None
    if is_task and ret_type is not None:
        ret_out = "ret"
        while any(a.name == ret_out for a in args):
            ret_out += "_"
    ctx.ret_out_name = ret_out
    try:
        body = lower_stmts(ctx, func.body)
    finally:
        ctx.import_if_ref, ctx.ret_out_name = saved

    if is_task:
        if ret_out is not None:
            args = [SVArg(name=ret_out, dtype=ret_type, direction="output")] + args
        tasks.append(SVTaskDecl(name=func.name, args=args, is_virtual=True,
                                body_lines=body))
    else:
        functions.append(SVFunctionDecl(
            name=func.name, args=args, return_type=ret_type or "void",
            is_virtual=True, body_lines=body))
