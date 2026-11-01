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

import re
from typing import Any, Dict, Iterable, List, Tuple

__all__ = ["HANDLE", "BUS", "RESERVED", "C_KEYWORDS", "check_identifier",
           "declared_names", "occupied_names", "parameter_clashes",
           "check"]

#: The component handle every generated function takes, and the constructor's
#: bus parameter. Both are only ever parameters and locals, where a leading
#: `_` is not reserved (C11 7.1.3 reserves it at file scope; C++ in the global
#: namespace) -- `__self` would be. A PSS identifier may still be spelled this
#: way, so `mangle` renames a user's `_self` as it renames a C keyword: the
#: parameter `s` a model wrote once collided with a handle called `s`.
HANDLE = "_self"
BUS = "_bus"
RESERVED = frozenset({HANDLE, BUS})

#: The C keywords: C11's, and the ones C23 adds without a leading `_`. A name
#: spelled as one is renamed by `lower_progseq.mangle` and refused by
#: `check_identifier`; this is the one list of them.
C_KEYWORDS = frozenset({
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if", "inline",
    "int", "long", "register", "restrict", "return", "short", "signed", "sizeof",
    "static", "struct", "switch", "typedef", "union", "unsigned", "void",
    "volatile", "while", "_Alignas", "_Alignof", "_Atomic", "_Bool",
    "_Complex", "_Generic", "_Imaginary", "_Noreturn", "_Static_assert",
    "_Thread_local",
    # C23
    "alignas", "alignof", "bool", "constexpr", "false", "nullptr",
    "static_assert", "thread_local", "true", "typeof", "typeof_unqual",
})

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


def check_identifier(name: str, node: Any = None,
                     reserved: Iterable[str] = ()) -> str:
    """``name``, if C lets the generated code declare it; else a `Refusal`
    located at ``node``. Refused: a name that is not an identifier, a C
    keyword (`C_KEYWORDS`), a name C reserves everywhere (a leading `__`, or
    `_` and a capital: C11 7.1.3), the generated code's own `RESERVED`
    names, and ``reserved`` (a style's or a target's own). A leading `_`
    and a lower-case letter passes: it is reserved only at file scope, so a
    caller naming a file-scope symbol checks that itself."""
    from ...diag import Refusal

    why = None
    if not isinstance(name, str) or not _IDENT.match(name):
        why = "is not a C identifier"
    elif name in C_KEYWORDS:
        why = "is a C keyword"
    elif name.startswith("__") or re.match(r"_[A-Z]", name):
        why = "is reserved by C (a leading '__', or '_' and a capital)"
    elif name in RESERVED:
        why = "is a name the generated code takes for itself"
    elif name in set(reserved):
        why = "is reserved by the target"
    if why is not None:
        raise Refusal(f"'{name}' {why}; it cannot name a C declaration",
                      *(() if node is None else (node,)))
    return name


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
    from .lower_progseq import (CEnums, _operations, _solve_operations,
                                 mangle)
    from .lower_reg_model import accessor_kinds, c_struct_name, map_type_name

    style, prefixes = backend.style, backend.prefixes
    mem = style.mem_access()
    api = _api(backend)
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
        if style.is_extern_type(e):
            continue                     # `occupied_names`
        out.append((c_member_type(e), f"enum '{_qual(e)}'"))
        for item in e.items:
            out.append((spell.item(e, item),
                        f"item '{item}' of enum '{_qual(e)}'"))
    for st in structs:
        if _short(st) != "addr_handle_t" and not style.is_extern_type(st):
            out.append((c_struct_name(st), f"struct '{_qual(st)}'"))
    for st in backend.value_structs:
        out.append((c_struct_name(st), f"register value '{_qual(st)}'"))
    if s.reg_map:
        for rm in reg_maps_for(model.reg_groups):
            out.append((map_type_name(rm.dtype, style),
                        f"register group '{_qual(rm.dtype)}'"))

    # -- functions ----------------------------------------------------------
    for node, ctx in _op_ctxs(backend, model):
        comp = node.dtype
        p, who = ctx.prefix, _short(comp)
        decls = getattr(backend, "emit_lifecycle_decls", None)
        if decls is None or decls(node, ctx):
            out += _lifecycle_names(model, s, style, comp, p, who)
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
    if s.yield_mode == "import" or s.chan_wait == "poll":
        out.append(("yield_", "the platform's yield (`--yield import` or "
                              "`--chan-wait poll`)"))
    return out


def _api(backend):
    from .lower_progseq import DEFAULT_API
    return backend if hasattr(backend, "signature") else DEFAULT_API


def _op_ctxs(backend, model):
    """``(node, OpCtx)`` for each component the backend emits operations
    for. Operations are named as the backend's `signature` names them, which
    a plugin may change: the name the clash check sees is the one emitted."""
    from .lower_progseq import OpCtx, regular_nodes
    style, prefixes, api = backend.style, backend.prefixes, _api(backend)
    for node in regular_nodes(model):
        comp = node.dtype
        yield node, OpCtx(comp=comp, prefix=prefixes[comp], qual="",
                          emitter_cls=None, be_kw={"style": style},
                          type_name=prefixes.type_name(comp), api=api)


#: What `declared_names`/`occupied_names` call a type or an enum item: a
#: parameter spelled as one hides it from the body.
_TYPE_KINDS = ("component type", "enum ", "item ", "struct ",
               "register value", "register group", "external ")


def _body_locals(fn) -> Dict[str, Any]:
    """The locals and loop variables ``fn``'s body declares, as `mangle`
    spells them, each with the node that declares it."""
    from ..ir_walk import walk
    from .lower_progseq import mangle
    out: Dict[str, Any] = {}
    for n in walk(getattr(fn, "body", None) or []):
        if type(n).__name__ not in ("StmtAnnAssign", "StmtFor",
                                    "StmtForeach"):
            continue
        for f in ("target", "index_var"):
            nm = getattr(getattr(n, f, None), "name", None)
            if isinstance(nm, str):
                out.setdefault(mangle(nm), n)
    return out


def parameter_clashes(backend, model, s) -> List[str]:
    """Each parameter of an operation's C signature (as the backend's
    `signature` names it) that C would refuse or that hides a name the
    body needs: one `check_identifier` refuses, one named twice, one a
    local of the body also declares, one spelled as a file-scope type or
    enum item."""
    from ...diag import Refusal
    from .lower_progseq import _operations, _solve_operations

    types = {n: who for n, who in (declared_names(backend, model, s)
                                   + occupied_names(backend, model, s))
             if who.startswith(_TYPE_KINDS)}
    api, bad = _api(backend), []
    for node, ctx in _op_ctxs(backend, model):
        comp = node.dtype
        for fn in (list(_operations(comp, model.ctor_names))
                   + list(_solve_operations(comp, model.ctor_names))):
            what = f"operation '{_short(comp)}.{fn.name}'"
            names = [p for _, p in api.signature(fn, ctx)[2]]
            local = _body_locals(fn)
            for i, p in enumerate(names):
                if p in names[:i]:
                    bad.append(f"{what} has two parameters named '{p}'")
                    continue
                if p not in RESERVED:
                    try:
                        check_identifier(p)
                    except Refusal as e:
                        bad.append(f"parameter of {what}: {e}")
                        continue
                if p in local:
                    bad.append(f"parameter '{p}' of {what} is also a "
                               f"local of its body")
                if p in types:
                    bad.append(f"parameter '{p}' of {what} hides "
                               f"{types[p]}")
    return bad


def _lifecycle_names(model, s, style, comp, p, who) -> List[Tuple[str, str]]:
    """A component's lifecycle functions, unless its backend emits none
    (`emit_lifecycle_decls` returns `[]`)."""
    from .lower_progseq import _ctor
    out = [(style.symbol(p, "init"), f"the constructor of '{who}'")]
    # `_init`'s two file-local halves (`lower_progseq._lifecycle_impl`).
    out.append((style.symbol(p, "pss_construct"),
                f"the construction of '{who}'"))
    if _ctor(comp, model.ctor_names) is not None:
        out.append((style.symbol(p, "pss_ctor"),
                    f"the constructor body of '{who}'"))
    if model.is_root(comp) and s.lifecycle == "malloc":
        out.append((style.symbol(p, "create"), f"the factory of '{who}'"))
        out.append((style.symbol(p, "destroy"), f"the destructor of '{who}'"))
    return out


def occupied_names(backend, model, s) -> List[Tuple[str, str]]:
    """``(C name, what it is)`` for the file-scope names the generated code
    USES but does not declare: an external type's name and its enum items
    (`StylePolicy.is_extern_type`), which the platform's header declares. A
    generated name equal to one is as much a clash as two generated ones."""
    from ..sv.lower_api_types import collect_api_types
    from .lower_api_types import c_member_type
    from .lower_progseq import CEnums
    from .lower_reg_model import c_struct_name

    style = backend.style
    enums, structs = collect_api_types(
        backend.comps, model.value_structs, model.ctor_names,
        type_map=getattr(model.ctx, "type_map", None) or {})
    spell = CEnums(style)
    out: List[Tuple[str, str]] = []
    for e in enums:
        if style.is_extern_type(e):
            out.append((c_member_type(e), f"external enum '{_qual(e)}'"))
            out += [(spell.item(e, item),
                     f"item '{item}' of external enum '{_qual(e)}'")
                    for item in e.items]
    for st in structs:
        if style.is_extern_type(st):
            out.append((c_struct_name(st), f"external struct '{_qual(st)}'"))
    return out


def check(backend, model, s) -> None:
    """Refuse two declarations of one C name (see the module docstring),
    then any `parameter_clashes`."""
    from ...driver import CompileError

    seen: Dict[str, str] = {}
    bad: List[str] = []
    for name, who in (declared_names(backend, model, s)
                      + occupied_names(backend, model, s)):
        if name in seen and seen[name] != who:
            bad.append(f"'{name}' is both {seen[name]} and {who}")
        seen.setdefault(name, who)
    if bad:
        raise CompileError(
            f"{len(bad)} name(s) would be declared twice in the generated C. "
            f"Rename one in the model, or give its component another prefix "
            f"with --prefix / --prefix-map", bad)
    bad = parameter_clashes(backend, model, s)
    if bad:
        raise CompileError(
            f"{len(bad)} parameter name(s) of the generated C would not "
            f"compile or would hide a name. Rename the parameter (in the "
            f"model, or where the target's `signature` names it)", bad)
