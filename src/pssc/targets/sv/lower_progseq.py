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
from typing import Any, Dict, List, Optional, Tuple

import zuspec.ir.core as ir

from .. import pkg_functions as pf
from ..bit_select import bit_select
from ..expr_types import is_integral
from ...diag import Refusal
from ..body_walker import (BodyWalker, assigns_local, foreach_parts,
                           in_as_compare, match_values)
from ..string_methods import method_of as string_method, substring
from ..validate_calls import callee_name, is_core_call
from ..array_literal import is_literal
from ..progseq_model import (channel_count, channel_dtype, enum_first_item, func_kind, FuncKind, field_is_reg_group, _dt_name,
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

# SystemVerilog's reserved words (IEEE 1800 Annex B, `keywords.py`); a PSS
# identifier spelled as one is renamed by appending '_' so generated code
# compiles.
from .keywords import SV_KEYWORDS as _SV_KEYWORDS


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
    # SV's `**` is PSS's (8.5.1 Table 13 is SV's Table 11-4): the left
    # operand's type, the right self-determined (E4).
    "Exp": "**",
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
                 ctx=None, consts=None):
        self.fn = fn
        self.comp = comp
        #: How a folded PSS constant is spelled (`constants.SvConstants`):
        #: by name where the package declares it. None spells every one as
        #: its value.
        self.consts = consts
        #: The translation context, for typing `message()` arguments. None
        #: types every argument as `int`.
        self.ctx = ctx
        self._types = None
        #: This compile's constructor names, from the model (never ambient).
        self.ctor_names = ctor_names
        # Restores field names to the folded masks `reg_rmw` produced. None
        # disables it, and every call site then emits the literal pair it
        # emitted before this existed -- so the naming is never load-bearing.
        self.namer = namer
        args = (fn.args.args if fn is not None and fn.args else [])
        # rename map for SV-keyword args
        self.arg_rename = {a.arg: mangle(a.arg) for a in args}
        self.arg_names = set(self.arg_rename)
        # component field name -> member name: its PSS name, SV-keyword safe.
        self.member_of = member_of
        #: The class this one extends, when inheritance is rendered natively:
        #: `super.f(...)` is a call of ITS `f`. None for a class with no user
        #: base.
        self.super_base = None
        #: Names of the locals the body declares. A field of the same name is
        #: then written `this.<name>`: SV would take the local, as PSS does
        #: for a bare name -- but the IR's `self.x` means the field.
        self.local_names = _local_names(getattr(fn, "body", None))
        #: What the import API supplies: the memory primitives and the
        #: model's import functions.
        self.imp_names = frozenset(
            [m for m, _, _ in _MEM_PRIMS]
            + [f.name for f in (getattr(ctx, "import_functions", None) or [])])
        self.own_fn_names = frozenset(
            f.name for f in (getattr(comp, "functions", None) or []))
        self.import_fn_names = frozenset(
            f.name for f in (getattr(ctx, "import_functions", None) or []))
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
                elem = getattr(channel_dtype(f), "element_type", None)
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
        if self._import_callee(call) is not None:
            task = self._task_callee(call)
            return task is not None and task.returns is not None
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
        recv = _chan_name(fn.value)
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
        imp = self._import_callee(v)
        if imp is not None:
            if func_kind(imp, self.ctor_names) is not FuncKind.IMPORT_TASK \
                    or imp.returns is None:
                return None
            self._check_callable(v)
            args = ", ".join([tgt] + self._args(v, imp))
            return f"{IMP_MEMBER}.{mangle(imp.name)}({args})"
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
        args = list(call.args)
        if len(args) < 2:
            raise ValueError("'message' needs a verbosity and a format string")
        return "$display(" + ", ".join(self._sv_format("message", args[1:])) \
            + ")"

    #: The SV task each PSS output function is, after its format is
    #: translated as `message`'s is (W11): `format` returns the text
    #: (`$sformatf`), `print` adds no newline (21.1.2),
    #: `error`/`fatal` are SV's own (21.3). `fatal`'s status has no SV
    #: counterpart -- `$fatal`'s first argument is a verbosity -- so it is
    #: dropped, and the run stops with SV's.
    _OUTPUT_TASKS = {"print": "$write", "error": "$error", "fatal": "$fatal",
                     "format": "$sformatf"}

    def _output_call(self, name: str, call) -> str:
        args = list(call.args)
        if name == "fatal":
            if not args:
                raise ValueError("'fatal' needs a status and a format string")
            args = args[1:]
        parts = self._sv_format(name, args)
        if name == "fatal":
            parts = ["1"] + parts
        return f"{self._OUTPUT_TASKS[name]}(" + ", ".join(parts) + ")"

    def _sv_format(self, name: str, rest) -> List[str]:
        """``[format literal, args...]`` for a PSS format and its arguments
        (LRM 21.1.1), translated as `_message` describes."""
        from ..py.pss_format import parse_format
        if not rest:
            raise ValueError(f"'{name}' needs a format string")
        fmt_e, vals = rest[0], rest[1:]
        fmt = getattr(fmt_e, "value", None)
        if _dt_name(fmt_e) != "ExprConstant" or not isinstance(fmt, str):
            raise Refusal(
                f"'{name}' needs a string literal as its format; got "
                f"{_dt_name(fmt_e)}", fmt_e)
        try:
            specs = parse_format(fmt)
        except ValueError as e:
            raise Refusal(f"{name}({fmt!r}): {e} (LRM 21.1.1 a)", fmt_e) from None
        if len(specs) != len(vals):
            raise Refusal(
                f"{name}({fmt!r}): {len(specs)} format specifier(s) for "
                f"{len(vals)} argument(s) (LRM 21.1.1 b)", fmt_e)

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
                raise Refusal(
                    f"{name}({fmt!r}): '{fmt[start:end]}' has no "
                    f"SystemVerilog rendering yet (only unpadded %d %u %x %o "
                    f"%b %n %s)", fmt_e)
            spec, arg = self._fmt_arg(conv, v)
            out.append(spec)
            sv_args.append(arg)
        out.append(lit(fmt[pos:]))
        return ['"' + "".join(out) + '"'] + sv_args

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
                # The PSS item's name (21.1.1), not the enumerator's.
                from .constants import SvConstants
                spell = self.consts if self.consts is not None \
                    else SvConstants()
                return "%s", f"{spell.name_fn(t.dtype)}({x})"
            raise ValueError(f"'%n' takes a bool or an enum; '{x}' is {kind}")
        # An integer conversion. A bool or enum formats as its value.
        signed = t.signed if (t is not None and kind == "int") else kind == "enum"
        if kind == "int" and t is not None and _dt_name(v) in (
                "ExprBin", "ExprUnary", "ExprIfExp", "ExprCast") \
                and self._block is not None and self._prefix is not None:
            # A message argument is self-determined (8.7.2, Table 23), as in
            # SV, and is held in a temporary of its PSS type first, which
            # IEEE 1800 makes the same value. Verilator 5.052 evaluates some
            # self-determined `$display` arguments too wide: an 8-bit
            # -2 ** 3 printed `fa0bf8`, even under a size cast (W27), and
            # `4'(w) + 4'(15)` lost the size cast on w (W28). An assignment
            # to a variable of the width is right in both.
            tmp = self._temp(_sv_of(t))
            self._emit_prefix([f"{tmp} = {x};"], "a message argument")
            x = tmp
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
        imp = self._import_callee(call)
        if imp is not None:
            return imp if func_kind(imp, self.ctor_names) is \
                FuncKind.IMPORT_TASK else None
        f = call.func
        if _dt_name(f) != "ExprAttribute":
            return None
        if _dt_name(f.value) == "TypeExprRefSelf":
            return self.op_fns.get(f.attr)
        if _dt_name(f.value) == "TypeExprRefSuper":
            return next((g for g in self._super_fns(f.attr)
                         if func_kind(g, self.ctor_names)
                         is FuncKind.EXPORT_OP), None)
        # An operation of a sub-component, reached through its instance.
        t = self.types.type_of(f.value)
        if t is None or t.kind != "comp" or t.dtype is None:
            return None
        return next((g for g in (getattr(t.dtype, "functions", None) or [])
                     if g.name == f.attr and func_kind(g, self.ctor_names)
                     is FuncKind.EXPORT_OP), None)

    def _import_callee(self, call):
        """The import function ``call`` reaches through the import API --
        `f(...)` or the qualified `p_pkg::f(...)` (see `expr_call`) -- or
        None. The component's own function of that name comes first."""
        f = call.func
        if _dt_name(f) == "ExprAttribute" \
                and _dt_name(f.value) == "TypeExprRefSelf":
            name = f.attr
        elif _dt_name(f) == "ExprRefUnresolved":
            name = f.name
        else:
            return None
        if name not in self.import_fn_names or name in self.own_fn_names:
            return None
        return next((g for g in _import_fns(self.ctx, self.ctor_names)
                     if g.name == name), None)

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
        elif _dt_name(f.value) == "TypeExprRefSuper":
            return next(iter(self._super_fns(f.attr)), None)
        else:
            t = self.types.type_of(f.value)
            owner = t.dtype if (t is not None and t.kind == "comp") else None
        return next((g for g in (getattr(owner, "functions", None) or [])
                     if g.name == f.attr), None)

    def _super_fns(self, name: str) -> List[object]:
        """The functions ``super.<name>`` may name: the base class's, as
        completed (so a function it inherits is there too)."""
        return [g for g in (getattr(self.super_base, "functions", None) or [])
                if g.name == name and exec_kind(g) is None]

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

    def expr_in(self, e) -> str:
        """`v in [...]` (8.5.9), as the comparisons it means (E3)."""
        return self.expr(in_as_compare(e, self.types,
                                       getattr(self.fn, "name", "?")))

    def expr_null(self, e) -> str:
        """`null` (7.7): the empty chandle. A chandle is an address on this
        target, as `addr_handle_t` (a `typedef chandle`) is, so it is 0 (E12)."""
        return "'0"

    def expr_constant(self, e) -> str:
        name = self.consts.name_of(e) if self.consts is not None else None
        if name is not None:
            return name
        v = e.value
        if isinstance(v, bool):
            return str(int(v))
        if isinstance(v, int):
            return _sv_int_literal(e)
        if isinstance(v, str):
            # NOT repr(): Python prefers single quotes, and 'x' in
            # SystemVerilog is the start of a based literal, not a string.
            # Every message() in the model came out as a syntax error.
            return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
        return repr(v)

    def expr_ref_local(self, e) -> str:
        # A local is renamed as a parameter or field is: `int wor;` does not
        # parse. `_local_names` already spells them this way.
        name = self.arg_rename.get(e.name) or mangle(e.name)
        if self.consts is not None and name in self.consts.declared():
            # A local named like a constant the package declares would hide
            # it for the whole task -- SV declarations are hoisted -- so a
            # `::G` read before or beside `int G` read the local (W63).
            return f"{name}_l"
        return name

    def type_expr_ref_self(self, e) -> str:
        return "this"

    def type_expr_ref_super(self, e) -> str:
        # `super.f(...)` and `super.x` are SystemVerilog's own: the base
        # class's `f`, statically, and the base class's `x`, which a derived
        # class's field of the same name hides and does not replace (17.1).
        return "super"

    def expr_attribute(self, e) -> str:
        base = e.value
        if _dt_name(base) == "TypeExprRefSelf":
            # self.<x>: a component field -> member; a function arg -> name
            # A parameter shadows a field of the same name (scope order).
            if e.attr in self.arg_names:
                return self.arg_rename[e.attr]
            if e.attr in self.member_of:
                m = self.member_of[e.attr]
                return f"this.{m}" if m in self.local_names else m
            if e.attr in self.imp_names and e.attr not in self.own_fn_names:
                # The platform's: a memory primitive or an import function,
                # reached through the import API every class holds. The
                # component's own function of that name comes first
                # (`validate_calls.ops_for_call`).
                return f"{IMP_MEMBER}.{e.attr}"
            return e.attr
        return f"{self.expr(base)}.{e.attr}"

    def _string_method(self, call, m: str) -> str:
        """A string method (7.6.3): SV's own, and `pssc_reg_pkg`'s
        `pssc_str_find[_last]` for the two it lacks."""
        s = self._string_receiver(call.func.value)
        args = [self.expr(a) for a in call.args]
        if m == "size":
            return f"{s}.len()"
        if m in ("lower", "upper"):
            return f"{s}.to{m}()"
        return f"pssc_str_{m}({', '.join([s] + args)})"

    def _string_receiver(self, e) -> str:
        """A string a method is called on. SV calls a method on a variable,
        not on a literal or a call's result, so anything else is held in a
        temporary first."""
        text = self._operand(e)
        if _is_path(e):
            return text
        tmp = self._temp("string")
        self._emit_prefix([f"{tmp} = {text};"], "a string method")
        return tmp

    def expr_subscript(self, e) -> str:
        sub = substring(e, self.types)
        if sub is not None:
            # `s[a..b]` (8.6.3): `substr(a, b)`, both positions included.
            s, lo, hi = sub
            st = self._string_receiver(s)
            lo_t = self.expr(lo) if lo is not None else "0"
            hi_t = self.expr(hi) if hi is not None else f"{st}.len() - 1"
            return f"{st}.substr({lo_t}, {hi_t})"
        sel = bit_select(e, self.types)
        if sel is None:
            from ..index_bounds import index_bound
            n = index_bound(e)
            if n is not None:
                # Out of range is a run-time error (8.6.2, R12): SV would
                # read a default and drop a write. `pssc_index` reports it.
                return (f"{self.expr(e.value)}"
                        f"[pssc_index({self.expr(e.slice)}, {n})]")
            return f"{self.expr(e.value)}[{self.expr(e.slice)}]"
        # A bit or part select (`bit_select`). SV has them natively, on
        # anything with a name -- a variable, a member, a packed struct
        # field -- which is also all an assignment can target. A select of
        # any other value (a call's result) is shifted down and masked.
        base = self.expr(sel.base)
        lo = (str(sel.lo_const) if sel.lo_const is not None
              else self.expr(sel.lo))
        if _is_path(sel.base):
            if sel.width == 1:
                return f"{base}[{lo}]"
            return f"{base}[{sel.hi_const}:{lo}]"
        return f"(({base} >> {lo}) & {sel.width}'h{sel.mask:x})"

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
        if op in ("==", "!=") and _dt_name(e.rhs) == "ExprStructLiteral":
            # `s == {...}` (8.5.3, Example 33): the literal is typed by the
            # struct it is compared with (E13).
            t = self.types.type_of(e.lhs)
            return (f"{self._operand(e.lhs)} {op} "
                    f"{self.value_of(t.dtype if t is not None else None, e.rhs)}")
        if op == "/" and self._signed_int(e) and not (
                _dt_name(e.rhs) == "ExprConstant"
                and getattr(e.rhs, "value", -1) != -1):
            # The most negative value divided by -1 wraps to itself in PSS
            # (8.5.1, 8.7), as `-x` does in SV; Verilator's `/` gives 0 (W29).
            lhs = self._operand(e.lhs)
            rhs = self._operand(e.rhs)
            if _ir_has_call(e.rhs):
                t = self.types.type_of(e.rhs)
                tmp = self._temp(_sv_of(t))
                self._emit_prefix([f"{tmp} = {rhs};"], "a signed '/'")
                rhs = tmp
            return f"(({rhs}) == -1 ? -({lhs}) : {lhs} / {rhs})"
        if op == ">>":
            # PSS `>>` on a signed operand is arithmetic (8.5.7); SV's `>>`
            # is always logical, and `>>>` follows the operand's sign.
            t = self.types.type_of(e.lhs)
            if t is not None and t.kind == "int" and t.signed:
                op = ">>>"
        return f"{self._operand(e.lhs)} {op} {self._operand(e.rhs)}"

    def _signed_int(self, e) -> bool:
        t = self.types.type_of(e)
        return t is not None and t.kind == "int" and bool(t.signed)

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
        text = f"{sv_cast(e.target_type)}'({self.expr(e.value)})"
        if _dt_name(e.target_type) != _DT_INT or sv_cast(e.target_type) \
                == "int":
            return text
        # A size cast keeps its OPERAND's signedness in SV: `8'(u)` of an
        # unsigned 200 is still 200, where PSS's (int[8])200 is -56. So the
        # target's sign is stated wherever the operand's differs (W14).
        want = bool(getattr(e.target_type, "signed", False))
        t = self.types.type_of(e.value)
        have = getattr(t.as_int(), "signed", want) if is_integral(t) \
            else want
        if want == have:
            return text
        return f"{'signed' if want else 'unsigned'}'({text})"

    def expr_unary(self, e) -> str:
        op = _UNOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported unary op {e.op.name}")
        return f"{op}({self.expr(e.operand)})"

    def expr_call(self, e) -> str:
        callee = e.func
        m = string_method(e, self.types)
        if m is not None:
            return self._string_method(e, m)
        if is_core_call(callee):
            # `std_pkg::message(...)` names the function `message(...)` does,
            # so it takes the form an unqualified call has. The spelling only
            # matters inside an executor's override, and this target refuses
            # overrides (`executors.check`).
            e = dc.replace(e, func=ir.ExprAttribute(
                value=ir.TypeExprRefSelf(), attr=callee_name(callee)))
            callee = e.func
        elif (_dt_name(callee) == "ExprRefUnresolved"
                and callee.name in self.import_fn_names):
            # `p_pkg::f(...)`: the import the linker found, by its own name
            # (ast2ir). It takes the form `f(...)` after `import p_pkg::*`
            # has -- unless this component declares an `f`, which that form
            # would reach instead of the import the model named.
            if callee.name in self.own_fn_names:
                raise ValueError(
                    f"cannot lower the qualified call to import "
                    f"'{callee.name}': component "
                    f"'{getattr(self.comp, 'name', '?')}' declares a function "
                    f"of the same name, and op-model-sv reaches an import "
                    f"only where nothing shadows it.")
            e = dc.replace(e, func=ir.ExprAttribute(
                value=ir.TypeExprRefSelf(), attr=callee.name))
            callee = e.func
        # Address-space builtins are arithmetic, not calls: there is no
        # address-space object in generated SV, only 64-bit addresses.
        if _dt_name(callee) == "ExprAttribute" and callee.attr in _ADDR_BUILTINS:
            return _ADDR_BUILTINS[callee.attr](self, e)
        if callee_name(callee) == "message":
            return self._message(e)
        name = callee_name(callee)
        if name in self._OUTPUT_TASKS and name not in {
                f.name for f in getattr(self.comp, "functions", []) or []}:
            return self._output_call(name, e)
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
        # The fields the source named (`reg_rmw` provenance) when it named
        # them whole; otherwise recovered from the mask. Both are whole fields,
        # LSB first, so the two agree wherever both answer.
        from .reg_field_names import whole_fields
        slices = whole_fields(call)
        refs = (self.namer.refs(callee.value, slices) if slices is not None
                else self.namer.fields_for(callee.value, int(mask_e.value)))
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
        decl = pad + sv_declarator(s.annotation, self.expr(s.target),
                                   "local array", self.consts)
        v = getattr(s, "value", None)
        if v is None and _dt_name(s.annotation) == "DataTypeArray":
            # Each element its type's default (8.1); an enum's is its first
            # item, and an SV enum element starts at 0.
            first = _first_item(self.types.resolve(
                _array_leaf(s.annotation)), self.consts)
            if first is None:
                return self._declare(s, decl, [])
            fill = f"'{{default: {first}}}"
            return self._declare(
                s, decl, [f"{pad}{self.expr(s.target)} = {fill};"],
                f"{decl} = {fill};")
        if v is None:
            # An enum's default is its FIRST item (7.5), which need not be 0;
            # an SV enum variable starts at 0 whatever its items are.
            dt = self.types.resolve(s.annotation)
            first = enum_first_item(dt)
            if first is not None:
                # The item, by its name, as the model would have written it.
                v = ir.ExprConstant(value=dt.items[first],
                                    ref=f"{dt.name}::{first}")
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

    def _declare(self, s, decl: str, sets: List[str],
                 inline: Optional[str] = None) -> List[str]:
        """``decl``, then the lines ``sets`` giving it its value; as one
        declaration-with-initializer, ``inline``, where the declaration is
        not hoisted to the top of its block (`enter_block`)."""
        if self._block is not None and id(s) in self._block[0]:
            self._block[1].append(f"{decl};")
            return sets
        return [inline] if inline is not None else [f"{decl};"] + sets

    def stmt_assign(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        target = s.targets[0]
        v = s.value
        rewritten = self._assign_from_call(target, v, pad)
        if rewritten is not None:
            return rewritten
        tt = self._target_type(target)
        if is_literal(v) and not all(_dt_name(x) in ("ExprConstant", "ExprList")
                                     for x in _nodes_of(v)):
            # Through a temporary: Verilator writes `w = '{w[1], w[0]}`
            # element by element, so `w[0]` is overwritten before it is read,
            # where PSS (8.1) and SV both read every element first.
            tmp = sv_declarator(self.types.resolve(tt), "pssc_lit",
                                consts=self.consts)
            return [f"{pad}begin",
                    f"{pad}  {tmp};",
                    f"{pad}  pssc_lit = {self.value_of(tt, v)};",
                    f"{pad}  {self.expr(target)} = pssc_lit;",
                    f"{pad}end"]
        return [f"{pad}{self.expr(target)} = {self.value_of(tt, v)};"]

    def stmt_aug_assign(self, s, ind: int) -> List[str]:
        op = _BINOP.get(s.op.name)
        if op is None:
            raise ValueError(f"unsupported augmented-assign op {s.op.name}")
        if op == ">>":
            # As in `expr_bin`: a signed target shifts arithmetically (W10).
            t = self.types.type_of(s.target)
            if t is not None and t.kind == "int" and t.signed:
                op = ">>>"
        return [f"{self.pad(ind)}{self.expr(s.target)} {op}= "
                f"{self.expr(s.value)};"]

    def stmt_expr(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        from ..packed_image import struct_call
        name = struct_call(s.expr)
        if name is not None:
            return self._struct_mem(name, s.expr, pad)
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

    def _struct_mem(self, name: str, call, pad: str) -> List[str]:
        """`write_struct(h, s)` / `read_struct(h, s)` (21.12).

        The image is a vector assembled -- or taken apart -- a field at a
        time from the PSS layout (`packed_image.leaves`), not the SV struct
        cast whole: an SV enum is a 32-bit int inside a `struct packed`, so
        a struct with one is not its own image. The vector then moves in
        the accesses `packed_image.access_plan` decides, each a slice at its
        byte offset: the same accesses, in the same order, as every other
        target makes. A read takes each slice through a temporary of the
        access's width (the primitive's `output`).
        """
        from ..packed_image import access_plan, check_call, leaves, struct_bits
        h_e, s_e = check_call(name, call.args, call)
        t = self.types.type_of(s_e)
        if t is None or t.kind != "struct":
            raise Refusal(f"'{name}': the second argument is not a struct",
                          call)
        dt = self.types.resolve(t.dtype)
        plan = access_plan(dt, where=(call,))
        fields = leaves(dt, where=(call,))
        nb = (struct_bits(dt) + 7) // 8 * 8
        h, sv = self.expr(h_e), self.expr(s_e)
        ip = pad + "  "
        lines = [f"{pad}begin", f"{ip}bit [{nb - 1}:0] pssc_img;"]

        def addr(a):
            return h if a.offset == 0 else f"({h} + {a.offset})"

        def member(lf):
            return ".".join((sv,) + lf.path)

        if name == "write_struct":
            lines.append(f"{ip}pssc_img = '0;")
            for lf in fields:
                lines.append(f"{ip}pssc_img[{lf.lsb} +: {lf.width}] = "
                             f"{member(lf)};")
            for a in plan:
                lines.append(f"{ip}{IMP_MEMBER}.write{a.width}({addr(a)}, "
                             f"pssc_img[{8 * a.offset} +: {a.width}]);")
        else:
            for w in sorted({a.width for a in plan}):
                lines.append(f"{ip}bit [{w - 1}:0] pssc_r{w};")
            for a in plan:
                lines += [f"{ip}{IMP_MEMBER}.read{a.width}({addr(a)}, "
                          f"pssc_r{a.width});",
                          f"{ip}pssc_img[{8 * a.offset} +: {a.width}] = "
                          f"pssc_r{a.width};"]
            for lf in fields:
                v = f"pssc_img[{lf.lsb} +: {lf.width}]"
                if _dt_name(lf.field.datatype) == "DataTypeEnum":
                    # SV converts no integer to an enum implicitly (6.19.3).
                    v = f"{sv_type(lf.field.datatype)}'({v})"
                lines.append(f"{ip}{member(lf)} = {v};")
        return lines + [f"{pad}end"]

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

    def stmt_if_chain(self, s, ind: int) -> List[str]:
        """`if ... else if ... else`, flat.

        A later clause's test runs only when every earlier one was false, so
        a call it hoists cannot go in front of the chain with the first
        test's: that clause opens an `else begin` holding the call and the
        rest of the chain, one level deeper."""
        first = s.clauses[0]
        pad = self.pad(ind)
        lines = [f"{pad}if ({self.expr(first.test)}) begin"]
        lines += self.stmts(first.body, ind + 1)
        opened: List[str] = []
        for c in s.clauses[1:]:
            saved_ind, self._ind = self._ind, ind + 1
            try:
                pre, test = self._capture(lambda: self.expr(c.test))
            finally:
                self._ind = saved_ind
            if pre:
                lines.append(f"{pad}end else begin")
                opened.append(pad)
                ind += 1
                pad = self.pad(ind)
                lines += self._placed(pre, ind)
                lines.append(f"{pad}if ({test}) begin")
            else:
                lines.append(f"{pad}end else if ({test}) begin")
            lines += self.stmts(c.body, ind + 1)
        if s.orelse:
            lines.append(f"{pad}end else begin")
            lines += self.stmts(s.orelse, ind + 1)
        lines.append(f"{pad}end")
        lines += [f"{p}end" for p in reversed(opened)]
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
        if exec_kind(self.fn) in INIT_EXEC_KINDS and self.super_base is not None:
            # Natively, the base class's hook of the same kind: its blocks of
            # this kind, or the empty hook every class has.
            return [f"{pad}super.pss_{exec_kind(self.fn)}();"]
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
        t = self.types.type_of(target)
        if t is not None and t.kind in ("array", "struct"):
            # A field or an element: an array literal needs its shape, a
            # struct literal its type (E14).
            return t.dtype
        return None

    def value_of(self, dtype, e) -> str:
        """Render ``e`` as a value of ``dtype``.

        The one case that matters: SV converts no integer to an enum
        implicitly (6.19.3). An item is written as its enumerator
        (`expr_constant`); any other value not already held as the enum --
        an integer, or an enum field of a register value, which is bits --
        is cast to it.
        """
        if dtype is not None:
            dtype = self.types.resolve(dtype)
        if _dt_name(e) == "ExprStructLiteral":
            return self._struct_literal(dtype, e)
        if is_literal(e) and _dt_name(dtype) == "DataTypeArray":
            # `{a, b}` (8.1) as an assignment pattern, `'{a, b}`: each
            # element an assignment-like context of the element type, and
            # every element read before the target is written.
            # `array_literal.shape_error` has checked that it fits.
            return "'{" + ", ".join(self.value_of(dtype.element_type, x)
                                    for x in e.elts) + "}"
        if dtype is not None and _dt_name(dtype) == _DT_ENUM \
                and not self._held_as(e, dtype):
            return f"{_strip_pkg(dtype.name)}'({self.expr(e)})"
        return self.expr(e)

    def _held_as(self, e, enum_dtype) -> bool:
        """Is *e* held in SV as a value of *enum_dtype*?"""
        if _dt_name(e) == "ExprConstant":
            return (self.consts is not None
                    and self.consts.name_of(e) is not None
                    and self.types.type_of(e).dtype is not None
                    and _strip_pkg(self.types.type_of(e).dtype.name)
                    == _strip_pkg(enum_dtype.name))
        t = self.types.type_of(e)
        if t is None or t.kind != "enum" or self._bits_held(e):
            return False
        return _strip_pkg(getattr(t.dtype, "name", "")) == \
            _strip_pkg(enum_dtype.name)

    def _bits_held(self, e) -> bool:
        """An enum field of a register value is held as its bits."""
        if _dt_name(e) != "ExprAttribute" or self.consts is None:
            return False
        base = self.types.type_of(e.value)
        return (base is not None and base.kind == "struct"
                and self.consts.int_storage(self.types.resolve(base.dtype)))

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
        elem = self.chan_elem_of.get(_chan_name(fn.value))
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
        if not any(not self._pattern_values(c.pattern) for c in s.cases):
            # Nothing matched and there is no default: an error (20.7.10 g;
            # ruling R2). SV's `case` would fall through silently (W16).
            name = getattr(self.fn, "name", "?")
            lines.append(f'{pad}  default: $error("{name}: unmatched match '
                         f'subject");')
        lines.append(f"{pad}endcase")
        return lines

    def _pattern_values(self, pattern) -> List[object]:
        """The label expressions of one `match` arm; none for `default`."""
        return match_values(pattern)

    def stmt_foreach(self, s, ind: int) -> List[str]:
        """`foreach (a[i]) { ... }` -> an indexed for loop.

        SV has `foreach` too, but only over its own arrays; the PSS collection
        may be a generated member with a known size, so an explicit index keeps
        one lowering for both.
        """
        pad = self.pad(ind)
        coll = self.expr(s.iter)
        index, it = foreach_parts(s)
        if index is not None:
            idx = index.name
        else:
            self._foreach_k = getattr(self, "_foreach_k", -1) + 1
            idx = f"pssc_i{self._foreach_k}"
        lines = [f"{pad}foreach ({coll}[{idx}]) begin"]
        if it is not None:
            # `foreach (v : a[i])`: v is the ELEMENT (20.7.8); it was taken
            # for the index (W5). Bound by value; a body writing it -- an
            # alias of the element in PSS -- is refused, as C and Python do.
            t = self.types.type_of(s.iter)
            elem = getattr(getattr(t, "dtype", None), "element_type", None)
            if elem is None or assigns_local(s.body, it.name):
                raise Refusal(
                    f"cannot lower `foreach ({it.name} : {coll})`: "
                    + ("its body assigns the iterator, which aliases the "
                       "element (LRM 20.7.8 c); assign the element by index"
                       if elem is not None else
                       "the iterator form is lowered over an array of data "
                       "only; use the index form"))
            lines.append(f"{self.pad(ind + 1)}{sv_type(elem)} "
                         f"{self.expr(it)} = {coll}[{idx}];")
        lines += self.stmts(s.body, ind + 1)
        lines.append(f"{pad}end")
        return lines


def _first_item(dtype, consts) -> Optional[str]:
    """An enum's first item, spelled, when SV's 0 is not it; else None."""
    if _dt_name(dtype) != _DT_ENUM:
        return None
    from .constants import SvConstants
    return (consts if consts is not None else SvConstants()).first_item(dtype)


def _local_names(body) -> frozenset:
    """Every local variable name ``body`` declares or refers to."""
    found = set()

    def walk(n):
        if isinstance(n, (list, tuple)):
            for x in n:
                walk(x)
            return
        if not dc.is_dataclass(n) or isinstance(n, (type, ir.DataType)):
            return
        if _dt_name(n) == "ExprRefLocal":
            found.add(mangle(n.name))
        for f in dc.fields(n):
            walk(getattr(n, f.name))

    walk(body or [])
    return frozenset(found)


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


def _is_path(e) -> bool:
    """A reference SV can select bits of directly: a name, a member, an
    element -- no call anywhere in it."""
    cn = _dt_name(e)
    if cn in ("ExprRefLocal", "TypeExprRefSelf"):
        return True
    if cn == "ExprAttribute":
        return _is_path(e.value)
    if cn == "ExprSubscript":
        return _is_path(e.value) and _dt_name(e.slice) != "ExprCall"
    return False


def _chan_name(e) -> Optional[str]:
    """The field name a channel receiver names: ``wake`` for ``wake`` and for
    an element of a channel array, ``wake[ch]``."""
    while _dt_name(e) == "ExprSubscript":
        e = e.value
    return getattr(e, "attr", None) or getattr(e, "id", None)


def _has_range(pattern) -> bool:
    cn = _dt_name(pattern)
    if cn == "PatternValue":
        return _dt_name(pattern.value) == "ExprRange"
    return any(_has_range(p) for p in (getattr(pattern, "patterns", None) or []))


#: PSS address-space builtins, lowered to arithmetic on a 64-bit address.
#: `addr_handle_t` is an opaque handle in PSS and a plain address here, so
#: deriving a handle from another is an offset add.
_ADDR_BUILTINS = {
    # `sub` (21.13.4.2) subtracts the offset; it was ignored (W33).
    "make_handle_from_handle": lambda be, e: (
        f"({be.expr(e.args[0])} + {be.expr(e.args[1])})" if len(e.args) < 3
        else f"(({be.expr(e.args[2])}) ? ({be.expr(e.args[0])} - "
             f"{be.expr(e.args[1])}) : ({be.expr(e.args[0])} + "
             f"{be.expr(e.args[1])}))"),
    # A handle IS the address here, so extracting its value is the identity.
    "addr_value": lambda be, e: be.expr(e.args[0]),
}


class _CtorEmitter(_BodyEmitter):
    """A constructor's body. Every statement renders as in any solve-context
    body; the address-binding calls (`regs.set_handle(h)`,
    `super.initialize(...)`) render as what they do (`lower_init`)."""

    def __init__(self, fn, comp, member_of, reg_groups=(), **kw):
        super().__init__(fn, comp, member_of, **kw)
        self.reg_groups = list(reg_groups)

    def stmt_expr(self, s, ind: int) -> List[str]:
        if _dt_name(s.expr) == "ExprCall":
            from .lower_init import binding_call
            lines = binding_call(s.expr, members=self.member_of,
                                 reg_groups=self.reg_groups, bus=IMP_MEMBER,
                                 expr=self.expr, pad=self.pad(ind))
            if lines is not None:
                return lines
        return super().stmt_expr(s, ind)


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

    def __init__(self, member_of, ctor=None, comp=None, consts=None):
        super().__init__(ctor, comp, member_of, consts=consts)


# --- emission --------------------------------------------------------------

# `ctor_names` comes down from the model, never from the ambient ContextVar
# (P6a.T5): which solve function is the constructor is the compile's answer,
# and an emitter that asks the process gets whichever compile set it last.

#: The import API every component class holds (in the component base), and
#: the factory's handle to the platform object. Generated names take the
#: `pss_` prefix: a component's members are its PSS names, and SV has one
#: namespace per class for properties and methods alike.
IMP_MEMBER = "pss_imp"

#: The factory's handle to the root component instance.
ROOT_MEMBER = "pss_root"

#: The component-side task an exported action's body becomes (design D12).
ACTION_PREFIX = "pss_action_"

#: An adapter's handle to its component, and the prefix of the members that
#: hold its children's adapters (`emit_adapters`).
COMP_MEMBER = "pss_comp"
SUB_PREFIX = "pss_sub_"


def _members(comp) -> Dict[str, str]:
    """Map every field of ``comp`` -- inherited ones included -- to its
    member name: its PSS name, escaped only if it is an SV keyword.

    A member is reached as PSS reaches it (`sub.a`, `ch[i].wake`), so it is
    public and named as declared. A local that shadows it is handled where
    the reference is written (`_BodyEmitter.expr_attribute`)."""
    return {f.name: mangle(f.name) for f in (getattr(comp, "fields", None) or [])}


#: The field types that are plain data members (`_data_fields`).
_DATA_KINDS = (_DT_INT, _DT_STRUCT, _DT_ENUM, _DT_CHANDLE, "DataTypeString")


def _data_fields(comp) -> List[object]:
    """Fields that become plain data members: not registers, not
    sub-components -- the component's own attributes."""
    subs = {s.name for s in sub_components(comp)}
    out = []
    for f in comp.fields:
        if field_is_reg_group(f) or f.name in subs:
            continue
        # A chandle field is an address handle (`sv_type`): a component
        # that keeps its base for its `init_down` to use holds one. A string
        # field was left out, so a body naming it did not compile (W21).
        if _dt_name(f.datatype) in _DATA_KINDS:
            out.append(f)
        elif (_dt_name(f.datatype) == "DataTypeArray"
              and _dt_name(_array_leaf(f.datatype)) in _DATA_KINDS):
            # A fixed-size array of data: an unpacked array member. It used to
            # be no member at all, so a body naming it did not compile.
            out.append(f)
    return out


def sv_declarator(dtype, name: str, what: str = "array", consts=None) -> str:
    """``T name`` for a value of ``dtype``; an array's bounds (`[2][3]`) go
    after its name, an unpacked array being a value SV assigns whole. A bound
    written as a constant is spelled by its name (``consts``)."""
    dims = ""
    dt = dtype
    while _dt_name(dt) == "DataTypeArray":
        try:
            n = int(getattr(dt, "size", None))
        except (TypeError, ValueError):
            n = -1
        if n < 0:
            raise ValueError(
                f"{what} '{name}' has no folded size; SV needs a bound and "
                "there is nothing to derive one from.")
        bound = consts.size_name(dt) if consts is not None else None
        dims += f"[{bound or n}]"
        dt = dt.element_type
    return f"{sv_type(dt)} {name}{dims}"


def _sv_int_literal(e) -> str:
    """An integer constant, spelled with its PSS type (Table 21): an SV
    unsized decimal is a signed 32-bit `int`, which only `int` constants are.
    `8'hFF` is 8 bits, `'d12` and `0x10` unsigned, `4'sd12` a signed 4-bit
    -4; written as plain decimals, `-'d12 / 3` divided signed and
    `8'hFF + 1` did not wrap."""
    from zuspec.ir.core.expr import int_literal_type
    v = e.value
    w, signed = int_literal_type(e)
    if signed and w == 32 and -(1 << 31) <= v < (1 << 31):
        return str(v)
    if signed:
        v = v - (1 << w) if (v >> (w - 1)) & 1 and v > 0 else v
        return f"-{w}'sd{-v}" if v < 0 else f"{w}'sd{v}"
    v &= (1 << w) - 1
    return f"{w}'d{v}" if v < 16 else f"{w}'h{v:x}"


def _ir_has_call(e) -> bool:
    """Whether IR expression *e* contains a call."""
    if _dt_name(e) in ("ExprCall", "ExprMemberCall"):
        return True
    if not dc.is_dataclass(e):
        return False
    for f in dc.fields(e):
        v = getattr(e, f.name, None)
        for x in (v if isinstance(v, (list, tuple)) else (v,)):
            if dc.is_dataclass(x) and _ir_has_call(x):
                return True
    return False


def _nodes_of(e):
    """*e*'s expression nodes below an array literal: its leaves' roots."""
    if is_literal(e):
        for x in e.elts:
            yield from _nodes_of(x)
    else:
        yield e


def _array_leaf(dtype):
    """The element type an array (of arrays) bottoms out in."""
    while _dt_name(dtype) == "DataTypeArray":
        dtype = dtype.element_type
    return dtype


def _data_decl(f, name: str, consts=None) -> str:
    """One data member's declaration."""
    return sv_declarator(f.datatype, name, "array member", consts) + ";"


def _ctor(comp, ctor_names=None):
    for fn in comp.functions:
        if func_kind(fn, ctor_names) == FuncKind.CONSTRUCTOR:
            return fn
    return None


# --- names -----------------------------------------------------------------

def _root_name(root) -> str:
    return _strip_pkg(root.name)


def component_base_name(root) -> str:
    """The generated base class every component class extends (design D8)."""
    return f"{_root_name(root)}_component"


def imp_if_name(root) -> str:
    """The import API: what the platform supplies."""
    return f"{_root_name(root)}_imp_if"


def exp_if_name(comp) -> str:
    """A component type's export API (`OpModel.apis`): ``<comp>_exp_if``.
    The root's is what the platform holds."""
    return f"{_strip_pkg(comp.name)}_exp_if"


def exp_impl_name(comp) -> str:
    """The class that implements `exp_if_name` on a component instance:
    ``<comp>_exp_impl``."""
    return f"{_strip_pkg(comp.name)}_exp_impl"


def factory_name(root) -> str:
    """The class that builds the tree and is the root context (design D5)."""
    return f"{_root_name(root)}_root"


# --- import API -------------------------------------------------------------

# The core memory-access ABI: (method, data-type, is_read). Frozen to match
# pssc_reg_pkg::pss_mem_if.
_MEM_PRIMS = [
    ("write8", "bit [7:0]", False), ("read8", "bit [7:0]", True),
    ("write16", "bit [15:0]", False), ("read16", "bit [15:0]", True),
    ("write32", "bit [31:0]", False), ("read32", "bit [31:0]", True),
    ("write64", "bit [63:0]", False), ("read64", "bit [63:0]", True),
]


def _import_fns(ctx, ctor_names=None) -> List[object]:
    """The model's `import` functions, in declaration order."""
    return [fn for fn in (getattr(ctx, "import_functions", None) or [])
            if func_kind(fn, ctor_names) in (FuncKind.IMPORT_TASK,
                                             FuncKind.IMPORT_SOLVE)]


def _import_proto(fn, ctor_names=None) -> str:
    """``task f(...)`` or ``function T f(...)``: an import function's SV
    prototype, without `pure virtual`."""
    if func_kind(fn, ctor_names) is FuncKind.IMPORT_TASK:
        return f"task {mangle(fn.name)}({_signature(fn)})"
    ret = sv_type(fn.returns) if fn.returns is not None else "void"
    params = ", ".join(f"input {sv_type(a.annotation)} {mangle(a.arg)}"
                       for a in fn.args.args)
    return f"function {ret} {mangle(fn.name)}({params})"


def emit_import_api(root, ctor_names=None, ctx=None) -> str:
    """``interface class <root>_imp_if extends pss_mem_if``, plus the
    model's `import` functions."""
    lines = [f"  interface class {imp_if_name(root)} extends pss_mem_if;"]
    for fn in _import_fns(ctx, ctor_names):
        lines.append(f"    pure virtual {_import_proto(fn, ctor_names)};")
    lines.append("  endclass")
    return "\n".join(lines)


# --- export API --------------------------------------------------------------

def _api_if(model, comp) -> str:
    """The export API's name for ``comp``'s type (`exp_if_name`)."""
    return exp_if_name(comp)


def _api_methods(model, api) -> List[Tuple[str, str, Any]]:
    """``[(prototype, body)]`` of one type's export API: its exported
    functions, then one accessor per child that leads to an export -- `x()`
    for an instance, `x(int i)` and `x_size()` for an array. ``body`` is what
    an adapter (``this`` holding the component in ``holder``) runs."""
    return _api_methods_on(model, api, COMP_MEMBER)


def _api_methods_on(model, api, holder) -> List[Tuple[str, str, Any]]:
    out: List[Tuple[str, str, Any]] = []
    for fn in api.exports:
        ret = ["status"] if fn.returns is not None else []
        call = ", ".join(ret + [mangle(a.arg) for a in fn.args.args])
        out.append((f"task {mangle(fn.name)}({_signature(fn)})",
                    f"{holder}.{mangle(fn.name)}({call});", fn))
    for sub in api.children:
        n = mangle(sub.name)
        cif = _api_if(model, sub.dtype)
        if sub.is_array:
            out.append((f"function {cif} {n}(int i)",
                        f"return {SUB_PREFIX}{n}[i];", sub))
            out.append((f"function int {n}_size()",
                        f"return {sub.size};", sub))
        else:
            out.append((f"function {cif} {n}()",
                        f"return {SUB_PREFIX}{n};", sub))
    return out


def _end(proto: str) -> str:
    return "endtask" if proto.startswith("task ") else "endfunction"


def emit_type_api(model, api) -> str:
    """``interface class <type>_exp_if``: one sub-component type's API
    (`OpModel.apis`): its public target functions (`op_api.py`) under their
    PSS names, and an accessor per public child that leads to an API.
    Component classes implement none of these: an adapter does
    (`emit_adapter`), since SV cannot have a member ``dma0`` and a method
    ``dma0()`` in one class."""
    lines = doc_block(getattr(api.comp, "doc", None), "  ") + [
        f"  interface class {exp_if_name(api.comp)};"]
    for proto, _, what in _api_methods(model, api):
        blank_line(lines)
        lines += doc_block(getattr(what, "doc", None), "    ")
        lines.append(f"    pure virtual {proto};")
    lines.append("  endclass")
    return "\n".join(lines)


def emit_context_api(model) -> str:
    """``interface class <root>_exp_if``: what the platform holds. The
    root's API, as `emit_type_api` gives a sub-component's, and the exported
    actions. A private or protected function is the model's own."""
    root = model.root
    lines = doc_block(getattr(root, "doc", None), "  ") + [
        f"  interface class {exp_if_name(root)};"]
    for proto, _, what in _api_methods(model, model.api(root)):
        blank_line(lines)
        lines += doc_block(getattr(what, "doc", None), "    ")
        lines.append(f"    pure virtual {proto};")
    for entry in model.entries:
        blank_line(lines)
        lines.append(f"    // Exported action `{entry.action}`.")
        lines.append(f"    pure virtual task {mangle(entry.name)}();")
    lines.append("  endclass")
    return "\n".join(lines)


def _sub_members(api, owner: str, at: str = "") -> Tuple[List[str], List[str]]:
    """``(declarations, construction)`` of the members holding ``api``'s
    children's adapters, built from the component handle ``owner``; ``at``
    is the object holding them, when not ``this``."""
    decls: List[str] = []
    build: List[str] = []
    for sub in api.children:
        n = mangle(sub.name)
        cls = exp_impl_name(sub.dtype)
        m = f"{at}{SUB_PREFIX}{n}"
        if sub.is_array:
            decls.append(f"    protected {cls} {SUB_PREFIX}{n}[{sub.size}];")
            build.append(f"foreach ({m}[i]) {m}[i] = new({owner}.{n}[i]);")
        else:
            decls.append(f"    protected {cls} {SUB_PREFIX}{n};")
            build.append(f"{m} = new({owner}.{n});")
    return decls, build


def emit_adapter(model, api) -> str:
    """``<type>_exp_impl implements <type>_exp_if``, for one sub-component type on
    the API: holds one component instance, forwards each function to it, and
    returns its children's adapters, each built once, with it -- so the same
    instance always answers with the same object."""
    cls = _strip_pkg(api.comp.name)
    decls, build = _sub_members(api, COMP_MEMBER)
    lines = [f"  // The API of one `{cls}` instance.",
             f"  class {exp_impl_name(api.comp)} implements "
             f"{exp_if_name(api.comp)};",
             f"    protected {cls} {COMP_MEMBER};"] + decls + [
             "",
             f"    function new({cls} comp);",
             f"      {COMP_MEMBER} = comp;"] + [
             f"      {b}" for b in build] + [
             "    endfunction"]
    for proto, body, _ in _api_methods(model, api):
        lines += ["", f"    virtual {proto};", f"      {body}",
                  f"    {_end(proto)}"]
    lines.append("  endclass")
    return "\n".join(lines)


# --- the component base -----------------------------------------------------

#: The construction hooks, in the order `pss_do_init` runs them.
_HOOKS = ("pss_init_down", "pss_init_subs", "pss_init_up")


def emit_component_base(root) -> str:
    """The common base of every component class (design D4, D8).

    It holds the import API and PSS construction (LRM 20.1.2): this
    component's `init_down`, then every sub-component's whole construction,
    then its `init_up` -- one hook per step, each overridden only by a class
    that has something to put there.
    """
    imp = imp_if_name(root)
    return "\n".join([
        "  // Common base of every component class: the import API, and PSS",
        "  // construction (LRM 20.1.2) as one hook per step.",
        f"  virtual class {component_base_name(root)};",
        f"    protected {imp} {IMP_MEMBER};",
        "",
        f"    function new({imp} imp);",
        f"      {IMP_MEMBER} = imp;",
        "    endfunction",
        "",
    ] + [f"    virtual function void {h}(); endfunction" for h in _HOOKS] + [
        "",
        "    function void pss_do_init();",
        "      pss_init_down();",
        "      pss_init_subs();",
        "      pss_init_up();",
        "    endfunction",
        "  endclass",
    ])


# --- component classes ------------------------------------------------------

class _View(object):
    """A component-shaped object with only what a class DECLARES: the member
    walks (`sub_components`, `_data_fields`, ...) read it as they would the
    component."""

    def __init__(self, comp, fields, functions):
        self.name = comp.name
        self.doc = getattr(comp, "doc", None)
        self.fields = list(fields)
        self.functions = list(functions)
        self.super = None


def declared_view(ctx, comp):
    """``(base, view)``: ``comp``'s user base component (or None) and what
    ``comp`` itself declares (`comp_inherit.declared`)."""
    from ..comp_inherit import declared
    dec = declared(ctx, comp)
    if dec is None:
        return None, comp
    return dec.base, _View(comp, dec.fields, dec.functions)


def _member_decls(view, subs: Dict[str, SubComp], consts=None) -> List[str]:
    """The declared members, in declaration order: register groups, data,
    channels and sub-components, each under its PSS name and public."""
    data = {id(f) for f in _data_fields(view)}
    chans = {id(f) for f in channel_fields(view)}
    lines: List[str] = []
    for f in view.fields:
        name = mangle(f.name)
        if field_is_reg_group(f):
            lines.append(f"    {_strip_pkg(f.datatype.name)} {name};")
        elif f.name in subs:
            sub = subs[f.name]
            bound = (consts.size_name(f.datatype)
                     if consts is not None and sub.is_array else None)
            dim = f"[{bound or sub.size}]" if sub.is_array else ""
            lines.append(f"    {_strip_pkg(sub.dtype.name)} {name}{dim};")
        elif id(f) in data:
            lines += comment_lines(getattr(f, "doc", None), "    ")
            lines.append(f"    {_data_decl(f, name, consts)}")
        elif id(f) in chans:
            # An array of channels is an unpacked array of handles (E10).
            n = channel_count(f)
            dim = f"[{n}]" if n is not None else ""
            lines.append(f"    {sv_type(channel_dtype(f))} {name}{dim};")
    return lines


def _field_defaults(view, members: Dict[str, str], consts=None) -> List[str]:
    """Assign PSS field initializers, before the address binding runs.

    A default is part of a field's meaning: `wb_dma_ch_caps_s` declares every
    capability `true`, and a model that reads back all-false silently refuses to
    attempt the operations those capabilities gate.
    """
    lines: List[str] = []
    # Channels first: an unconstructed channel handle is null, and a null
    # dereference in SV is a run-time error at the first `get`/`try_put`.
    for f in channel_fields(view):
        m = members[f.name]
        if channel_count(f) is None:
            lines.append(f"      {m} = new();")
        else:
            lines.append(f"      foreach ({m}[i]) {m}[i] = new();")
    be = _ExprOnly(members, comp=view, consts=consts)
    for f in _data_fields(view):
        if f.initial_value is not None:
            lines.append(f"      {members[f.name]} = "
                         f"{be.value_of(f.datatype, f.initial_value)};")
            continue
        # An enum starts at its first item (7.5); an SV enum member at 0.
        first = _first_item(f.datatype, consts)
        if first is not None:
            lines.append(f"      {members[f.name]} = {first};")
            continue
        if (_dt_name(f.datatype) == "DataTypeArray"
                and enum_first_item(_array_leaf(f.datatype))):
            first = _first_item(_array_leaf(f.datatype), consts)
            m = members[f.name]
            if _dt_name(f.datatype.element_type) == "DataTypeArray":
                lines.append(f"      {m} = '{{default: {first}}};")
            else:
                lines.append(f"      foreach ({m}[i]) {m}[i] = {first};")
            continue
        # A struct-typed attribute carries its defaults on the STRUCT's fields,
        # not on the instance, so they have to be walked out member by member.
        if _dt_name(f.datatype) == _DT_STRUCT:
            for sf in getattr(f.datatype, "fields", []) or []:
                if sf.initial_value is not None:
                    lines.append(
                        f"      {members[f.name]}.{sf.name} = "
                        f"{be.value_of(sf.datatype, sf.initial_value)};")
    return lines


def _construct_body(view, members, subs, consts=None) -> List[str]:
    """The body of `new()`, after `super.new(imp)` (design D2).

    Fields at their defaults, channels built, register groups at address 0,
    and EVERY sub-component instance constructed -- whether or not the model
    has a constructor for it (SV-3). Where each register group really lives is
    `initialize`'s business, which runs after this. Only what this class
    declares: its base's `new` did the rest.
    """
    lines = _field_defaults(view, members, consts)
    for f in view.fields:
        if field_is_reg_group(f):
            lines.append(f"      {members[f.name]} = new({IMP_MEMBER}, 0);")
    for sub in subs.values():
        m = members[sub.name]
        if sub.is_array:
            lines.append(f"      foreach ({m}[i]) {m}[i] = new({IMP_MEMBER});")
        else:
            lines.append(f"      {m} = new({IMP_MEMBER});")
    return lines


def _initialize_def(comp, ctor, members, *, super_base=None, namer=None,
                    ctor_names=None, ctx=None, consts=None) -> List[str]:
    """The op-model constructor, as a method (design D3). Not virtual: every
    caller uses its member's declared type, so a derived class may declare
    one with another signature and still reach its base's through `super`.

    With a body, that body IS the address binding, rendered as any solve
    body is (`_CtorEmitter`). An EMPTY body keeps the flat convention the
    backends share: every register group -- inherited ones too -- sits at the
    first argument.
    """
    params = ", ".join(f"{sv_type(a.annotation)} {mangle(a.arg)}"
                       for a in ctor.args.args)
    reg_groups = [f.name for f in comp.fields if field_is_reg_group(f)]
    if ctor.body:
        be = _CtorEmitter(ctor, comp, members, reg_groups=reg_groups,
                          namer=namer, ctor_names=ctor_names, ctx=ctx,
                          consts=consts)
        be.super_base = super_base
        body = be.stmts(ctor.body, 3)
    elif ctor.args.args:
        base = mangle(ctor.args.args[0].arg)
        body = [f"      {members[g]} = new({IMP_MEMBER}, {base});"
                for g in reg_groups]
    else:
        body = []
    lines = doc_block(getattr(ctor, "doc", None), "    ")
    lines.append(f"    function void {mangle(ctor.name)}({params});")
    lines += body
    lines.append("    endfunction")
    lines.append("")
    return lines


def _inherited_ctor_def(comp, view, ctor, members) -> List[str]:
    """A derived class that declares register groups and no constructor of
    its own: the base's constructor, then this class's groups bound the way
    an empty constructor binds them (at the first argument). Without it the
    groups it adds would stay at address 0."""
    own = [f.name for f in view.fields if field_is_reg_group(f)]
    if ctor is None or not own:
        return []
    params = ", ".join(f"{sv_type(a.annotation)} {mangle(a.arg)}"
                       for a in ctor.args.args)
    fwd = ", ".join(mangle(a.arg) for a in ctor.args.args)
    addr = mangle(ctor.args.args[0].arg) if ctor.args.args else "0"
    return ([
        "    // The PSS constructor is the base's; this class binds its own",
        "    // register groups after it.",
        f"    function void {mangle(ctor.name)}({params});",
        f"      super.{mangle(ctor.name)}({fwd});"]
        + [f"      {members[g]} = new({IMP_MEMBER}, {addr});" for g in own]
        + ["    endfunction", ""])


def _emitter(fn, comp, members, base, namer, ctor_names, ctx, consts=None):
    be = _BodyEmitter(fn, comp, members, namer=namer, ctor_names=ctor_names,
                      ctx=ctx, consts=consts)
    be.super_base = base
    return be


def _init_hook_defs(comp, view, base, members, subs, namer=None,
                    ctor_names=None, ctx=None, consts=None) -> List[str]:
    """The construction hooks this class has something to put in.

    `pss_init_down`/`pss_init_up` hold its `exec` blocks of that kind, in
    source order (LRM 20.1 d); a class that declares none inherits its base's
    (Table 27), and `super;` in one is `super.pss_init_down()`.
    `pss_init_subs` runs its base's sub-components first, then each of its
    own, in declaration order.
    """
    lines: List[str] = []
    for kind in INIT_EXEC_KINDS:
        blocks = [fn for fn in view.functions
                  if exec_kind(fn) == kind
                  and SUPER_BLOCK not in (fn.metadata or {})]
        if not blocks:
            continue
        lines.append(f"    virtual function void pss_{kind}();")
        for fn in blocks:
            be = _emitter(fn, comp, members, base, namer, ctor_names, ctx,
                          consts)
            lines += be.stmts(fn.body, 3)
        lines.append("    endfunction")
        lines.append("")
    if subs:
        lines.append("    virtual function void pss_init_subs();")
        if base is not None:
            lines.append("      super.pss_init_subs();")
        for sub in subs.values():
            m = members[sub.name]
            if sub.is_array:
                lines.append(f"      foreach ({m}[i]) {m}[i].pss_do_init();")
            else:
                lines.append(f"      {m}.pss_do_init();")
        lines.append("    endfunction")
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


def _method_defs(comp, view, base, members, namer=None, ctor_names=None,
                 ctx=None, consts=None) -> List[str]:
    """The functions this class declares -- `solve` ones as SV functions,
    `target` and unqualified ones as tasks -- `virtual`: component
    functions are virtual (a base's code calling `f` runs the override). A
    `static` one is not polymorphic (20.2), so it is not `virtual`: a call
    binds to the function of the class it is written in (W47)."""
    lines: List[str] = []
    for fn in view.functions:
        if func_kind(fn, ctor_names) not in (FuncKind.EXPORT_OP,
                                             FuncKind.EXPORT_SOLVE):
            continue
        be = _emitter(fn, comp, members, base, namer, ctor_names, ctx,
                      consts)
        blank_line(lines)
        virt = "" if (fn.metadata or {}).get("static") else "virtual "
        lines += _routine(fn, be, mangle(fn.name), virt, "    ")
        lines.append("")
    return lines


def _overrides_ok(comp, view, base, ctor_names=None) -> None:
    """Refuse a virtual override whose prototype differs from its base's
    (design D10): SystemVerilog requires them to match, and PSS lets a
    derived function shadow one of another signature. The constructor is
    not virtual, so it may differ."""
    if base is None:
        return
    inherited = {f.name: f for f in (base.functions or [])
                 if exec_kind(f) is None}
    for fn in view.functions:
        if func_kind(fn, ctor_names) not in (FuncKind.EXPORT_OP,
                                             FuncKind.EXPORT_SOLVE):
            continue
        b = inherited.get(fn.name)
        if b is None or func_kind(b, ctor_names) is FuncKind.CONSTRUCTOR:
            continue
        def ret(f):
            return sv_type(f.returns) if f.returns is not None else "void"
        if (is_task(fn) != is_task(b) or _signature(fn) != _signature(b)
                or ret(fn) != ret(b)):
            raise ValueError(
                f"'{_strip_pkg(comp.name)}::{fn.name}' shadows "
                f"'{_strip_pkg(base.name)}::{fn.name}' with a different "
                f"signature; op-model-sv renders component functions as "
                f"virtual methods, which must match their base's prototype")


def _entry_defs(comp, entries, members, base, namer=None, ctor_names=None,
                ctx=None, consts=None) -> List[str]:
    """Exported actions (`--export-action`) that run in this component, each
    a task: no arguments, no result (`export_action.py`). The factory's
    method of the entry's name calls it (design D12). The base actions'
    bodies its `super;` reaches are private tasks of their own, so their
    locals and a `return` in one stay their own."""
    lines: List[str] = []
    for entry in entries:
        for k, fn in enumerate(entry.functions):
            be = _emitter(fn, comp, members, base, namer, ctor_names, ctx,
                          consts)
            blank_line(lines)
            if k == 0:
                lines.append(f"    // Exported action `{entry.action}`.")
                lines.append(f"    virtual task {ACTION_PREFIX}{entry.name}();")
            else:
                lines.append(f"    // `super;` of `{entry.action}`: the body "
                             f"of `{fn.metadata.get('super_of', '?')}`.")
                lines.append(f"    protected task {fn.name}();")
            lines += be.stmts(fn.body, 3)
            lines.append("    endtask")
            lines.append("")
    return lines


def emit_component_class(model, comp, namer=None, consts=None) -> str:
    """One PSS component type as one SV class (design D1).

    It extends its PSS base's class, or the generated component base, and
    declares only what the component declares; the rest is its base's, and
    `super.f(...)`, `super.x` and `super;` are SystemVerilog's own. Members
    are public and keep their PSS names, so another component reaches them
    as PSS does (`sub.a`, `ch[i].wake`). `new(imp)` takes only the import
    API, so a derived class's `super.new(imp)` is always expressible (D2);
    the model's constructor is a method (D3).
    """
    root, ctx, ctor_names = model.root, model.ctx, model.ctor_names
    base, view = declared_view(ctx, comp)
    _overrides_ok(comp, view, base, ctor_names)
    members = _members(comp)
    subs = {s.name: s for s in sub_components(view)}
    parent = (_strip_pkg(base.name) if base is not None
              else component_base_name(root))

    lines = doc_block(getattr(comp, "doc", None), "  ") + [
        f"  class {_strip_pkg(comp.name)} extends {parent};"]
    decls = _member_decls(view, subs, consts)
    lines += decls + ([""] if decls else [])
    lines += [f"    function new({imp_if_name(root)} imp);",
              "      super.new(imp);"]
    lines += _construct_body(view, members, subs, consts)
    lines += ["    endfunction", ""]

    own_ctor = _ctor(view, ctor_names)
    if own_ctor is not None:
        lines += _initialize_def(comp, own_ctor, members, super_base=base,
                                 namer=namer, ctor_names=ctor_names, ctx=ctx,
                                 consts=consts)
    elif base is not None:
        lines += _inherited_ctor_def(comp, view, _ctor(comp, ctor_names),
                                     members)
    lines += _init_hook_defs(comp, view, base, members, subs, namer,
                             ctor_names, ctx, consts)
    lines += _method_defs(comp, view, base, members, namer, ctor_names, ctx,
                          consts)
    lines += _entry_defs(comp, model.entries_of(comp), members, base, namer,
                         ctor_names, ctx, consts)
    while lines and lines[-1] == "":
        lines.pop()
    lines.append("  endclass")
    return "\n".join(lines)


# --- the factory ------------------------------------------------------------

def _entry_path(model, entry) -> str:
    """The expression, from the factory, of the one component instance an
    exported action runs in.

    The root's own entry runs in the root. Elsewhere, it is matched with the
    instance of its component type -- and with several, one is chosen at
    random per call (design D12), which is not generated yet: refused, so the
    choice is never made silently."""
    if model.is_root(entry.comp):
        return ROOT_MEMBER
    paths: List[str] = []

    def walk(comp, path):
        for sub in sub_components(comp):
            here = [f"{path}.{mangle(sub.name)}[{i}]" for i in range(sub.size)]                 if sub.is_array else [f"{path}.{mangle(sub.name)}"]
            for p in here:
                if sub.dtype is entry.comp:
                    paths.append(p)
                walk(sub.dtype, p)

    walk(model.root, ROOT_MEMBER)
    if len(paths) != 1:
        raise ValueError(
            f"exported action '{entry.action}' runs in "
            f"'{_strip_pkg(entry.comp.name)}', which has {len(paths)} "
            f"instances under the root; op-model-sv calls an exported action "
            f"in exactly one instance for now")
    return paths[0]


def _check_context_names(model) -> None:
    """The factory implements the context API AND the import API, in one SV
    method namespace (design D14): refuse a clash rather than emit a class
    with two methods of one name."""
    taken: Dict[str, str] = {"create": "the factory's create()",
                             "new": "the factory's constructor"}
    for m, _, _ in _MEM_PRIMS:
        taken[m] = f"the memory primitive '{m}'"
    for fn in _import_fns(model.ctx, model.ctor_names):
        taken[mangle(fn.name)] = f"the import function '{fn.name}'"
    rapi = model.api(model.root)
    where = f"methods of {factory_name(model.root)}"
    _claim(taken, _api_names(rapi) if rapi else [], where)
    _claim(taken, [(e.name, f"the exported action '{e.name}'")
                   for e in model.entries], where)
    # Each sub-component type's adapter: its API, and `new`.
    for api in model.apis:
        if api.comp is not model.root:
            _claim({"new": "the adapter's constructor"}, _api_names(api),
                   f"methods of {exp_impl_name(api.comp)}")
    # The generated class names, one package namespace.
    classes: Dict[str, str] = {}
    for c in model.classes:
        classes[_strip_pkg(c.name)] = f"the component class '{c.name}'"
    _claim(classes, [(component_base_name(model.root), "the component base"),
                     (imp_if_name(model.root), "the import API"),
                     (exp_if_name(model.root), "the root's export API"),
                     (factory_name(model.root), "the factory")],
           "classes of the package")
    for api in model.apis:
        if api.comp is not model.root:
            _claim(classes, [
                (exp_if_name(api.comp), f"the export API of '{api.comp.name}'"),
                (exp_impl_name(api.comp), f"the adapter of '{api.comp.name}'")],
                "classes of the package")


def _api_names(api) -> List[Tuple[str, str]]:
    """``[(PSS name, what)]`` of the methods ``api`` puts on its class."""
    out = [(fn.name, f"the function '{fn.name}'")
           for fn in api.exports]
    for sub in api.children:
        out.append((sub.name, f"the accessor of sub-component '{sub.name}'"))
        if sub.is_array:
            out.append((f"{sub.name}_size",
                        f"the size of sub-component array '{sub.name}'"))
    return out


def _claim(taken: Dict[str, str], names, where: str) -> None:
    """Add each ``(name, what)`` to ``taken``, refusing one already there."""
    for name, what in names:
        other = taken.get(mangle(name))
        if other is not None:
            raise ValueError(
                f"{what[4:] if what.startswith('the ') else what} has the "
                f"name of {other}: both are {where}; rename one")
        taken[mangle(name)] = what


def emit_factory(model) -> str:
    """``<root>_root #(Timp)``: builds the tree, and is the root context.

    The one class that knows the platform's type (design D5). It implements
    the import API by forwarding to the platform object, and hands ITSELF to
    the tree, so no component class is parameterized. `create()` runs the
    root's constructor with its arguments, then PSS construction (D3), and
    returns the context API (D9)."""
    _check_context_names(model)
    root = model.root
    name = factory_name(root)
    rcls = _root_name(root)
    imp_if = imp_if_name(root)
    ctor = _ctor(root, model.ctor_names)
    args = list(ctor.args.args) if ctor is not None else []
    params = "".join(f", {sv_type(a.annotation)} {mangle(a.arg)}" for a in args)
    fwd = ", ".join(mangle(a.arg) for a in args)

    lines = [
        f"  class {name} #(type Timp = {imp_if}) implements {imp_if}, "
        f"{exp_if_name(root)};",
        f"    protected Timp {IMP_MEMBER};",
        f"    protected {rcls} {ROOT_MEMBER};",
        "",
        "    // Only create() builds a model.",
        "    protected function new(Timp imp);",
        f"      {IMP_MEMBER} = imp;",
        f"      {ROOT_MEMBER} = new(this);",
        "    endfunction",
        "",
        # The factory's own names take the `pss_` prefix, like every other
        # generated name: a root constructor parameter `imp` was declared twice.
        f"    static function {exp_if_name(root)} create(Timp {IMP_MEMBER}"
        f"{params});",
        f"      {name} #(Timp) pss_model = new({IMP_MEMBER});",
    ]
    if ctor is not None:
        lines.append(
            f"      pss_model.{ROOT_MEMBER}.{mangle(ctor.name)}({fwd});")
    rapi = model.api(root)
    decls, build = (_sub_members(rapi, f"pss_model.{ROOT_MEMBER}",
                                 "pss_model.") if rapi else ([], []))
    lines += [f"      pss_model.{ROOT_MEMBER}.pss_do_init();"]
    lines += [f"      {b}" for b in build]
    lines += [
        "      return pss_model;",
        "    endfunction",
    ]
    # The children's adapters, built once the tree is.
    lines[3:3] = decls

    for proto, body, what in (_api_methods_on(model, rapi, ROOT_MEMBER)
                              if rapi else []):
        if hasattr(what, "args"):
            head = f"    // `{what.name}`, run on the root."
        else:
            head = f"    // Sub-component `{what.name}`'s API."
        lines += ["", head, f"    virtual {proto};", f"      {body}",
                  f"    {_end(proto)}"]
    for entry in model.entries:
        lines += ["",
                  f"    // Exported action `{entry.action}`.",
                  f"    virtual task {mangle(entry.name)}();",
                  f"      {_entry_path(model, entry)}.{ACTION_PREFIX}{entry.name}();",
                  "    endtask"]

    # The import API, forwarded to the platform object.
    lines.append("")
    for meth, dt, is_read in _MEM_PRIMS:
        data = f"output {dt} data" if is_read else f"{dt} data"
        lines.append(
            f"    virtual task {meth}(addr_handle_t addr, {data}); "
            f"{IMP_MEMBER}.{meth}(addr, data); endtask")
    for fn in _import_fns(model.ctx, model.ctor_names):
        names = [mangle(a.arg) for a in fn.args.args]
        if func_kind(fn, model.ctor_names) is FuncKind.IMPORT_TASK:
            call = ", ".join((["status"] if fn.returns is not None else [])
                             + names)
            lines.append(f"    virtual {_import_proto(fn, model.ctor_names)}; "
                         f"{IMP_MEMBER}.{mangle(fn.name)}({call}); endtask")
        else:
            ret = "return " if fn.returns is not None else ""
            lines.append(f"    virtual {_import_proto(fn, model.ctor_names)}; "
                         f"{ret}{IMP_MEMBER}.{mangle(fn.name)}"
                         f"({', '.join(names)}); endfunction")
    lines.append("  endclass")
    return "\n".join(lines)


# --- package functions --------------------------------------------------------

def emit_package_functions(functions, ctx=None, ctor_names=None,
                           consts=None) -> str:
    """Package-scope functions the model calls (`pkg_functions.py`), as
    `automatic` package tasks and functions -- automatic, because a PSS
    function may recurse. A package function has no component, so one that
    reaches the platform (a memory primitive, an import) is refused: nothing
    here gives it the import API yet."""
    lines: List[str] = []
    for fn in functions:
        be = _BodyEmitter(fn, None, {}, ctor_names=ctor_names, ctx=ctx,
                          consts=consts)
        text = _routine(fn, be, pkg_function_name(fn), "automatic ", "  ")
        if any(f"{IMP_MEMBER}." in l for l in text[1:]):
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
