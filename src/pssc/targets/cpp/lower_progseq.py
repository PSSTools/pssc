"""Lower a PSS component TREE to the C++ programming API.

Each regular component becomes a pure-virtual export interface (``<Cls>_if``)
and a class implementing it; the model's `import target/solve function`s become
one ``<ns>_import_if``, which extends ``pssc::mem_if`` so the platform supplies
memory access and its own hooks through a single object.

WHERE THIS DIFFERS FROM THE C BACKEND, AND WHY. C has no member functions, so
its backend bakes a free accessor per register and threads an explicit handle
through every call. C++ has the register model as objects already
(``pssc::reg<T, ACC>``, 1:1 with SystemVerilog's ``reg_c #(T, ACC)``), so a PSS
register access lowers to the SAME PATH SPELLED THE SAME WAY --
``this->regs.channels[ch].CSR.write(v)`` -- and needs no per-register
generation at all. Sub-components are members rather than an inlined struct
tree, operations are methods rather than functions taking `s`, and channels are
typed (``pssc::chan1<bool>``) rather than widened to a 64-bit payload.

CONSTRUCTION IS TWO-PHASE, and it has to be. A PSS constructor binds a register
group at a handle it computes (`regs.set_handle(h)`), and a parent computes its
CHILDREN's handles in its own constructor body -- which runs after the children,
as members, already exist. So a component is constructed with the seam alone and
`initialize(...)` runs the PSS constructor body, rebinding register groups as it
goes. `create()` does both for the root, which is what a caller normally wants.
See ``pssc::reg``'s note on why it holds a pointer rather than a reference.

Design: docs/design/pss-c-cpp-progseq-gen-design.md (§4.4, §4.5).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set

import zuspec.ir.core as ir

from .. import comp_inherit as ci
from ..body_walker import (match_values, foreach_parts, assigns_local,
                           in_as_compare, match_needs_chain,
                           match_arm_test)

from ...diag import Refusal
from ..comments import LINE, append_trailing, blank_line, comment_lines, doc_block
from ..array_literal import is_literal
from ..progseq_model import (
    func_kind, FuncKind, field_is_reg_group, _dt_name, channel_fields,
    sub_components, array_base_stride, scalar_offset,
)
from ..bit_select import bit_select
from ..c.lower_progseq import (
    Prefixes, regular_nodes, post_order, c_string_literal, _array_size,
    _builtin_name, _str_const, func_kind_name, parse_prefix_map,
    c_select_read, c_select_write, select_type, CIntSemantics, _c_carrier,
    handle_from_handle, checked_index,
    _c_unbracket, _has_call, by_handle_params, _prefixed, with_defaults,
)
from ..expr_types import (CONTEXT_BINARY, CONTEXT_UNARY, LEFT_TYPED,
                          INT, PssType, RELATIONAL, is_integral)
from ..c.lower_reg_model import c_struct_name, _prim_bits, _strip_pkg

_DT_STRUCT = "DataTypeStruct"
_DT_INT = "DataTypeInt"
_DT_BOOL = "DataTypeBool"
_DT_ENUM = "DataTypeEnum"
_DT_CHANDLE = "DataTypeChandle"
_DT_ARRAY = "DataTypeArray"

_CPP_KEYWORDS = frozenset({
    "alignas", "alignof", "and", "asm", "auto", "bool", "break", "case", "catch",
    "char", "class", "const", "constexpr", "continue", "default", "delete", "do",
    "double", "else", "enum", "explicit", "export", "extern", "false", "float",
    "for", "friend", "goto", "if", "inline", "int", "long", "mutable", "namespace",
    "new", "not", "operator", "or", "private", "protected", "public", "register",
    "return", "short", "signed", "sizeof", "static", "struct", "switch", "template",
    "this", "throw", "true", "try", "typedef", "typename", "union", "unsigned",
    "using", "virtual", "void", "volatile", "while", "xor",
})

#: The seam reference every generated class holds, and the bound handle. Named
#: with a trailing underscore so they cannot collide with a PSS field name --
#: PSS identifiers do not end in one by convention, and a collision here would
#: be a redeclaration inside the generated class.
_IMP = "imp_"

#: Register methods `pssc::reg` implements, and how many arguments each takes.
#: The C++ spelling is identical to the PSS spelling, so this table exists to
#: CHECK rather than to translate: an unknown method would otherwise become a
#: call the compiler rejects with a message about a template class the reader
#: never wrote.
_REG_METHODS = {"read": 0, "write": 1, "read_val": 0, "write_val": 1,
                "write_val_masked": 2}

#: PSS memory primitives -> `pssc::mem_if` methods. Same names; listed so an
#: unrecognised one is refused rather than emitted.
_MEM_PRIMS = frozenset({"read8", "read16", "read32", "read64",
                        "write8", "write16", "write32", "write64"})

#: PSS built-ins this target claims a rendering for. Its half of the contract in
#: `targets/call_legality.py`: every name claimed there must be rendered here.
CPP_BUILTINS = frozenset({
    "message", "print", "error", "fatal", "make_handle_from_handle", "addr_value",
}) | _MEM_PRIMS


#: Names the generated code takes in a scope a model's names share: the
#: factory's platform parameter and its local.
_RESERVED = frozenset({"_imp", "_self"})


def mangle(name: str) -> str:
    return name + "_" if name in _CPP_KEYWORDS or name in _RESERVED else name


def parse_class_map(spec) -> Dict[str, str]:
    """``["wb_dma_ch_c=channel"]`` -> ``{"wb_dma_ch_c": "channel"}``.

    The C backend's `--prefix-map`, under the name it has here. Same parser:
    the option means the same thing in both languages.
    """
    return parse_prefix_map(spec)


class _ClassNames:
    """Component type -> C++ class name: the PSS type name, or the name
    `--prefix-map` gave it. A view onto C's `Prefixes`, which owns the rule
    and the collision check."""

    def __init__(self, prefixes: Prefixes):
        self._p = prefixes

    def __getitem__(self, dtype) -> str:
        return self._p.type_name(dtype)

    def __contains__(self, dtype) -> bool:
        return dtype in self._p


def class_names(model, root_class: str, overrides=None) -> _ClassNames:
    """Per-component-type class name: the PSS type name, as in C.

    Shared with the C backend because it is the same question -- what a
    component type is called, and whether two are called the same -- and one
    answer is better than two that can disagree. A class has no symbol prefix:
    its members are scoped by it, so only the class names must be distinct.
    """
    return _ClassNames(Prefixes(model, root_class, overrides,
                                language="C++", check_prefixes=False))


def cpp_type(dtype) -> str:
    """C++ type for a PSS type in an API signature or a local declaration."""
    cn = _dt_name(dtype)
    if cn == _DT_INT:
        bits = int(getattr(dtype, "bits", 32) or 32)
        signed = bool(getattr(dtype, "signed", False))
        if not signed and bits == 1:
            return "bool"
        if signed:
            return "int" if bits <= 32 else "std::int64_t"
        return f"std::uint{_prim_bits(bits)}_t"
    if cn == _DT_BOOL:
        return "bool"
    if cn == _DT_ENUM:
        from .lower_api_types import cpp_enum_name
        return cpp_enum_name(dtype)
    if cn == _DT_CHANDLE:
        # `typedef chandle addr_handle_t` -- the typedef name is not in the IR,
        # and the address handle is the only chandle this API can reach.
        return "pssc::addr_t"
    if cn == _DT_STRUCT:
        nm = _strip_pkg(dtype.name)
        # Older stdlibs declared addr_handle_t as a placeholder struct.
        if nm == "addr_handle_t":
            return "pssc::addr_t"
        return c_struct_name(dtype)
    if cn == _DT_ARRAY:
        # `std::array`, which unlike C's `T x[N]` is a value type: it can be
        # assigned, returned and default-initialised like everything else.
        n = _array_size(dtype)
        if n is None:
            raise ValueError("an array with no folded size; the generated "
                             "declaration needs a bound.")
        return f"std::array<{cpp_type(dtype.element_type)}, {n}>"
    raise ValueError(f"unsupported C++ type for {cn}")


def _array_init(dtype) -> str:
    """The initializer giving an array its elements' defaults: `{}` (each
    element value-initialised, a struct by its default member initializers)
    unless an element is an enum whose first item is not 0 (LRM 7.5). Double
    braces at each level, so no brace elision decides what a brace means."""
    def init(dt) -> Optional[str]:
        if _dt_name(dt) == _DT_ARRAY:
            sub = init(dt.element_type)
            if sub is None:
                return None
            return "{{" + ", ".join([sub] * _array_size(dt)) + "}}"
        from .lower_api_types import cpp_first_item
        return cpp_first_item(dt)
    return init(dtype) or "{}"


# --- component introspection ------------------------------------------------

# `ctor_names` comes down from the model, never from the ambient ContextVar
# (P6a.T5): which solve function is the constructor is the compile's answer,
# and an emitter that asks the process gets whichever compile set it last.

def _solve_functions(comp, ctor_names=None) -> List[object]:
    """Solve functions other than the constructor: members of the class, and
    not of its interface -- only solve context may call one (22.2.3)."""
    return [fn for fn in comp.functions
            if func_kind(fn, ctor_names) == FuncKind.EXPORT_SOLVE]


def _solve_member(fn, be, cls) -> List[str]:
    """One solve function as a member function, rendered as the constructor's
    body is. Not virtual and not on `<cls>_if`."""
    lines = [""] + doc_block(getattr(fn, "doc", None), "    ", LINE)
    lines.append(f"    // A solve function: the constructor's to call, not on "
                 f"{cls}_if.")
    lines.append(f"    {_ret(fn)} {mangle(fn.name)}({_params(fn)}) {{")
    lines += be.stmts(fn.body, 2)
    lines.append("    }")
    return lines


def _operations(comp, ctor_names=None) -> List[object]:
    return [fn for fn in comp.functions
            if func_kind(fn, ctor_names) == FuncKind.EXPORT_OP]


def _ctor(comp, ctor_names=None):
    for fn in comp.functions:
        if func_kind(fn, ctor_names) == FuncKind.CONSTRUCTOR:
            return fn
    return None


def _reg_group_fields(comp) -> Dict[str, object]:
    return {f.name: f.datatype for f in comp.fields if field_is_reg_group(f)}


def data_members(comp) -> List[object]:
    """Component fields that become plain data members.

    Register groups, sub-components and channels are all members too, but each
    is declared by its own rule below; what is left is the component's ordinary
    state (`chan`, `caps`, `num_ch`), and it MUST be declared -- a body's
    `this->caps` has nowhere to resolve to otherwise.
    """
    subs = {s.name for s in sub_components(comp)}
    chans = {f.name for f in channel_fields(comp)}
    return [f for f in getattr(comp, "fields", [])
            if f.name not in subs and f.name not in chans
            and not field_is_reg_group(f)]


def _parent_types(model, comp) -> List[object]:
    """Component types that hold ``comp`` as a sub-component.

    A list rather than one type: a component type instantiated under two
    different parents has two.
    """
    out: List[object] = []
    for node in regular_nodes(model):
        for sub in sub_components(node.dtype):
            if id(sub.dtype) == id(comp) and node.dtype not in out:
                out.append(node.dtype)
    return out


def _declared(model, comp):
    """``(base, fields, functions)`` as *comp* DECLARES them
    (`comp_inherit.declared`); its members as they are when it has no base."""
    dec = ci.declared(model.ctx, comp)
    if dec is None:
        return (None, list(getattr(comp, "fields", None) or []),
                list(getattr(comp, "functions", None) or []))
    return dec.base, list(dec.fields), list(dec.functions)


def _view(fields):
    """A component-shaped object with just *fields*, for the member walks."""
    import types
    return types.SimpleNamespace(fields=list(fields), functions=[], name="",
                                 super=None)


def _fn_named(comp, name):
    """A component's function by name, or ``None``."""
    for fn in (getattr(comp, "functions", None) or []):
        if fn.name == name:
            return fn
    return None


def _sub_member(sub) -> str:
    """The member name of a sub-component.

    Trailing underscore because the PLAIN name belongs to the accessor: a
    caller writes `dma->ch(3)`, which reads as the model does, and a class
    cannot have both a member and a member function called `ch`.
    """
    return mangle(sub.name) + "_"


# --- body translation -------------------------------------------------------

_BINOP = {
    "Add": "+", "Sub": "-", "Minus": "-", "Mult": "*", "Mul": "*",
    "Div": "/", "Mod": "%", "Eq": "==", "NotEq": "!=", "Ne": "!=",
    "Lt": "<", "LtE": "<=", "Le": "<=", "Gt": ">", "GtE": ">=", "Ge": ">=",
    "And": "&&", "Or": "||", "BitAnd": "&", "BitOr": "|", "BitXor": "^",
    "LShift": "<<", "Shl": "<<", "RShift": ">>", "Shr": ">>",
}

_UNOP = {
    "Not": "!", "LogNot": "!",
    "Invert": "~", "BitNot": "~", "Neg": "-", "USub": "-", "Minus": "-",
    "UAdd": "+", "Plus": "+",
}


class _BodyEmitter(CIntSemantics):
    #: The seam's integer power (`pssc_env.hpp`).
    POW_FN = "pssc::ipow"

    #: The node kinds `expr` and `_stmt_lines` render: what the gate's
    #: pre-screen (`body_kinds`) asks, since this emitter dispatches by a
    #: cascade rather than `BodyWalker` hooks.
    #: `test_emitter_kind_registry.py` holds it to the two cascades.
    KINDS = frozenset({
        "ExprAttribute", "ExprBin", "ExprCall", "ExprCast", "ExprConstant",
        "ExprIfExp", "ExprIn", "ExprNull", "ExprRefBottomUp", "ExprRefLocal",
        "ExprRefUnresolved", "ExprSubscript", "ExprUnary",
        "StmtAnnAssign", "StmtAssign", "StmtAugAssign", "StmtBreak",
        "StmtContinue", "StmtExpr", "StmtFor", "StmtForeach", "StmtIf",
        "StmtMatch", "StmtRepeatWhile", "StmtReturn", "StmtWhile",
        "StmtYield"})

    """Translate one operation body to C++ lines.

    Integers are carried out at their PSS types by `CIntSemantics`, which C++
    shares with C: the two languages' integer rules are the same."""

    def __init__(self, fn, comp, names, *, imports=None,
                 yield_mode: str = "none", match_default: str = "message",
                 message_style: str = "import", cls: str = "",
                 ctor_names=None, ctx=None, value_structs=()):
        self.fn = fn
        #: Register value structs: C++ bitfields of unsigned integers, so an
        #: enum field of one is held as an integer (`_enum_type`).
        self.value_structs = {id(v) for v in value_structs}
        #: The compile context, for `ExprTypes`.
        self.ctx = ctx
        #: `IntSemantics`' per-root final types.
        self._final = {}
        #: This compile's constructor names, for the call site that has only a
        #: name to classify (`ch[i].initialize(...)`).
        self.ctor_names = ctor_names
        self.comp = comp
        self.names = names
        self.cls = cls
        self.yield_mode = yield_mode
        self.match_default = match_default
        self.message_style = message_style
        self.imports = dict(imports or {})
        self.model_ops = {f.name for f in comp.functions}
        self.arg_rename = {a.arg: mangle(a.arg) for a in fn.args.args}
        self.arg_names = set(self.arg_rename)
        self.reg_groups = _reg_group_fields(comp)
        self.chan_fields = {f.name for f in channel_fields(comp)}
        self.subs = {s.name: s for s in sub_components(comp)}
        self.comp_fields = {f.name for f in getattr(comp, "fields", [])}
        self.field_types = {f.name: f.datatype
                            for f in getattr(comp, "fields", [])}
        #: The base component and its class, for `super` -- set when this
        #: body belongs to a class rendered as a derived C++ class
        #: (`_native`). `super.f(...)` is `base::f(...)`, a static call.
        self.super_base = None
        self.super_cls: Optional[str] = None
        self.write_only_locals: Set[str] = self._scan_write_only(fn)
        #: Declared type of every local and argument, for enum coercion below.
        self.local_types: Dict[str, object] = {
            a.arg: a.annotation for a in fn.args.args}
        self._scan_locals(fn.body)

    def _scan_locals(self, node) -> None:
        """Record every `StmtAnnAssign`'s declared type, at any nesting depth.

        Nested, not just top level: a declaration inside an `if` or a `foreach`
        is still a local whose type a later statement needs.
        """
        import dataclasses as dc
        if isinstance(node, (list, tuple)):
            for x in node:
                self._scan_locals(x)
            return
        if not dc.is_dataclass(node):
            return
        if _dt_name(node) == "StmtAnnAssign" and \
                _dt_name(node.target) == "ExprRefLocal":
            self.local_types[node.target.name] = node.annotation
        for f in dc.fields(node):
            self._scan_locals(getattr(node, f.name, None))

    # -- enum conversion ----------------------------------------------------
    #
    # A PSS enum is an `enum class`, which C++ converts to and from an integer
    # only when told. An item keeps its name and its type through the IR
    # (`ExprConstant.ref`, typed by `ExprTypes`), so it is written as the item;
    # an integer where an enum is expected is a `static_cast` to it, and an
    # enum operand of integer arithmetic a `static_cast` to `int`.

    @property
    def types(self):
        """Static PSS types of this body's expressions (`ExprTypes`)."""
        if getattr(self, "_types", None) is None:
            from ..expr_types import ExprTypes
            self._types = ExprTypes(self.fn, self.comp, self.ctx)
        return self._types

    # -- integer spelling: C's, with C++'s names (`CIntSemantics`) -----------

    def int_type_name(self, ct: str) -> str:
        return f"std::{ct}"

    def int_cast_type(self, dtype) -> str:
        return cpp_type(dtype)

    def int_cast_text(self, ct: str, text: str, atom: bool) -> str:
        # `static_cast`, as everywhere in this backend (see `expr`'s cast).
        return f"static_cast<{ct}>({text})"

    def int_leaf_text(self, e, s, t) -> str:
        """C's rule, plus one: `bit` is a C++ `bool`, and arithmetic on a
        `bool` is refused by `-Wbool-operation` (`~x`), so a one-bit operand
        is converted to an integer first."""
        if s.width == 1 and _c_carrier(s, t.as_int()) is None \
                and e is not getattr(self, "_int_root_e", None):
            return self.int_cast_text("std::uint32_t", self.expr(e), True)
        own = self.types.type_of(e)
        if own is not None and own.kind == "enum":
            # An `enum class` takes part in no arithmetic until converted.
            return self.int_cast_text("std::int32_t", self.expr(e), True)
        return CIntSemantics.int_leaf_text(self, e, s, t)

    def _expr_type(self, e):
        """Declared type of an expression, or ``None`` when it is not known.

        Deliberately shallow -- it answers "is this already the enum I want?"
        for the handful of shapes that appear on the right-hand side of a typed
        context, and says nothing otherwise.
        """
        cn = _dt_name(e)
        if cn == "ExprRefLocal":
            return self.local_types.get(e.name)
        if cn == "ExprCast":
            return e.target_type
        if cn == "ExprAttribute":
            if _dt_name(e.value) == "TypeExprRefSelf":
                if e.attr in self.field_types:
                    return self.field_types[e.attr]
                return self.local_types.get(e.attr)
            base = self._expr_type(e.value)
            for f in (getattr(base, "fields", None) or []):
                if f.name == e.attr:
                    return f.datatype
        if cn == "ExprCall":
            fn = self._callee(e)
            return getattr(fn, "returns", None) if fn is not None else None
        if cn == "ExprSubscript":
            # An array element: its element type, so an assignment to one
            # is converted to it (8.7.2) as to any other target (W26).
            base = self.types.resolve(self._expr_type(e.value))
            if _dt_name(base) == _DT_ARRAY:
                return base.element_type
        return None

    def _literal(self, lst, dtype) -> str:
        """`{a, b}` as a `std::array` of *dtype*: a temporary, so every
        element is read before the target is written (`w = {w[1], w[0]}`
        swaps). Each element is converted to the element type (8.7.2), and
        an integer one cast, since a brace initializer refuses a narrowing
        conversion. `array_literal.shape_error` has checked that it fits."""
        elem = self.types.resolve(dtype.element_type)
        parts = []
        for e in lst.elts:
            text = self._coerce(e, elem)
            t = self.types.of_datatype(elem)
            if t is not None and t.kind == "int" \
                    and _dt_name(e) != "ExprConstant":
                text = f"static_cast<{cpp_type(elem)}>({text})"
            parts.append(text)
        return f"{cpp_type(dtype)}{{{{" + ", ".join(parts) + "}}"

    def _coerce(self, e, dtype) -> str:
        """Render ``e`` where a value of ``dtype`` is expected: converted to
        it if it is an integer type (8.7.2), named if it is an enum."""
        if is_literal(e) and _dt_name(dtype) == _DT_ARRAY:
            return self._literal(e, dtype)
        if _dt_name(e) == "ExprStructLiteral" and dtype is not None:
            return self.struct_literal_text(e, self.types.resolve(dtype))
        if dtype is not None and _dt_name(dtype) != _DT_ENUM:
            target = self.types.of_datatype(dtype)
            if target is not None and target.kind == "int":
                return self.convert_to(e, target)
        s = self.expr(e)
        if dtype is None or _dt_name(dtype) != _DT_ENUM:
            return s
        t = self._enum_type(e)
        if t is not None and _strip_pkg(getattr(t.dtype, "name", "")) == \
                _strip_pkg(getattr(dtype, "name", "")):
            return s
        from .lower_api_types import cpp_enum_name
        return f"static_cast<{cpp_enum_name(dtype)}>({s})"

    def _enum_type(self, e):
        """*e*'s enum type, if it is held as one in C++; else None.

        An enum field of a register value struct is PSS-typed as its enum
        but held in an unsigned bitfield, so it is converted both ways."""
        t = self.types.type_of(e)
        if t is None or t.kind != "enum" or self._int_storage(e):
            return None
        return t

    def _int_storage(self, e) -> bool:
        if _dt_name(e) != "ExprAttribute":
            return False
        base = self.types.type_of(e.value)
        return (base is not None and base.kind == "struct"
                and id(self.types.resolve(base.dtype)) in self.value_structs)

    def _callee(self, call):
        """The `Function` a call resolves to, or ``None``.

        Only the cases whose parameter types this emitter needs: an operation of
        this component, an operation or constructor of a sub-component, and a
        declared import.
        """
        func = call.func
        cn = _dt_name(func)
        if cn == "ExprRefUnresolved":
            return (_fn_named(self.comp, func.name)
                    or self.imports.get(func.name))
        if cn != "ExprAttribute":
            return None
        if _dt_name(func.value) == "TypeExprRefSelf":
            return _fn_named(self.comp, func.attr) or self.imports.get(func.attr)
        if _dt_name(func.value) == "TypeExprRefSuper":
            return _fn_named(self.super_base, func.attr)
        dt = self._chain_comp(self._chain(func.value))
        return _fn_named(dt, func.attr) if dt is not None else None

    def _chain_comp(self, chain):
        """The component type a self-rooted path of sub-components names
        (`dma.ch[i]`, any depth), or None if the path is not one."""
        if not chain:
            return None
        dt = self.comp
        for name, _ in chain:
            sub = {s.name: s for s in sub_components(dt)}.get(name)
            if sub is None:
                return None
            dt = sub.dtype
        return dt

    def _call_args(self, call) -> List[str]:
        """A call's arguments, each rendered in its parameter's type."""
        fn = self._callee(call)
        params = list(fn.args.args) if (fn is not None and fn.args) else []
        args = with_defaults(call.args, fn) if params else call.args
        out = []
        for i, a in enumerate(args):
            want = params[i].annotation if i < len(params) else None
            out.append(self._coerce(a, want))
        return out

    # -- locals the model assigns and never reads ---------------------------

    def _scan_write_only(self, fn) -> Set[str]:
        """Same analysis as the C backend, for the same reason.

        `ok = inflight.try_get(tok)` in `wait_completion` discards its result on
        purpose -- the operation is draining a token it knows it holds. C++ warns
        about it under -Wunused-but-set-variable exactly as C does, the model is
        right to discard, and the emitter states the discard rather than dropping
        a statement whose CALL has effects.
        """
        import dataclasses as dc
        declared: Set[str] = set()
        read: Set[str] = set()

        def walk(n, in_read: bool):
            if isinstance(n, (list, tuple)):
                for x in n:
                    walk(x, in_read)
                return
            if not dc.is_dataclass(n):
                return
            cn = _dt_name(n)
            if cn == "ExprRefLocal" and in_read:
                read.add(n.name)
                return
            if cn == "StmtAnnAssign":
                if _dt_name(n.target) == "ExprRefLocal":
                    declared.add(n.target.name)
                walk(getattr(n, "value", None), True)
                return
            if cn == "StmtAssign":
                for t in n.targets:
                    walk(t, _dt_name(t) != "ExprRefLocal")
                walk(n.value, True)
                return
            if cn == "StmtAugAssign":
                walk(n.target, True)
                walk(n.value, True)
                return
            for f in dc.fields(n):
                walk(getattr(n, f.name, None), True)

        walk(fn.body, True)
        return declared - read

    # -- access paths -------------------------------------------------------

    def _chain(self, e):
        """Flatten `self.a.b[i]` to `[[name, index|None], ...]`, rooted at self.

        ``None`` means the expression is not a self-rooted access path.
        """
        cn = _dt_name(e)
        if cn == "TypeExprRefSelf":
            return []
        if cn == "ExprAttribute":
            b = self._chain(e.value)
            if b is None:
                return None
            b.append([e.attr, None])
            return b
        if cn == "ExprSubscript":
            b = self._chain(e.value)
            if not b:
                return None
            b[-1][1] = e.slice
            return b
        return None

    def _self_member(self, name: str) -> str:
        """How `self.<name>` is spelled in a body.

        `this->`, not a bare name, and not because C++ requires it. The C
        backend's one silent defect was a bare member reference resolving
        against an unrelated object of the same name; `this->` cannot do that,
        and it also states in the output which names are component state and
        which are package constants.
        """
        # A parameter shadows a member of the same name (scope order).
        if name in self.arg_names:
            return self.arg_rename[name]
        if name in self.subs:
            return f"this->{_sub_member(self.subs[name])}"
        if name in self.comp_fields:
            return f"this->{mangle(name)}"
        # Not a member: a package-scope constant, which is a plain identifier.
        return name

    # -- calls --------------------------------------------------------------

    def _reg_call(self, call) -> Optional[str]:
        """A register access -- which needs no translation, only checking.

        The C++ register model mirrors the PSS structure, so
        `regs.channels[i].CSR.write_val_masked(m, v)` is already the C++ for
        itself. What this does is REFUSE a method `pssc::reg` does not have:
        letting it through would produce a compile error naming a template
        class the reader never wrote, several layers from the PSS line at fault.
        """
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        rest, subs = chain, self.subs
        while rest and rest[0][0] in subs:
            # `s.a.STS.write_val(1)`: a register of a sub-component. Its
            # register object was built at its own group's handle, so the
            # path through the member is the whole access.
            comp = subs[rest[0][0]].dtype
            subs = {s.name: s for s in sub_components(comp)}
            groups = {f.name for f in getattr(comp, "fields", []) or []
                      if field_is_reg_group(f)}
            rest = rest[1:]
            if rest and rest[0][0] in groups and len(rest) >= 2:
                chain = [[rest[0][0], None]] + rest[1:]
                break
        # A register lives inside a group, so its path is at least
        # `<group>.<reg>`. A one-element chain is a call on the GROUP itself
        # (`regs.set_handle(...)`), which is not a register access.
        if rest is chain:
            if not chain or len(chain) < 2 or \
                    chain[0][0] not in self.reg_groups:
                return None
        elif not rest or len(rest) < 2:
            return None

        path = ".".join(c[0] for c in chain)
        want = _REG_METHODS.get(func.attr)
        if want is None:
            raise ValueError(
                f"unsupported register method '{func.attr}' on '{path}'. "
                f"`pssc::reg` implements: {', '.join(sorted(_REG_METHODS))}.")
        if len(call.args) != want:
            raise ValueError(
                f"register method '{func.attr}' takes {want} argument(s), "
                f"got {len(call.args)}")
        # A value written is converted to the register's width (21.14.1),
        # like any argument to a `bit[N]` parameter; a value struct is
        # passed whole.
        from ..reg_layout import value_bits
        from ..expr_types import PssType
        reg_t = self.types.type_of(func.value)
        bits = (value_bits(reg_t.dtype)
                if reg_t is not None and _dt_name(reg_t.dtype) == "DataTypeRegister"
                else 32)
        val_t = PssType("int", bits, False)
        args = ", ".join(self.convert_to(a, val_t) for a in call.args)
        return f"{self.expr(func.value)}.{func.attr}({args})"

    def _chan_receiver(self, chain) -> Optional[str]:
        """The channel a call is made on, as a C++ expression, or ``None``.

        TWO SHAPES, because the model uses both. `inflight.try_put(1)` is this
        component's own channel; `ch[i].wake.try_put(1)` is a CHILD's, which is
        how a parent notifies its children (the WB DMA model's `notify_irq`
        does exactly this on the event-wait profile). PSS component state is
        reachable along the hierarchy, so the second is not a special case in
        the language -- it is only a special case here because a C++ class
        member is private by default. See `_friends`, which is what makes it
        legal.

        Anything deeper is refused rather than guessed at: reaching a
        grandchild's channel would need each intermediate class to befriend the
        one above it, and nothing in the tree asks for that yet.
        """
        if not chain:
            return None
        if len(chain) == 1:
            if chain[0][0] not in self.chan_fields:
                return None
            recv = self._self_member(chain[0][0])
            idx = chain[0][1]
            return recv if idx is None else f"{recv}[{self.expr(idx)}]"
        if len(chain) == 2 and chain[0][0] in self.subs:
            sub = self.subs[chain[0][0]]
            if chain[1][0] in {f.name for f in channel_fields(sub.dtype)}:
                base = self._self_member(chain[0][0])
                idx = chain[0][1]
                if idx is not None:
                    base = f"{base}[{self.expr(idx)}]"
                return f"{base}.{mangle(chain[1][0])}"
        return None

    def _chan_call(self, call) -> Optional[str]:
        """`inflight.try_get(tok)` / `ch[i].wake.try_put(1)` -> `pssc::chan1`.

        Blocking `get()`/`put()` are refused by name rather than lowered: they
        are the two operations that need a scheduler, this target generates
        none, and a `get()` returning whatever was in the object would report a
        completion nobody signalled.
        """
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        recv = self._chan_receiver(chain)
        if recv is None:
            return None
        name = ".".join(c[0] for c in chain)
        m = func.attr
        if m in ("try_put", "try_get"):
            if len(call.args) != 1:
                raise ValueError(f"channel {m}() takes one argument")
            return f"{recv}.{m}({self.expr(call.args[0])})"
        if m in ("get", "put"):
            raise ValueError(
                f"'{name}.{m}()' is a BLOCKING channel operation, and the C++ "
                f"target has no scheduler to suspend to (HAVE_EVENT_WAIT is "
                f"false for this target -- see targets/target_cfg.py). Use "
                f"try_{m}(), or guard the call with "
                f"`compile if (target_cfg_pkg::HAVE_EVENT_WAIT)` as "
                f"src/pss/wb_dma_ch_c/functions/wait_hint.pss does.")
        raise ValueError(
            f"unsupported channel method '{m}' on '{name}'. The C++ channel "
            f"runtime implements try_put/try_get (share/cpp/pssc_chan.hpp).")

    def _builtin_call(self, call) -> Optional[str]:
        """PSS built-ins, which have no definition to call."""
        name = _builtin_name(call.func)
        if name is None or name not in CPP_BUILTINS:
            return None
        args = call.args
        if name == "make_handle_from_handle":
            # A handle is opaque in PSS and an address here, so deriving one
            # from another is an add rather than a call.
            return handle_from_handle(self, args)
        if name == "addr_value":
            return self.expr(args[0])
        if name in _MEM_PRIMS:
            parts = [self.expr(a) for a in args]
            if name.startswith("write") and len(args) == 2:
                width = int(name[len("write"):])
                # Converted to the primitive's `bit[width]` (8.7.2; W8).
                parts[1] = self.convert_to(args[1],
                                           PssType("int", width, False))
            return f"this->{_IMP}.{name}({', '.join(parts)})"
        if name in ("message", "print"):
            return self._message_call(name, args)
        if name in ("error", "fatal"):
            rest = args[1:] if name == "fatal" else args
            text = self._message_call("print", _prefixed(name, rest))
            if name == "error":
                return text
            # `pssc::fatal` does not return (21.3; ruling R2).
            return f"({text}, pssc::fatal({self.expr(args[0])}))"
        raise ValueError(f"built-in '{name}' is claimed by the C++ target but "
                         f"has no rendering; see targets/cpp/lower_progseq.py")

    def _message_call(self, name: str, args) -> str:
        if self.message_style == "none":
            return "(void)0"
        # message(verbosity, fmt, args...) -- the verbosity has no C++ analogue
        # and is dropped, exactly as the SV and C projections drop it.
        rest = args[1:] if (name == "message" and len(args) >= 2) else args
        if not rest:
            raise ValueError(f"'{name}' needs a format string")
        fmt_e, vals = rest[0], rest[1:]
        if name == "print" and _dt_name(fmt_e) == "ExprConstant" and \
                isinstance(fmt_e.value, str) and fmt_e.value.endswith("\n"):
            # `print` adds no newline (21.1.2) and ends its own line with
            # `\n`; the message seam is line-oriented and ends every call's
            # line itself, so the format's last newline is that one.
            fmt_e = ir.ExprConstant(value=fmt_e.value[:-1])
        fmt = getattr(fmt_e, "value", None)
        if _dt_name(fmt_e) != "ExprConstant" or not isinstance(fmt, str):
            raise Refusal(
                f"'{name}' needs a string literal as its format, so the "
                f"format can be checked when the model is generated; got "
                f"{_dt_name(fmt_e)}", fmt_e)
        # Formatted here, not by the platform's printf (`message_format`).
        from ..c.message_format import CppFormat, lower_format
        parts = lower_format(name, fmt, vals, self.types, self.expr,
                             CppFormat(), c_string_literal)
        return f"pssc::message({', '.join(parts)})"

    def struct_literal_text(self, e, dtype) -> str:
        """Aggregate initialisation, every member in declaration order (C++17
        has no designated initializers): one the PSS literal leaves out has
        its default (E5)."""
        from .lower_api_types import _cpp_const, cpp_first_item
        parts = []
        for f, v in self.struct_literal_members(e, dtype):
            if v is not None:
                parts.append(self._coerce(v, f.datatype))
                continue
            iv = getattr(f, "initial_value", None)
            parts.append(_cpp_const(iv, f.datatype, self.types._tm)
                         if iv is not None
                         else cpp_first_item(f.datatype) or "{}")
        return f"{cpp_type(dtype)}{{{', '.join(parts)}}}"

    def _offset_fold(self, call) -> Optional[str]:
        """`g.get_offset_of_instance("R")` / `..._array("A", i)`: a register
        group's offset, folded at generation time (21.14.6) -- in an
        operation as in a constructor (E9). ``None`` for any other call."""
        func = call.func
        if _dt_name(func) != "ExprAttribute" or func.attr not in (
                "get_offset_of_instance", "get_offset_of_instance_array"):
            return None
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.reg_groups:
            return None
        group = self.reg_groups[chain[0][0]]
        m, args = func.attr, call.args
        if m == "get_offset_of_instance":
            return f"0x{scalar_offset(group, _str_const(args[0], m)):x}u"
        base, stride = array_base_stride(group, _str_const(args[0], m))
        return f"(0x{base:x}u + 0x{stride:x}u * {self._operand(args[1])})"

    def _sub_op_call(self, call) -> Optional[str]:
        """`ch[i].probe_status()` -- an operation of a SUB-component.

        Reached through the member rather than the accessor, because the member
        is the concrete class and the accessor returns the interface: both work
        for an operation, and only the member works for `initialize`, so one
        spelling covers the constructor case too.
        """
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        # A sub-component at any depth (`dma.ch[1].f()`, E18): every step is
        # the MEMBER of the concrete class, not the accessor of the same name.
        chain = self._chain(func.value)
        if self._chain_comp(chain) is None:
            return None
        args = ", ".join(self._call_args(call))
        if len(chain) == 1:
            return f"{self.expr(func.value)}.{mangle(func.attr)}({args})"
        dt, path = self.comp, "this->"
        for k, (name, idx) in enumerate(chain):
            sub = {s.name: s for s in sub_components(dt)}[name]
            path += ("" if k == 0 else ".") + _sub_member(sub)
            if idx is not None:
                path += f"[{self.expr(idx)}]"
            dt = sub.dtype
        return f"{path}.{mangle(func.attr)}({args})"

    def _super(self) -> str:
        if self.super_cls is None:
            raise ValueError(
                f"`super` in '{getattr(self.fn, 'name', '?')}': "
                f"'{getattr(self.comp, 'name', '?')}' has no base component")
        return self.super_cls

    def _super_call(self, call) -> str:
        """`super.f(args)` -> `base::f(args)`: the base's `f`, statically.
        `super.initialize(...)` is the base's PSS constructor."""
        name = call.func.attr
        callee = _fn_named(self.super_base, name)
        if callee is None:
            raise ValueError(
                f"'super.{name}(...)' in '{getattr(self.fn, 'name', '?')}': "
                f"no base of '{getattr(self.comp, 'name', '?')}' declares it")
        args = ", ".join(self._call_args(call))
        if func_kind(callee, self.ctor_names) == FuncKind.CONSTRUCTOR:
            return f"{self._super()}::initialize({args})"
        return f"{self._super()}::{mangle(name)}({args})"

    def _model_call(self, call) -> str:
        """A call on the model itself: an operation of this component, or an
        `import target/solve function` the platform supplies."""
        func = call.func
        args = self._call_args(call)
        name = None
        if _dt_name(func) == "ExprRefUnresolved":
            name = func.name
        elif _dt_name(func) == "ExprAttribute" and \
                _dt_name(func.value) == "TypeExprRefSelf":
            name = func.attr
        if name is not None and name in self.model_ops:
            return f"this->{mangle(name)}(" + ", ".join(args) + ")"
        if name is not None and name in self.imports:
            # An import is the PLATFORM's function, reached through the same
            # object as memory access -- see `<ns>_import_if`.
            return f"this->{_IMP}.{mangle(name)}(" + ", ".join(args) + ")"
        raise ValueError(
            f"call to '{name or _dt_name(func)}' has no lowering in the C++ "
            f"target. It is not a register access, not a channel operation, "
            f"not a PSS built-in the C++ target claims "
            f"({', '.join(sorted(CPP_BUILTINS))}), not an operation of "
            f"'{getattr(self.comp, 'name', '?')}', and not a declared `import "
            f"target/solve function`. Emitting it verbatim would produce C++ "
            f"naming a function nothing declares -- which is the defect "
            f"targets/call_legality.py exists to prevent.")

    # -- expressions --------------------------------------------------------

    #: Comparisons, where one enum-typed operand types the other. `<` and `>`
    #: are included: an enum with ordered encodings is compared by range in
    #: real models, and PSS permits it.
    _COMPARISONS = frozenset({"Eq", "NotEq", "Ne", "Lt", "LtE", "Le",
                              "Gt", "GtE", "Ge"})

    def _binop(self, e, op: str) -> str:
        """A comparison with an enum on one side only (`status != x`, `x` an
        integer) compares in the enum's type: an `enum class` compares with
        nothing else."""
        lhs, rhs = e.lhs, e.rhs
        if e.op.name in ("Eq", "NotEq", "Ne") \
                and _dt_name(rhs) == "ExprStructLiteral":
            # `s == {...}` (8.5.3, Example 33): the literal is typed by the
            # struct it is compared with (E13).
            return (f"{self._operand(lhs)} {op} "
                    f"{self.convert_to(rhs, self.types.type_of(lhs))}")
        if e.op.name in self._COMPARISONS:
            lt, rt = self._enum_type(lhs), self._enum_type(rhs)
            lt_is, rt_is = lt is not None, rt is not None
            if lt_is and not rt_is:
                return f"{self._operand(lhs)} {op} {self._coerce(rhs, lt.dtype)}"
            if rt_is and not lt_is:
                return f"{self._coerce(lhs, rt.dtype)} {op} {self._operand(rhs)}"
        return f"{self._operand(lhs)} {op} {self._operand(rhs)}"

    def _operand(self, e) -> str:
        """An operand of a binary expression, parenthesised if it is one too.

        The IR states how the expression groups and C++ precedence only happens
        to agree; where it does not the output is wrong, and where it does,
        `-Wparentheses -Werror` still rejects `a & b | c`.
        """
        s = self.expr(e)
        return f"({s})" if _dt_name(e) == "ExprBin" else s

    def expr(self, e) -> str:
        cn = _dt_name(e)
        if cn == "ExprIn":
            # `v in [...]` (8.5.9), as the comparisons it means (E3).
            return self.expr(in_as_compare(e, self.types,
                                           getattr(self.fn, "name", "?")))
        if cn == "ExprNull":
            # `null` (7.7): the empty chandle, an address here as
            # `addr_handle_t` (a `typedef chandle`) is (E12).
            return "0"
        if cn == "ExprConstant":
            from .lower_api_types import cpp_enum_constant
            item = cpp_enum_constant(e, self.types._tm)
            if item is not None:
                return item
            v = e.value
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, int):
                return str(v)
            if isinstance(v, str):
                return c_string_literal(v)
            raise ValueError(f"unsupported constant of type {type(v).__name__}")
        if cn == "ExprRefLocal":
            # A local is renamed as a parameter is: `int char;` does not compile.
            return self.arg_rename.get(e.name) or mangle(e.name)
        if cn == "ExprRefUnresolved":
            # A package-scope constant or enumerator: a plain identifier here.
            return e.name
        if cn == "ExprAttribute":
            base = e.value
            if _dt_name(base) == "TypeExprRefSelf":
                return self._self_member(e.attr)
            if _dt_name(base) == "TypeExprRefSuper":
                # The base's field: a derived class's own of that name hides
                # it and does not replace it (17.1), exactly as in C++.
                return f"this->{self._super()}::{mangle(e.attr)}"
            return f"{self.expr(base)}.{e.attr}"
        if cn == "ExprSubscript":
            sel = bit_select(e, self.types)
            if sel is not None:
                return c_select_read(sel, self.expr)
            return (checked_index(self, e)
                    or f"{self.expr(e.value)}[{self.expr(e.slice)}]")
        if cn == "ExprBin":
            if e.op.name in RELATIONAL:
                text = self._int_compare(e)     # None if an operand is an enum
                if text is not None:
                    return text
            elif e.op.name in CONTEXT_BINARY or e.op.name in LEFT_TYPED:
                t = self.types.type_of(e)
                if is_integral(t) and t.kind != "enum":
                    return self._int_root(e, t.as_int()).text
            op = _BINOP.get(e.op.name)
            if op is None:
                raise ValueError(f"unsupported binop {e.op.name}")
            return self._binop(e, op)
        if cn == "ExprUnary":
            if e.op.name in CONTEXT_UNARY:
                t = self.types.type_of(e)
                if is_integral(t) and t.kind != "enum":
                    return self._int_root(e, t.as_int()).text
            op = _UNOP.get(e.op.name)
            if op is None:
                raise ValueError(f"unsupported unary op {e.op.name}")
            return f"{op}({self.expr(e.operand)})"
        if cn == "ExprIfExp":
            t = self.types.type_of(e)
            if t is not None and t.kind == "int":
                return self._int_root(e, t).text
            # `c ? a : b` (8.5.6): only the chosen arm is evaluated, as in C++.
            return (f"(({self.expr(e.test)}) ? ({self.expr(e.body)}) : "
                    f"({self.expr(e.orelse)}))")
        if cn == "ExprCast":
            # `static_cast`, not a C cast: the model's casts here are between
            # arithmetic types and an enum, all of which static_cast covers, and
            # a C cast would silently also permit the ones it does not.
            return f"static_cast<{cpp_type(e.target_type)}>({self.expr(e.value)})"
        if cn == "ExprCall":
            if _dt_name(e.func) == "ExprAttribute" and \
                    _dt_name(e.func.value) == "TypeExprRefSuper":
                return self._super_call(e)
            for handler in (self._reg_call, self._chan_call, self._sub_op_call,
                            self._builtin_call, self._offset_fold):
                out = handler(e)
                if out is not None:
                    return out
            return self._model_call(e)
        if cn == "ExprRefBottomUp":
            _reject_upward_ref(self.comp, e)
        raise ValueError(f"unsupported expr {cn}")

    # -- statements ---------------------------------------------------------

    def stmts(self, body, ind: int) -> List[str]:
        out: List[str] = []
        for s in body:
            out += self.stmt(s, ind)
        return out

    def stmt(self, s, ind: int) -> List[str]:
        """Emit *s*, wrapped in whatever the PSS source wrote around it.

        One site for the whole tree, mirroring the SV and C emitters' hook.
        """
        try:
            lines = self._stmt_lines(s, ind)
        except Refusal as e:
            raise e.at_least(s)
        if not lines:
            return lines
        pad = "    " * ind
        return comment_lines(getattr(s, "comment", None), pad, LINE) + \
            append_trailing(lines, getattr(s, "comment_trailing", None), LINE)

    def _stmt_lines(self, s, ind: int) -> List[str]:
        pad = "    " * ind
        cn = _dt_name(s)
        if cn == "StmtAnnAssign":
            return self._decl(s, pad)
        if cn == "StmtAssign":
            tgt = s.targets[0]
            sel = bit_select(tgt, self.types)
            if sel is not None:
                value = c_select_write(
                    sel, self.expr, self.convert_to(s.value, select_type(sel)),
                    getattr(self.fn, "name", "?"))
                return [f"{pad}{self.expr(sel.base)} = {value};"]
            if self._int_storage(tgt) and self._enum_type(s.value) is not None:
                # An enum into a register value field: its integer value.
                value = f"static_cast<std::int32_t>({self.expr(s.value)})"
            else:
                value = self._coerce(s.value, self._expr_type(tgt))
            return [f"{pad}{self.expr(tgt)} = {value};"]
        if cn == "StmtAugAssign":
            # `x op= e` is `x = x op e` (8.3), converted back to x's type --
            # as C's `stmt_aug_assign` does, and for the same reason: C++'s
            # own `op=` keeps the carry a `bit[4]` must lose (W1).
            op = _BINOP.get(s.op.name)
            if op is None:
                raise ValueError(f"unsupported augmented-assign op {s.op.name}")
            whole = ir.ExprBin(lhs=s.target, op=s.op, rhs=s.value)
            if bit_select(s.target, self.types) is not None:
                # A select is written by read-modify-write (W17).
                return self._stmt_lines(
                    ir.StmtAssign(targets=[s.target], value=whole), ind)
            target = self.types.type_of(s.target)
            if target is None or target.kind != "int" \
                    or not is_integral(self.types.type_of(whole)):
                return [f"{pad}{self.expr(s.target)} {op}= "
                        f"{self.expr(s.value)};"]
            tgt = self.expr(s.target)
            value = self.convert_to(whole, target)
            head = f"{tgt} {op} "
            if value.startswith(head):
                return [f"{pad}{tgt} {op}= "
                        f"{_c_unbracket(value[len(head):])};"]
            if _has_call(s.target):
                raise ValueError(
                    f"in '{getattr(self.fn, 'name', '?')}': `{tgt} {op}= "
                    f"...` needs its result converted back to the target's "
                    f"type, which evaluates the target twice, and the target "
                    f"contains a call. Read the index into a local first.")
            return [f"{pad}{tgt} = {value};"]
        if cn == "StmtExpr":
            return [f"{pad}{self.expr(s.expr)};"]
        if cn == "StmtReturn":
            if s.value is not None:
                return [f"{pad}return {self._coerce(s.value, self.fn.returns)};"]
            return [f"{pad}return;"]
        if cn == "StmtIf":
            lines = [f"{pad}if ({self.expr(s.test)}) {{"]
            lines += self.stmts(s.body, ind + 1)
            if getattr(s, "orelse", None):
                lines.append(f"{pad}}} else {{")
                lines += self.stmts(s.orelse, ind + 1)
            lines.append(f"{pad}}}")
            return lines
        if cn == "StmtRepeatWhile":
            lines = [f"{pad}do {{"]
            lines += self.stmts(s.body, ind + 1)
            lines.append(f"{pad}}} while ({self.expr(s.condition)});")
            return lines
        if cn == "StmtWhile":
            # `.test`, not `.condition` -- `StmtRepeatWhile` above uses the
            # other spelling, and copying this arm from it is a mistake the C
            # backend actually shipped.
            lines = [f"{pad}while ({self.expr(s.test)}) {{"]
            lines += self.stmts(s.body, ind + 1)
            lines.append(f"{pad}}}")
            return lines
        if cn == "StmtBreak":
            return [f"{pad}break;"]
        if cn == "StmtContinue":
            return [f"{pad}continue;"]
        if cn == "StmtFor":
            return self._for(s, ind)
        if cn == "StmtForeach":
            return self._foreach(s, ind)
        if cn == "StmtMatch":
            return self._match(s, ind)
        if cn == "StmtYield":
            return self._yield(s, ind)
        raise ValueError(f"unsupported stmt {cn}")

    def _decl(self, s, pad: str) -> List[str]:
        ct = cpp_type(s.annotation)
        name = self.expr(s.target)
        # `(void)x;` right after the declaration, which is what silences
        # -Wunused-but-set-variable (placement matters: after the assignment,
        # the compiler has already decided).
        tail = ([f"{pad}(void){name};   // PSS assigns it and never reads it"]
                if getattr(s.target, "name", None) in self.write_only_locals
                else [])
        if _dt_name(s.annotation) == _DT_ARRAY:
            if getattr(s, "value", None) is not None:
                return [f"{pad}{ct} {name} = "
                        f"{self._coerce(s.value, s.annotation)};"] + tail
            return [f"{pad}{ct} {name}{_array_init(s.annotation)};"] + tail
        if getattr(s, "value", None) is not None:
            return [f"{pad}{ct} {name} = "
                    f"{self._coerce(s.value, s.annotation)};"] + tail
        # Value-initialised rather than left indeterminate. PSS gives a
        # declared variable a defined value; C++ does not, and the difference
        # is a read of stack garbage that behaves differently under -O2.
        from .lower_api_types import cpp_first_item
        first = cpp_first_item(s.annotation)
        if first is not None:
            # An enum starts at its first item (7.5), which need not be 0.
            return [f"{pad}{ct} {name} = {first};"] + tail
        return [f"{pad}{ct} {name}{{}};"] + tail

    def _for(self, s, ind: int) -> List[str]:
        """`repeat ([i :] n) { ... }` (LRM 20.7.6) -> a counted `for`.

        The count is evaluated once, before the first iteration, and a count
        of zero or less runs none: it is held in a local, because a `for`
        condition is re-evaluated. The index is an `int` counting from 0,
        scoped to the loop.
        """
        pad = "    " * ind
        count = self.expr(s.iter)
        k = self._repeat_n = getattr(self, "_repeat_n", -1) + 1
        n = f"_pssc_n{k}"
        name = getattr(getattr(s, "target", None), "name", None)
        if name is None:
            return ([f"{pad}for (std::int64_t {n} = {count}; {n} > 0; --{n}) {{"]
                    + self.stmts(s.body, ind + 1) + [f"{pad}}}"])
        idx = self.expr(s.target)
        had, prev = name in self.types.loop_vars, self.types.loop_vars.get(name)
        self.types.loop_vars[name] = INT
        try:
            body = self.stmts(s.body, ind + 2)
        finally:
            if had:
                self.types.loop_vars[name] = prev
            else:
                del self.types.loop_vars[name]
        return ([f"{pad}{{",
                 f"{pad}    const std::int64_t {n} = {count};",
                 f"{pad}    for (int {idx} = 0; {idx} < {n}; ++{idx}) {{"]
                + body + [f"{pad}    }}", f"{pad}}}"])

    def _foreach(self, s, ind: int) -> List[str]:
        """`foreach (a[i]) { ... }` -> an indexed `for` over the folded size.

        A range-for would read better and cannot be used: the body indexes the
        collection by the loop variable (`ch[i]`, and often a second collection
        at the same index), so the INDEX is what the model needs, not the
        element.
        """
        pad = "    " * ind
        coll = self.expr(s.iter)
        dtype = self._iter_dtype(s)
        n = _array_size(dtype)
        if n is None:
            raise ValueError(
                f"cannot lower `foreach` over '{coll}': its size is not known "
                "at generation time.")
        index, it = foreach_parts(s)
        if index is not None:
            idx = mangle(index.name)
        else:
            self._tmp_k = getattr(self, "_tmp_k", -1) + 1
            idx = f"_pssc_i{self._tmp_k}"
        head = [f"{pad}for (std::size_t {idx} = 0; {idx} < {n}u; ++{idx}) {{"]
        if it is None:
            return head + self.stmts(s.body, ind + 1) + [f"{pad}}}"]
        # `foreach (v : a[i])`: v is the ELEMENT (20.7.8), as C binds it.
        elem = getattr(dtype, "element_type", None)
        if elem is None or self.types.of_datatype(elem) is None or \
                assigns_local(s.body, it.name):
            raise Refusal(
                f"cannot lower `foreach ({it.name} : {coll})`: "
                + ("its body assigns the iterator, which aliases the element "
                   "(LRM 20.7.8 c); assign the element by index"
                   if elem is not None else
                   "the iterator form is lowered over an array of data only; "
                   "use the index form"))
        names = {it.name: self.types.of_datatype(elem)}
        if index is not None:
            names[index.name] = INT
        saved = {k: self.types.loop_vars.get(k) for k in names}
        self.types.loop_vars.update(names)
        try:
            body = self.stmts(s.body, ind + 1)
        finally:
            for k, v in saved.items():
                if v is None:
                    self.types.loop_vars.pop(k, None)
                else:
                    self.types.loop_vars[k] = v
        bind = f"{pad}    {cpp_type(elem)} {mangle(it.name)} = {coll}[{idx}];"
        return head + [bind] + body + [f"{pad}}}"]

    def _iter_dtype(self, s):
        """Declared type of a `foreach` collection: a field, a local, an
        element of either."""
        t = self.types.type_of(s.iter)
        return t.dtype if t is not None and t.kind == "array" else None

    def _match(self, s, ind: int) -> List[str]:
        """PSS `match` -> `switch`, with every arm breaking.

        PSS arms do not fall through, so omitting the break would silently
        change the model's meaning into C++'s.
        """
        pad = "    " * ind
        if match_needs_chain(s):
            unmatched = [ln.strip() for ln in self._default_arm("")[1:-1]]
            return self._match_chain(s, ind, unmatched)
        lines = [f"{pad}switch ({self.expr(s.subject)}) {{"]
        saw_default = False
        for case in s.cases:
            labels = self._pattern_labels(case.pattern)
            if labels:
                for lb in labels:
                    lines.append(f"{pad}case {lb}:")
            else:
                saw_default = True
                lines.append(f"{pad}default:")
            lines += self.stmts(case.body, ind + 1)
            lines.append(f"{pad}    break;")
        if not saw_default:
            lines += self._default_arm(pad)
        lines.append(f"{pad}}}")
        return lines

    def _match_chain(self, s, ind: int, unmatched) -> List[str]:
        """A `match` as an `if` chain (`match_needs_chain`): a range is two
        comparisons, and a `break` in an arm leaves the enclosing loop, as
        PSS means. The subject is read once per arm, so one containing a
        call is first held in a local. ``unmatched`` is what no arm and no
        `default` matching does (the `switch` form's default arm)."""
        import zuspec.ir.core as ir
        pad = "    " * ind
        lines: List[str] = []
        subj, inner = s.subject, ind
        t = self.types.type_of(subj)
        held = None
        if _has_call(subj):
            if t is None or t.kind not in ("int", "enum", "bool"):
                raise ValueError(
                    f"in '{getattr(self.fn, 'name', '?')}': a `match` on a "
                    f"call's value needs its subject held in a local")
            held = f"_pssc_m{self._next_match()}"
            ct = "int64_t" if t.as_int().signed else "uint64_t"
            lines += [f"{pad}{{",
                      f"{pad}    {ct} {held} = {self.expr(subj)};"]
            subj, inner = ir.ExprRefLocal(name=held), ind + 1
            self.types.loop_vars[held] = t
        ipad = "    " * inner
        kw, default = "if", None
        for case in s.cases:
            vals = match_values(case.pattern)
            if not vals:
                default = case
                continue
            test = self.expr(match_arm_test(subj, vals))
            lines.append(f"{ipad}{kw} ({test}) {{")
            lines += self.stmts(case.body, inner + 1)
            kw = "} else if"
        tail = (self.stmts(default.body, inner + 1) if default is not None
                else ["    " * (inner + 1) + ln for ln in unmatched])
        if kw == "if":                      # only a default arm
            lines += ["    " * inner + ln.strip() for ln in tail]
        else:
            if tail:
                lines.append(f"{ipad}}} else {{")
                lines += tail
            lines.append(f"{ipad}}}")
        if held is not None:
            self.types.loop_vars.pop(held, None)
            lines.append(f"{pad}}}")
        return lines

    def _next_match(self) -> int:
        self._match_k = getattr(self, "_match_k", -1) + 1
        return self._match_k

    def _default_arm(self, pad: str) -> List[str]:
        """What an unmatched subject does when the model states no default.

        A `switch` with no default is legal C++ that silently does nothing, and
        PSS makes an unmatched `match` an error (3.1 §22.7.9) -- so saying
        nothing is available but is not the default.
        """
        if self.match_default == "none":
            return []
        out = [f"{pad}default:"]
        if self.match_default == "message" and self.message_style != "none":
            out.append(f'{pad}    pssc::message("error: {self.cls}: unmatched '
                       f'match subject in {self.fn.name}");')
        elif self.match_default == "unreachable":
            out.append(f"{pad}    PSSC_UNREACHABLE();")
        out.append(f"{pad}    break;")
        return out

    def _pattern_labels(self, pattern) -> List[str]:
        """The arm's labels, rendered; none for `default` (`match_values`)."""
        return [self.expr(v) for v in match_values(pattern)]

    def _yield(self, s, ind: int) -> List[str]:
        """`yield` -- the polling wait primitive.

        Lowered, not rejected, and by default to NOTHING: `yield` is a hint to
        a scheduler and this target generates none, so the surrounding loop
        becomes a tight poll -- which is what a model reaching `yield` on this
        profile asked for, having been told it has no event to wait on.
        """
        pad = "    " * ind
        if self.yield_mode == "import":
            return [f"{pad}this->{_IMP}.yield_();"]
        return [f"{pad}// yield: nothing to yield to on this target"]


def _reject_upward_ref(comp, expr) -> None:
    raise ValueError(
        f"component '{getattr(comp, 'name', '?')}' refers UPWARD to its parent "
        f"({_dt_name(expr)}). The C++ lowering holds sub-components by value "
        f"and emits no parent back-pointer, so there is nothing for this to "
        f"resolve to. Move the shared state down, or pass it as an argument.")


class _CtorEmitter(_BodyEmitter):
    """The `initialize` body: ordinary procedural code, plus the three forms
    that only ever appear in a constructor.

    Register-group binding and offset folding have no runtime representation --
    an offset becomes a constant and a binding becomes a rebind of the group
    member -- and a sub-component's constructor is reached through its member.
    """

    def _group_call(self, call) -> Optional[str]:
        """A call on a register GROUP rather than on a register inside one."""
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.reg_groups:
            return None
        field = chain[0][0]
        group = self.reg_groups[field]
        m = func.attr
        args = call.args
        if m == "set_handle":
            # Binding the group IS constructing it at that handle: every
            # register inside folds its own offset from the base.
            gt = _strip_pkg(group.name)
            return (f"{self._self_member(field)} = "
                    f"{gt}(this->{_IMP}, {self.expr(args[0])})")
        folded = self._offset_fold(call)
        if folded is not None:
            return folded
        raise ValueError(
            f"unsupported register-group method '{m}'. The C++ target folds "
            f"group offsets at generation time; a method it does not know "
            f"would become a call on a group class that does not declare it.")

    def _sub_ctor_call(self, call) -> Optional[str]:
        """`ch[i].initialize(...)` -> the sub-component member's `initialize`."""
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.subs:
            return None
        sub = self.subs[chain[0][0]]
        if func_kind_name(func.attr,
                          self.ctor_names) is not FuncKind.CONSTRUCTOR:
            return None         # an ordinary operation: `_sub_op_call` has it
        args = ", ".join(self._call_args(call))
        return f"{self.expr(func.value)}.initialize({args})"

    def expr(self, e) -> str:
        if _dt_name(e) == "ExprCall":
            for handler in (self._group_call, self._sub_ctor_call):
                out = handler(e)
                if out is not None:
                    return out
        return super().expr(e)


# --- declarations -----------------------------------------------------------

def _params(fn) -> str:
    """A by-handle struct parameter (LRM 20.3.2; `by_handle_params`) is a
    reference, so the callee's writes are the caller's; a `const` one is a
    value."""
    handle = by_handle_params(fn)
    return ", ".join(f"{cpp_type(a.annotation)} "
                     f"{'&' if a.arg in handle else ''}{mangle(a.arg)}"
                     for a in fn.args.args)


def _ret(fn) -> str:
    return cpp_type(fn.returns) if fn.returns is not None else "void"


def _iface(names, dtype) -> str:
    return f"{names[dtype]}_if"


def emit_export_api(node, names, ctor_names=None, model=None) -> str:
    """The pure-virtual operation interface for one component.

    Sub-component access is ON THE INTERFACE, returning the child's interface:
    the root factory hands back a `<Cls>_if`, so anything not reachable through
    it is not reachable at all.
    """
    comp = node.dtype
    cls = names[comp]
    ops, subs, head = (_operations(comp, ctor_names), sub_components(comp),
                       f"struct {cls}_if {{")
    if model is not None and ci.in_hierarchy(model.ctx, comp):
        # Its own operations and sub-components; its base interface's by
        # inheritance -- VIRTUAL, as the class derives from both.
        base, fields, fns = _declared(model, comp)
        ops = [f for f in fns if func_kind(f, ctor_names) == FuncKind.EXPORT_OP]
        subs = sub_components(_view(fields))
        if base is not None:
            head = f"struct {cls}_if : public virtual {_iface(names, base)} {{"
    lines = doc_block(getattr(comp, "doc", None), "", LINE)
    lines.append(head)
    lines.append(f"    virtual ~{cls}_if() = default;")
    for fn in ops:
        blank_line(lines)
        lines += doc_block(getattr(fn, "doc", None), "    ", LINE)
        lines.append(f"    virtual {_ret(fn)} {mangle(fn.name)}({_params(fn)}) = 0;")
    for sub in subs:
        blank_line(lines)
        sub_if = _iface(names, sub.dtype)
        if sub.is_array:
            if sub.size is None or sub.size < 0:
                raise ValueError(
                    f"sub-component array '{sub.name}' has no folded size; the "
                    f"generated accessor needs a bound.")
            lines.append(f"    static constexpr std::size_t "
                         f"{mangle(sub.name)}_count = {sub.size};")
            lines.append(f"    virtual {sub_if} &{mangle(sub.name)}"
                         f"(std::size_t i) = 0;")
        else:
            lines.append(f"    virtual {sub_if} &{mangle(sub.name)}() = 0;")
    lines.append("};")
    return "\n".join(lines)


def emit_import_api(imports, ns: str, *, yield_mode: str = "none") -> str:
    """What the PLATFORM supplies: memory access, plus the model's imports.

    ONE object rather than two. `pssc::mem_if` is the fixed part -- identical
    for every model -- and an `import target function void plat_delay_us(int)`
    is by definition whatever this model declared. Extending the seam rather
    than adding a second parameter means the platform implements one class and
    a generated body reaches everything it needs through one reference.

    Every DECLARED import gets a pure virtual, not only every called one: the
    set is the platform's contract, and a platform implementing one function
    too many pays nothing while one discovering a requirement later pays a
    rebuild. Same position as the C backend's prototypes and the SV
    projection's `import_api_if`.

    `is_solve` vs `is_target` does not survive, and correctly: the distinction
    is about when a function runs relative to solving, and this target has no
    solver (HAVE_RUNTIME_SOLVER=false).

    A MODEL WITH NO IMPORTS GETS AN ALIAS, not an empty struct that derives from
    `pssc::mem_if`. The name is what every generated signature says, so it has
    to exist either way -- but an empty derived class would mean an existing
    `mem_if` implementation (including the shipped `pssc::mmio_mem`) could not
    be passed without being re-wrapped, to gain nothing. The alias says the true
    thing: for this model, what the platform must supply IS memory access.

    `--yield import` ADDS `yield_()` here, because that mode lowers a PSS
    `yield` to a call on the platform -- and everything the generated code
    calls of the platform has to be declared where the platform can see it. The
    SV projection puts it on `import_api_if` for the same reason.
    """
    extra = []
    if yield_mode == "import":
        extra.append("    // `--yield import`: what a PSS `yield` costs on this"
                     " platform (a WFI,")
        extra.append("    // a watchdog kick, a delay). See the C target's"
                     " --yield.")
        extra.append("    virtual void yield_() = 0;")

    lines = ["// ----- Supplied by the PLATFORM: memory access"
             + (" + the model's imports. -----" if (imports or extra)
                else ". -----")]
    if not imports and not extra:
        lines.append(f"using {ns}_import_if = pssc::mem_if;"
                     f"   // this model declares no imports")
        return "\n".join(lines)
    lines.append(f"struct {ns}_import_if : pssc::mem_if {{")
    for name in sorted(imports):
        fn = imports[name]
        lines.append(f"    virtual {_ret(fn)} {mangle(name)}({_params(fn)}) = 0;")
    lines += extra
    lines.append("};")
    return "\n".join(lines)


# --- the class --------------------------------------------------------------

def _chan_member(f) -> str:
    """One channel member, after checking the backend can implement it. An
    array of channels is a `std::array` of them (E10)."""
    from ..progseq_model import channel_count, channel_dtype
    dt = channel_dtype(f)
    depth = int(getattr(dt, "depth", 1) or 1)
    if depth != 1:
        raise Refusal(
            f"channel '{f.name}' has depth {depth}; the C++ target implements "
            f"only depth-1 channels (share/cpp/pssc_chan.hpp). A deeper channel "
            f"is a ring buffer, which nothing in scope declares.", f)
    elem = getattr(dt, "element_type", None)
    if elem is None:
        raise ValueError(f"channel '{f.name}' has no element type")
    t = f"pssc::chan1<{cpp_type(elem)}>"
    n = channel_count(f)
    if n is not None:
        t = f"std::array<{t}, {n}>"
    return f"{t} {mangle(f.name)};"


def _data_member(f) -> str:
    """One plain data member, value-initialised.

    An array becomes `std::array`, which unlike C's `T x[N]` is a value type: it
    can be assigned, returned and default-initialised like everything else here.
    """
    dt = f.datatype
    if _dt_name(dt) == _DT_ARRAY:
        if _array_size(dt) is None:
            raise ValueError(
                f"array member '{f.name}' has no folded size; the generated "
                f"member needs a bound.")
        return f"{cpp_type(dt)} {mangle(f.name)}{_array_init(dt)};"
    from .lower_api_types import cpp_first_item
    first = cpp_first_item(dt)
    if first is not None:
        # An enum starts at its first item (7.5), which need not be 0.
        return f"{cpp_type(dt)} {mangle(f.name)} = {first};"
    return f"{cpp_type(dt)} {mangle(f.name)}{{}};"


def _binds_handle(ctor, field: str) -> bool:
    """Does the constructor body call `<field>.set_handle(...)` itself?

    Asked so the generator's DEFAULT binding is emitted only where the model
    states none. Both bindings are needed in general -- an empty constructor
    leaves the binding to the generator, while the WB DMA model states it -- but
    emitting both where the model already bound the group produces the same
    register group constructed twice, once with the wrong base.
    """
    import dataclasses as dc
    if ctor is None:
        return False
    found = False

    def walk(n):
        nonlocal found
        if isinstance(n, (list, tuple)):
            for x in n:
                walk(x)
            return
        if not dc.is_dataclass(n) or found:
            return
        if _dt_name(n) == "ExprCall":
            f = n.func
            if _dt_name(f) == "ExprAttribute" and f.attr == "set_handle" and \
                    _dt_name(f.value) == "ExprAttribute" and \
                    f.value.attr == field:
                found = True
                return
        for fl in dc.fields(n):
            walk(getattr(n, fl.name, None))

    walk(ctor.body)
    return found


#: The function a field default is rendered in: none of the component's own,
#: since a default belongs to construction, not to a PSS function.
_DEFAULTS_FN = ir.Function(name="<field defaults>", args=ir.Arguments(args=[]),
                           body=[], returns=None)


def _field_defaults(comp, emitter, pad: str) -> List[str]:
    """PSS field initializers, as assignments in the class constructor.

    A default is part of a field's MEANING: `wb_dma_ch_caps_s` declares every
    capability true, and a model that reads back all-false silently refuses the
    operations those capabilities gate -- a driver reporting the device cannot
    do things it can.

    Only the field's OWN initializer: a struct-typed field's defaults are its
    type's default member initializers (`lower_api_types.emit_struct`), which
    every value of that type gets -- a local or an array element as well.
    """
    out: List[str] = []
    for f in data_members(comp):
        iv = getattr(f, "initial_value", None)
        if is_literal(iv):
            out.append(f"{pad}this->{mangle(f.name)} = "
                       f"{emitter._literal(iv, f.datatype)};")
        elif iv is not None and _dt_name(f.datatype) == _DT_ENUM:
            # An item reaches the IR as its value; C++ names it.
            out.append(f"{pad}this->{mangle(f.name)} = "
                       f"{emitter._coerce(iv, f.datatype)};")
        elif iv is not None:
            out.append(f"{pad}this->{mangle(f.name)} = {emitter.expr(iv)};")
    return out


def _addr_arg(ctor) -> Optional[str]:
    """The constructor's first address-handle argument, or ``None``.

    The FIRST ADDRESS-TYPED one, not `args[0]`: `initialize(int id,
    addr_handle_t bank)` would otherwise bind the register base to the channel
    number.
    """
    if ctor is None:
        return None
    for a in ctor.args.args:
        if cpp_type(a.annotation) == "pssc::addr_t":
            return mangle(a.arg)
    return None


def _sub_array_maker(sub, names, ns: str) -> List[str]:
    """Build a `std::array` of sub-components, all sharing the seam.

    `std::array` has no fill constructor for a non-default-constructible
    element, and a sub-component takes the seam -- so the pack expansion is how
    N copies get built in the member initialiser list, exactly as the register
    model builds its arrays.
    """
    t = names[sub.dtype]
    member = _sub_member(sub)
    arr = f"std::array<{t}, {sub.size}>"
    return [
        f"    template <std::size_t... I>",
        f"    static {arr} make_{member}impl({ns}_import_if &imp, "
        f"std::index_sequence<I...>) {{",
        f"        return {{ ((void)I, {t}(imp))... }};",
        f"    }}",
        f"    static {arr} make_{member}({ns}_import_if &imp) {{",
        f"        return make_{member}impl(imp, "
        f"std::make_index_sequence<{sub.size}>{{}});",
        f"    }}",
    ]


def emit_component(node, names, ns: str, *, imports=None, is_root: bool,
                   parents=(), ctor_names=None, model=None, **be_kw) -> str:
    """One component class: state, lifecycle, operations, sub-component access."""
    if model is not None and ci.in_hierarchy(model.ctx, node.dtype):
        return emit_hier_component(node, names, ns, model, imports=imports,
                                   is_root=is_root, parents=parents,
                                   ctor_names=ctor_names, **be_kw)
    be_kw = dict(be_kw, imports=imports, ctor_names=ctor_names,
                 ctx=getattr(model, "ctx", None),
                 value_structs=getattr(model, "value_structs", ()))
    comp = node.dtype
    cls = names[comp]
    ctor = _ctor(comp, ctor_names)
    subs = sub_components(comp)
    groups = _reg_group_fields(comp)

    lines: List[str] = [f"class {cls} : public {cls}_if {{"]

    # PSS component state is reachable ALONG THE HIERARCHY -- a parent may read
    # a child's attributes and post to its channels, which is what
    # `notify_irq()` does in the WB DMA model. C++ makes a class member private,
    # so the enclosing component is named as a friend. Stated for every parent
    # whether or not it currently reaches in: it is a property of the model's
    # structure, not of today's operation bodies.
    for parent in parents:
        lines.append(f"    friend class {names[parent]};")

    # -- state ---------------------------------------------------------------
    lines.append(f"    {ns}_import_if &{_IMP};")
    for f in data_members(comp):
        lines += comment_lines(getattr(f, "doc", None), "    ", LINE)
        lines.append(f"    {_data_member(f)}")
    for f in channel_fields(comp):
        lines.append(f"    {_chan_member(f)}")
    for name, g in groups.items():
        lines.append(f"    {_strip_pkg(g.name)} {mangle(name)};")
    for sub in subs:
        t = names[sub.dtype]
        if sub.is_array:
            lines.append(f"    std::array<{t}, {sub.size}> {_sub_member(sub)};")
        else:
            lines.append(f"    {t} {_sub_member(sub)};")
    makers = [ln for sub in subs if sub.is_array
              for ln in _sub_array_maker(sub, names, ns)]
    if makers:
        lines.append("")
        lines += makers

    # -- construction --------------------------------------------------------
    inits = [f"{_IMP}(imp)"]
    for name, g in groups.items():
        # Bound at 0 until `initialize` says otherwise. Not a valid address and
        # not meant to be: an operation called before `initialize` should fault
        # at address 0 rather than quietly read somewhere plausible.
        inits.append(f"{mangle(name)}({_strip_pkg(g.name)}(imp, 0))")
    for sub in subs:
        inits.append(f"{_sub_member(sub)}(make_{_sub_member(sub)}(imp))"
                     if sub.is_array else
                     f"{_sub_member(sub)}({names[sub.dtype]}(imp))")

    lines.append("")
    lines.append("public:")
    lines.append(f"    explicit {cls}({ns}_import_if &imp)")
    # Field defaults are part of CONSTRUCTION, as in C and Python: every
    # instance has them, whether or not anything calls its `initialize`. They
    # were emitted only into `initialize`, and only when the component had a
    # PSS constructor -- so `int a = 5;` in a component without one, or in a
    # sub-component whose parent never calls its `initialize`, was 0.
    defaults = _field_defaults(
        comp, _CtorEmitter(_DEFAULTS_FN, comp, names, cls=cls, **be_kw),
        "        ")
    if defaults:
        lines.append("      : " + ", ".join(inits) + " {")
        lines += defaults
        lines.append("    }")
    else:
        lines.append("      : " + ", ".join(inits) + " {}")
    lines.append("")

    # -- initialize (the PSS constructor) ------------------------------------
    lines += _emit_initialize(comp, cls, names, ctor, groups, **be_kw)

    # -- operations ----------------------------------------------------------
    for fn in _operations(comp, ctor_names):
        be = _BodyEmitter(fn, comp, names, cls=cls, **be_kw)
        lines.append("")
        lines += doc_block(getattr(fn, "doc", None), "    ", LINE)
        lines.append(f"    {_ret(fn)} {mangle(fn.name)}({_params(fn)}) override {{")
        lines += be.stmts(fn.body, 2)
        lines.append("    }")
    for fn in _solve_functions(comp, ctor_names):
        lines += _solve_member(fn, _CtorEmitter(fn, comp, names, cls=cls,
                                                **be_kw), cls)

    # -- sub-component access ------------------------------------------------
    for sub in subs:
        sub_if = _iface(names, sub.dtype)
        member = _sub_member(sub)
        lines.append("")
        if sub.is_array:
            lines.append(f"    {sub_if} &{mangle(sub.name)}(std::size_t i) "
                         f"override {{ return {member}[i]; }}")
        else:
            lines.append(f"    {sub_if} &{mangle(sub.name)}() override "
                         f"{{ return {member}; }}")

    # -- factory (root only) -------------------------------------------------
    if is_root:
        lines.append("")
        lines += _emit_factory(cls, ns, ctor)

    lines.append("};")
    return "\n".join(lines)


def _native(be, base, names):
    """Configure *be* for a body of a class derived from *base*'s."""
    if base is not None:
        be.super_base = base
        be.super_cls = names[base]
    return be


def emit_hier_component(node, names, ns: str, model, *, imports=None,
                        is_root: bool, parents=(), ctor_names=None,
                        **be_kw) -> str:
    """A component in an inheritance hierarchy, as a C++ class derived from
    its base component's class (LRM 17.1, Table 27).

    It declares only what the component DECLARES; the rest is its base's.
    Functions are virtual (every operation is, through the interfaces), and
    `super.f(...)` is `base::f(...)`. A field a derived class declares again
    hides the base's and does not replace it -- 17.1's rule and C++'s. State
    is `protected` so a derived class's code reaches what it inherits. Both
    interfaces are derived from VIRTUALLY: the class implements its base's
    interface through its base class, and its own, which extends that one.
    """
    be_kw = dict(be_kw, imports=imports, ctor_names=ctor_names,
                 ctx=getattr(model, "ctx", None),
                 value_structs=getattr(model, "value_structs", ()))
    comp = node.dtype
    cls = names[comp]
    base, own_fields, own_fns = _declared(model, comp)
    view = _view(own_fields)
    subs = sub_components(view)
    groups = _reg_group_fields(view)
    head = (f"class {cls} : public {names[base]}, public virtual {cls}_if {{"
            if base is not None else f"class {cls} : public virtual {cls}_if {{")
    lines: List[str] = [head]
    for parent in parents:
        lines.append(f"    friend class {names[parent]};")
    lines.append("protected:")

    # -- state: its own ------------------------------------------------------
    if base is None:
        lines.append(f"    {ns}_import_if &{_IMP};")
    for f in data_members(view):
        lines += comment_lines(getattr(f, "doc", None), "    ", LINE)
        lines.append(f"    {_data_member(f)}")
    for f in channel_fields(view):
        lines.append(f"    {_chan_member(f)}")
    for name, g in groups.items():
        lines.append(f"    {_strip_pkg(g.name)} {mangle(name)};")
    for sub in subs:
        t = names[sub.dtype]
        if sub.is_array:
            lines.append(f"    std::array<{t}, {sub.size}> {_sub_member(sub)};")
        else:
            lines.append(f"    {t} {_sub_member(sub)};")
    makers = [ln for sub in subs if sub.is_array
              for ln in _sub_array_maker(sub, names, ns)]
    if makers:
        lines.append("")
        lines += makers

    # -- construction: the base's first --------------------------------------
    inits = [f"{names[base]}(imp)" if base is not None else f"{_IMP}(imp)"]
    for name, g in groups.items():
        inits.append(f"{mangle(name)}({_strip_pkg(g.name)}(imp, 0))")
    for sub in subs:
        inits.append(f"{_sub_member(sub)}(make_{_sub_member(sub)}(imp))"
                     if sub.is_array else
                     f"{_sub_member(sub)}({names[sub.dtype]}(imp))")
    lines += ["", "public:", f"    explicit {cls}({ns}_import_if &imp)"]
    defaults = _field_defaults(
        view, _native(_CtorEmitter(_DEFAULTS_FN, comp, names, cls=cls,
                                   **be_kw), base, names), "        ")
    if defaults:
        lines.append("      : " + ", ".join(inits) + " {")
        lines += defaults
        lines.append("    }")
    else:
        lines.append("      : " + ", ".join(inits) + " {}")

    # -- initialize: its own, or its base's plus its own register groups -----
    own_ctor = next((f for f in own_fns
                     if func_kind(f, ctor_names) == FuncKind.CONSTRUCTOR), None)
    if base is None or own_ctor is not None:
        # Every register group it has, inherited ones included, is bound by
        # default here: a constructor that shadows the base's does not run it.
        lines.append("")
        lines += _emit_initialize(comp, cls, names, own_ctor,
                                  _reg_group_fields(comp), native=base,
                                  **be_kw)
    elif groups:
        lines.append("")
        eff = _ctor(comp, ctor_names)
        params = _params(eff) if eff is not None else ""
        fwd = ", ".join(mangle(a.arg) for a in (eff.args.args if eff else []))
        addr = _addr_arg(eff) or "0"
        lines += [
            "    // The PSS constructor is the base's; this class binds its "
            "own register",
            "    // groups after it.",
            f"    void initialize({params}) {{",
            f"        {names[base]}::initialize({fwd});"]
        for name, g in groups.items():
            lines.append(f"        this->{mangle(name)} = "
                         f"{_strip_pkg(g.name)}(this->{_IMP}, {addr});")
        lines.append("    }")

    # -- operations: those it declares ---------------------------------------
    for fn in own_fns:
        if func_kind(fn, ctor_names) != FuncKind.EXPORT_OP:
            continue
        be = _native(_BodyEmitter(fn, comp, names, cls=cls, **be_kw),
                     base, names)
        lines.append("")
        lines += doc_block(getattr(fn, "doc", None), "    ", LINE)
        lines.append(f"    {_ret(fn)} {mangle(fn.name)}({_params(fn)}) override {{")
        lines += be.stmts(fn.body, 2)
        lines.append("    }")
    for fn in own_fns:
        if func_kind(fn, ctor_names) is FuncKind.EXPORT_SOLVE:
            lines += _solve_member(
                fn, _native(_CtorEmitter(fn, comp, names, cls=cls, **be_kw),
                            base, names), cls)

    # -- sub-component access: its own ---------------------------------------
    for sub in subs:
        sub_if = _iface(names, sub.dtype)
        member = _sub_member(sub)
        lines.append("")
        if sub.is_array:
            lines.append(f"    {sub_if} &{mangle(sub.name)}(std::size_t i) "
                         f"override {{ return {member}[i]; }}")
        else:
            lines.append(f"    {sub_if} &{mangle(sub.name)}() override "
                         f"{{ return {member}; }}")

    if is_root:
        lines.append("")
        lines += _emit_factory(cls, ns, _ctor(comp, ctor_names))
    lines.append("};")
    return "\n".join(lines)


def _emit_initialize(comp, cls, names, ctor, groups, native=None,
                     **be_kw) -> List[str]:
    """`initialize(...)`: the PSS constructor body, plus what precedes it.

    The default register-group binding comes FIRST and the body may override it
    with its own `set_handle`. Both exist because models legitimately do it
    both ways: `src/pssc/testing/models` declares an EMPTY constructor
    and says the binding is the generator's job, while the WB DMA model states
    it. Emitting only one of the two breaks a working model either way.
    """
    params = _params(ctor) if ctor is not None else ""
    lines = ["    // The PSS constructor. Called by `create()`; call it "
             "yourself if you",
             "    // constructed this object directly.",
             f"    void initialize({params}) {{"]
    body: List[str] = []

    addr = _addr_arg(ctor)
    for name, g in groups.items():
        if _binds_handle(ctor, name):
            continue        # the model binds this one itself, below
        gt = _strip_pkg(g.name)
        bind = addr if addr is not None else "0"
        # Nothing to bind to leaves the group at 0. That is the honest answer:
        # every register then offsets from 0, which is visibly wrong in a trace
        # rather than quietly wrong.
        body.append(f"        this->{mangle(name)} = {gt}(this->{_IMP}, {bind});")

    be = (_native(_CtorEmitter(ctor, comp, names, cls=cls, **be_kw), native,
                  names) if ctor is not None else None)
    if be is not None:
        body += be.stmts(ctor.body, 2)
    if not body:
        body = ["        // The model's constructor is empty and this "
                "component has no register groups."]
    lines += body
    lines.append("    }")
    return lines


def _emit_factory(cls: str, ns: str, ctor) -> List[str]:
    """`create()` -- construct and initialize in one call, for the root.

    Returns the INTERFACE. A caller that wants the concrete class can construct
    one directly; what `create` is for is handing back the API surface without
    the caller naming the implementation.
    """
    params = _params(ctor) if ctor is not None else ""
    # `_imp` and `_self`, as C names its handle: a root constructor parameter
    # `imp` or `self` was declared twice. `mangle` renames a model's own.
    all_params = f"{ns}_import_if &_imp" + (f", {params}" if params else "")
    fwd = ", ".join(mangle(a.arg) for a in (ctor.args.args if ctor else []))
    return [
        f"    static std::unique_ptr<{cls}_if> create({all_params}) {{",
        f"        auto _self = std::make_unique<{cls}>(_imp);",
        f"        _self->initialize({fwd});",
        f"        return _self;",
        f"    }}",
    ]


def _class_nodes(model) -> List[object]:
    """A node per class: children first, and each base class before the
    classes derived from it -- including bases nothing instantiates
    (`OpModel.classes`)."""
    import types
    order = post_order(model)
    classes = getattr(model, "classes", None)
    if not classes or not any(ci.in_hierarchy(model.ctx, c) for c in classes):
        return order        # nothing derives: exactly the tree, as always
    by_type = {}
    for n in order:
        by_type.setdefault(id(n.dtype), n)
    return [by_type.get(id(c)) or types.SimpleNamespace(dtype=c)
            for c in classes]


def lower_components(model, names, ns: str, *, imports=None, **be_kw) -> str:
    """Every component's interface and class.

    ALL interfaces first, then the classes, and the classes CHILDREN FIRST. A
    parent's interface names its children's interfaces, and a parent's class
    holds its children by value -- so each needs the other side complete, and
    only splitting the two into separate passes satisfies both.
    """
    nodes = _class_nodes(model)
    parts: List[str] = ["// ----- Export API. -----"]
    for node in nodes:
        parts.append(emit_export_api(node, names, model.ctor_names, model))
        parts.append("")
    parts.append("// ----- Components. -----")
    for node in nodes:
        parts.append(emit_component(node, names, ns, imports=imports,
                                    is_root=model.is_root(node.dtype),
                                    parents=_parent_types(model, node.dtype),
                                    ctor_names=model.ctor_names, model=model,
                                    **be_kw))
        parts.append("")
    return "\n".join(parts).rstrip()
