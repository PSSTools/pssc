"""Top-level entry point: IR Context -> List[SV IR nodes].

Orchestrates all lowering passes in dependency order.
"""
from __future__ import annotations

from typing import Any, List, Tuple

import zuspec.ir.core as ir
from zuspec.be.sv.ir.sv import SVForwardDecl, SVRawItem

from ...ast2ir import AstToIrContext
from .context import LoweringContext
from .lower_types import lower_enum, lower_struct
from .lower_components import lower_component
from .lower_actions import lower_action
from .lower_imports import lower_import_interface


# PSS stdlib packages whose types are provided by zsp_rt_pkg.sv and must
# not be emitted into user-generated SV.
_STDLIB_PREFIXES = (
    "executor_pkg::",
    "addr_reg_pkg::",
    "sync_pkg::",
    "std_pkg::",
)

# Bare stdlib names that appear without a package prefix in the type_map
_STDLIB_BARE = frozenset([
    "array", "list", "set", "map",
    "bool", "int", "string",
    "endianness_e", "message_verbosity_e", "reg_access",
    "channel_c", "actor_c",
    "executor_trait_s", "empty_executor_trait_s",
    "executor_base_c", "executor_c", "executor_group_c", "executor_group_default_c",
    "executor_claim_s",
    "addr_space_base_c", "addr_space_group_c",
    "addr_trait_s", "empty_addr_trait_s", "addr_handle_t",
    "addr_region_base_s", "addr_region_s", "transparent_addr_region_s",
    "addr_claim_base_s", "addr_claim_s", "transparent_addr_claim_s",
    "sizeof_s", "sized_addr_handle_s",
    "contiguous_addr_space_c", "transparent_addr_space_c",
    "reg_c", "reg_group_c",
    "packed_s",
])


def _is_stdlib(qname: str) -> bool:
    """Return True if *qname* belongs to a PSS stdlib package."""
    if any(qname.startswith(pfx) for pfx in _STDLIB_PREFIXES):
        return True
    # Bare aliases for stdlib types start with bit[ or int[
    if qname.startswith(("bit[", "int[")):
        return True
    return qname in _STDLIB_BARE


def _split_reg_model(ctx, components, structs) -> str:
    """Remove the register model from ``components``/``structs`` (in place)
    and return its SV text (value structs + group classes), or ``''``."""
    from ..progseq_model import is_reg_group, collect_value_structs, _dt_name
    from .lower_reg_model import lower_register_model
    groups = [c for _, c in components if is_reg_group(c)]
    if not groups:
        return ""
    vstructs = {id(s) for s in collect_value_structs(groups)}
    gids = {id(g) for g in groups}
    components[:] = [(q, c) for q, c in components
                     if id(c) not in gids and _dt_name(c) != "DataTypeRegister"]
    structs[:] = [(q, s) for q, s in structs if id(s) not in vstructs]
    body, _, _ = lower_register_model(groups)
    ctx.uses_reg_model = True
    return body


def pss_to_sv(ir_ctx: AstToIrContext) -> List[Any]:
    """Lower a Zuspec IR context to a list of SV IR nodes.

    Thin wrapper around :func:`pss_to_sv_with_ctx` that discards the lowering
    context. Prefer :func:`pss_to_sv_with_ctx` when the caller needs the context
    (e.g. for name mangling during export-API / top-module generation).
    """
    return pss_to_sv_with_ctx(ir_ctx)[0]


def pss_to_sv_with_ctx(ir_ctx: AstToIrContext) -> Tuple[List[Any], LoweringContext]:
    """Lower a Zuspec IR context to SV IR nodes, returning the context too.

    The returned list is in dependency order:
    1. Forward declarations
    2. Enums
    3. Structs (plain data types)
    4. Import interface classes
    5. Components
    6. Actions

    Uses the qualified name from the type_map key (e.g. ``top_c::hello``)
    for name mangling rather than the dtype's own ``name`` field, which
    may be unqualified (e.g. ``hello``).

    Args:
        ir_ctx: The translation context from ``AstToIrTranslator.translate()``.

    Returns:
        Tuple of (ordered list of SV IR nodes ready for ``SVEmitter``, the
        :class:`LoweringContext` used during lowering). The context exposes
        ``mangle_name()`` / ``sv_name_map`` for downstream generation.
    """
    ctx = LoweringContext(ir_ctx=ir_ctx)
    result: List[Any] = []

    # Classify IR types, keeping the qualified name from the type_map key
    # --- Pre-pass: run activity analysis for every action that has an
    #     activity, so downstream lowering can reuse the results. ---
    try:
        from .analyze_activity import analyze_activity as _analyze_activity
        import zuspec.ir.core as _ir
        for _qname, _dtype in ir_ctx.type_map.items():
            if not isinstance(_dtype, _ir.DataTypeClass):
                continue
            _act_ir = getattr(_dtype, 'activity_ir', None)
            if _act_ir is None:
                continue
            try:
                _plan = _analyze_activity(ctx, _act_ir)
                ctx.activity_plans[_qname] = _plan
            except Exception:
                pass  # analysis failures are non-fatal
    except ImportError:
        pass  # analyze_activity module optional

    enums: List[Tuple[str, ir.DataTypeEnum]] = []
    structs: List[Tuple[str, ir.DataTypeStruct]] = []
    components: List[Tuple[str, ir.DataTypeComponent]] = []
    actions: List[Tuple[str, ir.DataTypeClass]] = []

    for name, dtype in ir_ctx.type_map.items():
        # Skip duplicates (same type registered under multiple names)
        if id(dtype) in ctx.emitted:
            continue
        ctx.emitted.add(id(dtype))

        # Skip PSS stdlib types: provided by zsp_rt_pkg.sv, not user code
        if _is_stdlib(name):
            continue

        if isinstance(dtype, ir.DataTypeEnum):
            enums.append((name, dtype))
        elif isinstance(dtype, ir.DataTypeComponent):
            components.append((name, dtype))
        elif isinstance(dtype, ir.DataTypeClass):
            actions.append((name, dtype))
        elif isinstance(dtype, ir.DataTypeStruct):
            structs.append((name, dtype))

    # Register model: reg-group components, register (sub)types and their
    # value structs are lowered as one unit onto pssc_reg_pkg (reg_c #(...)),
    # as for sv-progseq, rather than as plain components/structs.
    reg_body = _split_reg_model(ctx, components, structs)

    # 1. Enums
    for qname, e in enums:
        result.append(lower_enum(ctx, e))

    # 2. Structs (forward decls then definitions)
    for qname, s in structs:
        sv_name = ctx.mangle_name(qname)
        result.append(SVForwardDecl(class_name=sv_name))
    for qname, s in structs:
        # Temporarily set qualified name for lowering
        orig_name = s.name
        s.name = qname
        result.append(lower_struct(ctx, s))
        s.name = orig_name

    if reg_body:
        result.append(SVRawItem(lines=reg_body.splitlines()))

    # 3. Import interfaces
    for qname, comp in components:
        orig_name = comp.name
        comp.name = qname
        imp_cls = lower_import_interface(ctx, comp)
        if imp_cls is not None:
            result.append(imp_cls)
        comp.name = orig_name

    # 4. Components (forward decls then definitions)
    for qname, comp in components:
        sv_name = ctx.mangle_name(qname)
        result.append(SVForwardDecl(class_name=sv_name))
    for qname, comp in components:
        orig_name = comp.name
        comp.name = qname
        result.append(lower_component(ctx, comp))
        comp.name = orig_name

    # 5. Actions (forward decls then definitions)
    for qname, act in actions:
        sv_name = ctx.mangle_name(qname)
        result.append(SVForwardDecl(class_name=sv_name))
    for qname, act in actions:
        comp_name = ir_ctx.parent_comp_names.get(qname)
        if not comp_name:
            # Fallback: try the unqualified name
            comp_name = ir_ctx.parent_comp_names.get(act.name)
        orig_name = act.name
        act.name = qname
        result.append(lower_action(ctx, act, comp_type_name=comp_name))
        act.name = orig_name

    return result, ctx
