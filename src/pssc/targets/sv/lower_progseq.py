"""Lower a regular PSS component (its operations + sub-components) to the SV
programming API: an export-API interface class and an implementation class.

The export API is one ``task`` per runtime operation (PSS ``int`` return ->
``output int status``; every following arg explicit ``input``; SV-keyword args
renamed). The impl translates each body 1:1, rewriting register access to the
task form (``x = r.read()`` -> ``r.read(x)``) and ``repeat{}while`` to
``forever .. break`` (design §6.5/§6.6).

It also emits the import-API interface and the component handle/factory class
(`emit_component`) -- a single class named after the component that redirects the
import API to the user object and exposes the static `create()`.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional

import zuspec.ir.core as ir

from .. import pkg_functions as pf
from ..body_walker import BodyWalker
from ..validate_calls import callee_name, is_core_call
from ..progseq_model import (func_kind, FuncKind, field_is_reg_group, _dt_name,
                             sub_components, SubComp, field_is_channel,
                             channel_fields, array_base_stride, scalar_offset,
                             OffsetFoldError, exec_kind, INIT_EXEC_KINDS)
from ..comp_inherit import SUPER_BLOCK
from ..comments import blank_line, comment_lines, doc_block

_DT_STRUCT = "DataTypeStruct"
_DT_INT = "DataTypeInt"
_DT_ENUM = "DataTypeEnum"
_DT_CHANDLE = "DataTypeChandle"
_DT_CHANNEL = "DataTypeChannel"

# SystemVerilog reserved words that can appear as PSS identifiers; renamed by
# appending '_' so generated code compiles. (Extend as needed.)
_SV_KEYWORDS = frozenset({
    "priority", "wait", "do", "final", "time", "table", "type", "begin", "end",
    "fork", "join", "wire", "reg", "logic", "bit", "byte", "int", "shortint",
    "longint", "module", "endmodule", "class", "endclass", "task", "function",
    "return", "default", "force", "release", "assign", "static", "automatic",
    "local", "protected", "virtual", "ref", "const", "event", "disable",
})


def _strip_pkg(name: Optional[str]) -> str:
    return name.split("::")[-1] if name else name


def mangle(name: str) -> str:
    """Rename an identifier that collides with an SV keyword."""
    return name + "_" if name in _SV_KEYWORDS else name


# --- type mapping ----------------------------------------------------------

def sv_type(dtype) -> str:
    """SV type string for a PSS datatype used as an arg/return/local."""
    cn = _dt_name(dtype)
    if cn == _DT_INT:
        bits = int(getattr(dtype, "bits", 32) or 32)
        signed = bool(getattr(dtype, "signed", False))
        if signed and bits == 32:
            return "int"
        # A signed integer of another width is signed here too: `int[16] c =
        # -5` held in a `bit [15:0]` compares, extends and prints as 65531.
        sign = " signed" if signed else ""
        if bits == 1:
            return f"bit{sign}"
        return f"bit{sign} [{bits - 1}:0]"
    if cn == "DataTypeString":
        return "string"
    if cn == _DT_CHANDLE:
        # `addr_reg_pkg::addr_handle_t` is `typedef chandle addr_handle_t`, and
        # a typedef leaves no name behind in the IR. The address handle is the
        # only chandle a programming-sequence API can reach -- every stdlib
        # function taking one takes an address -- so the mapping is total.
        return "addr_handle_t"
    if cn == _DT_STRUCT:
        nm = _strip_pkg(dtype.name)
        # Older stdlibs declared addr_handle_t as a placeholder struct.
        return "addr_handle_t" if nm == "addr_handle_t" else nm
    if cn == _DT_ENUM:
        # The typedef is emitted alongside the API (see lower_api_types).
        return _strip_pkg(dtype.name)
    if cn == _DT_CHANNEL:
        # `pssc_reg_pkg::channel_c`, the runtime's mailbox-backed channel -- NOT
        # a class generated from the model. Both parameters are passed through:
        # the depth is what makes a depth-1 channel coalesce, so defaulting it
        # here would change the model's behaviour rather than just its text.
        elem = getattr(dtype, "element_type", None)
        depth = getattr(dtype, "depth", None) or 1
        return f"channel_c #({sv_type(elem) if elem is not None else 'bit'}, {depth})"
    raise ValueError(f"unsupported SV type for {cn}")


def sv_cast(dtype) -> str:
    """The cast prefix for ``dtype``: `32'(x)`, not `bit [31:0]'(x)`.

    SV casts take a simple type name or a size -- a packed-vector type
    expression is a syntax error there, which is a rule the type mapping above
    does not know about because every OTHER position accepts the vector form.
    """
    cn = _dt_name(dtype)
    if cn == _DT_INT:
        bits = int(getattr(dtype, "bits", 32) or 32)
        if bits == 32 and bool(getattr(dtype, "signed", False)):
            return "int"
        return str(bits)
    return sv_type(dtype)


# --- signatures ------------------------------------------------------------

def _arg_names(fn) -> List[str]:
    return [a.arg for a in fn.args.args]


def param_dir(dtype, is_const: bool = False) -> str:
    """`ref` for a struct parameter, `input` otherwise.

    PSS passes an aggregate as a handle to the caller's instance (20.3.2,
    Example 298): a callee's `p.x = a` is seen by the caller, which an SV
    `input` copy would lose. A `const` parameter (20.2.3) cannot be written,
    so a copy is indistinguishable from the handle -- and `input` accepts an
    aggregate literal, which SV's `ref` (even `const ref`) does not. An
    address handle is a scalar here, whatever the stdlib declares it as."""
    if (not is_const and _dt_name(dtype) == _DT_STRUCT
            and _strip_pkg(dtype.name) != "addr_handle_t"):
        return "ref"
    return "input"


def _const_params(fn) -> frozenset:
    """The names of ``fn``'s `const` parameters (ast2ir records them)."""
    return frozenset((getattr(fn, "metadata", None) or {}).get(
        "const_params", ()))


def _signature(fn) -> str:
    """Build the parenthesized task signature for operation ``fn``.

    ``int`` return becomes a leading ``output int status``; every following arg
    is explicit ``input`` (SV inherits the previous direction otherwise -- a
    real codegen pitfall).
    """
    parts: List[str] = []
    if fn.returns is not None:
        parts.append(f"output {sv_type(fn.returns)} status")
    consts = _const_params(fn)
    for a in fn.args.args:
        parts.append(f"{param_dir(a.annotation, a.arg in consts)} "
                     f"{sv_type(a.annotation)} {mangle(a.arg)}")
    return ", ".join(parts)


# --- body translation ------------------------------------------------------

_BINOP = {
    "Add": "+", "Sub": "-", "Minus": "-", "Mult": "*", "Mul": "*",
    "Div": "/", "Mod": "%", "Eq": "==", "NotEq": "!=", "Ne": "!=",
    "Lt": "<", "LtE": "<=", "Le": "<=", "Gt": ">", "GtE": ">=", "Ge": ">=",
    "And": "&&", "Or": "||", "BitAnd": "&", "BitOr": "|", "BitXor": "^",
    "LShift": "<<", "Shl": "<<", "RShift": ">>", "Shr": ">>",
}


_UNOP = {"Not": "!", "Invert": "~", "USub": "-", "Minus": "-", "UAdd": "+"}

#: Memory-read primitives: the value returns through a trailing output argument.
_MEM_READS = frozenset({"read8", "read16", "read32", "read64"})


class _StatusTarget:
    """Stands in for the generated `status` output argument, which has no IR
    node of its own -- a PSS `return <expr>` names no variable."""


_STATUS_TARGET = _StatusTarget()


class _Discard(_StatusTarget):
    """The temporary a discarded task result is written to."""


_DISCARD = _Discard()


def is_task(fn, ctor_names=None) -> bool:
    """Is ``fn`` a task here? `target` and unqualified functions are; a
    `solve function` (the constructor included) and an exec block are SV
    functions. The uniform mapping: it says nothing about whether a body
    actually blocks -- a non-blocking model-internal function rendered as an
    SV function is a later refinement."""
    if getattr(fn, "is_solve", False):
        return False
    return exec_kind(fn) is None


def pkg_function_name(fn) -> str:
    """The package-level name of a package-scope function: its qualified PSS
    name with `::` spelled `__`, so two packages' `f` stay apart."""
    return mangle(pf.qualified_name(fn).replace("::", "__"))


def _is_true_const(e) -> bool:
    return _dt_name(e) == "ExprConstant" and e.value is True


class _BodyEmitter(BodyWalker):
    """Translate one function body to SV lines.

    The walk, and carrying each statement's PSS comment into the output, are
    `targets/body_walker.py`'s; what is here is the SystemVerilog rendering,
    one hook per node kind, named after the node.
    """

    indent = "  "

    def __init__(self, fn, comp, member_of, namer=None, ctor_names=None,
                 ctx=None):
        self.fn = fn
        self.comp = comp
        #: The translation context, for typing `message()` arguments. None
        #: types every argument as `int`.
        self.ctx = ctx
        self._types = None
        #: This compile's constructor names -- see `_operations`.
        self.ctor_names = ctor_names
        # Restores field names to the folded masks `reg_rmw` produced. None
        # disables it, and every call site then emits the literal pair it
        # emitted before this existed -- so the naming is never load-bearing.
        self.namer = namer
        args = (fn.args.args if fn is not None and fn.args else [])
        # rename map for SV-keyword args
        self.arg_rename = {a.arg: mangle(a.arg) for a in args}
        self.arg_names = set(self.arg_rename)
        # component field name -> member ref (reg-group fields become m_<name>)
        self.member_of = member_of  # dict: field_name -> "m_<field>"
        # Register-group fields by PSS name, so a `regs.get_offset_of_*()` call
        # can be resolved to the group whose offsets answer it (see _fold_offset).
        self.reg_group_of = {
            f.name: f.datatype
            for f in (getattr(comp, "fields", None) or []) if field_is_reg_group(f)
        }
        # Channel field name -> the SV type of its ELEMENT. Needed because
        # `channel_c::get` is a task with an `output Te` argument, so a PSS
        # `c.get();` that discards the value still has to supply somewhere to
        # put it, and that temp must be `Te`-wide -- a `bit` temp against a
        # wider channel is a WIDTHTRUNC warning, which `-Werror`-style lint
        # treats as a failure. See `_discarded_get`.
        self.chan_elem_of = {}
        for f in (getattr(comp, "fields", None) or []):
            if field_is_channel(f):
                elem = getattr(f.datatype, "element_type", None)
                self.chan_elem_of[f.name] = (
                    sv_type(elem) if elem is not None else "bit")
        #: A value-returning TASK yields its result through a leading
        #: `output status`; an SV function (`solve`, `returns_value`) returns
        #: it. See `is_task`.
        self.returns_value = fn is not None and not is_task(fn, ctor_names)
        self.has_status = (fn is not None and fn.returns is not None
                           and not self.returns_value)
        #: Solve context -- an SV function, which cannot call a task: a
        #: `solve function`, the constructor, an `exec init_down`/`init_up`.
        self.solve_ctx = fn is not None and (
            getattr(fn, "is_solve", False)
            or exec_kind(fn) in INIT_EXEC_KINDS)
        self._pkg = None
        #: Lines the statement being emitted needs IN FRONT of it: blocking
        #: calls hoisted out of its expressions (`_hoist`). Unindented;
        #: `render_stmt` places them. None outside a statement, where nothing
        #: can be hoisted.
        self._prefix: Optional[List[str]] = None
        self._tmp_n = 0
        #: The indent of the statement being emitted: where its block's
        #: declarations sit.
        self._ind = 1
        self._builtin_hook = None
        # Operations of this component that return a value: their result comes
        # back through an output argument, not a return value.
        self.out_calls = {
            f.name for f in (getattr(comp, "functions", None) or [])
            if f.returns is not None
            and func_kind(f, ctor_names) is FuncKind.EXPORT_OP
        }
        #: This component's operations -- tasks -- by name, for the
        #: solve-context check and a discarded result's type.
        self.op_fns = {
            f.name: f for f in (getattr(comp, "functions", None) or [])
            if func_kind(f, ctor_names) is FuncKind.EXPORT_OP}
        # Declared types of local variables, so an enum-valued assignment can be
        # written with the enum's mnemonic rather than its number.
        self.local_types: Dict[str, object] = {}
        #: The declarations the block being emitted hoists: ``(ids of the
        #: StmtAnnAssign nodes to hoist, their bare declarations)``. See
        #: `enter_block`.
        self._block = None

    # -- hoisting blocking calls out of expressions (SV-2) ------------------
    #
    # A blocking call is a TASK here, and a task has no value: its result
    # comes back through an output argument. Inside an expression it is
    # therefore HOISTED -- a temporary receives the value, the call is emitted
    # as a statement in front of the one that uses it, and the temporary takes
    # its place:
    #
    #     x = f(a) + g(b);        ->   f(pssc_tmp_0, a);
    #                                  g(pssc_tmp_1, b);
    #                                  x = pssc_tmp_0 + pssc_tmp_1;
    #
    # PSS expressions have no side effects other than calls, so moving the
    # calls in front, in evaluation order (left to right, arguments first),
    # changes nothing -- except where an operand is evaluated CONDITIONALLY or
    # REPEATEDLY:
    #
    # * `a && f()`, `a || f()`: `f` runs only when `a` does not decide; the
    #   hoisted call sits under an `if` (`expr_bin`).
    # * `c ? f() : g()`: each arm's calls under its branch (`expr_if_exp`).
    # * a loop condition is evaluated each iteration, so it is hoisted INTO
    #   the loop, not in front of it (`stmt_while`, `stmt_repeat_while`).
    #
    # Temporaries are declared at the top of the enclosing block, as hoisted
    # declarations are (`enter_block`).

    def render_stmt(self, s, ind: int) -> List[str]:
        """The statement, preceded by the calls hoisted out of it."""
        saved, self._prefix = self._prefix, []
        saved_ind, self._ind = self._ind, ind
        try:
            lines = super().render_stmt(s, ind)
        finally:
            prefix, self._prefix = self._prefix, saved
            self._ind = saved_ind
        return self._placed(prefix, ind) + lines

    def _placed(self, prefix: List[str], ind: int) -> List[str]:
        pad = self.pad(ind)
        return [pad + ln for ln in prefix]

    def _capture(self, render):
        """``(prefix, text)``: ``render()``'s text, and what it hoisted,
        kept apart from the current statement's prefix."""
        saved, self._prefix = self._prefix, []
        try:
            text = render()
        finally:
            pre, self._prefix = self._prefix, saved
        return pre, text

    def _emit_prefix(self, lines: List[str], what: str) -> None:
        if self._prefix is None:
            raise ValueError(
                f"{what} needs a blocking call hoisted to a temporary, and "
                f"it is not in a statement that can hold one")
        self._prefix += lines

    def _temp(self, sv_t: str) -> str:
        """A fresh temporary of SV type ``sv_t``, declared at the top of the
        enclosing block."""
        if self._block is None:
            raise ValueError("a hoisted call's temporary needs an enclosing block")
        name = f"pssc_tmp_{self._tmp_n}"
        self._tmp_n += 1
        self._block[1].append(f"{self.pad(self._ind)}{sv_t} {name};")
        return name

    def _is_value_task(self, call) -> bool:
        """Does ``call`` block and yield a value -- a task with an output
        argument (`_task_form`)? Decided without rendering anything."""
        if self._pkg_callee(call) is not None:
            pfn = self._pkg_callee(call)
            return is_task(pfn) and pfn.returns is not None
        fn = call.func
        if _dt_name(fn) != "ExprAttribute":
            return False
        if fn.attr in ("read", "read_val", "get") and not call.args:
            return True
        if fn.attr in _MEM_READS:
            return True
        task = self._task_callee(call)
        return (_dt_name(fn.value) == "TypeExprRefSelf"
                and fn.attr in self.out_calls) or (
                    task is not None and task.returns is not None)

    def _value_type(self, call) -> str:
        """The SV type of a blocking call's value, for its temporary."""
        pfn = self._pkg_callee(call)
        task = pfn if pfn is not None else self._task_callee(call)
        if task is not None and task.returns is not None:
            return sv_type(task.returns)
        fn = call.func
        if fn.attr in _MEM_READS:
            return f"bit [{int(fn.attr[4:]) - 1}:0]"
        recv = getattr(fn.value, "attr", None)
        if fn.attr == "get" and recv in self.chan_elem_of:
            return self.chan_elem_of[recv]
        t = self.types.type_of(call)
        if t is None:
            raise ValueError(
                f"cannot type the value of '{fn.attr}(...)' to hoist it")
        return _sv_of(t)

    def _hoist(self, call) -> str:
        """Hoist the blocking ``call``; the temporary that holds its value."""
        tmp = self._temp(self._value_type(call))
        text = self._task_form(call, tmp)
        self._emit_prefix([f"{text};"], f"'{callee_name(call.func)}(...)'")
        return tmp

    def _task_form(self, v, tgt: str) -> Optional[str]:
        """``v`` as a task call writing its value to ``tgt``, or None if it is
        not a blocking value call. The output argument's POSITION differs:

          register accessor  r.read(x)          -- value last, sole argument
          channel receive    c.get(x)           -- value last, sole argument
          memory primitive   read32(addr, x)    -- value last
          operation / pkg    f(x, args...)      -- status FIRST (see _signature)
        """
        pfn = self._pkg_callee(v)
        if pfn is not None:
            if not is_task(pfn) or pfn.returns is None:
                return None
            self._check_callable(v)
            return self._call_text(v, tgt)
        fn = v.func
        if _dt_name(fn) != "ExprAttribute":
            return None
        name = fn.attr
        if name in ("read", "read_val", "get") and not v.args:
            return f"{self.expr(fn.value)}.{name}({tgt})"
        if name in _MEM_READS:
            args = ", ".join([self.expr(a) for a in v.args] + [tgt])
            return f"{self.expr(fn)}({args})"
        task = self._task_callee(v)
        if name in self.out_calls or (task is not None
                                      and task.returns is not None):
            self._check_callable(v)
            args = ", ".join([tgt] + self._args(v, task))
            return f"{self.expr(fn)}({args})"
        return None

    def _args(self, call, fn) -> List[str]:
        """``call``'s arguments, each as a value of its parameter's type --
        an enum parameter takes the mnemonic, as an assignment does."""
        params = list(fn.args.args) if fn is not None and fn.args else []
        return [self.value_of(params[i].annotation if i < len(params) else None, a)
                for i, a in enumerate(call.args)]

    def _blocks(self, e) -> bool:
        """Does evaluating ``e`` make a blocking call (`_is_value_task`)?"""
        if e is None:
            return False
        calls = pf.calls_in(e)
        return any(self._is_value_task(c) for c in calls)

    def enter_block(self, body):
        """PSS declares a local anywhere in a block (20.7.1); SV only at the
        start of one. A declaration that follows a statement is emitted as a
        bare declaration at the top of the block, and its initializer as an
        assignment where it was written (`stmt_ann_assign`).

        Nothing reads a local before its declaration, so the value it holds
        until then is never seen; a loop body is entered afresh each
        iteration, so a local declared without an initializer is still its
        default there. Declarations that already lead the block stay as they
        are.
        """
        hoist = set()
        seen_stmt = False
        for s in body or []:
            if _dt_name(s) != "StmtAnnAssign":
                seen_stmt = True
            elif seen_stmt or self._blocks(getattr(s, "value", None)):
                # A declaration whose initializer blocks is a statement too:
                # its calls are emitted in front of it.
                hoist.add(id(s))
                seen_stmt = True
        saved = self._block
        self._block = (hoist, [])
        return saved

    def leave_block(self, token, lines):
        _, decls = self._block
        self._block = token
        return decls + lines

    # expressions ----------------------------------------------------------

    def _builtin_call(self, call) -> Optional[str]:
        """A PSS exec built-in rendered as its SV analogue, or ``None``."""
        if self._builtin_hook is None:
            from .sv_builtins import make_pss_builtin_call_hook
            self._builtin_hook = make_pss_builtin_call_hook(self.expr)
        return self._builtin_hook(call)

    @property
    def types(self):
        if self._types is None:
            from ..expr_types import ExprTypes
            self._types = ExprTypes(self.fn, self.comp, self.ctx)
        return self._types

    def _message(self, call) -> str:
        """`message(verbosity, fmt, args...)` -> `$display(...)`, per LRM 21.1.1.

        Not `sv_builtins`' verbatim copy of the format: SV's `%d` pads to the
        operand's width (`%d` of a `bit[8]` 5 is "  5"), and SV has no `%n`. Each
        specifier is translated at its argument's PSS type:

        * `%d` prints the value signed at its own width (rule c), `%u`
          unsigned; both unpadded (`%0d`).
        * `%x %o %b` print it unsigned, unpadded.
        * `%n` prints a bool as `true`/`false` and an enum as its item name.

        The format is checked as op-model-py checks it (`parse_format`), so a
        bad one is a build error. A width, flag or precision, and `%X`, have no
        rendering yet and are refused rather than approximated. The verbosity
        has no SV analogue and is dropped.
        """
        from ..py.pss_format import parse_format
        args = list(call.args)
        if len(args) < 2:
            raise ValueError("'message' needs a verbosity and a format string")
        fmt_e, vals = args[1], args[2:]
        fmt = getattr(fmt_e, "value", None)
        if _dt_name(fmt_e) != "ExprConstant" or not isinstance(fmt, str):
            raise ValueError(
                f"'message' needs a string literal as its format; got "
                f"{_dt_name(fmt_e)}")
        try:
            specs = parse_format(fmt)
        except ValueError as e:
            raise ValueError(f"message({fmt!r}): {e} (LRM 21.1.1 a)") from None
        if len(specs) != len(vals):
            raise ValueError(
                f"message({fmt!r}): {len(specs)} format specifier(s) for "
                f"{len(vals)} argument(s) (LRM 21.1.1 b)")

        def lit(text):
            return (text.replace("\\", "\\\\").replace('"', '\\"')
                    .replace("\n", "\\n").replace("\t", "\\t"))

        out: List[str] = []
        sv_args: List[str] = []
        pos = 0
        for (start, end, flags, width, prec, conv), v in zip(specs, vals):
            out.append(lit(fmt[pos:start]))
            pos = end
            if flags or width is not None or prec is not None or conv == "X":
                raise ValueError(
                    f"message({fmt!r}): '{fmt[start:end]}' has no "
                    f"SystemVerilog rendering yet (only unpadded %d %u %x %o "
                    f"%b %n %s)")
            spec, arg = self._fmt_arg(conv, v)
            out.append(spec)
            sv_args.append(arg)
        out.append(lit(fmt[pos:]))
        return "$display(" + ", ".join(['"' + "".join(out) + '"'] + sv_args) + ")"

    def _fmt_arg(self, conv: str, v):
        """``(sv specifier, sv argument)`` for one PSS specifier."""
        t = self.types.type_of(v)
        kind = t.kind if t is not None else "int"
        x = self.expr(v)
        if conv == "s":
            return "%s", x
        if conv == "n":
            if kind == "bool":
                # `string'`: the two literals are packed vectors of
                # different widths, and `?:` pads the shorter (" true").
                return "%s", f'(({x}) ? string\'("true") : string\'("false"))'
            if kind == "enum":
                return "%s", f"{x}.name()"
            raise ValueError(f"'%n' takes a bool or an enum; '{x}' is {kind}")
        # An integer conversion. A bool or enum formats as its value.
        signed = t.signed if (t is not None and kind == "int") else kind == "enum"
        if kind in ("bool", "enum"):
            x = f"int'({x})"
        if conv == "d":
            return "%0d", (x if signed else f"$signed({x})")
        if conv == "u":
            return "%0d", (f"$unsigned({x})" if signed else x)
        return {"x": "%0h", "o": "%0o", "b": "%0b", "B": "%0b"}[conv], x

    def _pkg_callee(self, call):
        """The package-scope function ``call`` calls, or None
        (`pkg_functions.callee`: decided by the IR form the linker gave it)."""
        if self.ctx is None:
            return None
        if self._pkg is None:
            self._pkg = pf.declared(self.ctx)
        return pf.callee(call, self._pkg)

    def _task_callee(self, call):
        """The function ``call`` calls if it is a TASK here, else None: a
        package-scope `target`/unqualified function, or one of this
        component's operations called on `self`."""
        pfn = self._pkg_callee(call)
        if pfn is not None:
            return pfn if is_task(pfn) else None
        f = call.func
        if _dt_name(f) != "ExprAttribute":
            return None
        if _dt_name(f.value) == "TypeExprRefSelf":
            return self.op_fns.get(f.attr)
        # An operation of a sub-component, reached through its instance.
        t = self.types.type_of(f.value)
        if t is None or t.kind != "comp" or t.dtype is None:
            return None
        return next((g for g in (getattr(t.dtype, "functions", None) or [])
                     if g.name == f.attr and func_kind(g, self.ctor_names)
                     is FuncKind.EXPORT_OP), None)

    def _callee_fn(self, call):
        """The declared function ``call`` calls -- a package function, or one
        of this component's or a sub-component's -- or None."""
        pfn = self._pkg_callee(call)
        if pfn is not None:
            return pfn
        f = call.func
        if _dt_name(f) != "ExprAttribute":
            return None
        if _dt_name(f.value) == "TypeExprRefSelf":
            owner = self.comp
        else:
            t = self.types.type_of(f.value)
            owner = t.dtype if (t is not None and t.kind == "comp") else None
        return next((g for g in (getattr(owner, "functions", None) or [])
                     if g.name == f.attr), None)

    def _check_callable(self, call) -> None:
        """Refuse a task called from solve context: an SV function cannot
        call a task, and every solve-context body is one."""
        callee = self._task_callee(call)
        if self.solve_ctx and callee is not None:
            raise ValueError(
                f"'{getattr(self.fn, 'name', '?')}' runs in solve context and "
                f"calls '{callee.name}', which is a task in op-model-sv (a "
                f"`target` or unqualified function); an SV function cannot "
                f"call a task. Declare '{callee.name}' a `solve function`")

    def _call_text(self, call, out: Optional[str] = None) -> str:
        """``name(args)`` for a package-scope function; ``out`` first when
        its value comes back through an output argument."""
        pfn = self._pkg_callee(call)
        args = self._args(call, pfn)
        return (f"{pkg_function_name(pfn)}("
                + ", ".join(([out] if out is not None else []) + args) + ")")

    def _operand(self, e) -> str:
        """An operand of a binary expression, parenthesised if it is one too.

        The IR tree already says how the expression groups; SystemVerilog
        precedence only sometimes agrees, and where it does not the generated
        code is silently wrong. `(enable & 1) << 6` printed flat is
        `enable & 1 << 6`, which SV reads as `enable & (1 << 6)` -- so setting
        a one-bit field at bit 6 wrote zero for every value of `enable`.
        Printing the tree's own structure costs a pair of brackets.
        """
        s = self.expr(e)
        return f"({s})" if _dt_name(e) == "ExprBin" else s

    # An expression class with no hook raises rather than returning None --
    # which is what this emitter used to do, and callers interpolate:
    # `f"{self.expr(base)}.{e.attr}"` yielded the literal text "None.attr".
    # An unhandled expression class must be a diagnostic, not output.

    def expr_constant(self, e) -> str:
        v = e.value
        if isinstance(v, bool):
            return str(int(v))
        if isinstance(v, int):
            # An unsized SV literal is 32 bits: `81985529216486895` is
            # truncated before anything sees it. A wider value is sized.
            if -(1 << 31) <= v < (1 << 31):
                return str(v)
            if 0 <= v < (1 << 64):
                return f"64'd{v}"
            if -(1 << 63) <= v < 0:
                return f"-64'sd{-v}"
            raise ValueError(f"integer literal {v} is wider than 64 bits")
        if isinstance(v, str):
            # NOT repr(): Python prefers single quotes, and 'x' in
            # SystemVerilog is the start of a based literal, not a string.
            # Every message() in the model came out as a syntax error.
            return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
        return repr(v)

    def expr_ref_local(self, e) -> str:
        return self.arg_rename.get(e.name, e.name)

    def type_expr_ref_self(self, e) -> str:
        return "this"

    def expr_attribute(self, e) -> str:
        base = e.value
        if _dt_name(base) == "TypeExprRefSelf":
            # self.<x>: a component field -> member; a function arg -> name
            # A parameter shadows a field of the same name (scope order).
            if e.attr in self.arg_names:
                return self.arg_rename[e.attr]
            if e.attr in self.member_of:
                return self.member_of[e.attr]
            return e.attr
        return f"{self.expr(base)}.{e.attr}"

    def expr_subscript(self, e) -> str:
        return f"{self.expr(e.value)}[{self.expr(e.slice)}]"

    def expr_bin(self, e) -> str:
        op = _BINOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported binop {e.op.name}")
        if op in ("&&", "||"):
            lhs = self._operand(e.lhs)
            pre, rhs = self._capture(lambda: self._operand(e.rhs))
            if not pre:
                return f"{lhs} {op} {rhs}"
            # The right operand's calls run only when the left does not
            # decide the result.
            t = self._temp("bit")
            guard = t if op == "&&" else f"!{t}"
            self._emit_prefix(
                [f"{t} = ({lhs}) ? 1'b1 : 1'b0;", f"if ({guard}) begin"]
                + ["  " + ln for ln in pre]
                + [f"  {t} = ({rhs}) ? 1'b1 : 1'b0;", "end"], f"'{op}'")
            return t
        if op == ">>":
            # PSS `>>` on a signed operand is arithmetic (8.5.7); SV's `>>`
            # is always logical, and `>>>` follows the operand's sign.
            t = self.types.type_of(e.lhs)
            if t is not None and t.kind == "int" and t.signed:
                op = ">>>"
        return f"{self._operand(e.lhs)} {op} {self._operand(e.rhs)}"

    def expr_if_exp(self, e) -> str:
        """`c ? a : b` (8.5.8). Only the chosen arm is evaluated, in both."""
        test = self.expr(e.test)
        pa, a = self._capture(lambda: self._operand(e.body))
        pb, b = self._capture(lambda: self._operand(e.orelse))
        if not pa and not pb:
            return f"({test} ? {a} : {b})"
        # Each arm's calls run only in its own branch.
        t = self.types.type_of(e)
        if t is None:
            raise ValueError("cannot type a `?:` whose arms must be hoisted")
        tmp = self._temp(_sv_of(t))
        self._emit_prefix(
            [f"if ({test}) begin"] + ["  " + ln for ln in pa]
            + [f"  {tmp} = {a};", "end else begin"] + ["  " + ln for ln in pb]
            + [f"  {tmp} = {b};", "end"], "'?:'")
        return tmp

    def expr_range(self, e) -> str:
        """`lo..hi` in a `match` pattern -> an SV `inside` range `[lo:hi]`
        (`stmt_match` then uses `case ... inside`)."""
        return f"[{self.expr(e.lower)}:{self.expr(e.upper)}]"

    def expr_cast(self, e) -> str:
        return f"{sv_cast(e.target_type)}'({self.expr(e.value)})"

    def expr_unary(self, e) -> str:
        op = _UNOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported unary op {e.op.name}")
        return f"{op}({self.expr(e.operand)})"

    def expr_call(self, e) -> str:
        callee = e.func
        if is_core_call(callee):
            # `std_pkg::message(...)` names the function `message(...)` does,
            # so it takes the form an unqualified call has. The spelling only
            # matters inside an executor's override, and this target refuses
            # overrides (`executors.check`).
            e = dc.replace(e, func=ir.ExprAttribute(
                value=ir.TypeExprRefSelf(), attr=callee_name(callee)))
            callee = e.func
        # Address-space builtins are arithmetic, not calls: there is no
        # address-space object in generated SV, only 64-bit addresses.
        if _dt_name(callee) == "ExprAttribute" and callee.attr in _ADDR_BUILTINS:
            return _ADDR_BUILTINS[callee.attr](self, e)
        if callee_name(callee) == "message":
            return self._message(e)
        self._check_callable(e)
        if self._is_value_task(e):
            # A task has no value: hoist it, and use its temporary (SV-2).
            return self._hoist(e)
        if self._pkg_callee(e) is not None:
            return self._call_text(e)
        # PSS exec built-ins have no definition to call: `message(...)` is
        # part of the language, not of the generated package, so emitting
        # it verbatim produces SV that references a task that does not
        # exist. The mapping already existed in sv_builtins for the other
        # SV targets; this one was not consulting it.
        builtin = self._builtin_call(e)
        if builtin is not None:
            return builtin
        # A folded masked write, spelled back as the field(s) it came from.
        named = self._field_write(e)
        if named is not None:
            return named
        # A register-group offset function: evaluated here, never emitted.
        folded = self._fold_offset(e)
        if folded is not None:
            return folded
        args = ", ".join(self._args(e, self._callee_fn(e)))
        return f"{self.expr(callee)}({args})"

    # --- register-group offset folding ------------------------------------

    def _fold_offset(self, call) -> Optional[str]:
        """``regs.get_offset_of_instance[_array](...)`` -> a folded expression.

        These are classified `FuncKind.REG_OFFSET` -- "evaluated, not emitted" --
        and the generated SV register-group class has no such method, so an
        emitted call is code that does not compile. It was emitted anyway,
        because nothing consulted the classification: see
        `docs/design/lowering-call-legality.md` §1.1.

        Returns ``None`` when the call is not one of these at all (so the caller
        falls through); raises :class:`OffsetFoldError` when it IS one and
        cannot be evaluated. That asymmetry is the point -- a fold that does not
        resolve must not degrade into an emitted call, because the PSS function
        answers an unknown instance with -1 and -1 wraps to a wild address.
        """
        fn = call.func
        if _dt_name(fn) != "ExprAttribute":
            return None
        which = fn.attr
        if which not in ("get_offset_of_instance", "get_offset_of_instance_array"):
            return None

        recv = fn.value
        recv_name = (fn.value.attr if _dt_name(recv) == "ExprAttribute"
                     and _dt_name(recv.value) == "TypeExprRefSelf" else None)
        group = self.reg_group_of.get(recv_name)
        if group is None:
            raise OffsetFoldError(
                f"'{which}' is a reg_group_c method; '{recv_name or self.expr(recv)}' "
                f"is not a register group of this component")

        if not call.args or _dt_name(call.args[0]) != "ExprConstant" \
                or not isinstance(call.args[0].value, str):
            raise OffsetFoldError(
                f"'{which}': the instance name must be a string literal, so the "
                f"offset can be evaluated at build time")
        name = call.args[0].value

        if which == "get_offset_of_instance":
            return f"64'h{scalar_offset(group, name):x}"

        if len(call.args) < 2:
            raise OffsetFoldError(
                "get_offset_of_instance_array takes (name, index)")
        base, stride = array_base_stride(group, name)
        idx = call.args[1]
        if _dt_name(idx) == "ExprConstant":
            return f"64'h{base + int(idx.value) * stride:x}"
        # A loop index or other run-time value: emit the affine form, at
        # addr_handle_t width -- a 32-bit intermediate here is what produced the
        # WIDTHEXPAND alongside the original error.
        return f"(64'h{base:x} + 64'h{stride:x} * {self.expr(idx)})"

    # --- field-named masked writes ---------------------------------------

    def _field_write(self, call) -> Optional[str]:
        """``write_val_masked(64, ..)`` -> ``write_field(WB_DMA_CH_CSR_ars, ..)``.

        Returns ``None`` for anything it cannot prove, which leaves the folded
        literals the reduction produced. Every step is an exact match: the mask
        must be exactly one field's bits or exactly a set of whole fields, and
        each value must un-place to what the model wrote. There is no partial
        credit, because a call that NAMES a field and writes different bits
        would be worse than the magic numbers it replaced.
        """
        from .reg_field_names import unplace

        if self.namer is None:
            return None
        callee = call.func
        if _dt_name(callee) != "ExprAttribute":
            return None
        if callee.attr != "write_val_masked" or len(call.args) != 2:
            return None
        mask_e, val_e = call.args
        if _dt_name(mask_e) != "ExprConstant" or not isinstance(mask_e.value, int):
            return None
        refs = self.namer.fields_for(callee.value, int(mask_e.value))
        if not refs:
            return None
        recv = self.expr(callee.value)

        if len(refs) == 1:
            v = unplace(val_e, refs[0].slice)
            if v is None:
                return None
            return f"{recv}.write_field({refs[0].const}, {self.expr(v)})"

        # Several fields in one transaction. Only the all-constant value is
        # decomposed: `_or_all` flattens a mixed constant/dynamic value into a
        # tree whose per-field parts cannot be recovered unambiguously, and
        # guessing is exactly what the docstring above rules out.
        if _dt_name(val_e) != "ExprConstant" or not isinstance(val_e.value, int):
            return None
        packed = int(val_e.value)
        if packed & ~int(mask_e.value):
            return None       # value carries bits the mask does not select
        vals = [str((packed & r.slice.mask) >> r.slice.lsb) for r in refs]
        names = ", ".join(r.const for r in refs)
        return f"{recv}.write_fields('{{{names}}}, '{{{', '.join(vals)}}})"

    # statements -----------------------------------------------------------

    def stmt_ann_assign(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        # A local named `status` in a value-returning function IS the
        # generated output argument, not a second variable.
        #
        # A PSS function returns a value; the SV lowering turns that into a
        # leading `output <T> status` (see _signature), because anything
        # that can consume time is a task and a task has no return value.
        # A model that names its own result `status` -- which is the
        # obvious name, and what this one uses -- then declared it twice:
        #
        #     virtual task wait_completion(output wb_dma_status_e status);
        #       wb_dma_status_e status;          // <- rejected
        #
        # Suppressing the declaration is not a rename: every assignment to
        # it already means "the value being returned", which is exactly
        # what the output argument carries.
        if (self.has_status
                and _dt_name(s.target) == "ExprRefLocal"
                and s.target.name == "status"):
            self.local_types["status"] = s.annotation
            v = getattr(s, "value", None)
            if v is None:
                return []
            rewritten = self._assign_from_call(s.target, v, pad)
            if rewritten is not None:
                return rewritten
            return [f"{pad}status = {self.value_of(s.annotation, v)};"]

        # Local variable declaration. An absent initializer defaults to 0.
        #
        # The initializer used to be DISCARDED here: `int x = 5;` came out
        # as `int x;`, which compiles, runs, and is wrong. Kept as an SV
        # declaration-with-initializer rather than a following assignment,
        # because a declaration must appear at the start of its block --
        # splitting it in two would move the assignment past that boundary.
        if _dt_name(s.target) == "ExprRefLocal":
            self.local_types[s.target.name] = s.annotation
        decl = f"{pad}{sv_type(s.annotation)} {self.expr(s.target)}"
        v = getattr(s, "value", None)
        if v is None:
            # An enum's default is its FIRST item (7.5), which need not be 0;
            # an SV enum variable starts at 0 whatever its items are.
            first = _enum_first(s.annotation, self.types)
            if first is not None:
                v = ir.ExprConstant(value=first)
        if self._block is not None and id(s) in self._block[0]:
            # Declared at the top of the block (`enter_block`); set here.
            self._block[1].append(f"{decl};")
            if v is None:
                return []
            rewritten = self._assign_from_call(s.target, v, pad)
            if rewritten is not None:
                return rewritten
            return [f"{pad}{self.expr(s.target)} = "
                    f"{self.value_of(s.annotation, v)};"]
        if v is None:
            return [f"{decl};"]
        # A task-valued initializer cannot be one: a task yields its result
        # through an output argument, so it needs the declaration and the
        # call as separate statements (see _assign_from_call).
        rewritten = self._assign_from_call(s.target, v, pad)
        if rewritten is not None:
            return [f"{decl};"] + rewritten
        return [f"{decl} = {self.value_of(s.annotation, v)};"]

    def stmt_assign(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        target = s.targets[0]
        v = s.value
        rewritten = self._assign_from_call(target, v, pad)
        if rewritten is not None:
            return rewritten
        return [f"{pad}{self.expr(target)} = {self.value_of(self._target_type(target), v)};"]

    def stmt_aug_assign(self, s, ind: int) -> List[str]:
        op = _BINOP.get(s.op.name)
        if op is None:
            raise ValueError(f"unsupported augmented-assign op {s.op.name}")
        return [f"{self.pad(ind)}{self.expr(s.target)} {op}= "
                f"{self.expr(s.value)};"]

    def stmt_expr(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        got = self._discarded_get(s.expr, pad)
        if got is not None:
            return got
        if _dt_name(s.expr) == "ExprCall" and self._is_value_task(s.expr):
            # Its value is discarded, but a task still needs somewhere to put
            # it -- `regs.STAT.read_val();` as much as `f(x);`: a temporary in
            # a block of its own.
            self._check_callable(s.expr)
            return ([f"{pad}begin",
                     f"{pad}  {self._value_type(s.expr)} pssc_discard;"]
                    + self._assign_from_call(_DISCARD, s.expr, pad + "  ")
                    + [f"{pad}end"])
        return [f"{pad}{self._discard_value(s.expr)};"]

    def stmt_return(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        if self.returns_value and s.value is not None:
            return [f"{pad}return {self.value_of(self.fn.returns, s.value)};"]
        if self.has_status and s.value is not None:
            # `return f();` where f is a task takes the same output-argument
            # rewrite as an assignment does -- `return wait_completion();`
            # becomes `wait_completion(status); return;`.
            call = self._assign_from_call(_STATUS_TARGET, s.value, pad)
            if call is not None:
                return call + [f"{pad}return;"]
            return [f"{pad}status = {self.value_of(self.fn.returns, s.value)};",
                    f"{pad}return;"]
        return [f"{pad}return;"]

    def stmt_if(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        lines = [f"{pad}if ({self.expr(s.test)}) begin"]
        lines += self.stmts(s.body, ind + 1)
        if getattr(s, "orelse", None):
            lines.append(f"{pad}end else begin")
            lines += self.stmts(s.orelse, ind + 1)
        lines.append(f"{pad}end")
        return lines

    def stmt_repeat_while(self, s, ind: int) -> List[str]:
        """PSS `repeat { } while (c)`: the body, then the test.

        `forever .. if (!c) break;` at the bottom, with the condition's
        blocking calls hoisted in front of the test -- inside the loop, so
        they run each iteration. A `continue` must still reach the test
        (20.7.7), which a `forever` would skip; a body that has one tests at
        the TOP of each iteration after the first instead.
        """
        pad = self.pad(ind)
        pre, cond = self._capture(lambda: self.expr(s.condition))
        test = self._placed(pre, ind + 1) + [f"{pad}  if (!({cond})) break;"]
        if not _has_continue(s.body):
            return ([f"{pad}forever begin"] + self.stmts(s.body, ind + 1)
                    + test + [f"{pad}end"])
        first = self._temp("bit")
        return ([f"{pad}{first} = 1'b1;", f"{pad}forever begin",
                 f"{pad}  if (!{first}) begin"]
                + ["  " + ln for ln in test]
                + [f"{pad}  end", f"{pad}  {first} = 1'b0;"]
                + self.stmts(s.body, ind + 1) + [f"{pad}end"])

    def stmt_while(self, s, ind: int) -> List[str]:
        # `while (true)` becomes `forever`: a constant loop condition is a
        # width/const-expression warning under a linting simulator, and a
        # generated package should lint clean.
        # (`test`, not `condition` -- StmtWhile and StmtRepeatWhile spell it
        # differently, and this branch had never run.)
        pad = self.pad(ind)
        pre, cond = self._capture(lambda: self.expr(s.test))
        if _is_true_const(s.test):
            lines = [f"{pad}forever begin"]
        elif pre:
            # The condition blocks: test it inside the loop, where its calls
            # run every iteration; a `continue` comes back round to them.
            lines = ([f"{pad}forever begin"] + self._placed(pre, ind + 1)
                     + [f"{pad}  if (!({cond})) break;"])
        else:
            lines = [f"{pad}while ({cond}) begin"]
        lines += self.stmts(s.body, ind + 1)
        lines.append(f"{pad}end")
        return lines

    def stmt_for(self, s, ind: int) -> List[str]:
        """`repeat ([i :] n) { ... }` (LRM 20.7.6).

        SV's `repeat` evaluates its count once, as PSS does, and runs no
        iteration for zero. An index is an `int` counting from 0; the count
        is then held in a local, because a `for` condition is re-evaluated.
        """
        pad = self.pad(ind)
        count = self.expr(s.iter)
        name = getattr(getattr(s, "target", None), "name", None)
        if name is None:
            return ([f"{pad}repeat ({count}) begin"]
                    + self.stmts(s.body, ind + 1) + [f"{pad}end"])
        idx = self.expr(s.target)
        n = f"pssc_count_{idx}"
        return ([f"{pad}begin",
                 f"{pad}  int {n} = {count};",
                 f"{pad}  for (int {idx} = 0; {idx} < {n}; {idx}++) begin"]
                + self.stmts(s.body, ind + 2)
                + [f"{pad}  end", f"{pad}end"])

    def stmt_super(self, s, ind: int) -> List[str]:
        """`super;` in an `exec init_down`/`init_up` block.

        The flattened view (`comp_inherit`) resolved it to private copies of
        the base's blocks of this kind, named in `metadata["super"]` and
        rendered as methods of this class (`_init_hook_defs`); None when the
        base declares none, and the statement does nothing.
        """
        pad = self.pad(ind)
        md = getattr(self.fn, "metadata", None) or {}
        if "export_action" in md:
            # An exported action's `super;`: the base action's body, a private
            # task of this class (`EntryPoint.supers`), or nothing.
            if md.get("super"):
                return [f"{pad}{md['super']}();"]
            return [f"{pad}// super: no base action declares an exec body"]
        if exec_kind(self.fn) not in INIT_EXEC_KINDS or "super" not in md:
            raise ValueError(
                f"'super;' in '{getattr(self.fn, 'name', '?')}' has no "
                f"SystemVerilog rendering here")
        names = md["super"] or []
        if not names:
            return [f"{pad}// super: no base declares an exec "
                    f"{exec_kind(self.fn)}"]
        return [f"{pad}{n}();" for n in names]

    def stmt_break(self, s, ind: int) -> List[str]:
        return [f"{self.pad(ind)}break;"]

    def stmt_continue(self, s, ind: int) -> List[str]:
        return [f"{self.pad(ind)}continue;"]

    def stmt_yield(self, s, ind: int) -> List[str]:
        # PSS `yield` (20.7.14) lets other threads run. In SystemVerilog that
        # is `#0`: the calling process gives way to every other process ready
        # in the same time step, which is where a device model's `always`
        # blocks and the testbench's threads run.
        return [f"{self.pad(ind)}#0;"]

    def _target_type(self, target):
        """Declared type of an assignment target, when it is known."""
        cn = _dt_name(target)
        if cn == "ExprRefLocal":
            return self.local_types.get(target.name)
        return None

    def value_of(self, dtype, e) -> str:
        """Render ``e`` as a value of ``dtype``.

        The one case that matters: an enum constant reaches the IR as a plain
        integer, and SV rejects an implicit integer-to-enum conversion. Writing
        the mnemonic keeps the generated code both legal and readable -- the
        alternative, a static cast, would compile and read as a magic number.
        """
        if dtype is not None:
            dtype = self.types.resolve(dtype)
        if _dt_name(e) == "ExprStructLiteral":
            return self._struct_literal(dtype, e)
        if (dtype is not None and _dt_name(dtype) == _DT_ENUM
                and _dt_name(e) == "ExprConstant" and isinstance(e.value, int)):
            for nm, val in dtype.items.items():
                if val == e.value:
                    return nm
        return self.expr(e)

    def _struct_literal(self, dtype, e) -> str:
        """`{.a = 1, .b = 2}` (8.2.2) as a typed SV assignment pattern,
        `s_t'{a: 1, b: 2}`. Every member is named: an SV pattern must cover
        them all, and a field the literal leaves out takes its declared
        default, as a declaration without an initializer does (7.8)."""
        if dtype is None or _dt_name(dtype) != _DT_STRUCT:
            raise ValueError(
                "an aggregate literal needs a struct type from its context "
                "(a parameter, or an assignment's target)")
        given = {f.name: f.value for f in (e.fields or [])}
        unknown = sorted(set(given) - {f.name for f in dtype.fields})
        if unknown:
            raise ValueError(f"'{_strip_pkg(dtype.name)}' has no field "
                             f"{', '.join(repr(u) for u in unknown)}")
        parts = []
        for f in dtype.fields:
            if f.name in given:
                v = self.value_of(f.datatype, given[f.name])
            elif getattr(f, "initial_value", None) is not None:
                v = self.value_of(f.datatype, f.initial_value)
            elif _dt_name(self.types.resolve(f.datatype)) == _DT_STRUCT:
                v = self._struct_literal(self.types.resolve(f.datatype),
                                         ir.ExprStructLiteral(fields=[]))
            else:
                v = "0"
            parts.append(f"{f.name}: {v}")
        return f"{sv_type(dtype)}'{{{', '.join(parts)}}}"

    def _assign_from_call(self, target, v, pad: str) -> Optional[List[str]]:
        """`x = f(...)` where `f` is a TASK, not a function.

        Anything that can consume time is a task in SV, and a task has no return
        value -- the result comes back through an output argument. So a PSS
        `status = wait_completion();` is not an assignment at all here, it is
        `wait_completion(status);`.

        Getting this wrong does not produce a warning: `x = some_task();` is a
        syntax error in one simulator and a subtly different construct in
        another. Three shapes, and the output argument's POSITION differs
        between them, which is the part worth stating rather than inferring:

          register accessor  r.read(x)          -- value last, sole argument
          channel receive    c.get(x)           -- value last, sole argument
          memory primitive   read32(addr, x)    -- value last
          component op       op(x, args...)     -- status FIRST (see _signature)

        `channel_c::get` joins the first group. It blocks (§21.9.1), so it is a
        task for the same reason a register read is, and `x = c.get()` has to
        become `c.get(x)`.

        The channel's other three functions need no rewrite and must not get
        one: `try_get`/`try_put` are non-blocking and stay SV *functions*
        returning a bit, and `put` blocks but yields no value, so it lowers as
        an ordinary statement-level task call.

        These are matched by METHOD NAME, not by the receiver's type, which the
        emitter does not carry. A no-argument, value-returning operation named
        `get` on a user component would be rewritten as though it were a
        channel -- the same latent ambiguity `read`/`read_val` have always had
        here, recorded rather than fixed.
        """
        if _dt_name(v) != "ExprCall":
            return None
        tgt = ("pssc_discard" if isinstance(target, _Discard) else "status"
               if isinstance(target, _StatusTarget) else self.expr(target))
        text = self._task_form(v, tgt)
        return None if text is None else [f"{pad}{text};"]

    #: Channel methods that are SV *functions* returning a bit. Discarding a
    #: non-void function's return is legal SV but warns (IEEE 1800-2023 13.4.1),
    #: and the lint gate treats a warning as a failure.
    _CHAN_PREDICATES = ("try_get", "try_put")

    def _discard_value(self, v) -> str:
        """Statement-position call whose value the model discards.

        `inflight.try_get(tok);` is ordinary PSS -- `try_get` answers whether it
        succeeded, and a caller that has already decided what to do either way
        may ignore it. SV disagrees: `function bit try_get(...)` used as a
        statement is IGNOREDRETURN. `void'(...)` is the idiom that says "yes,
        deliberately", and it is what a hand-written testbench would write.

        Restricted to the channel predicates rather than applied to every call:
        a *task* wrapped in `void'()` is a syntax error, and the emitter cannot
        tell a task from a function by name alone. These two it can.

        Matched by METHOD NAME only, not by the receiver being a channel field
        of *this* component. `notify_irq()` is why:

            foreach (ch[i]) { ch[i].wake.try_put(1); }

        the channel belongs to the child, so a receiver-based test misses it and
        the same construct comes out wrapped in one function and bare in
        another. Verilator happens not to warn on the indexed form today, which
        is exactly the kind of difference that does not survive a change of
        simulator. Name matching accepts the ambiguity this file already accepts
        for `read`/`read_val`/`get` (see `_assign_from_call`): a user component
        method named `try_put` returning void would be mis-wrapped.
        """
        if _dt_name(v) == "ExprCall":
            fn = v.func
            if (_dt_name(fn) == "ExprAttribute"
                    and fn.attr in self._CHAN_PREDICATES):
                return f"void'({self.expr(v)})"
        return self.expr(v)

    def _discarded_get(self, v, pad: str):
        """PSS ``c.get();`` -- a blocking receive whose value is thrown away.

        `sync_pkg` declares `target function Te get();`: it takes **no
        arguments** and returns the element. The SV runtime cannot, because
        `get` blocks and so must be a `task`, and a task returns nothing --
        `pssc_reg_pkg` declares `task get(output Te t);`. The assignment form
        `x = c.get()` is already rewritten to `c.get(x)` by `_assign_from_call`.

        This is the form with no `x`. `wait_hint()` is the case that matters:

            wake.get();          // PSS -- the token carries no information

        Emitting that verbatim produces `wake.get();`, which is not a syntax
        error -- it is a *missing argument*, which Verilator rejects only at
        elaboration. Nothing earlier in the pipeline sees it, which is why this
        went undetected until the model was corrected to the valid PSS spelling
        (it previously wrote `wake.get(tok)`, passing an argument the PSS
        declaration does not have, which the emitter happened to copy through).

        The temp is `Te`-wide rather than `bit`: a narrower one is a WIDTHTRUNC
        warning, and the lint gate treats warnings as failures.

        Matched by method name and receiver, like the rest of this file -- see
        `_assign_from_call`'s note on the same latent ambiguity.
        """
        if _dt_name(v) != "ExprCall" or v.args:
            return None
        fn = v.func
        if _dt_name(fn) != "ExprAttribute" or fn.attr != "get":
            return None
        recv = getattr(fn.value, "attr", None) or getattr(fn.value, "id", None)
        elem = self.chan_elem_of.get(recv)
        if elem is None:
            return None
        # A fresh block so the temp cannot collide with a model local, and so
        # this stays a single statement to whatever encloses it (an unbraced
        # `if` arm, for instance).
        return [f"{pad}begin",
                f"{pad}  {elem} pssc_discard;",
                f"{pad}  {self.expr(fn.value)}.get(pssc_discard);",
                f"{pad}end"]

    def stmt_match(self, s, ind: int) -> List[str]:
        """PSS `match` -> SV `case`.

        An arm with no pattern value is the `default`. Arms are emitted in
        source order, so a model that relies on first-match ordering keeps its
        meaning.
        """
        pad = self.pad(ind)
        # A range label needs `case ... inside`; plain values keep `case`.
        inside = any(_has_range(c.pattern) for c in s.cases)
        lines = [f"{pad}case ({self.expr(s.subject)})"
                 + (" inside" if inside else "")]
        # An enum subject takes its labels as mnemonics: SV does not convert
        # an integer label to the enum implicitly.
        st = self.types.type_of(s.subject)
        enum = st.dtype if (st is not None and st.kind == "enum") else None
        for case in s.cases:
            labels = [self.value_of(enum, v) if enum is not None
                      and _dt_name(v) == "ExprConstant" else self.expr(v)
                      for v in self._pattern_values(case.pattern)]
            label = ", ".join(labels) if labels else "default"
            lines.append(f"{pad}  {label}: begin")
            lines += self.stmts(case.body, ind + 2)
            lines.append(f"{pad}  end")
        lines.append(f"{pad}endcase")
        return lines

    def _pattern_values(self, pattern) -> List[object]:
        """The label expressions of one `match` arm; none for `default`."""
        if pattern is None:
            return []
        cn = _dt_name(pattern)
        if cn == "PatternAs" and getattr(pattern, "pattern", None) is None:
            return []          # the wildcard: `default`
        if cn == "PatternValue":
            return [pattern.value]
        if cn in ("PatternOr", "PatternSequence"):
            out: List[object] = []
            for p in pattern.patterns:
                out += self._pattern_values(p)
            return out
        raise ValueError(f"unsupported match pattern {cn}")

    def stmt_foreach(self, s, ind: int) -> List[str]:
        """`foreach (a[i]) { ... }` -> an indexed for loop.

        SV has `foreach` too, but only over its own arrays; the PSS collection
        may be a generated member with a known size, so an explicit index keeps
        one lowering for both.
        """
        pad = self.pad(ind)
        idx = getattr(getattr(s, "target", None), "name", "i")
        coll = self.expr(s.iter)
        lines = [f"{pad}foreach ({coll}[{idx}]) begin"]
        lines += self.stmts(s.body, ind + 1)
        lines.append(f"{pad}end")
        return lines


def _enum_first(dtype, types) -> Optional[int]:
    """The first item's value of an enum ``dtype`` when it is not 0, else
    None (0 is SV's default already)."""
    dt = types.resolve(dtype)
    if _dt_name(dt) != _DT_ENUM or not dt.items:
        return None
    first = next(iter(dt.items.values()))
    return int(first) if int(first) != 0 else None


def _has_continue(body) -> bool:
    """Does ``body`` hold a `continue` for THIS loop (not a nested one's)?"""
    for st in body or []:
        cn = _dt_name(st)
        if cn == "StmtContinue":
            return True
        if cn in ("StmtWhile", "StmtRepeatWhile", "StmtFor", "StmtForeach"):
            continue
        if _has_continue(getattr(st, "body", None)) or \
                _has_continue(getattr(st, "orelse", None)):
            return True
        for case in getattr(st, "cases", None) or []:
            if _has_continue(getattr(case, "body", None)):
                return True
    return False


def _sv_of(t) -> str:
    """The SV type of an `ExprTypes.PssType`."""
    if t.kind == "int":
        if t.signed and t.width == 32:
            return "int"
        sign = " signed" if t.signed else ""
        return f"bit{sign}" if t.width == 1 else f"bit{sign} [{t.width - 1}:0]"
    if t.kind == "bool":
        return "bit"
    if t.kind == "string":
        return "string"
    if t.dtype is not None:
        return sv_type(t.dtype)
    raise ValueError(f"no SystemVerilog type for a {t.kind} value")


def _has_range(pattern) -> bool:
    cn = _dt_name(pattern)
    if cn == "PatternValue":
        return _dt_name(pattern.value) == "ExprRange"
    return any(_has_range(p) for p in (getattr(pattern, "patterns", None) or []))


#: PSS address-space builtins, lowered to arithmetic on a 64-bit address.
#: `addr_handle_t` is an opaque handle in PSS and a plain address here, so
#: deriving a handle from another is an offset add.
_ADDR_BUILTINS = {
    "make_handle_from_handle":
        lambda be, e: f"({be.expr(e.args[0])} + {be.expr(e.args[1])})",
    # A handle IS the address here, so extracting its value is the identity.
    "addr_value": lambda be, e: be.expr(e.args[0]),
}


class _ExprOnly(_BodyEmitter):
    """Expression rendering without a surrounding function.

    Used where an expression appears outside an operation body -- a field
    initializer, or an argument inside a lowered `init`.

    ``comp`` is not optional in practice: an `init` binds addresses, and the
    address arithmetic is exactly where a model calls the register group's own
    offset functions. Without the component this emitter cannot resolve `regs`
    to a register group, and the fold in `_fold_offset` degrades to an emitted
    call -- which is the defect this path had.
    """

    def __init__(self, member_of, ctor=None, comp=None):
        super().__init__(ctor, comp, member_of)


# --- emission --------------------------------------------------------------

# `ctor_names` comes down from the model, never from the ambient ContextVar
# (P6a.T5): which solve function is the constructor is the compile's answer,
# and an emitter that asks the process gets whichever compile set it last.

def _operations(comp, ctor_names=None) -> List[object]:
    return [fn for fn in comp.functions
            if func_kind(fn, ctor_names) == FuncKind.EXPORT_OP]


def _reg_group_members(comp) -> Dict[str, str]:
    """Map reg-group field name -> SV member name (``m_<name>``)."""
    return {f.name: f"m_{f.name}" for f in comp.fields if field_is_reg_group(f)}


def _members(comp) -> Dict[str, str]:
    """Map EVERY component field to its generated member name.

    All of them, not just the register groups: an operation body may read a
    plain attribute (`chan`, `caps.ars`) as readily as a register, and a field
    missing from this map is emitted as a bare identifier that resolves to
    nothing.

    CHANNELS KEEP THEIR PSS NAME, with no `m_` prefix, because they are the one
    kind of field another component reaches THROUGH an instance handle. The
    interrupt-notify shape is exactly this:

        foreach (ch[i]) { ch[i].wake.try_put(1); }   // PSS
        foreach (m_ch[i]) m_ch[i].wake.try_put(1);   // SV

    The trailing `.wake` there is a member path on a *different* object, so it
    is emitted from the PSS field name and cannot be renamed -- only the head of
    the path goes through this map. Naming the declaration `m_wake` produced
    code that referred to both `m_wake` and `.wake` for one field.

    The cost is that a channel shares a namespace with task-local names and
    operation arguments; an operation argument named after a channel would
    shadow it. Nothing checks that, and the `m_` prefix is what protects every
    other field from it.
    """
    return {f.name: (f.name if field_is_channel(f) else f"m_{f.name}")
            for f in comp.fields}


def _data_fields(comp) -> List[object]:
    """Fields that become plain data members: not registers, not
    sub-components -- the component's own attributes."""
    subs = {s.name for s in sub_components(comp)}
    out = []
    for f in comp.fields:
        if field_is_reg_group(f) or f.name in subs:
            continue
        # A chandle field is an address handle (`sv_type`): a component
        # that keeps its base for its `init_down` to use holds one.
        if _dt_name(f.datatype) in (_DT_INT, _DT_STRUCT, _DT_ENUM, _DT_CHANDLE):
            out.append(f)
    return out


def emit_export_api(comp, ctor_names=None, entries=()) -> str:
    """The export interface class: one pure virtual task per operation, plus an
    accessor per sub-component instance.

    Sub-components are exposed as **methods**, not as members, because an SV
    interface class cannot declare data. An array becomes `ch(int index)` plus
    `ch_size()`; a scalar instance becomes a bare `sub()`. The accessor returns
    the sub-component's own *interface* type, which is why sub-component classes
    are not parameterized (see `emit_subcomponent_class`).
    """
    cls = f"{_strip_pkg(comp.name)}_if"
    lines = doc_block(getattr(comp, "doc", None), "  ") + [
        f"  interface class {cls};"]
    for fn in _operations(comp, ctor_names):
        # The interface is the API surface a caller reads and the
        # implementation is what someone debugging reads, so the doc block goes
        # on both. This is the one place a comment is deliberately duplicated.
        blank_line(lines)
        lines += doc_block(getattr(fn, "doc", None), "    ")
        lines.append(f"    pure virtual task {mangle(fn.name)}({_signature(fn)});")
    for entry in entries:
        blank_line(lines)
        lines.append(f"    // Exported action `{entry.action}`.")
        lines.append(f"    pure virtual task {mangle(entry.name)}();")
    for sub in sub_components(comp):
        # One blank before each sub-component's accessors, not between them:
        # `ch()` and `ch_size()` are one member's plumbing, not two entries.
        blank_line(lines)
        sub_if = f"{_strip_pkg(sub.dtype.name)}_if"
        if sub.is_array:
            lines.append(f"    pure virtual function {sub_if} {mangle(sub.name)}(int index);")
            lines.append(f"    pure virtual function int {mangle(sub.name)}_size();")
        else:
            lines.append(f"    pure virtual function {sub_if} {mangle(sub.name)}();")
    lines.append("  endclass")
    return "\n".join(lines)


def _ctor(comp, ctor_names=None):
    for fn in comp.functions:
        if func_kind(fn, ctor_names) == FuncKind.CONSTRUCTOR:
            return fn
    return None


def lower_component_api(comp, ctor_names=None, entries=()) -> str:
    """Export interface for a regular component, as package-body text.

    The implementation is folded into the component class itself (see
    `emit_component`), so there is no separate `<comp>_impl`.
    """
    return emit_export_api(comp, ctor_names, entries)


# --- import API, adapter, factory (Phase 5) --------------------------------

# The core memory-access ABI: (method, data-type, is_read). Frozen to match
# pssc_reg_pkg::pss_mem_if.
_MEM_PRIMS = [
    ("write8", "bit [7:0]", False), ("read8", "bit [7:0]", True),
    ("write16", "bit [15:0]", False), ("read16", "bit [15:0]", True),
    ("write32", "bit [31:0]", False), ("read32", "bit [31:0]", True),
    ("write64", "bit [63:0]", False), ("read64", "bit [63:0]", True),
]


def emit_import_api(root, ctor_names=None) -> str:
    """``interface class <root>_import_if extends pss_mem_if`` plus any
    engine-specific import functions (none for the WB DMA engine)."""
    cls = f"{_strip_pkg(root.name)}_import_if"
    lines = [f"  interface class {cls} extends pss_mem_if;"]
    for fn in root.functions:
        k = func_kind(fn, ctor_names)
        if k == FuncKind.IMPORT_TASK:
            lines.append(f"    pure virtual task {mangle(fn.name)}({_signature(fn)});")
        elif k == FuncKind.IMPORT_SOLVE:
            ret = sv_type(fn.returns) if fn.returns is not None else "void"
            lines.append(f"    pure virtual function {ret} {mangle(fn.name)}({_signature(fn)});")
    lines.append("  endclass")
    return "\n".join(lines)


def _member_decls(comp, members: Dict[str, str], subs: Dict[str, SubComp]) -> List[str]:
    """Declarations for a component's register groups, data and sub-components."""
    lines: List[str] = []
    for f in comp.fields:
        if field_is_reg_group(f):
            lines.append(f"    protected {_strip_pkg(f.datatype.name)} {members[f.name]};")
    for f in _data_fields(comp):
        lines += comment_lines(getattr(f, "doc", None), "    ")
        lines.append(f"    protected {sv_type(f.datatype)} {members[f.name]};")
    # Channels are PUBLIC, deliberately. Every other member is `protected`
    # because the export interface is the supported surface; a channel is
    # reached by name from the owning component's siblings and parent (see
    # `_members`), which `protected` would reject at compile time.
    for f in channel_fields(comp):
        lines.append(f"    {sv_type(f.datatype)} {members[f.name]};")
    for sub in subs.values():
        cls = _strip_pkg(sub.dtype.name)
        dim = f"[{sub.size}]" if sub.is_array else ""
        lines.append(f"    protected {cls} {members[sub.name]}{dim};")
    return lines


def _field_defaults(comp, members: Dict[str, str], subs: Dict[str, SubComp]) -> List[str]:
    """Assign PSS field initializers, before the address binding runs.

    A default is part of a field's meaning: `wb_dma_ch_caps_s` declares every
    capability `true`, and a model that reads back all-false silently refuses to
    attempt the operations those capabilities gate.
    """
    lines: List[str] = []
    # Channels first: an unconstructed channel handle is null, and a null
    # dereference in SV is a run-time error at the first `get`/`try_put`, long
    # after the construction that should have happened. Nothing else in the
    # constructor depends on them, so they go at the top where they cannot be
    # skipped by an early return in a hand-written `init`.
    for f in channel_fields(comp):
        lines.append(f"      {members[f.name]} = new();")
    be = _ExprOnly(members, comp=comp)
    for f in _data_fields(comp):
        if f.initial_value is not None:
            lines.append(f"      {members[f.name]} = {be.expr(f.initial_value)};")
            continue
        # A struct-typed attribute carries its defaults on the STRUCT's fields,
        # not on the instance, so they have to be walked out member by member.
        if _dt_name(f.datatype) == _DT_STRUCT:
            for sf in getattr(f.datatype, "fields", []) or []:
                if sf.initial_value is not None:
                    lines.append(
                        f"      {members[f.name]}.{sf.name} = {be.expr(sf.initial_value)};")
    return lines


def component_base_name(root) -> str:
    """The generated base class every component class extends (design D8)."""
    return f"{_strip_pkg(root.name)}_component"


def emit_component_base(root) -> str:
    """The common base of every non-root component class (design D4, D8).

    It holds the import API and PSS construction (LRM 20.1.2): this
    component's `init_down`, then every sub-component's whole construction,
    then its `init_up` -- one hook per step, each overridden only by a class
    that has something to put there. The `pss_` prefix keeps the hooks clear
    of PSS functions: SV has one method namespace per class.
    """
    name = component_base_name(root)
    bus_if = f"{_strip_pkg(root.name)}_import_if"
    return "\n".join([
        "  // Common base of every component class: the import API, and PSS",
        "  // construction (LRM 20.1.2) as one hook per step.",
        f"  virtual class {name};",
        f"    protected {bus_if} m_imp;",
        "",
        f"    function new({bus_if} imp);",
        "      m_imp = imp;",
        "    endfunction",
        "",
    ] + _empty_hooks(()) + [""] + _DO_INIT + [
        "  endclass",
    ])


#: The construction hooks, in the order `pss_do_init` runs them.
_HOOKS = ("pss_init_down", "pss_init_subs", "pss_init_up")

#: The one method that runs PSS construction, defined once per hierarchy.
_DO_INIT = [
    "    function void pss_do_init();",
    "      pss_init_down();",
    "      pss_init_subs();",
    "      pss_init_up();",
    "    endfunction",
    "",
]


def _empty_hooks(defined) -> List[str]:
    """Empty definitions of the hooks not in ``defined``."""
    return [f"    virtual function void {h}(); endfunction"
            for h in _HOOKS if h not in defined]


def _defined_hooks(comp, subs) -> set:
    """The hooks `_init_hook_defs` gives ``comp`` a body for."""
    defined = {f"pss_{k}" for k in INIT_EXEC_KINDS if _init_blocks(comp, k)}
    if subs:
        defined.add("pss_init_subs")
    return defined


def _construct_body(comp, members, subs, *, bus: str) -> List[str]:
    """The body of `new()`, after the import API is stored (design D2).

    Fields at their defaults, channels built, register groups at address 0,
    and EVERY sub-component instance constructed -- whether or not the model
    has a constructor for it (SV-3). Where each register group really lives is
    `initialize`'s business, which runs after this.
    """
    lines = _field_defaults(comp, members, subs)
    for f in comp.fields:
        if field_is_reg_group(f):
            lines.append(f"      {members[f.name]} = new({bus}, 0);")
    for sub in subs.values():
        m = members[sub.name]
        if sub.is_array:
            lines.append(f"      foreach ({m}[i]) {m}[i] = new({bus});")
        else:
            lines.append(f"      {m} = new({bus});")
    return lines


def _initialize_def(comp, ctor, members, subs, *, bus: str) -> List[str]:
    """The op-model constructor, as a method (design D3). Not virtual: every
    caller uses its member's declared type.

    With a body, that body IS the address binding and is lowered statement by
    statement (`lower_init`). An EMPTY body keeps the flat convention the
    backends share: every register group sits at the first argument.
    """
    if ctor is None:
        return []
    params = ", ".join(f"{sv_type(a.annotation)} {mangle(a.arg)}"
                       for a in ctor.args.args)
    reg_groups = [f.name for f in comp.fields if field_is_reg_group(f)]
    if ctor.body:
        from .lower_init import lower_init
        be = _ExprOnly(members, ctor=ctor, comp=comp)
        body = lower_init(ctor, members=members, reg_groups=reg_groups,
                          subs=subs, bus=bus, expr=be.expr, indent=3)
    elif ctor.args.args:
        base = mangle(ctor.args.args[0].arg)
        body = [f"      {members[g]} = new({bus}, {base});" for g in reg_groups]
    else:
        body = []
    lines = doc_block(getattr(ctor, "doc", None), "    ")
    lines.append(f"    function void {mangle(ctor.name)}({params});")
    lines += body
    lines.append("    endfunction")
    lines.append("")
    return lines


def _init_blocks(comp, kind: str) -> List[object]:
    return [fn for fn in (comp.functions or [])
            if exec_kind(fn) == kind
            and SUPER_BLOCK not in (fn.metadata or {})]


def _super_blocks(comp) -> List[object]:
    return [fn for fn in (comp.functions or [])
            if SUPER_BLOCK in (fn.metadata or {})]


def _init_hook_defs(comp, members, subs, namer=None,
                    ctor_names=None, ctx=None) -> List[str]:
    """The construction hooks this component has something to put in.

    `pss_init_down`/`pss_init_up` hold its `exec` blocks of that kind, in
    source order (LRM 20.1 d); `pss_init_subs` runs each sub-component's whole
    construction, in declaration order. A base's blocks that `super;` reaches
    (the flattened view) are private methods of their own.
    """
    lines: List[str] = []

    def block_body(fns, method):
        body = []
        for fn in fns:
            be = _BodyEmitter(fn, comp, members, namer=namer,
                              ctor_names=ctor_names, ctx=ctx)
            body += be.stmts(fn.body, 3)
        lines.append(f"    {method}")
        lines.extend(body)
        lines.append("    endfunction")
        lines.append("")

    for fn in _super_blocks(comp):
        block_body([fn], f"function void {fn.name}();")
    for kind in INIT_EXEC_KINDS:
        blocks = _init_blocks(comp, kind)
        if blocks:
            block_body(blocks, f"virtual function void pss_{kind}();")
    if subs:
        lines.append("    virtual function void pss_init_subs();")
        for sub in subs.values():
            m = members[sub.name]
            if sub.is_array:
                lines.append(f"      foreach ({m}[i]) {m}[i].pss_do_init();")
            else:
                lines.append(f"      {m}.pss_do_init();")
        lines.append("    endfunction")
        lines.append("")
    return lines


def _operation_defs(comp, members: Dict[str, str], namer=None,
                    ctor_names=None, ctx=None) -> List[str]:
    """Export operations. `virtual`, not plain -- they implement the export
    interface's pure virtuals, and stricter simulators require the override."""
    lines: List[str] = []
    for fn in _operations(comp, ctor_names):
        be = _BodyEmitter(fn, comp, members, namer=namer,
                          ctor_names=ctor_names, ctx=ctx)
        blank_line(lines)
        lines += doc_block(getattr(fn, "doc", None), "    ")
        lines.append(f"    virtual task {mangle(fn.name)}({_signature(fn)});")
        lines += be.stmts(fn.body, 3)
        lines.append("    endtask")
        lines.append("")
    return lines


def _params(fn, be, *, status: bool) -> str:
    """The parenthesized parameter list: a leading `output <T> status` when a
    task returns a value, then every argument explicit `input`, with its PSS
    default (22.2.4) where it has one."""
    args = list(fn.args.args) if fn.args else []
    defaults = list(getattr(fn.args, "defaults", None) or [])
    first = len(args) - len(defaults)
    parts: List[str] = []
    if status and fn.returns is not None:
        parts.append(f"output {sv_type(fn.returns)} status")
    for i, a in enumerate(args):
        p = (f"{param_dir(a.annotation, a.arg in _const_params(fn))} "
             f"{sv_type(a.annotation)} {mangle(a.arg)}")
        if i >= first:
            p += f" = {be.expr(defaults[i - first])}"
        parts.append(p)
    return ", ".join(parts)


def _routine(fn, be, name: str, prefix: str, pad: str) -> List[str]:
    """One function rendered as an SV task or function, per `is_task`."""
    lines = doc_block(getattr(fn, "doc", None), pad)
    if is_task(fn):
        lines.append(f"{pad}{prefix}task {name}({_params(fn, be, status=True)});")
        end = "endtask"
    else:
        ret = sv_type(fn.returns) if fn.returns is not None else "void"
        lines.append(f"{pad}{prefix}function {ret} {name}"
                     f"({_params(fn, be, status=False)});")
        end = "endfunction"
    lines += be.stmts(fn.body, len(pad) // 2 + 1)
    lines.append(f"{pad}{end}")
    return lines


def _solve_fn_defs(comp, members: Dict[str, str], namer=None,
                   ctor_names=None, ctx=None) -> List[str]:
    """The component's `solve function`s (the constructor aside): SV
    functions, called from solve context -- another solve function, the
    constructor, an `exec init_*` block -- and returning their value."""
    lines: List[str] = []
    for fn in comp.functions:
        if func_kind(fn, ctor_names) is not FuncKind.EXPORT_SOLVE:
            continue
        be = _BodyEmitter(fn, comp, members, namer=namer,
                          ctor_names=ctor_names, ctx=ctx)
        blank_line(lines)
        lines += _routine(fn, be, mangle(fn.name), "virtual ", "    ")
        lines.append("")
    return lines


def emit_package_functions(functions, ctx=None, ctor_names=None) -> str:
    """Package-scope functions the model calls (`pkg_functions.py`), as
    `automatic` package tasks and functions -- automatic, because a PSS
    function may recurse. A package function has no component, so one that
    reaches the platform (a memory primitive, `yield`) is refused: nothing
    here gives it the import API yet."""
    lines: List[str] = []
    for fn in functions:
        be = _BodyEmitter(fn, None, {}, ctor_names=ctor_names, ctx=ctx)
        text = _routine(fn, be, pkg_function_name(fn), "automatic ", "  ")
        if any("m_imp." in l or any(f"{m}(" in l for m, _, _ in _MEM_PRIMS)
               for l in text[1:]):
            raise ValueError(
                f"package function '{pf.qualified_name(fn)}' reaches the "
                f"platform; op-model-sv does not pass package functions the "
                f"import API yet")
        blank_line(lines)
        # `automatic` belongs after the keyword: `task automatic f(...)`.
        text = [l.replace("automatic task ", "task automatic ", 1)
                .replace("automatic function ", "function automatic ", 1)
                for l in text]
        lines += text
    return "\n".join(lines)


def _entry_defs(comp, entries, members: Dict[str, str], namer=None,
                ctor_names=None, ctx=None) -> List[str]:
    """Exported actions (`--export-action`), each a task of the component it
    runs in: no arguments, no result (`export_action.py`). The base actions'
    bodies its `super;` reaches are private tasks of their own, so their
    locals and a `return` in one stay their own."""
    lines: List[str] = []
    for entry in entries:
        for k, fn in enumerate(entry.functions):
            be = _BodyEmitter(fn, comp, members, namer=namer,
                              ctor_names=ctor_names, ctx=ctx)
            blank_line(lines)
            if k == 0:
                lines.append(f"    // Exported action `{entry.action}`.")
                lines.append(f"    virtual task {mangle(fn.name)}();")
            else:
                lines.append(f"    // `super;` of `{entry.action}`: the body "
                             f"of `{fn.metadata.get('super_of', '?')}`.")
                lines.append(f"    protected task {fn.name}();")
            lines += be.stmts(fn.body, 3)
            lines.append("    endtask")
            lines.append("")
    return lines


def _accessor_defs(comp, members: Dict[str, str], subs: Dict[str, SubComp]) -> List[str]:
    """Implementations of the interface's sub-component accessors."""
    lines: List[str] = []
    for sub in subs.values():
        sub_if = f"{_strip_pkg(sub.dtype.name)}_if"
        m = members[sub.name]
        nm = mangle(sub.name)
        if sub.is_array:
            lines.append(f"    virtual function {sub_if} {nm}(int index);")
            lines.append(f"      return {m}[index];")
            lines.append(f"    endfunction")
            lines.append(f"    virtual function int {nm}_size();")
            lines.append(f"      return {sub.size};")
            lines.append(f"    endfunction")
        else:
            lines.append(f"    virtual function {sub_if} {nm}();")
            lines.append(f"      return {m};")
            lines.append(f"    endfunction")
        lines.append("")
    return lines


def emit_subcomponent_class(comp, root, namer=None, ctor_names=None,
                            entries=(), ctx=None) -> str:
    """A non-root component's implementation class.

    Deliberately **not** parameterized. Only the root carries `#(type IMP_T)`,
    because the root's accessors return sub-component interface handles: if a
    sub-component class were parameterized, its parameter would leak into the
    root's interface, and the export API would stop being a plain handle type.

    It extends the generated component base, which holds the import API --
    the root's import interface, so a sub-component can both reach memory and
    use the root's other import functions. `new()` takes only that, so every
    class is constructible the same way (design D2); the model's constructor
    is a method (D3).
    """
    name = _strip_pkg(comp.name)
    api = f"{name}_if"
    bus_if = f"{_strip_pkg(root.name)}_import_if"
    members = _members(comp)
    subs = {s.name: s for s in sub_components(comp)}

    lines = doc_block(getattr(comp, "doc", None), "  ") + [
        f"  class {name} extends {component_base_name(root)} implements {api};",
    ]
    lines += _member_decls(comp, members, subs)
    lines.append("")
    lines.append(f"    function new({bus_if} imp);")
    lines.append(f"      super.new(imp);")
    lines += _construct_body(comp, members, subs, bus="m_imp")
    lines.append("    endfunction")
    lines.append("")
    lines += _initialize_def(comp, _ctor(comp, ctor_names), members, subs,
                             bus="m_imp")
    lines += _init_hook_defs(comp, members, subs, namer, ctor_names, ctx)
    lines += _solve_fn_defs(comp, members, namer, ctor_names, ctx)
    lines += _operation_defs(comp, members, namer, ctor_names, ctx)
    lines += _entry_defs(comp, entries, members, namer, ctor_names, ctx)
    lines += _accessor_defs(comp, members, subs)
    lines.append("  endclass")
    return "\n".join(lines)


def emit_component(root, namer=None,
                   ctor_names=None, entries=(), ctx=None) -> str:
    """The component class, named after the component itself -- one class that is

      * the **export implementation** (`implements <comp>_if`, the operations),
      * the **import redirect** (`implements <comp>_import_if`, forwarding each
        primitive to the user object `m_imp`), and
      * the **factory** (static `create()`).

    It holds the register model and the sub-components, constructed with
    ``this`` as the bus: because the class is-a ``pss_mem_if`` (via the import
    interface), register accesses route ``m_regs -> this.write32/read32 ->
    m_imp`` to the user object. ``IMP_T`` defaults to the component's
    ``import_if``; override it to wire a signature-compatible (duck-typed)
    object that does not formally implement it.

    It does not extend the component base: its `m_imp` is the user object,
    of type ``IMP_T``. It carries the construction hooks itself instead.
    """
    name = _strip_pkg(root.name)
    api = f"{name}_if"
    import_if = f"{name}_import_if"
    members = _members(root)
    subs = {s.name: s for s in sub_components(root)}

    ctor = _ctor(root, ctor_names)
    ctor_params = ""
    fwd = ""
    if ctor is not None and ctor.args.args:
        ps = [f"{sv_type(a.annotation)} {mangle(a.arg)}" for a in ctor.args.args]
        names = [mangle(a.arg) for a in ctor.args.args]
        ctor_params = ", " + ", ".join(ps)
        fwd = ", ".join(names)

    lines = doc_block(getattr(root, "doc", None), "  ") + [
        f"  class {name} #(type IMP_T = {import_if}) implements {api}, {import_if};",
        f"    protected IMP_T m_imp;",
    ]
    lines += _member_decls(root, members, subs)
    lines.append("")

    # Construction only: `create()` runs the model's constructor and then
    # PSS construction, in that order (design D3).
    lines.append(f"    protected function new(IMP_T imp);")
    lines.append(f"      m_imp = imp;")
    lines += _construct_body(root, members, subs, bus="this")
    lines.append("    endfunction")
    lines.append("")
    lines += _initialize_def(root, ctor, members, subs, bus="this")
    # No base to inherit the empty hooks or `pss_do_init` from.
    empty = _empty_hooks(_defined_hooks(root, subs))
    lines += empty + ([""] if empty else []) + _DO_INIT
    lines += _init_hook_defs(root, members, subs, namer, ctor_names, ctx)

    lines += _solve_fn_defs(root, members, namer, ctor_names, ctx)
    lines += _operation_defs(root, members, namer, ctor_names, ctx)
    lines += _entry_defs(root, entries, members, namer, ctor_names, ctx)
    lines += _accessor_defs(root, members, subs)

    # import redirect: forward each memory-access primitive to the user object.
    for meth, dt, is_read in _MEM_PRIMS:
        if is_read:
            lines.append(
                f"    virtual task {meth}(addr_handle_t addr, output {dt} data); "
                f"m_imp.{meth}(addr, data); endtask")
        else:
            lines.append(
                f"    virtual task {meth}(addr_handle_t addr, {dt} data); "
                f"m_imp.{meth}(addr, data); endtask")

    # static factory entry point -> returns the export-interface handle.
    lines += [
        f"    static function {api} create(IMP_T imp{ctor_params});",
        f"      {name} #(IMP_T) self = new(imp);",
    ]
    if ctor is not None:
        lines.append(f"      self.{mangle(ctor.name)}({fwd});")
    lines += [
        f"      self.pss_do_init();",
        f"      return self;",
        f"    endfunction",
        f"  endclass",
    ]
    return "\n".join(lines)


def lower_root_glue(root) -> str:
    """Import interface + the merged component class for the root component."""
    return "\n\n".join([emit_import_api(root), emit_component(root)])
