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

from ..comments import LINE, append_trailing, blank_line, comment_lines, doc_block
from ..progseq_model import (
    func_kind, FuncKind, field_is_reg_group, _dt_name, channel_fields,
    sub_components, array_base_stride, scalar_offset,
)
from ..c.lower_progseq import (
    Prefixes, regular_nodes, post_order, c_string_literal, _array_size,
    _builtin_name, _str_const, func_kind_name, parse_prefix_map,
)
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
    "message", "print", "make_handle_from_handle", "addr_value",
}) | _MEM_PRIMS


def mangle(name: str) -> str:
    return name + "_" if name in _CPP_KEYWORDS else name


def parse_class_map(spec) -> Dict[str, str]:
    """``["wb_dma_ch_c=channel"]`` -> ``{"wb_dma_ch_c": "channel"}``.

    The C backend's `--prefix-map`, under the name it has here. Same parser:
    the option means the same thing in both languages.
    """
    return parse_prefix_map(spec)


def class_names(model, root_class: str, overrides=None) -> Prefixes:
    """Per-component-type class name, with the same collision rules as C.

    Shared with the C backend because it is the same question -- two component
    types that collide after `_c`-stripping would have their operations emitted
    under one name -- and one answer is better than two that can disagree.
    """
    return Prefixes(model, root_class, overrides, language="C++")


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
    raise ValueError(f"unsupported C++ type for {cn}")


# --- component introspection ------------------------------------------------

# `ctor_names` comes down from the model, never from the ambient ContextVar
# (P6a.T5): which solve function is the constructor is the compile's answer,
# and an emitter that asks the process gets whichever compile set it last.

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


class _BodyEmitter:
    """Translate one operation body to C++ lines."""

    def __init__(self, fn, comp, names, *, imports=None,
                 yield_mode: str = "none", match_default: str = "message",
                 message_style: str = "import", cls: str = "",
                 ctor_names=None):
        self.fn = fn
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

    # -- enum coercion ------------------------------------------------------
    #
    # PSS ENUMERATORS DO NOT SURVIVE INTO THE IR. `return PENDING` arrives here
    # as `ExprConstant(2)`, its name and its type both folded away. C converts
    # int to enum implicitly, so the C backend never had to notice; C++ does not,
    # so an uncoerced `return 2;` from a function returning an enum is a hard
    # compile error -- which is how this was found, and is the good case.
    #
    # The recovery is to look the value back up in the enum's own table and emit
    # the NAME, which restores what the model wrote. A value with no matching
    # enumerator (arithmetic on an encoding, say) falls back to a `static_cast`,
    # which states the conversion rather than performing it silently.

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
        return None

    def _coerce(self, e, dtype) -> str:
        """Render ``e`` where a value of ``dtype`` is expected."""
        s = self.expr(e)
        if dtype is None or _dt_name(dtype) != _DT_ENUM:
            return s
        actual = self._expr_type(e)
        if actual is not None and _dt_name(actual) == _DT_ENUM and \
                _strip_pkg(getattr(actual, "name", "")) == \
                _strip_pkg(getattr(dtype, "name", "")):
            return s
        if _dt_name(e) == "ExprConstant" and isinstance(e.value, int) and \
                not isinstance(e.value, bool):
            for name, value in (getattr(dtype, "items", None) or {}).items():
                if value == e.value:
                    return name
        from .lower_api_types import cpp_enum_name
        return f"static_cast<{cpp_enum_name(dtype)}>({s})"

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
        chain = self._chain(func.value)
        if chain and len(chain) == 1 and chain[0][0] in self.subs:
            return _fn_named(self.subs[chain[0][0]].dtype, func.attr)
        return None

    def _call_args(self, call) -> List[str]:
        """A call's arguments, each rendered in its parameter's type."""
        fn = self._callee(call)
        params = list(fn.args.args) if (fn is not None and fn.args) else []
        out = []
        for i, a in enumerate(call.args):
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
        # A register lives inside a group, so its path is at least
        # `<group>.<reg>`. A one-element chain is a call on the GROUP itself
        # (`regs.set_handle(...)`), which is not a register access.
        if not chain or len(chain) < 2 or chain[0][0] not in self.reg_groups:
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
        args = ", ".join(self.expr(a) for a in call.args)
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
            return (self._self_member(chain[0][0])
                    if chain[0][0] in self.chan_fields else None)
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
            return f"({self.expr(args[0])} + {self.expr(args[1])})"
        if name == "addr_value":
            return self.expr(args[0])
        if name in _MEM_PRIMS:
            rendered = ", ".join(self.expr(a) for a in args)
            return f"this->{_IMP}.{name}({rendered})"
        if name in ("message", "print"):
            return self._message_call(name, args)
        raise ValueError(f"built-in '{name}' is claimed by the C++ target but "
                         f"has no rendering; see targets/cpp/lower_progseq.py")

    def _message_call(self, name: str, args) -> str:
        if self.message_style == "none":
            return "(void)0"
        # message(verbosity, fmt, args...) -- the verbosity has no C++ analogue
        # and is dropped, exactly as the SV and C projections drop it.
        rest = args[1:] if (name == "message" and len(args) >= 2) else args
        rendered = ", ".join(self.expr(a) for a in rest)
        return f"pssc::message({rendered})"

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
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.subs:
            return None
        args = ", ".join(self._call_args(call))
        return f"{self.expr(func.value)}.{mangle(func.attr)}({args})"

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
        """`status != PENDING` -- with the enumerator, not with `2`.

        The IR folded the enumerator to an integer, so the naive rendering is
        `status != 2`, which compiles (an unscoped enum promotes to int) and
        says nothing. Where one side of a comparison is enum-typed, the other
        is rendered in that type, which recovers the name the model wrote.
        """
        lhs, rhs = e.lhs, e.rhs
        if e.op.name in self._COMPARISONS:
            lt, rt = self._expr_type(lhs), self._expr_type(rhs)
            lt_is = lt is not None and _dt_name(lt) == _DT_ENUM
            rt_is = rt is not None and _dt_name(rt) == _DT_ENUM
            if lt_is and not rt_is:
                return f"{self._operand(lhs)} {op} {self._coerce(rhs, lt)}"
            if rt_is and not lt_is:
                return f"{self._coerce(lhs, rt)} {op} {self._operand(rhs)}"
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
        if cn == "ExprConstant":
            v = e.value
            if isinstance(v, bool):
                return "true" if v else "false"
            if isinstance(v, int):
                return str(v)
            if isinstance(v, str):
                return c_string_literal(v)
            raise ValueError(f"unsupported constant of type {type(v).__name__}")
        if cn == "ExprRefLocal":
            return self.arg_rename.get(e.name, e.name)
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
            return f"{self.expr(e.value)}[{self.expr(e.slice)}]"
        if cn == "ExprBin":
            op = _BINOP.get(e.op.name)
            if op is None:
                raise ValueError(f"unsupported binop {e.op.name}")
            return self._binop(e, op)
        if cn == "ExprUnary":
            op = _UNOP.get(e.op.name)
            if op is None:
                raise ValueError(f"unsupported unary op {e.op.name}")
            return f"{op}({self.expr(e.operand)})"
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
                            self._builtin_call):
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
        lines = self._stmt_lines(s, ind)
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
            value = self._coerce(s.value, self._expr_type(tgt))
            return [f"{pad}{self.expr(tgt)} = {value};"]
        if cn == "StmtAugAssign":
            op = _BINOP.get(s.op.name)
            if op is None:
                raise ValueError(f"unsupported augmented-assign op {s.op.name}")
            return [f"{pad}{self.expr(s.target)} {op}= {self.expr(s.value)};"]
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
        if getattr(s, "value", None) is not None:
            return [f"{pad}{ct} {name} = "
                    f"{self._coerce(s.value, s.annotation)};"] + tail
        # Value-initialised rather than left indeterminate. PSS gives a
        # declared variable a defined value; C++ does not, and the difference
        # is a read of stack garbage that behaves differently under -O2.
        return [f"{pad}{ct} {name}{{}};"] + tail

    def _foreach(self, s, ind: int) -> List[str]:
        """`foreach (a[i]) { ... }` -> an indexed `for` over the folded size.

        A range-for would read better and cannot be used: the body indexes the
        collection by the loop variable (`ch[i]`, and often a second collection
        at the same index), so the INDEX is what the model needs, not the
        element.
        """
        pad = "    " * ind
        idx = mangle(getattr(getattr(s, "target", None), "name", None) or "i")
        coll = self.expr(s.iter)
        n = _array_size(self._iter_dtype(s))
        if n is None:
            raise ValueError(
                f"cannot lower `foreach` over '{coll}': its size is not known "
                "at generation time.")
        lines = [f"{pad}for (std::size_t {idx} = 0; {idx} < {n}u; ++{idx}) {{"]
        lines += self.stmts(s.body, ind + 1)
        lines.append(f"{pad}}}")
        return lines

    def _iter_dtype(self, s):
        """Declared type of a `foreach` collection, when it is a member."""
        it = s.iter
        if _dt_name(it) == "ExprAttribute" and \
                _dt_name(it.value) == "TypeExprRefSelf":
            for f in getattr(self.comp, "fields", []):
                if f.name == it.attr:
                    return f.datatype
        return None

    def _match(self, s, ind: int) -> List[str]:
        """PSS `match` -> `switch`, with every arm breaking.

        PSS arms do not fall through, so omitting the break would silently
        change the model's meaning into C++'s.
        """
        pad = "    " * ind
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
            out.append(f'{pad}    pssc::message("{self.cls}: unmatched match '
                       f'subject in {self.fn.name}");')
        elif self.match_default == "unreachable":
            out.append(f"{pad}    PSSC_UNREACHABLE();")
        out.append(f"{pad}    break;")
        return out

    def _pattern_labels(self, pattern) -> List[str]:
        if pattern is None:
            return []
        cn = _dt_name(pattern)
        if cn == "PatternValue":
            return [self.expr(pattern.value)]
        if cn in ("PatternOr", "PatternSequence"):
            out: List[str] = []
            for p in pattern.patterns:
                out += self._pattern_labels(p)
            return out
        raise ValueError(f"unsupported match pattern {cn}")

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
        if m == "get_offset_of_instance":
            return f"0x{scalar_offset(group, _str_const(args[0], m)):x}u"
        if m == "get_offset_of_instance_array":
            base, stride = array_base_stride(group, _str_const(args[0], m))
            return f"(0x{base:x}u + 0x{stride:x}u * {self._operand(args[1])})"
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
    return ", ".join(f"{cpp_type(a.annotation)} {mangle(a.arg)}"
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
    """One channel member, after checking the backend can implement it."""
    dt = f.datatype
    depth = int(getattr(dt, "depth", 1) or 1)
    if depth != 1:
        raise ValueError(
            f"channel '{f.name}' has depth {depth}; the C++ target implements "
            f"only depth-1 channels (share/cpp/pssc_chan.hpp). A deeper channel "
            f"is a ring buffer, which nothing in scope declares.")
    elem = getattr(dt, "element_type", None)
    if elem is None:
        raise ValueError(f"channel '{f.name}' has no element type")
    return f"pssc::chan1<{cpp_type(elem)}> {mangle(f.name)};"


def _data_member(f) -> str:
    """One plain data member, value-initialised.

    An array becomes `std::array`, which unlike C's `T x[N]` is a value type: it
    can be assigned, returned and default-initialised like everything else here.
    """
    dt = f.datatype
    if _dt_name(dt) == _DT_ARRAY:
        n = _array_size(dt)
        if n is None:
            raise ValueError(
                f"array member '{f.name}' has no folded size; the generated "
                f"member needs a bound.")
        return f"std::array<{cpp_type(dt.element_type)}, {n}> {mangle(f.name)}{{}};"
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

    Assignments rather than member initialisers because a struct-typed
    attribute carries its defaults on the STRUCT's fields, which have to be
    walked out member by member.
    """
    out: List[str] = []
    for f in data_members(comp):
        iv = getattr(f, "initial_value", None)
        if iv is not None:
            out.append(f"{pad}this->{mangle(f.name)} = {emitter.expr(iv)};")
            continue
        if _dt_name(f.datatype) == _DT_STRUCT:
            for sf in getattr(f.datatype, "fields", []) or []:
                siv = getattr(sf, "initial_value", None)
                if siv is not None:
                    out.append(f"{pad}this->{mangle(f.name)}.{sf.name} = "
                               f"{emitter.expr(siv)};")
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
    be_kw = dict(be_kw, imports=imports, ctor_names=ctor_names)
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
    be_kw = dict(be_kw, imports=imports, ctor_names=ctor_names)
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
    all_params = f"{ns}_import_if &imp" + (f", {params}" if params else "")
    fwd = ", ".join(mangle(a.arg) for a in (ctor.args.args if ctor else []))
    return [
        f"    static std::unique_ptr<{cls}_if> create({all_params}) {{",
        f"        auto self = std::make_unique<{cls}>(imp);",
        f"        self->initialize({fwd});",
        f"        return self;",
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
