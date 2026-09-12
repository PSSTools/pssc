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

from typing import Dict, List, Optional

from ..body_walker import BodyWalker
from ..progseq_model import (func_kind, FuncKind, field_is_reg_group, _dt_name,
                             sub_components, SubComp, field_is_channel,
                             channel_fields, array_base_stride, scalar_offset,
                             OffsetFoldError)
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
        if bits == 1:
            return "bit"
        return f"bit [{bits - 1}:0]"
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


def _signature(fn) -> str:
    """Build the parenthesized task signature for operation ``fn``.

    ``int`` return becomes a leading ``output int status``; every following arg
    is explicit ``input`` (SV inherits the previous direction otherwise -- a
    real codegen pitfall).
    """
    parts: List[str] = []
    if fn.returns is not None:
        parts.append(f"output {sv_type(fn.returns)} status")
    for a in fn.args.args:
        parts.append(f"input {sv_type(a.annotation)} {mangle(a.arg)}")
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


def _is_true_const(e) -> bool:
    return _dt_name(e) == "ExprConstant" and e.value is True


class _BodyEmitter(BodyWalker):
    """Translate one function body to SV lines.

    The walk, and carrying each statement's PSS comment into the output, are
    `targets/body_walker.py`'s; what is here is the SystemVerilog rendering,
    one hook per node kind, named after the node.
    """

    indent = "  "

    def __init__(self, fn, comp, member_of, namer=None, ctor_names=None):
        self.fn = fn
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
        self.has_status = fn is not None and fn.returns is not None
        self._builtin_hook = None
        # Operations of this component that return a value: their result comes
        # back through an output argument, not a return value.
        self.out_calls = {
            f.name for f in (getattr(comp, "functions", None) or [])
            if f.returns is not None
            and func_kind(f, ctor_names) is FuncKind.EXPORT_OP
        }
        # Declared types of local variables, so an enum-valued assignment can be
        # written with the enum's mnemonic rather than its number.
        self.local_types: Dict[str, object] = {}

    # expressions ----------------------------------------------------------

    def _builtin_call(self, call) -> Optional[str]:
        """A PSS exec built-in rendered as its SV analogue, or ``None``."""
        if self._builtin_hook is None:
            from .sv_builtins import make_pss_builtin_call_hook
            self._builtin_hook = make_pss_builtin_call_hook(self.expr)
        return self._builtin_hook(call)

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
            return str(v)
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
            if e.attr in self.member_of:
                return self.member_of[e.attr]
            if e.attr in self.arg_names:
                return self.arg_rename[e.attr]
            return e.attr
        return f"{self.expr(base)}.{e.attr}"

    def expr_subscript(self, e) -> str:
        return f"{self.expr(e.value)}[{self.expr(e.slice)}]"

    def expr_bin(self, e) -> str:
        op = _BINOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported binop {e.op.name}")
        return f"{self._operand(e.lhs)} {op} {self._operand(e.rhs)}"

    def expr_cast(self, e) -> str:
        return f"{sv_cast(e.target_type)}'({self.expr(e.value)})"

    def expr_unary(self, e) -> str:
        op = _UNOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported unary op {e.op.name}")
        return f"{op}({self.expr(e.operand)})"

    def expr_call(self, e) -> str:
        callee = e.func
        # Address-space builtins are arithmetic, not calls: there is no
        # address-space object in generated SV, only 64-bit addresses.
        if _dt_name(callee) == "ExprAttribute" and callee.attr in _ADDR_BUILTINS:
            return _ADDR_BUILTINS[callee.attr](self, e)
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
        args = ", ".join(self.expr(a) for a in e.args)
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
        return [f"{pad}{self._discard_value(s.expr)};"]

    def stmt_return(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
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
        # PSS do-while: execute body, continue while cond -> forever .. break
        pad = self.pad(ind)
        lines = [f"{pad}forever begin"]
        lines += self.stmts(s.body, ind + 1)
        lines.append(f"{pad}  if (!({self.expr(s.condition)})) break;")
        lines.append(f"{pad}end")
        return lines

    def stmt_while(self, s, ind: int) -> List[str]:
        # `while (true)` becomes `forever`: a constant loop condition is a
        # width/const-expression warning under a linting simulator, and a
        # generated package should lint clean.
        # (`test`, not `condition` -- StmtWhile and StmtRepeatWhile spell it
        # differently, and this branch had never run.)
        pad = self.pad(ind)
        if _is_true_const(s.test):
            lines = [f"{pad}forever begin"]
        else:
            lines = [f"{pad}while ({self.expr(s.test)}) begin"]
        lines += self.stmts(s.body, ind + 1)
        lines.append(f"{pad}end")
        return lines

    def stmt_break(self, s, ind: int) -> List[str]:
        return [f"{self.pad(ind)}break;"]

    def stmt_continue(self, s, ind: int) -> List[str]:
        return [f"{self.pad(ind)}continue;"]

    def stmt_yield(self, s, ind: int) -> List[str]:
        # The blocking primitive. Handing it to the import layer is the
        # whole interrupt strategy: the generated model states WHERE it
        # waits, the integration decides HOW. See the design, §4.4 -- and
        # note the implementer's obligation there, that a pure
        # interrupt-only binding deadlocks.
        return [f"{self.pad(ind)}m_imp.yield_();"]

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
        if (dtype is not None and _dt_name(dtype) == _DT_ENUM
                and _dt_name(e) == "ExprConstant" and isinstance(e.value, int)):
            for nm, val in dtype.items.items():
                if val == e.value:
                    return nm
        return self.expr(e)

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
        fn = v.func
        if _dt_name(fn) != "ExprAttribute":
            return None
        name = fn.attr
        tgt = "status" if isinstance(target, _StatusTarget) else self.expr(target)

        if name in ("read", "read_val", "get") and not v.args:
            return [f"{pad}{self.expr(fn.value)}.{name}({tgt});"]
        if name in _MEM_READS:
            args = ", ".join([self.expr(a) for a in v.args] + [tgt])
            return [f"{pad}{self.expr(fn)}({args});"]
        if name in self.out_calls:
            args = ", ".join([tgt] + [self.expr(a) for a in v.args])
            return [f"{pad}{self.expr(fn)}({args});"]
        return None

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
        lines = [f"{pad}case ({self.expr(s.subject)})"]
        for case in s.cases:
            labels = self._pattern_labels(case.pattern)
            label = ", ".join(labels) if labels else "default"
            lines.append(f"{pad}  {label}: begin")
            lines += self.stmts(case.body, ind + 2)
            lines.append(f"{pad}  end")
        lines.append(f"{pad}endcase")
        return lines

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
        if _dt_name(f.datatype) in (_DT_INT, _DT_STRUCT, "DataTypeEnum"):
            out.append(f)
    return out


def emit_export_api(comp, ctor_names=None) -> str:
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


def lower_component_api(comp, ctor_names=None) -> str:
    """Export interface for a regular component, as package-body text.

    The implementation is folded into the component class itself (see
    `emit_component`), so there is no separate `<comp>_impl`.
    """
    return emit_export_api(comp, ctor_names)


# --- import API, adapter, factory (Phase 5) --------------------------------

# The core memory-access ABI: (method, data-type, is_read). Frozen to match
# pssc_reg_pkg::pss_mem_if.
_MEM_PRIMS = [
    ("write8", "bit [7:0]", False), ("read8", "bit [7:0]", True),
    ("write16", "bit [15:0]", False), ("read16", "bit [15:0]", True),
    ("write32", "bit [31:0]", False), ("read32", "bit [31:0]", True),
    ("write64", "bit [63:0]", False), ("read64", "bit [63:0]", True),
]


def uses_yield(components) -> bool:
    """Does any operation in ``components`` block?

    The import API declares `yield_` only when the model actually waits. An
    unconditional declaration would be a demand on every integration -- including
    the ones whose models never block -- and would break a duck-typed import
    object that has no reason to provide it.
    """
    def in_stmts(body):
        for s in (body or []):
            if _dt_name(s) == "StmtYield":
                return True
            if in_stmts(getattr(s, "body", None)) or in_stmts(getattr(s, "orelse", None)):
                return True
            for case in (getattr(s, "cases", None) or []):
                if in_stmts(getattr(case, "body", None)):
                    return True
        return False

    return any(in_stmts(fn.body) for c in components for fn in (c.functions or []))


def emit_import_api(root, needs_yield: bool = False, ctor_names=None) -> str:
    """``interface class <root>_import_if extends pss_mem_if`` plus any
    engine-specific import functions (none for the WB DMA engine)."""
    cls = f"{_strip_pkg(root.name)}_import_if"
    lines = [f"  interface class {cls} extends pss_mem_if;"]
    # The blocking primitive. Every wait in the model routes through it, and
    # what it must do is a CONTRACT, not an implementation detail: it has to
    # return when the device may have made progress, and it must not wait on an
    # interrupt alone. Two of this model's waits cannot be woken by one --
    # an unrouted channel raises no interrupt (INT_SRC is masked), and the
    # pause_engine readback has no interrupt behind it at all. An
    # interrupt-only binding therefore hangs rather than fails.
    if needs_yield:
        lines.append("    // Blocking: return when the device may have progressed.")
        lines.append("    // MUST NOT wait on an interrupt alone -- see the yield contract.")
        lines.append("    pure virtual task yield_();")
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


def _bind_body(comp, ctor, members, subs, *, bus: str, base_arg: str) -> List[str]:
    """The address-binding half of a constructor.

    With an `init`, that function IS the binding and is lowered statement by
    statement (`lower_init`). Without one, fall back to the flat convention:
    every register group sits at the base address.
    """
    reg_groups = [f.name for f in comp.fields if field_is_reg_group(f)]
    if ctor is not None and ctor.body:
        from .lower_init import lower_init
        be = _ExprOnly(members, ctor=ctor, comp=comp)
        return lower_init(ctor, members=members, reg_groups=reg_groups, subs=subs,
                          bus=bus, expr=be.expr, indent=3)
    return [f"      {members[g]} = new({bus}, {base_arg});" for g in reg_groups]


def _operation_defs(comp, members: Dict[str, str], namer=None,
                    ctor_names=None) -> List[str]:
    """Export operations. `virtual`, not plain -- they implement the export
    interface's pure virtuals, and stricter simulators require the override."""
    lines: List[str] = []
    for fn in _operations(comp, ctor_names):
        be = _BodyEmitter(fn, comp, members, namer=namer,
                          ctor_names=ctor_names)
        blank_line(lines)
        lines += doc_block(getattr(fn, "doc", None), "    ")
        lines.append(f"    virtual task {mangle(fn.name)}({_signature(fn)});")
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


def emit_subcomponent_class(comp, root, namer=None, ctor_names=None) -> str:
    """A non-root component's implementation class.

    Deliberately **not** parameterized. Only the root carries `#(type IMP_T)`,
    because the root's accessors return sub-component interface handles: if a
    sub-component class were parameterized, its parameter would leak into the
    root's interface, and the export API would stop being a plain handle type.

    The bus arrives as the root's import interface, so a sub-component can both
    reach memory and use the root's other import functions.
    """
    name = _strip_pkg(comp.name)
    api = f"{name}_if"
    bus_if = f"{_strip_pkg(root.name)}_import_if"
    members = _members(comp)
    subs = {s.name: s for s in sub_components(comp)}

    ctor = _ctor(comp, ctor_names)
    ctor_params = ""
    if ctor is not None and ctor.args.args:
        ctor_params = ", " + ", ".join(
            f"{sv_type(a.annotation)} {mangle(a.arg)}" for a in ctor.args.args)
    base_arg = (mangle(ctor.args.args[0].arg)
                if ctor is not None and ctor.args.args else "base")

    lines = doc_block(getattr(comp, "doc", None), "  ") + [
        f"  class {name} implements {api};",
        f"    protected {bus_if} m_imp;",
    ]
    lines += _member_decls(comp, members, subs)
    lines.append("")
    lines.append(f"    function new({bus_if} bus{ctor_params});")
    lines.append(f"      m_imp = bus;")
    lines += _field_defaults(comp, members, subs)
    lines += _bind_body(comp, ctor, members, subs, bus="m_imp", base_arg=base_arg)
    lines.append("    endfunction")
    lines.append("")
    lines += _operation_defs(comp, members, namer, ctor_names)
    lines += _accessor_defs(comp, members, subs)
    lines.append("  endclass")
    return "\n".join(lines)


def emit_component(root, needs_yield: bool = False, namer=None,
                   ctor_names=None) -> str:
    """The component class, named after the component itself -- one class that is

      * the **export implementation** (`implements <comp>_if`, the operations),
      * the **import redirect** (`implements <comp>_import_if`, forwarding each
        primitive to the user object `m_imp`), and
      * the **factory** (static `create()`).

    It holds the register model, constructed with ``this`` as the bus: because
    the class is-a ``pss_mem_if`` (via the import interface), register accesses
    route ``m_regs -> this.write32/read32 -> m_imp`` to the user object. ``IMP_T``
    defaults to the component's ``import_if``; override it to wire a
    signature-compatible (duck-typed) object that does not formally implement it.
    """
    name = _strip_pkg(root.name)
    api = f"{name}_if"
    import_if = f"{name}_import_if"
    members = _members(root)
    subs = {s.name: s for s in sub_components(root)}

    ctor = _ctor(root, ctor_names)
    ctor_params = ""
    fwd = ""
    base_arg = "base"
    if ctor is not None and ctor.args.args:
        ps = [f"{sv_type(a.annotation)} {mangle(a.arg)}" for a in ctor.args.args]
        names = [mangle(a.arg) for a in ctor.args.args]
        ctor_params = ", " + ", ".join(ps)
        fwd = ", " + ", ".join(names)
        base_arg = mangle(ctor.args.args[0].arg)

    lines = doc_block(getattr(root, "doc", None), "  ") + [
        f"  class {name} #(type IMP_T = {import_if}) implements {api}, {import_if};",
        f"    protected IMP_T m_imp;",
    ]
    lines += _member_decls(root, members, subs)
    lines.append("")

    # Constructor: store the user object, then bind the address map. The
    # register model is built with `this` as the bus -- this class is-a
    # pss_mem_if via the import interface, so accesses route back to m_imp --
    # and so are the sub-components, which is what lets a channel's operations
    # reach the user object without a second handle.
    lines.append(f"    function new(IMP_T imp{ctor_params});")
    lines.append(f"      m_imp = imp;")
    lines += _field_defaults(root, members, subs)
    lines += _bind_body(root, ctor, members, subs, bus="this", base_arg=base_arg)
    lines.append("    endfunction")
    lines.append("")

    lines += _operation_defs(root, members, namer, ctor_names)
    lines += _accessor_defs(root, members, subs)

    # import redirect: forward each memory-access primitive to the user object.
    if needs_yield:
        lines.append("    virtual task yield_(); m_imp.yield_(); endtask")
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
        f"      {name} #(IMP_T) self = new(imp{fwd});",
        f"      return self;",
        f"    endfunction",
        f"  endclass",
    ]
    return "\n".join(lines)


def lower_root_glue(root) -> str:
    """Import interface + the merged component class for the root component."""
    return "\n\n".join([emit_import_api(root), emit_component(root)])
