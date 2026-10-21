"""Every file-scope name the generated C declares, and the check that no two
are the same.

C has ONE namespace for typedefs, functions and objects, and the backend fills
it from several rules at once: a component's type is its PSS name, its
operations and sub-component accessors are `<prefix>_<name>`, its lifecycle is
`<prefix>_init/_create/_destroy`, its register accessors are
`<prefix>_<path>_<reg>_<kind>`, and an import keeps its PSS name. Each rule is
collision-free on its own; together they are not. A sub-component named
`create` has the accessor `wb_dma_create` -- the lifecycle function's name --
and C reports that as a conflicting declaration somewhere in a header the user
did not write.

So the names are gathered from the same functions that spell them, and two
sources of one name are a `CompileError` naming both PSS sources, raised
before any file is written. Struct tags and macros are not gathered: a tag has
a namespace of its own, and the macros are counts in upper case.
"""
from __future__ import annotations

from typing import Dict, List, Tuple

__all__ = ["HANDLE", "BUS", "RESERVED", "declared_names", "check"]

#: The component handle every generated function takes, and the constructor's
#: bus parameter. Both are only ever parameters and locals, where a leading
#: `_` is not reserved (C11 7.1.3 reserves it at file scope; C++ in the global
#: namespace) -- `__self` would be. A PSS identifier may still be spelled this
#: way, so `mangle` renames a user's `_self` as it renames a C keyword: the
#: parameter `s` a model wrote once collided with a handle called `s`.
HANDLE = "_self"
BUS = "_bus"
RESERVED = frozenset({HANDLE, BUS})


def _short(dtype) -> str:
    return (getattr(dtype, "name", None) or "?").split("::")[-1]


def _qual(dtype) -> str:
    """The QUALIFIED name: `p::x_s` and `q::x_s` are two types that both
    become `x_s`, and a description by short name would call them one."""
    return getattr(dtype, "name", None) or "?"


def declared_names(backend, model, s) -> List[Tuple[str, str]]:
    """``(C name, what declares it)`` for everything the backend's header and
    source declare at file scope. ``backend`` is a prepared
    `COpModelBackend`: its `prefixes`, `style`, `comps` and `accs` are the
    ones the emitters use."""
    from ..sv.lower_api_types import collect_api_types
    from ..reg_layout import reg_maps_for
    from .lower_api_types import c_member_type
    from ..progseq_model import sub_components
    from .lower_progseq import (CEnums, DEFAULT_API, OpCtx, _ctor,
                                 _operations, _solve_operations, mangle,
                                 regular_nodes)
    from .lower_reg_model import accessor_kinds, c_struct_name, map_type_name

    style, prefixes = backend.style, backend.prefixes
    mem = style.mem_access()
    api = backend if hasattr(backend, "signature") else DEFAULT_API
    out: List[Tuple[str, str]] = []

    # -- types --------------------------------------------------------------
    for comp in backend.comps:
        out.append((style.type_name(prefixes.type_name(comp)),
                    f"component type '{_qual(comp)}'"))
    enums, structs = collect_api_types(
        backend.comps, model.value_structs, model.ctor_names,
        type_map=getattr(model.ctx, "type_map", None) or {})
    spell = CEnums(style)
    for e in enums:
        out.append((c_member_type(e), f"enum '{_qual(e)}'"))
        for item in e.items:
            out.append((spell.item(e, item),
                        f"item '{item}' of enum '{_qual(e)}'"))
    for st in structs:
        if _short(st) != "addr_handle_t":
            out.append((c_struct_name(st), f"struct '{_qual(st)}'"))
    for st in backend.value_structs:
        out.append((c_struct_name(st), f"register value '{_qual(st)}'"))
    if s.reg_map:
        for rm in reg_maps_for(model.reg_groups):
            out.append((map_type_name(rm.dtype, style),
                        f"register group '{_qual(rm.dtype)}'"))

    # -- functions ----------------------------------------------------------
    for node in regular_nodes(model):
        comp = node.dtype
        p, who = prefixes[comp], _short(comp)
        out.append((style.symbol(p, "init"), f"the constructor of '{who}'"))
        # `_init`'s two file-local halves (`lower_progseq._lifecycle_impl`).
        out.append((style.symbol(p, "pss_construct"),
                    f"the construction of '{who}'"))
        if _ctor(comp, model.ctor_names) is not None:
            out.append((style.symbol(p, "pss_ctor"),
                        f"the constructor body of '{who}'"))
        if model.is_root(comp) and s.lifecycle == "malloc":
            out.append((style.symbol(p, "create"), f"the factory of '{who}'"))
            out.append((style.symbol(p, "destroy"),
                        f"the destructor of '{who}'"))
        # Named as the backend's `signature` names them, which a plugin may
        # change: the name the clash check sees is the one emitted.
        ctx = OpCtx(comp=comp, prefix=p, qual="", emitter_cls=None,
                    be_kw={"style": style}, type_name=prefixes.type_name(comp),
                    api=api)
        for fn in _operations(comp, model.ctor_names):
            out.append((api.signature(fn, ctx)[1],
                        f"operation '{who}.{fn.name}'"))
        for fn in _solve_operations(comp, model.ctor_names):
            out.append((api.signature(fn, ctx)[1],
                        f"solve function '{who}.{fn.name}'"))
        for sub in sub_components(comp):
            out.append((style.symbol(p, mangle(sub.name)),
                        f"the accessor of sub-component '{who}.{sub.name}'"))
    if not s.reg_map:
        addr_only = style.reg_accessor_form() == "macro"
        for acc in backend.accs.values():
            for kind in accessor_kinds(acc, addr_only):
                out.append((mem.accessor(acc.base, kind),
                            f"a register accessor of '{acc.base}'"))
    for name in sorted(backend.imports or ()):
        out.append((mangle(name), f"import '{name}'"))
    if s.yield_mode == "import":
        out.append(("yield_", "the platform's yield (`--yield import`)"))
    return out


def check(backend, model, s) -> None:
    """Refuse two declarations of one C name (see the module docstring)."""
    from ...driver import CompileError

    seen: Dict[str, str] = {}
    bad: List[str] = []
    for name, who in declared_names(backend, model, s):
        if name in seen and seen[name] != who:
            bad.append(f"'{name}' is both {seen[name]} and {who}")
        seen.setdefault(name, who)
    if bad:
        raise CompileError(
            f"{len(bad)} name(s) would be declared twice in the generated C. "
            f"Rename one in the model, or give its component another prefix "
            f"with --prefix / --prefix-map", bad)
