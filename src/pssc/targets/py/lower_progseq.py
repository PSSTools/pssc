"""One operation body, rendered as Python.

The walk, the comment attachment and the call dispatch are
`targets/body_walker.py`'s -- the same ones the C emitter uses. What is here is
the Python rendering, and it is deliberately the whole of what this backend
knows about the language: fourteen statement hooks, eleven expression hooks,
and one hook per `Disposition`.

Python differs from C in five places that are not cosmetic, and each is
commented where it happens:

* **An int has no width.** PSS carries every operation out at a width and a
  signedness (LRM 8.7) and truncates division toward zero; Python does
  neither. The type each operation is carried out at is `ExprTypes.propagate`'s,
  and a value is wrapped back into range wherever it could have left it --
  which its known range usually rules out. See the section above `_Val`.

* **A suite cannot be empty.** `yield` lowers to a comment on both targets, but
  a C block of only comments compiles and a Python one does not, so every
  block goes through :meth:`_BodyEmitter.block`, which supplies `pass`.
* **There is no do-while.** `repeat {} while (c)` becomes `while True:` with a
  trailing `if not (c): break`, which is the same loop and states so.
* **There is no `switch`.** `match` becomes an if/elif chain over a subject
  bound to a temporary, because a PSS subject may be a register read and
  evaluating it once per arm would issue one bus transaction per arm.
* **There are no out-parameters.** `ok = ch.try_get(tok)` writes through a
  pointer in C, and an int is immutable here. The local is declared as a
  one-element cell and read as `tok[0]` -- and WHICH locals those are is the
  shared `scan_output_locals`, the same analysis that widens them to
  `uint64_t` in C.

AND, in the async form, a fifth: **an awaited call is HOISTED to a statement of
its own** unless it already is one. `await` is legal in an arbitrary expression
position, and putting it there is how this backend would get a subtly wrong
program rather than a broken one -- see `_awaited` for the three ways, each of
which is a wrong VALUE and not a syntax error.
"""
from __future__ import annotations

import dataclasses as dc
from contextlib import contextmanager
from typing import Dict, FrozenSet, List, Optional, Set

import zuspec.ir.core as ir

from .. import pkg_functions as pf
from ..body_walker import BodyWalker, CallDispatch, scan_output_locals
from ..call_legality import Ctx
from ..validate_calls import callee_name, is_super_call
from ..comments import HASH
from .. import executors as xtr
from ..progseq_model import (FuncKind, INIT_EXEC_KINDS, _dt_name,
                             array_base_stride, channel_fields, exec_kind,
                             func_kind, resolve_ref, struct_base,
                             field_is_reg_group, scalar_offset, sub_components)
from ..expr_types import (CONTEXT_BINARY, CONTEXT_UNARY, LEFT_TYPED,
                          INT, RELATIONAL, ExprTypes, PssType,
                          assign_source_type,
                          convert, int_range, is_integral, merge)
from . import pss_int
from .lower_reg_model import accessor_map, value_class_name
from .pss_format import parse_format
from .naming import (CTOR_METHOD, DEFAULT_EXECUTOR, EXECUTOR_ATTR,
                     IMPORTS_ATTR, class_name, group_base, init_kind_method,
                     mangle, reg_symbol)

_DT_STRUCT = "DataTypeStruct"
_DT_INT = "DataTypeInt"
_DT_BOOL = "DataTypeBool"
_DT_ENUM = "DataTypeEnum"
_DT_CHANDLE = "DataTypeChandle"
_DT_ARRAY = "DataTypeArray"

#: PSS binary operators, in Python. `&&`/`||` become `and`/`or`, which are the
#: same operator with a different spelling; every other entry is shared with C.
_BINOP = {
    "Add": "+", "Sub": "-", "Minus": "-", "Mult": "*", "Mul": "*",
    "Div": "//", "Mod": "%", "Eq": "==", "NotEq": "!=", "Ne": "!=",
    "Lt": "<", "LtE": "<=", "Le": "<=", "Gt": ">", "GtE": ">=", "Ge": ">=",
    "And": "and", "Or": "or", "BitAnd": "&", "BitOr": "|", "BitXor": "^",
    "LShift": "<<", "Shl": "<<", "RShift": ">>", "Shr": ">>",
}
#
# `Div` is the one entry that is a TRANSLATION rather than a spelling. PSS `/`
# is integer division on integral operands and Python's `/` is not, so `nbytes
# / 4` rendered with `/` produces a float and programs a float into a size
# register. `//` is the operator that means what the model means.

_UNOP = {
    "Not": "not ", "LogNot": "not ",
    "Invert": "~", "BitNot": "~", "Neg": "-", "USub": "-", "Minus": "-",
    "UAdd": "+", "Plus": "+",
}

_MEM_PRIMS = {
    "read8": ("read", 8), "read16": ("read", 16),
    "read32": ("read", 32), "read64": ("read", 64),
    "write8": ("write", 8), "write16": ("write", 16),
    "write32": ("write", 32), "write64": ("write", 64),
}

#: PSS built-ins this target claims a rendering for -- its half of the contract
#: in `targets/call_legality.py`. A name claimed there with no rendering here is
#: a call that would reach the output verbatim, which is the whole defect the
#: registry exists to prevent.
PY_BUILTINS = frozenset({
    "message", "print",
    "make_handle_from_handle", "addr_value",
    "add_region", "add_nonallocatable_region",
}) | frozenset(_MEM_PRIMS)

#: Register methods this target renders, and the argument count each takes.
_REG_ACCESSORS = {
    "read": 0, "write": 1, "read_val": 0, "write_val": 1,
    "write_val_masked": 2,
}


def _array_size(dtype) -> Optional[int]:
    """Folded element count of an array type, or ``None``.

    ``None`` matters: the IR carries ``size: -1`` for a bound that is a package
    constant, and treating that as a number emits `range(-1)`, a loop that
    silently does nothing.
    """
    if dtype is None or _dt_name(dtype) != _DT_ARRAY:
        return None
    try:
        n = int(getattr(dtype, "size", None))
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def py_string_literal(s: str) -> str:
    """A PSS string constant as a Python literal. `repr` IS Python's spelling."""
    return repr(s)


def pkg_function_name(fn) -> str:
    """The module-level name of a package-scope function: its qualified PSS
    name with `::` spelled `__`, so two packages' `f` stay apart."""
    return mangle(pf.qualified_name(fn).replace("::", "__"))


def _nodes(node):
    """Every IR node under *node*, by dataclass fields (see validate_calls)."""
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _nodes(n)
        return
    if not dc.is_dataclass(node) or isinstance(node, type):
        return
    yield node
    for f in dc.fields(node):
        yield from _nodes(getattr(node, f.name, None))


def _assigns_local(body, name: str) -> bool:
    """Whether *body* assigns the local *name*."""
    for n in _nodes(body):
        cn = _dt_name(n)
        if cn in ("StmtAssign", "StmtAugAssign"):
            tgts = n.targets if cn == "StmtAssign" else [n.target]
            if any(_dt_name(t) == "ExprRefLocal" and t.name == name
                   for t in tgts):
                return True
    return False



def default_value(dtype, type_map=None) -> str:
    """The value a PSS variable of *dtype* starts with, as Python.

    ONE answer, for a local, a component field and a struct field alike:
    0 for a number, a bool (false) and a handle, the empty string, an enum's
    FIRST item -- which need not be 0, nor its smallest (LRM 7.5 k) -- and a
    new value for a struct and for each element of an array.
    """
    dt = resolve_ref(dtype, type_map)
    cn = _dt_name(dt)
    if cn == _DT_STRUCT:
        # `addr_handle_t` is an opaque handle in PSS and an integer address
        # here, the same mapping `chandle` gets.
        if (getattr(dt, "name", "") or "").split("::")[-1] == "addr_handle_t":
            return "0"
        return f"{value_class_name(dt)}()"
    if cn == _DT_ENUM:
        items = list((getattr(dt, "items", None) or {}).values())
        return str(int(items[0])) if items else "0"
    if cn == "DataTypeString":
        return '""'
    if cn == _DT_ARRAY:
        n = _array_size(dt)
        if n is None:
            return "[]"
        elem = default_value(getattr(dt, "element_type", None), type_map)
        # A struct element is a VALUE per element; `[x] * n` would be n
        # names for one object.
        if elem.endswith("()"):
            return f"[{elem} for _ in range({n})]"
        return f"[{elem}] * {n}"
    if cn in (_DT_INT, _DT_BOOL, _DT_CHANDLE):
        return "0"
    raise ValueError(f"no default value for a variable of type {cn}")


def _params(fn):
    """The declared parameters of *fn*, or ``()``."""
    return tuple(getattr(getattr(fn, "args", None), "args", None) or ())


# -- integer arithmetic (LRM 8.5.1, 8.7) ---------------------------------------
#
# A Python int has no width, so every PSS operation is carried out at the type
# `ExprTypes.propagate` gives it and brought back into range where it could
# leave it. "Could" is decided from each value's RANGE, which is known at
# generation time far more often than not: a `bit[8]` read is in [0, 255], a
# constant is itself. A wrap that cannot change the value is not emitted, so
# `x = y` stays `x = y` and a counter's `n + 1` gets its `& 0xffffffff`.
#
# Two relaxations make that affordable, and both are exact:
#
# * the RING operators (`+ - * & | ^ << ~`, unary `-`) give the same low N bits
#   whatever the operands' higher bits are, so an operand that is one of them
#   is left unwrapped and the wrap happens once, where the value is used;
# * `//` and `%` ARE PSS's `/` and `%` on operands that cannot be negative,
#   which is every unsigned operation. Only a possibly-negative one needs
#   `_pss_div`, which truncates toward zero.

#: Binary operators that commute with reduction modulo 2**N.
_RING_BIN = {"Add", "Sub", "Mult", "BitAnd", "BitOr", "BitXor", "LShift"}

_INT_PYOP = {"Add": "+", "Sub": "-", "Mult": "*", "BitAnd": "&",
             "BitOr": "|", "BitXor": "^", "LShift": "<<", "RShift": ">>",
             "Div": "//", "Mod": "%"}

_FOLD = {
    "Add": lambda a, b: a + b, "Sub": lambda a, b: a - b,
    "Mult": lambda a, b: a * b, "BitAnd": lambda a, b: a & b,
    "BitOr": lambda a, b: a | b, "BitXor": lambda a, b: a ^ b,
    "LShift": lambda a, b: a << b, "RShift": lambda a, b: a >> b,
    "Div": pss_int._pss_div, "Mod": pss_int._pss_mod,
}


@dc.dataclass
class _Val:
    """An integer expression as rendered: its text and what it may hold.

    ``lo``/``hi`` are ``None`` where the range is not known -- which is what
    makes the value be wrapped before anything relies on it.
    """
    text: str
    lo: Optional[int]
    hi: Optional[int]
    atom: bool = True
    const: Optional[int] = None

    def within(self, lo: int, hi: int) -> bool:
        return (self.lo is not None and self.hi is not None
                and lo <= self.lo and self.hi <= hi)

    def nonneg(self) -> bool:
        return self.lo is not None and self.lo >= 0


def _atomic(text: str) -> bool:
    """Whether *text* can be an operand as it stands: a name, a call, an
    attribute or subscript chain, a non-negative number, or bracketed whole."""
    if not text or text[0] == "-" or text.startswith(("not ", "await ")):
        return False
    depth = 0
    quote = None
    escaped = False
    for c in text:
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c in " +-*/%&|^<>=!~,":
            return False
    return True


def _unbracket(text: str) -> str:
    """*text* without the one pair of brackets that encloses all of it."""
    if text.startswith("(") and text.endswith(")") and _atomic(text):
        depth = 0
        for i, c in enumerate(text):
            depth += c == "("
            depth -= c == ")"
            if depth == 0:
                return text[1:-1] if i == len(text) - 1 else text
    return text


def _br(v: _Val) -> str:
    return v.text if v.atom else f"({v.text})"


def _const(c: int, hexa: bool = False) -> _Val:
    text = f"0x{c:x}" if hexa and c >= 0 else str(c)
    return _Val(text, c, c, atom=c >= 0, const=c)


def _mul_range(a: _Val, b: _Val):
    if None in (a.lo, a.hi, b.lo, b.hi):
        return None, None
    p = [a.lo * b.lo, a.lo * b.hi, a.hi * b.lo, a.hi * b.hi]
    return min(p), max(p)


def _bitwise_range(op: str, a: _Val, b: _Val, lo: int, hi: int):
    """The range of `a <op> b`, for `& | ^`. Two's complement on unbounded
    ints: an operand in [0, m] bounds `&` by m whatever the other is, and two
    operands inside a type's range give a result inside it."""
    if op == "BitAnd":
        cands = [v.hi for v in (a, b) if v.nonneg() and v.hi is not None]
        if cands:
            return 0, min(cands)
    if a.nonneg() and b.nonneg() and a.hi is not None and b.hi is not None:
        return 0, (1 << max(a.hi.bit_length(), b.hi.bit_length())) - 1
    if a.within(lo, hi) and b.within(lo, hi):
        return lo, hi
    return None, None


@contextmanager
def _nullctx():
    """Does nothing. For the `with A if c else _nullctx()` forms below."""
    yield


class _BodyEmitter(CallDispatch, BodyWalker):
    """Translate one operation body to Python lines.

    A node kind is handled by a hook named after it (`StmtForeach` ->
    `stmt_foreach`), and a CALL by its `Disposition` from the same registry the
    legality gate consults. Neither of those is this backend's -- which is the
    claim P8.T1 exists to test.
    """

    indent = "    "
    comment_style = HASH

    legality_target = "op-model-py"
    call_context = Ctx.TARGET

    #: Does this emitter colour the calls it renders? A constructor body does
    #: not: it is a SOLVE context, `__init__` cannot be `async def`, and a
    #: target-only call there is already a diagnostic. Stated as a flag rather
    #: than inferred from `call_context` so that the two facts -- "runs at solve
    #: time" and "emits no awaits" -- are separately visible.
    awaits = True

    def __init__(self, fn, comp, model, *, imports=None, ctor_names=None,
                 await_style: str = "sync",
                 pkg_async: FrozenSet[int] = frozenset()):
        self.fn = fn
        #: `id()`s of the package functions that are `async def` in this
        #: form; a call to one is awaited. Decided by the backend
        #: (`PyOpModelBackend._pkg_async`), which renders them.
        self.pkg_async = pkg_async
        #: Set when this body produces an `await`: how the backend learns
        #: whether a package function has to be coloured.
        self.awaited_any = False
        self.comp = comp
        self.model = model
        self.await_style = await_style
        self.ctor_names = ctor_names
        #: Static PSS types of this body's expressions, built on first use.
        self._types = None
        #: `id(node)` -> the type the integer expression being rendered is
        #: carried out at (`ExprTypes.propagate`). Scoped to one root; a
        #: call's arguments inside it are roots of their own.
        self._final = {}
        #: Statements that must be emitted BEFORE the one being rendered:
        #: the hoisted awaited calls. Managed by `render_stmt`, which is the
        #: single dispatch point every statement passes through.
        self._pending: List[str] = []
        #: The indent of the statement being rendered, so a hoisted line lands
        #: at the same level as the statement it feeds.
        self._ind = 0
        #: Names for hoisted values. Per FUNCTION, so they are stable and short;
        #: the leading underscore keeps them clear of PSS locals, whose names
        #: come from the model and do not start with one.
        self._tmp_n = 0
        #: The one call node that must NOT be hoisted, because the statement
        #: being rendered already is that call. See `_no_hoist_for`.
        self._no_hoist: Optional[int] = None
        self._has_executors: Optional[bool] = None
        self.imports = dict(imports or {})
        self.accs = accessor_map(comp)
        self.arg_rename = {a.arg: mangle(a.arg) for a in (fn.args.args if fn.args else [])}
        self.arg_names = set(self.arg_rename)
        self.comp_fields = {f.name for f in getattr(comp, "fields", [])}
        #: Set by the backend for a class in an inheritance hierarchy, which
        #: is rendered NATIVELY: `super` is Python's, and a field a base and a
        #: derived class both declare lives under a per-class name.
        self.native = False
        #: ``{field: attribute}`` for `self.<field>` and for `super.<field>`.
        self.field_storage: Dict[str, str] = {}
        self.super_storage: Dict[str, str] = {}
        #: The base component type `super` reaches, when there is one.
        self.super_base = None
        self.reg_fields = {f.name for f in comp.fields if field_is_reg_group(f)}
        self.reg_groups = {f.name: f.datatype for f in comp.fields
                           if field_is_reg_group(f)}
        self.chan_fields = {f.name for f in channel_fields(comp)}
        self.subs = {s.name: s for s in sub_components(comp)}
        self.model_ops = {f.name for f in comp.functions}
        #: Locals a channel `try_get` writes through. They are emitted as
        #: ONE-ELEMENT LISTS and read as `name[0]`, because a PSS output
        #: argument has no other form here: an int is immutable and a name
        #: rebound inside a call is not rebound outside it. The C backend uses
        #: the same scan for the same reason and reaches a different answer --
        #: it widens the declaration to `uint64_t` because `try_get` writes
        #: through a pointer. Both are "the declaration has to change"; only
        #: the change differs, which is what makes the SCAN language-neutral
        #: and the response not.
        self.chan_out_locals: Set[str] = scan_output_locals(
            fn.body, lambda n: self._is_chan_method(n, "try_get"),
            what="channel try_get()")
        #: `match` subjects are bound to a temporary; the counter keeps nested
        #: matches from shadowing each other.
        self._match_depth = 0
        #: The loop variables in scope, by IR name, with their PSS types --
        #: how `ExprTypes` learns the type of one. See `_loop_var`.
        self.loop_vars = {}

    # -- blocks --------------------------------------------------------------

    def block(self, body, ind: int) -> List[str]:
        """A Python suite: *body*, or `pass` when it renders to no statements.

        Not the same question as "is the body empty". A body of one `yield`
        lowers to one COMMENT on this target, and a suite of only comments is a
        syntax error where the equivalent C block merely has nothing in it.
        """
        lines = self.stmts(body, ind)
        if not any(l.strip() and not l.strip().startswith("#") for l in lines):
            lines.append(self.pad(ind) + "pass")
        return lines

    def emit(self, body, ind: int = 1) -> List[str]:
        return self.block(body, ind)

    # -- awaiting, and where the awaited value goes ---------------------------

    def _awaited(self, text: str, node=None) -> str:
        """*text* is a call that crosses the seam. Render it for this form.

        Sync: unchanged, and nothing else here runs -- the sync output is
        byte-for-byte what it was before this backend had a second form.

        Async: `await` is legal in an arbitrary expression position, and this
        backend does not put it in one. The call becomes a statement of its
        own and what is returned is the NAME OF ITS RESULT:

            csr = await self.regs_CSR_read()
            if csr.ERR == 1:

        rather than `if (await self.regs_CSR_read()).ERR == 1`. Three reasons,
        and the first is the one that makes it not a matter of taste:

        * **`await` binds loosely.** `await self.f() & mask` is legal Python and
          means `await (self.f() & mask)`. The failure is a WRONG VALUE, not a
          syntax error: nothing raises, no golden diff looks odd, and the model
          programs a register with something that is not what it computed.
          Parenthesising at every site would fix it and would depend on getting
          every site right forever; hoisting removes the position where the
          question can be asked.
        * **Evaluation order becomes visible.** A body that reads two registers
          in one expression states an order, and the generated code should show
          the same order a trace will.
        * **It is what the generated code should read like.** A suspension point
          is a real event in an async program, and one per line is how Python
          programmers write them.

        A call that ALREADY is a whole statement (`plat_delay_us(n)`) or is the
        whole right-hand side of one (`t = plat_ticks()`) is not hoisted --
        there is nowhere for it to go and `t = await ...` is already the form
        this produces. `_no_hoist_for` marks that node.
        """
        if not (self.awaits and self.await_style == "async"):
            return text
        self.awaited_any = True
        if node is not None and self._no_hoist == id(node):
            return f"await {text}"
        name = f"_v{self._tmp_n}"
        self._tmp_n += 1
        self._pending.append(f"{self.pad(self._ind)}{name} = await {text}")
        return name

    def _no_hoist_for(self, e):
        """Mark *e* as the call this statement already is, if it is a call.

        Used by the four statement forms whose whole value is one call. Returns
        a context manager so the marking cannot outlive the statement -- an
        exemption that leaked would put a bare `await` back into a subexpression,
        which is precisely what this machinery exists to prevent.
        """
        @contextmanager
        def _mark():
            prev = self._no_hoist
            self._no_hoist = id(e) if _dt_name(e) == "ExprCall" else None
            try:
                yield
            finally:
                self._no_hoist = prev
        return _mark()

    def _take_pending(self) -> List[str]:
        """The hoisted statements so far, removed from the buffer.

        For the two hooks that must place them somewhere other than "just before
        this statement": a loop whose CONDITION crosses the seam has to
        re-evaluate it every iteration, so its hoisted reads belong inside the
        loop. `render_stmt` emits whatever is left.
        """
        out, self._pending = self._pending, []
        return out

    def render_stmt(self, s, ind: int) -> List[str]:
        """The dispatch point, plus the hoisted statements the hook produced.

        EVERY statement passes through here, including nested ones, and each
        gets its own buffer -- so a hoisted read inside an `if` body lands
        inside that body rather than in front of the `if`.
        """
        outer, self._pending = self._pending, []
        outer_ind, self._ind = self._ind, ind
        try:
            lines = super().render_stmt(s, ind)
            return self._pending + lines
        finally:
            self._pending = outer
            self._ind = outer_ind

    # -- expressions ---------------------------------------------------------

    def _operand(self, e) -> str:
        """An operand of a compound expression, bracketed if it is one too.

        The IR tree says how the expression groups and Python precedence only
        happens to agree. Printing the tree's own structure settles it, and
        costs a pair of brackets.
        """
        s = self.expr(e)
        return f"({s})" if _dt_name(e) in ("ExprBin", "ExprUnary") else s

    def expr_constant(self, e) -> str:
        v = e.value
        if isinstance(v, bool):
            return "True" if v else "False"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            return py_string_literal(v)
        raise ValueError(f"unsupported constant of type {type(v).__name__}")

    def expr_ref_local(self, e) -> str:
        name = self.arg_rename.get(e.name, mangle(e.name))
        # A channel-output local is a cell; every use of it is a use of its
        # contents. The one place that wants the cell itself is the `try_get`
        # call, which asks for it directly (`_chan_call`).
        return f"{name}[0]" if e.name in self.chan_out_locals else name

    def type_expr_ref_self(self, e) -> str:
        return "self"

    def expr_attribute(self, e) -> str:
        base = e.value
        if _dt_name(base) == "TypeExprRefSelf":
            # A component DATA MEMBER is an attribute of the instance; an
            # argument shadows nothing and stays a local. Anything else is a
            # package-scope constant, which reaches Python as a bare name -- and
            # if nothing defines it, as a NameError at the first call rather
            # than as C's silent resolution against whatever else is in scope.
            # A parameter shadows a field of the same name (20.3): the front
            # end spells both `self.a`, so scope order decides, as it does in
            # `ExprTypes` and in bc.
            if e.attr in self.arg_names:
                return self.arg_rename[e.attr]
            if e.attr in self.comp_fields:
                return f"self.{self.field_storage.get(e.attr, mangle(e.attr))}"
            return mangle(e.attr)
        if _dt_name(base) == "TypeExprRefSuper":
            # `super.a`: the base's field, which a derived class declaring its
            # own `a` does not replace (17.1).
            if e.attr not in self.super_storage:
                raise ValueError(
                    f"'super.{e.attr}' in '{getattr(self.fn, 'name', '?')}': "
                    f"no base of '{getattr(self.comp, 'name', '?')}' has a "
                    f"field '{e.attr}'")
            return f"self.{self.super_storage[e.attr]}"
        return f"{self.expr(base)}.{e.attr}"

    def expr_subscript(self, e) -> str:
        return f"{self.expr(e.value)}[{self.expr(e.slice)}]"

    def expr_bin(self, e) -> str:
        op = e.op.name
        if op in RELATIONAL:
            text = self._int_compare(e)
            if text is not None:
                return text
        elif op in CONTEXT_BINARY or op in LEFT_TYPED:
            t = self.types.type_of(e)
            if is_integral(t):
                return self._int_root(e, t.as_int()).text
        return self._untyped_bin(e)

    def _untyped_bin(self, e) -> str:
        """An operator whose operands this backend cannot type, or that is not
        integer arithmetic at all (`&&`, `==` on strings or structs)."""
        op = _BINOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported binop {e.op.name}")
        lhs = self._operand(e.lhs)
        mark = len(self._pending)
        rhs = self._operand(e.rhs)
        if op in ("and", "or") and len(self._pending) > mark:
            # REFUSED rather than lowered, and this is the one place this
            # backend gives up something the sync form can do.
            #
            # `&&` and `||` SHORT-CIRCUIT: the right operand runs only if the
            # left did not settle the answer. Hoisting the right operand's
            # awaited call to a statement in front of the expression would run
            # it unconditionally -- and on a status register whose read clears
            # bits, an access that should not have happened is not a
            # performance note. The alternatives are both worse: an inline
            # `(await ...)` puts the precedence hazard back, and rewriting the
            # operator into nested `if`s changes a condition into control flow
            # that no longer resembles the model.
            #
            # Splitting the condition in the PSS is a one-line change and says
            # what the author means about the order.
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': the right-hand side of "
                f"'{e.op.name}' crosses the platform seam, and '{op}' "
                f"short-circuits -- so the async form cannot render it without "
                f"either performing an access the model says is conditional, or "
                f"placing an `await` inside an expression, where it binds "
                f"loosely enough to change the value. Split the condition: "
                f"evaluate the right-hand side into a local first, or use "
                f"nested `if`s.")
        if e.op.name in ("Div", "Mod"):
            # Untyped operands may still be negative, and PSS `/` truncates
            # toward zero where `//` floors.
            return f"_pss_{e.op.name.lower()}({lhs}, {rhs})"
        return f"{lhs} {op} {rhs}"

    def expr_unary(self, e) -> str:
        if e.op.name in CONTEXT_UNARY:
            t = self.types.type_of(e)
            if is_integral(t):
                return self._int_root(e, t.as_int()).text
        op = _UNOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported unary op {e.op.name}")
        return f"{op}({self.expr(e.operand)})"

    def expr_if_exp(self, e) -> str:
        """`c ? a : b` (8.5.8) -> `(a if c else b)`.

        Only the chosen arm is evaluated, in both languages. Integer arms are
        carried out at the type of the whole (Table 22), like `+`'s operands.
        """
        t = self.types.type_of(e)
        if t is not None and t.kind == "int":
            return self._int_root(e, t).text
        test = self.expr(e.test)
        a, b = self._arms(e, self.expr)
        return f"({a} if {test} else {b})"

    def _arms(self, e, render):
        """The two arms of `?:`, refused if either would be hoisted: an arm
        runs only if chosen, and a hoisted call runs regardless -- the
        short-circuit problem `&&` has (see `_untyped_bin`)."""
        mark = len(self._pending)
        a, b = render(e.body), render(e.orelse)
        if len(self._pending) > mark:
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': an arm of `?:` crosses "
                f"the platform seam, and only the chosen arm may run -- so the "
                f"async form cannot hoist it. Use an `if` statement.")
        return a, b

    def expr_cast(self, e) -> str:
        """`(bit[32])x` -> `x & 0xffffffff`; `(int[8])x` -> `_pss_sint(x, 8)`.

        A cast is an assignment-like context (8.7.2), converted the way an
        assignment is -- and NOT dropped, which is the one place where Python
        needs more than C rather than less: a C `uint32_t` truncates on
        assignment and a Python int does not. Where the operand provably fits,
        nothing is emitted. A cast to a non-integer type is the identity.
        """
        text = self.convert_to(e.value, self.types.of_datatype(e.target_type))
        return text if _atomic(text) else f"({text})"

    # -- integer arithmetic ----------------------------------------------------

    @property
    def types(self) -> ExprTypes:
        if self._types is None:
            self._types = ExprTypes(self.fn, self.comp,
                                    getattr(self.model, "ctx", None),
                                    loop_vars=self.loop_vars)
        return self._types

    def convert_to(self, e, target) -> str:
        """*e* in an assignment-like context whose target type is *target*
        (LRM 8.7.2): evaluated at the target's width if that is wider, then
        truncated or extended to the target.

        Anything that is not integer-to-integer renders as it stands: an enum,
        a struct, a string, or a value this backend cannot type.
        """
        v = self._convert_val(e, target)
        return self.expr(e) if v is None else v.text

    def _convert_val(self, e, target) -> Optional[_Val]:
        """`convert_to`, with the range of the result; ``None`` where the
        conversion does not apply."""
        s = self.types.type_of(e)
        if target is None or target.kind != "int" or not is_integral(s):
            return None
        # The source is evaluated at least as wide as the target, so the
        # target's wrap alone decides the value: a ring operator's result
        # need not be wrapped to the source type first.
        return self._fit(self._int_root(e, assign_source_type(target, s),
                                        ring=True), target)

    def converts_as_is(self, e, target) -> bool:
        """Whether `convert_to(e, target)` is *e* rendered as it stands, known
        WITHOUT rendering it. For the statements whose value may be a call
        that must not be hoisted -- which it has to be, if it gets wrapped."""
        s = self.types.type_of(e)
        if target is None or target.kind != "int" or not is_integral(s):
            return True
        return _Val("", *int_range(s)).within(*int_range(target))

    def call_args(self, args, fn) -> List[str]:
        """Call arguments to *fn*, each converted to its parameter's declared
        type (8.7.2, "function call parameters"). A parameter this backend
        cannot type -- a generic one, or *fn* None -- takes its argument as
        it stands.

        Trailing arguments the call omits take the parameters' DEFAULTS
        (22.2.1), rendered here: `f(h)` for a declared `f(h, d = {})` passes
        a fresh `d`, which a Python default could not -- it is evaluated
        once, and a struct is a value.
        """
        params = list(_params(fn))
        out = []
        for i, a in enumerate(args):
            ann = getattr(params[i], "annotation", None) \
                if i < len(params) else None
            out.append(self.convert_to(a, self.types.of_datatype(ann))
                       if ann is not None else self.expr(a))
        if len(args) < len(params):
            defaults = list(getattr(fn.args, "defaults", None) or ())
            first = len(params) - len(defaults)
            for i in range(len(args), len(params)):
                if i < first:
                    raise ValueError(
                        f"call to '{fn.name}' omits '{params[i].arg}', which "
                        f"has no default")
                out.append(self._default_arg(fn, params[i],
                                             defaults[i - first]))
        return out

    def _default_arg(self, fn, param, e) -> str:
        """A parameter's default value, in the caller.

        Only a value that means the same in any scope: a constant expression,
        or `{}` for an aggregate -- its type's default. A default naming a
        field or calling a function would be evaluated where the CALL is, and
        mean something else.
        """
        ann = getattr(param, "annotation", None)
        if _dt_name(e) == "ExprList":
            if e.elts:
                raise ValueError(
                    f"'{fn.name}': the default of '{param.arg}' is an "
                    f"aggregate literal, which is not lowered; only `{{}}` is")
            return default_value(ann, self.types._tm)
        for n in _nodes(e):
            if _dt_name(n) not in ("ExprConstant", "ExprBin", "ExprUnary",
                                   "ExprIfExp", "ExprCast"):
                raise ValueError(
                    f"'{fn.name}': the default of '{param.arg}' is not a "
                    f"constant expression ({_dt_name(n)}); it would be "
                    f"evaluated in the caller's scope, not the callee's")
        return (self.convert_to(e, self.types.of_datatype(ann))
                if ann is not None else self.expr(e))

    def _int_root(self, e, final, ring: bool = False) -> _Val:
        """*e*, evaluated at *final*: in range, or with *ring*, only right
        modulo 2**N (see `_int_opnd`) for a caller that wraps it anyway."""
        saved = self._final
        self._final = {}
        try:
            self.types.propagate(e, final, self._final)
            return self._int_opnd(e) if ring else self._int_val(e)
        finally:
            self._final = saved

    def _int_compare(self, e) -> Optional[str]:
        """A relational or equality operator over integers (8.5.2, Table 22):
        both operands at the larger width, unsigned unless both are signed.
        ``None`` if an operand is not an integer this backend can type."""
        lt = self.types.type_of(e.lhs)
        rt = self.types.type_of(e.rhs)
        if not (is_integral(lt) and is_integral(rt)):
            return None
        if lt.kind == "enum" or rt.kind == "enum":
            # Enum against enum compares items; there is nothing to convert.
            return None
        p = merge(lt, rt)
        a = self._int_root(e.lhs, p)
        b = self._int_root(e.rhs, p)
        return f"{_br(a)} {_BINOP[e.op.name]} {_br(b)}"

    def _fit(self, v: _Val, t) -> _Val:
        """*v* brought into the range of *t*, if it may be outside it."""
        lo, hi = int_range(t)
        if v.const is not None:
            c = convert(v.const, t)
            return _const(c, hexa=c != v.const and c > 0xffff)
        if v.within(lo, hi):
            return v
        if t.as_int().signed:
            return _Val(f"_pss_sint({v.text}, {t.as_int().width})", lo, hi)
        return _Val(f"{_br(v)} & 0x{hi:x}", lo, hi, atom=False)

    def _int_val(self, e) -> _Val:
        """*e* at its final type, in range."""
        return self._fit(self._int_raw(e), self._final[id(e)])

    def _int_opnd(self, e) -> _Val:
        """*e* as the operand of a ring operator: left unwrapped if it is one
        too, since the result is the same modulo 2**N (see `_RING_BIN`)."""
        cn = _dt_name(e)
        if ((cn == "ExprBin" and e.op.name in _RING_BIN)
                or (cn == "ExprUnary" and e.op.name in CONTEXT_UNARY)):
            return self._int_raw(e)
        if (cn not in ("ExprBin", "ExprUnary", "ExprIfExp")
                and self.types.type_of(e).as_int().width
                >= self._final[id(e)].as_int().width):
            # A primary no narrower than the operation: extending it changes
            # no bit the operation keeps.
            return self._int_leaf(e, ring=True)
        return self._int_val(e)

    def _int_self(self, e) -> _Val:
        """A self-determined operand -- a shift amount, an exponent."""
        if id(e) in self._final:
            return self._int_val(e)
        text = self.expr(e)
        return _Val(text, None, None, atom=_atomic(text))

    def _int_raw(self, e) -> _Val:
        cn = _dt_name(e)
        if cn == "ExprBin" and (e.op.name in CONTEXT_BINARY
                                or e.op.name in LEFT_TYPED):
            return self._int_bin(e)
        if cn == "ExprUnary" and e.op.name in CONTEXT_UNARY:
            return self._int_unary(e)
        if cn == "ExprIfExp":
            test = self.expr(e.test)
            a, b = self._arms(e, self._int_val)
            lo = hi = None
            if None not in (a.lo, a.hi, b.lo, b.hi):
                lo, hi = min(a.lo, b.lo), max(a.hi, b.hi)
            return _Val(f"({_br(a)} if {test} else {_br(b)})", lo, hi)
        return self._int_leaf(e)

    def _int_leaf(self, e, ring: bool = False) -> _Val:
        """A primary, converted to the type its context gives it (8.7.1):
        sign-extended if that is signed, which a Python int already is, and
        zero-extended if it is unsigned -- its own bit pattern, masked. With
        *ring*, left as it is (see `_int_opnd`)."""
        t = self._final[id(e)]
        s = self.types.type_of(e).as_int()
        v = getattr(e, "value", None) if _dt_name(e) == "ExprConstant" else None
        if isinstance(v, int) and not isinstance(v, bool):
            return _const(v if t.signed else v & ((1 << s.width) - 1))
        if _dt_name(e) == "ExprCast":
            # Its range is what the conversion produced, which is often much
            # less than the whole of the cast type: `(bit[32])flag` is 0 or 1.
            cv = self._convert_val(e.value,
                                   self.types.of_datatype(e.target_type))
            if cv is not None:
                cv = dc.replace(cv, text=cv.text if cv.atom
                                else f"({cv.text})", atom=True)
                if not (s.signed and not t.signed and not ring) \
                        or cv.nonneg():
                    return cv
        text = self.expr(e)
        lo, hi = int_range(s)
        if s.signed and not t.signed and not ring:
            m = (1 << s.width) - 1
            return _Val(f"{text if _atomic(text) else f'({text})'} & 0x{m:x}",
                        0, m, atom=False)
        return _Val(text, lo, hi, atom=_atomic(text))

    def _int_unary(self, e) -> _Val:
        a = self._int_opnd(e.operand)
        op = e.op.name
        if op == "UAdd":
            return a
        if op == "USub":
            if a.const is not None:
                return _const(-a.const)
            lo = -a.hi if a.hi is not None else None
            hi = -a.lo if a.lo is not None else None
            return _Val(f"-{_br(a)}", lo, hi, atom=False)
        if a.const is not None:
            return _const(~a.const)
        lo = -a.hi - 1 if a.hi is not None else None
        hi = -a.lo - 1 if a.lo is not None else None
        return _Val(f"~{_br(a)}", lo, hi, atom=False)

    def _int_bin(self, e) -> _Val:
        op = e.op.name
        t = self._final[id(e)].as_int()
        tlo, thi = int_range(t)
        if op == "Exp":
            return self._int_pow(e, t)
        if op in _RING_BIN and op != "LShift":
            a, b = self._int_opnd(e.lhs), self._int_opnd(e.rhs)
        elif op == "LShift":
            a, b = self._int_opnd(e.lhs), self._int_self(e.rhs)
        elif op == "RShift":
            a, b = self._int_val(e.lhs), self._int_self(e.rhs)
        else:                                   # Div, Mod
            a, b = self._int_val(e.lhs), self._int_val(e.rhs)
        if a.const is not None and b.const is not None:
            if op in ("Div", "Mod") and b.const == 0:
                raise ValueError(
                    f"in '{getattr(self.fn, 'name', '?')}': division by zero "
                    f"in a constant expression (LRM 8.5.1)")
            if op in ("LShift", "RShift") and b.const < 0:
                raise ValueError(
                    f"in '{getattr(self.fn, 'name', '?')}': a negative shift "
                    f"amount (LRM 8.5.7)")
            return _const(_FOLD[op](a.const, b.const))
        lo = hi = None
        if op == "Add" and None not in (a.lo, a.hi, b.lo, b.hi):
            lo, hi = a.lo + b.lo, a.hi + b.hi
        elif op == "Sub" and None not in (a.lo, a.hi, b.lo, b.hi):
            lo, hi = a.lo - b.hi, a.hi - b.lo
        elif op == "Mult":
            lo, hi = _mul_range(a, b)
        elif op in ("BitAnd", "BitOr", "BitXor"):
            lo, hi = _bitwise_range(op, a, b, tlo, thi)
        elif op == "LShift":
            if b.const is not None and b.const >= 0 and None not in (a.lo, a.hi):
                lo, hi = a.lo << b.const, a.hi << b.const
        elif op == "RShift":
            # `>>` of an in-range value stays in range; Python's is
            # arithmetic on a negative one, which is 8.5.7's fill with ones.
            lo, hi = min(a.lo, 0), max(a.hi, 0)
            if b.const is not None and b.const >= 0:
                lo, hi = a.lo >> b.const, a.hi >> b.const
        else:                                   # Div, Mod
            # |a / b| <= |a|, and a remainder has a's sign and at most its
            # size -- so both stay in the type, but for the one quotient
            # that cannot: the most negative value divided by -1.
            lo, hi = min(a.lo, -a.hi, 0), max(a.hi, -a.lo, 0)
            if op == "Mod":
                lo, hi = min(a.lo, 0), max(a.hi, 0)
            elif a.lo > tlo or b.lo > -1 or b.hi < -1:
                lo, hi = max(lo, tlo), min(hi, thi)
            if a.nonneg() and b.nonneg():
                return _Val(f"{_br(a)} {_INT_PYOP[op]} {_br(b)}", lo, hi,
                            atom=False)
            return _Val(f"_pss_{op.lower()}({a.text}, {b.text})", lo, hi)
        return _Val(f"{_br(a)} {_INT_PYOP[op]} {_br(b)}", lo, hi, atom=False)

    def _int_pow(self, e, t) -> _Val:
        """`a ** b` (8.5.1): the left operand's type; the exponent is
        self-determined. Reduced modulo 2**N as it is computed, which is both
        the PSS result and the only way `2 ** n` stays cheap."""
        a, b = self._int_opnd(e.lhs), self._int_self(e.rhs)
        m = 1 << t.width
        if a.const is not None and b.const is not None:
            return _const(pss_int._pss_pow(a.const, b.const, t.width))
        if b.nonneg():
            return _Val(f"pow({a.text}, {b.text}, 0x{m:x})", 0, m - 1)
        return _Val(f"_pss_pow({a.text}, {b.text}, {t.width})", 0, m - 1)

    def expr_ref_bottom_up(self, e) -> str:
        raise ValueError(
            f"component '{getattr(self.comp, 'name', '?')}' refers UPWARD to "
            f"its parent ({_dt_name(e)}). This lowering constructs each "
            f"sub-component with its own bus and base and emits no parent "
            f"back-pointer, so there is nothing for this to resolve to. Move "
            f"the shared state down, or pass it as an argument.")

    # -- super ---------------------------------------------------------------

    def expr_call(self, call) -> str:
        func = getattr(call, "func", None)
        if is_super_call(func) and func.attr not in _MEM_PRIMS:
            return self._super_call(call)
        return super().expr_call(call)

    def _super_call(self, call) -> str:
        """`super.f(args)`: the base's `f`, statically -- Python's `super()`.

        Only in a class rendered natively (`native`), whose base class is the
        base component's. `super.initialize(...)` is the base's PSS
        constructor, which such a class holds in `_pss_ctor`.
        """
        name = call.func.attr
        base = self.super_base
        callee = next((f for f in (getattr(base, "functions", None) or ())
                       if f.name == name and exec_kind(f) is None), None)
        if not self.native or callee is None:
            raise ValueError(
                f"'super.{name}(...)' in '{getattr(self.fn, 'name', '?')}': "
                f"no base of '{getattr(self.comp, 'name', '?')}' declares "
                f"'{name}'")
        args = self.call_args(call.args, callee)
        if func_kind(callee, self.ctor_names) is FuncKind.CONSTRUCTOR:
            return f"super().{CTOR_METHOD}(" + ", ".join(args) + ")"
        return self._awaited(
            f"super().{mangle(name)}(" + ", ".join(args) + ")", call)

    # -- calls, one hook per Disposition -------------------------------------

    def call_names(self):
        from ..validate_calls import model_names

        _, ops, ctors = model_names(self.comp, self.ctor_names)
        return dict(model_ops=ops, imports=frozenset(self.imports),
                    subcomps=ctors, pkg_funcs=self._pkg_contexts())

    def _pkg_functions(self):
        """``{name: function}`` for the package functions this model carries,
        under every name a call may use."""
        have = {id(f) for f in getattr(self.model, "functions", ()) or ()}
        return {n: f for n, f in
                pf.declared(getattr(self.model, "ctx", None)).items()
                if id(f) in have}

    def _pkg_contexts(self):
        return {n: pf.contexts_of(f) for n, f in self._pkg_functions().items()}

    def call_pkg_func(self, call) -> Optional[str]:
        """A package-scope function: a module-level function of the generated
        module, passed the calling component.

        The component is how the function reaches the platform (a `message`,
        a `write32`): it has no component of its own, and the seam is the one
        thing every component carries. It reads nothing else from it.
        """
        fn = pf.callee(call, self._pkg_functions())
        if fn is None:
            return None
        args = ["self"] + self.call_args(call.args, fn)
        text = f"{pkg_function_name(fn)}({', '.join(args)})"
        if id(fn) not in self.pkg_async:
            return text
        if not self.awaits:
            raise ValueError(
                f"'{pf.qualified_name(fn)}' is `async def` in this form -- it "
                f"reaches the platform -- and is called from a constructor, "
                f"which cannot await")
        return self._awaited(text, call)

    def call_reg(self, call) -> Optional[str]:
        return self._reg_call(call)

    def call_channel(self, call) -> Optional[str]:
        return self._chan_call(call)

    def call_mem(self, call) -> Optional[str]:
        return self._builtin_call(call)

    #: Address arithmetic and the console are both rendered by the same method,
    #: which dispatches on the built-in's own name.
    call_addr = call_mem
    call_utility = call_mem

    def call_model_op(self, call) -> Optional[str]:
        return self._model_call(call)

    call_import = call_model_op

    # -- registers -----------------------------------------------------------

    def _chain(self, e):
        """Flatten `self.<group>...<reg>[i]` to `[[name, index|None], ...]`.

        ``None`` for anything not rooted at `self`, which is how a call on
        something else falls through to the generic paths.
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

    def _reg_call(self, call) -> Optional[str]:
        """A register access -> the generated accessor method.

        The method NAME is asked of `naming.reg_symbol`, the same function that
        named the definition, so a rename cannot leave the two disagreeing. An
        unrecognised method on a register raises rather than falling through:
        `regs.csr.write_val(x)` reaching the generic path would emit
        `self.regs.csr.write_val(x)`, an attribute chain that exists nowhere and
        that says nothing useful when it fails.
        """
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        # A register lives inside a group, so its path is at least
        # `<group>.<reg>`. A one-element chain is a call on the GROUP.
        if not chain or len(chain) < 2 or chain[0][0] not in self.reg_fields:
            return None

        reg = chain[-1][0]
        segs = [c[0] for c in chain[:-1]]
        idx = [self.expr(c[1]) for c in chain if c[1] is not None]
        method = func.attr
        if method not in _REG_ACCESSORS:
            raise ValueError(
                f"unsupported register method '{method}' on "
                f"'{'.'.join(segs + [reg])}'. This target emits one accessor "
                f"per register method; known: "
                f"{', '.join(sorted(_REG_ACCESSORS))}.")
        want = _REG_ACCESSORS[method]
        if len(call.args) != want:
            raise ValueError(
                f"register method '{method}' takes {want} argument(s), got "
                f"{len(call.args)}")
        acc = self.accs.get(tuple(segs) + (reg,))
        if acc is None:
            raise ValueError(
                f"'{'.'.join(segs + [reg])}' is accessed as a register, but "
                f"'{getattr(self.comp, 'name', '?')}' emits no accessor for it. "
                f"Reachable: {', '.join('.'.join(p) for p in sorted(self.accs))}")
        # A value written is converted to the register's width, like any
        # argument to a `bit[N]` parameter; a value struct is passed whole.
        val_t = PssType("int", acc.value_bits, False)
        args = [self.convert_to(a, val_t) for a in call.args]
        name = reg_symbol(acc.segs, acc.name, method)
        # AWAITED: every register accessor except `_addr` crosses the seam, and
        # `_addr` is never called from here -- a body says `regs.CSR.read()`,
        # never `regs.CSR.addr()`.
        return self._awaited(
            f"self.{name}(" + ", ".join(idx + args) + ")", call)

    # -- channels ------------------------------------------------------------

    def _chan_ref(self, call) -> Optional[str]:
        """`wake.try_put(1)` -> `self.wake`; `ch[i].wake...` -> `self.ch[i].wake`.

        NOT just this component's own channels. A channel reached THROUGH a
        sub-component is how a model fans an event out -- `notify_irq()` posts
        to every channel's `wake` -- and a sub-component is a real object here,
        so the reference is simply longer. The walk descends the declared field
        types rather than assuming the chain is well-formed: a name that is not
        a sub-component, or a final segment that is not a channel, returns
        ``None`` and falls through to the generic paths, which is what makes an
        unrelated method call on an unrelated object still lower.
        """
        func = getattr(call, "func", None)
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if not chain:
            return None
        comp = self.comp
        parts: List[str] = []
        for k, (name, idx) in enumerate(chain):
            if k == len(chain) - 1:
                if name not in {f.name for f in channel_fields(comp)}:
                    return None
                if idx is not None:
                    # A channel ARRAY. Nothing in scope declares one, and
                    # inventing an indexing rule for it here would be a guess.
                    return None
                parts.append(mangle(name))
                return "self." + ".".join(parts)
            sub = {s.name: s for s in sub_components(comp)}.get(name)
            if sub is None:
                return None
            parts.append(mangle(name) + (f"[{self.expr(idx)}]"
                                         if idx is not None else ""))
            comp = sub.dtype
        return None

    def _is_chan_method(self, call, method: str) -> bool:
        return (_dt_name(getattr(call, "func", None)) == "ExprAttribute"
                and call.func.attr == method
                and self._chan_ref(call) is not None)

    def _chan_call(self, call) -> Optional[str]:
        """`wake.try_put(1)` -> `self.wake.try_put(1)`, on the shipped `Chan1`.

`try_get` is handed the output local's CELL: see
        `Chan1.try_get` for why a PSS output argument reaches Python that way.

        Blocking `get()`/`put()` are refused by the registry before they get
        here; `Chan1` also raises, for the model that reached it another way.
        """
        ref = self._chan_ref(call)
        if ref is None:
            return None
        m = call.func.attr
        if m == "try_put":
            if len(call.args) != 1:
                raise ValueError("channel try_put() takes one argument")
            # NOT awaited, in either form: `try_put` is the non-blocking one and
            # its whole purpose is to answer immediately.
            return f"{ref}.try_put({self.expr(call.args[0])})"
        if m == "try_get":
            if len(call.args) != 1:
                raise ValueError("channel try_get() takes one output argument")
            # The CELL, not its contents: this call is what fills it.
            out = call.args[0]
            cell = self.arg_rename.get(out.name, mangle(out.name))
            return f"{ref}.try_get({cell})"
        if m in ("get", "put") and self.await_style == "async":
            # Blocking, which is what these MEAN, and which the async form can
            # finally render: `pssc_rt_async.Chan1` suspends on an event. The
            # sync form never reaches here -- the legality registry refuses the
            # call before lowering.
            args = ", ".join(self.expr(a) for a in call.args)
            return self._awaited(f"{ref}.{m}({args})", call)
        raise ValueError(
            f"unsupported channel method '{m}' on '{ref}'. This form of the "
            f"Python channel runtime implements try_put/try_get "
            f"(share/py/pssc_rt.py); get/put need --py-await async.")

    # -- built-ins and model calls -------------------------------------------

    def _builtin_name(self, func) -> Optional[str]:
        cn = _dt_name(func)
        if cn == "ExprRefUnresolved":
            # `std_pkg::message` is `message` (`validate_calls.callee_name`).
            return callee_name(func)
        if cn == "ExprAttribute":
            return func.attr
        return None

    def _builtin_call(self, call) -> Optional[str]:
        """PSS built-ins, which have no definition to call.

        The address built-ins are not calls at all here: `addr_handle_t` is
        opaque in PSS and a plain integer address in the generated code, so
        deriving a handle from another is an add and reading its value is the
        identity.
        """
        name = self._builtin_name(call.func)
        if name is None or name not in PY_BUILTINS:
            return None
        args = call.args
        if name == "make_handle_from_handle":
            return f"({self.expr(args[0])} + {self.expr(args[1])})"
        if name == "addr_value":
            return self.expr(args[0])
        if name in ("add_region", "add_nonallocatable_region"):
            return self._add_region(name, args)
        if name in _MEM_PRIMS:
            return self._mem_call(name, args, call)
        if name in ("message", "print"):
            return self._message_call(name, args)
        raise ValueError(
            f"built-in '{name}' is claimed by the Python target but has no "
            f"rendering; see targets/py/lower_progseq.py")

    def _add_region(self, name: str, args) -> str:
        """`space.add_region(r)` -> `r.addr`, for a transparent region.

        The handle to a region's start (21.10.1.2) is an address here, and a
        TRANSPARENT region states its start in `addr` (21.10.3.3). Adding it
        needs no object: the address space exists for the solver's
        allocation, which an operation model does not do, so nothing else is
        recorded. Any other region is refused -- its address is the PSS
        tool's choice, and there is no tool here to make it.
        """
        if len(args) != 1:
            raise ValueError(f"'{name}' takes one region, got {len(args)}")
        t = self.types.type_of(args[0])
        dt = t.dtype if t is not None and t.kind == "struct" else None
        tm = self.types._tm
        chain = []
        while dt is not None:
            chain.append((getattr(dt, "name", "") or "").split("::")[-1])
            dt = struct_base(dt, tm)
        if "transparent_addr_region_s" not in chain:
            raise ValueError(
                f"'{name}' of a region whose address is not stated"
                f" ({chain[0] if chain else 'an untyped value'}): where such "
                f"a region goes is the PSS tool's choice, and an operation "
                f"model has no tool to make it. Use a "
                f"`transparent_addr_region_s` and set its `addr` (LRM "
                f"21.10.3.3).")
        region = self.expr(args[0])
        return f"{region if _atomic(region) else f'({region})'}.addr"

    def _mem_call(self, name: str, args, call=None) -> str:
        """`read32(h)` / `write32(h, v)` -> the import API, or the executor.

        NOT register accesses -- a model uses these to put a descriptor into
        system RAM -- but they cross the same seam and are rendered against the
        same object, so one import-API implementation serves both. Awaited for
        the same reason: crossing the seam is what can consume time.

        In a model with an executor the call is DELEGATED (LRM 21.13.9.5): to
        the component's executor, which overrides the primitive or inherits
        the default that calls the seam (`_PssDefaultExecutor`). The
        descriptor is passed, defaulted to `{}` as the prototype has it. With
        no executor nothing reads it, and 21.13.9 lets a tool ignore it.

        `addr_reg_pkg::read32(h)` is the same function as `read32(h)` and is
        delegated the same way; only `super.read32(h)`, in an executor, is the
        default implementation.
        """
        direction, width = _MEM_PRIMS[name]
        want = 1 if direction == "read" else 2
        if len(args) not in (want, want + 1):
            raise ValueError(
                f"'{name}' takes {want} argument(s) and an optional "
                f"mem_access_desc_s, got {len(args)}")
        rendered = [self.expr(args[0])]
        if direction == "write":
            rendered.append(self.convert_to(
                args[1], PssType("int", width, False)))
        desc = args[want] if len(args) > want else None
        if not self.has_executors:
            if desc is not None and pf.calls_in(desc):
                raise ValueError(
                    f"'{name}': its mem_access_desc_s argument calls a "
                    f"function, and no executor in this model reads the "
                    f"argument -- ignoring it (LRM 21.13.9) would drop the "
                    f"call")
            return self._awaited(
                f"self.{IMPORTS_ATTR}.{name}(" + ", ".join(rendered) + ")",
                call)
        rendered.append(self._desc_arg(desc))
        if call is not None and is_super_call(call.func):
            # `super.write32(...)` in an executor: its base's `write32`, the
            # default implementation -- not the override that `write32(...)`
            # and `addr_reg_pkg::write32(...)` both reach (LRM 21.13.9.5).
            if not xtr.is_executor(self.comp, self.types._tm):
                raise ValueError(
                    f"'super.{name}' in '{getattr(self.comp, 'name', '?')}', "
                    f"which is not an executor: only an executor's base "
                    f"declares the memory primitives (LRM Syntax 159)")
            # Python's `super()`: the class this body is declared in derives
            # from its base executor's class, or from the default
            # implementation, so method resolution finds the right one.
            return self._awaited(
                f"super().{name}(" + ", ".join(rendered) + ")", call)
        return self._awaited(
            f"self.{EXECUTOR_ATTR}.{name}(" + ", ".join(rendered) + ")", call)

    @property
    def has_executors(self) -> bool:
        """Whether this model renders executor delegation
        (`executors.has_executors`)."""
        if self._has_executors is None:
            self._has_executors = xtr.has_executors(self.model)
        return self._has_executors

    def _desc_arg(self, desc) -> str:
        """A delegated call's descriptor: *desc* as written, or a new default
        one -- a fresh value per call, as `= {}` is (20.3.2)."""
        if desc is None or (_dt_name(desc) == "ExprList" and not desc.elts):
            tm = self.types._tm
            return default_value(xtr.mem_access_desc(tm), tm)
        return self.expr(desc)

    def _message_call(self, name: str, args) -> str:
        """`message(verbosity, fmt, args...)` -> `self._imports.message(text)`.

        Routed through the import API rather than to `print` so that a harness
        driving several models can collect their output; `MemoryBus.message`
        defaults to printing, which is what a bare script wants. The verbosity
        has no analogue and is dropped, exactly as the SV and C projections drop
        it.

        The platform receives ONE finished string. A format with no `%` is
        passed as it stands. Anything else is formatted by `_pss_fmt`, which
        the module carries (`pss_format.py`), with each value tagged with its
        PSS type: `%d` on a `bit[8]` and on an `int` print the same host value
        differently, and `%n` needs the enum's item names. The format is checked
        HERE, against the LRM 21.1.1 rules, so a bad one is a build error.
        """
        rest = args[1:] if (name == "message" and len(args) >= 2) else args
        if not rest:
            raise ValueError(f"'{name}' needs a format string")
        fmt_e, vals = rest[0], rest[1:]
        fmt = getattr(fmt_e, "value", None)
        if _dt_name(fmt_e) != "ExprConstant" or not isinstance(fmt, str):
            raise ValueError(
                f"'{name}' needs a string literal as its format, so the format "
                f"can be checked when the model is generated; got "
                f"{_dt_name(fmt_e)}")
        if "%" not in fmt and not vals:
            return f"self.{IMPORTS_ATTR}.message({self.expr(fmt_e)})"
        try:
            specs = parse_format(fmt)
        except ValueError as e:
            raise ValueError(f"{name}({fmt!r}): {e} (LRM 21.1.1 a)") from None
        if len(specs) != len(vals):
            raise ValueError(
                f"{name}({fmt!r}): {len(specs)} format specifier(s) for "
                f"{len(vals)} argument(s) (LRM 21.1.1 b)")
        parts = [self.expr(fmt_e)]
        for k, (spec, v) in enumerate(zip(specs, vals), 1):
            parts.append(f"({self.expr(v)}, "
                         f"{self._fmt_type(name, fmt, k, spec[5], v)})")
        return (f"self.{IMPORTS_ATTR}.message(_pss_fmt("
                + ", ".join(parts) + "))")

    def _fmt_type(self, name, fmt, k, conv, v) -> str:
        """The `_pss_fmt` type tag of argument ``k``; LRM 21.1.1 c) checked."""
        t = self.types.type_of(v)
        where = f"{name}({fmt!r}), argument {k}"
        if t is None:
            raise ValueError(
                f"{where}: cannot determine its PSS type, which %{conv} needs "
                f"to print it")
        ok = {"n": ("bool", "enum"), "s": ("string",)}.get(
            conv, ("int", "bool", "enum"))
        if t.kind not in ok:
            raise ValueError(
                f"{where}: %{conv} cannot format a {t.kind} (LRM 21.1.1 c)")
        if t.kind == "bool":
            return '("b",)'
        if t.kind == "string":
            return '("s",)'
        if t.kind == "enum":
            return f'("e", {t.items!r})'
        return f'("i", {t.width}, {t.signed})'

    def _sub_op_receiver(self, func):
        """`e.run(...)`, `ch[i].run(...)`, `a.b.run(...)`: the rendered
        sub-component object an operation is called on and the operation, or
        ``None`` if the receiver is not a sub-component of this one.

        Each step must be a sub-component of the one before, and the method an
        operation of the last. A sub-component is a real object here, so the
        call is a method call on it -- awaited like any other operation.
        """
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if not chain:
            return None
        dtype, text = self.comp, "self"
        for name, idx in chain:
            sub = next((s for s in sub_components(dtype) if s.name == name),
                       None)
            if sub is None:
                return None
            text += f".{mangle(name)}"
            if idx is not None:
                text += f"[{self.expr(idx)}]"
            dtype = sub.dtype
        op = next((f for f in getattr(dtype, "functions", None) or []
                   if f.name == func.attr), None)
        if op is None or func_kind(op, self.ctor_names) is not FuncKind.EXPORT_OP:
            raise ValueError(
                f"'{'.'.join(n for n, _ in chain)}.{func.attr}()' is not an "
                f"operation of '{getattr(dtype, 'name', '?')}'")
        return text, op

    def _model_call(self, call) -> str:
        """An operation of this component or of a sub-component, or a declared
        import."""
        func = call.func
        sub = self._sub_op_receiver(func)
        if sub is not None:
            recv, op = sub
            args = self.call_args(call.args, op)
            return self._awaited(
                f"{recv}.{mangle(func.attr)}(" + ", ".join(args) + ")", call)
        name = None
        if _dt_name(func) == "ExprRefUnresolved":
            name = func.name
        elif (_dt_name(func) == "ExprAttribute"
                and _dt_name(func.value) == "TypeExprRefSelf"):
            name = func.attr
        callee = (self.imports.get(name) if name in self.imports else
                  next((f for f in getattr(self.comp, "functions", None) or ()
                        if f.name == name), None))
        args = self.call_args(call.args, callee)
        if name is not None and name in self.model_ops:
            # Another operation of this component is a PSS target function and
            # is generated `async def` in this form, so calling it is an await.
            return self._awaited(
                f"self.{mangle(name)}(" + ", ".join(args) + ")", call)
        if name is not None and name in self.imports:
            # An `import target/solve function` is a PLATFORM function, not a
            # method of this component: it is called on the import-API object,
            # which is the one thing the environment supplies. The generated
            # Protocol declares it from the same declaration, so the two cannot
            # disagree about its signature.
            #
            # An `import SOLVE function` is NOT awaited, in either form. The
            # distinction is about when a function runs relative to solving, and
            # a solve function cannot consume time -- so colouring it would put
            # a suspension point where the model says there is none, and would
            # demand an `async def` the platform has no reason to write.
            rendered = f"self.{IMPORTS_ATTR}.{mangle(name)}(" \
                       + ", ".join(args) + ")"
            fn = self.imports[name]
            if getattr(fn, "is_solve", False):
                return rendered
            return self._awaited(rendered, call)
        raise ValueError(
            f"call to '{name or _dt_name(func)}' has no lowering in the Python "
            f"target. It is not a register access, not a PSS built-in this "
            f"target claims ({', '.join(sorted(PY_BUILTINS))}), not an "
            f"operation of '{getattr(self.comp, 'name', '?')}', and not a "
            f"declared `import target/solve function`.")

    # -- statements ----------------------------------------------------------

    def stmt_ann_assign(self, s, ind: int) -> List[str]:
        """A declaration. Python has none, so this is an initialisation.

        The value matters even where the PSS gives none: `dma_ch_csr_s csr;`
        followed by three field assignments and a write means "all other fields
        zero", and a name that did not exist yet would be an UnboundLocalError
        at the first field assignment.
        """
        pad = self.pad(ind)
        name = self.expr(s.target)
        value = getattr(s, "value", None)
        target = self.types.of_datatype(s.annotation)
        if value is not None and self._struct_dtype(target) is not None:
            return [f"{pad}{name} = {self._struct_value(value, target)}"]
        with self._no_hoist_as_is(value, target):
            init = (self.convert_to(value, target) if value is not None
                    else self._zero(s.annotation))
        if getattr(s.target, "name", None) in self.chan_out_locals:
            # A CELL, deliberately, and stated in the output because `bit tok`
            # becoming `[0]` is otherwise an unexplained discrepancy with the
            # PSS source. See `Chan1.try_get`.
            cell = name[:-3] if name.endswith("[0]") else name
            return [f"{pad}{cell} = [{init}]"
                    f"   # PSS local, held in a cell: channel try_get output"]
        return [f"{pad}{name} = {init}"]

    def _zero(self, dtype) -> str:
        """The default value of a declared local."""
        return default_value(dtype, self.types._tm)

    def _no_hoist_as_is(self, value, target):
        """`_no_hoist_for(value)`, when *value* is rendered as it stands.

        `x = await f()` is already the shape `_awaited` produces, so a call
        that IS the right-hand side needs no temporary -- unless converting it
        to the target wraps it, when it is an operand after all and is hoisted
        like one.
        """
        if value is None or not self.converts_as_is(value, target):
            return _nullctx()
        return self._no_hoist_for(value)

    # -- struct values (LRM 8.3, 20.3.2) --------------------------------------
    #
    # A PSS struct is a value and a Python object is a reference, so the one
    # thing never emitted is a second name for a struct someone else owns:
    # `d = desc` followed by `d.next = 0` would write the CALLER's descriptor.
    # A value is FRESH when nothing else can hold it -- a call's result, since
    # a `return` copies anything it does not own. Anything else is copied.

    def _struct_dtype(self, t):
        """The struct datatype of PSS type *t*, if it is a struct VALUE (an
        `addr_handle_t` is an address here, not a struct)."""
        if t is None or t.kind != "struct" or t.dtype is None:
            return None
        if (getattr(t.dtype, "name", "") or "").split("::")[-1] \
                == "addr_handle_t":
            return None
        return t.dtype

    def _owned(self, e) -> bool:
        """Is *e* a local this body owns? Not a parameter -- an aggregate one
        is the caller's instance (20.3.2) -- and not a `foreach` iterator,
        which is an alias to the element (20.7.8 c)."""
        return (_dt_name(e) == "ExprRefLocal" and e.name not in self.arg_names
                and e.name not in self.loop_vars)

    def _same_struct(self, e, target) -> bool:
        t = self._struct_dtype(self.types.type_of(e))
        return t is not None and t is self._struct_dtype(target)

    def _struct_value(self, e, target, owned_ok: bool = False) -> str:
        """*e* as a value of struct *target* that the receiver may keep: as
        it stands if it is fresh (or, with *owned_ok*, a local of this body's
        that is going out of scope), otherwise a copy."""
        keep = _dt_name(e) == "ExprCall" or (owned_ok and self._owned(e))
        if keep and self._same_struct(e, target):
            with self._no_hoist_for(e):
                return self.expr(e)
        cls = value_class_name(self._struct_dtype(target))
        return f"{cls}._pss_copy({self.expr(e)})"

    def stmt_assign(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        tgt = s.targets[0]
        target = self.types.type_of(tgt)
        if self._struct_dtype(target) is not None:
            # Into the target, in place: that is what an assignment to an
            # aggregate parameter means, and for anything else it is the same
            # value. A fresh value into a local of ours is simply bound.
            if self._owned(tgt) and _dt_name(s.value) == "ExprCall" \
                    and self._same_struct(s.value, target):
                with self._no_hoist_for(s.value):
                    return [f"{pad}{self.expr(tgt)} = {self.expr(s.value)}"]
            value = self.expr(s.value)
            return [f"{pad}{self.expr(tgt)}._pss_assign({value})"]
        with self._no_hoist_as_is(s.value, target):
            value = self.convert_to(s.value, target)
        return [f"{pad}{self.expr(tgt)} = {value}"]

    def stmt_aug_assign(self, s, ind: int) -> List[str]:
        """`x op= e` is `x = x op e` (8.3), converted back to x's type.

        Rendered `x op= ...` whenever that conversion is a no-op, which is the
        common case and reads as the source does; otherwise spelled out, which
        evaluates the target twice -- so a target with a call in it (an index
        from a register read) is refused rather than read twice.
        """
        op = _BINOP.get(s.op.name)
        if op is None:
            raise ValueError(f"unsupported augmented-assign op {s.op.name}")
        if op in ("and", "or"):
            raise ValueError(
                f"'{s.op.name}' has no augmented-assignment form in Python")
        pad = self.pad(ind)
        bop = {"Pow": "Exp"}.get(s.op.name, s.op.name)
        target = self.types.type_of(s.target)
        whole = ir.ExprBin(lhs=s.target, op=getattr(ir.BinOp, bop),
                           rhs=s.value)
        if target is None or target.kind != "int" \
                or not is_integral(self.types.type_of(whole)):
            return [f"{pad}{self.expr(s.target)} {op}= {self.expr(s.value)}"]
        tgt = self.expr(s.target)
        value = self.convert_to(whole, target)
        pyop = "**" if bop == "Exp" else _INT_PYOP.get(bop, op)
        # The unconverted form is exactly `<target> <op> <operand>`: a wrap
        # or a helper call would enclose the whole of it.
        head = f"{tgt} {pyop} "
        if value.startswith(head):
            return [f"{pad}{tgt} {pyop}= {_unbracket(value[len(head):])}"]
        if any(_dt_name(n) == "ExprCall" for n in _nodes(s.target)):
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': `{tgt} {op}= ...` "
                f"needs its result converted back to the target's type, "
                f"which evaluates the target twice, and the target contains "
                f"a call. Read the index into a local first.")
        return [f"{pad}{tgt} = {value}"]

    def stmt_expr(self, s, ind: int) -> List[str]:
        # The statement IS the call. `await self.f(x)` on its own line is what a
        # hoist would produce anyway, minus a temporary nothing reads.
        with self._no_hoist_for(s.expr):
            return [f"{self.pad(ind)}{self.expr(s.expr)}"]

    def stmt_return(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        if s.value is not None:
            # `return` converts to the function's return type (8.7.2).
            rt = getattr(self.fn, "returns", None)
            target = self.types.of_datatype(rt) if rt is not None else None
            if self._struct_dtype(target) is not None:
                # A local of ours ends with the call, so it can be handed out;
                # a parameter, a field or an element must not be.
                return [f"{pad}return "
                        f"{self._struct_value(s.value, target, owned_ok=True)}"]
            with self._no_hoist_as_is(s.value, target):
                return [f"{pad}return {self.convert_to(s.value, target)}"]
        return [f"{pad}return"]

    def stmt_if(self, s, ind: int) -> List[str]:
        # A hoisted read in the TEST belongs in front of the `if`, which is
        # where `render_stmt` puts it: the test is evaluated once.
        pad = self.pad(ind)
        lines = [f"{pad}if {self.expr(s.test)}:"]
        lines += self.block(s.body, ind + 1)
        if getattr(s, "orelse", None):
            lines.append(f"{pad}else:")
            lines += self.block(s.orelse, ind + 1)
        return lines

    def stmt_while(self, s, ind: int) -> List[str]:
        """`while (c) { ... }`.

        A test that crosses the seam turns this into a bottom-tested loop, and
        the reason is not style. The test is re-evaluated EVERY ITERATION, and a
        hoisted read placed in front of the `while` would be evaluated once --
        so a completion poll would read the status register a single time and
        then spin on that value forever. The transform below keeps the read
        where the model put it:

            while True:
                _v0 = await self.regs_STATUS_read()
                if not (_v0 != DONE):
                    break
                <body>

        Same loop, same number of reads, and the `while (c)` form is still
        emitted whenever the test hoists nothing -- which is every sync
        generation and most async ones.
        """
        pad = self.pad(ind)
        test = self.expr(s.test)
        hoisted = self._take_pending()
        if not hoisted:
            return [f"{pad}while {test}:"] + self.block(s.body, ind + 1)
        inner = self.pad(ind + 1)
        lines = [f"{pad}while True:"]
        lines += [f"{self.indent}{h}" for h in hoisted]
        lines.append(f"{inner}if not ({test}):")
        lines.append(f"{inner}{self.indent}break")
        lines += self.block(s.body, ind + 1)
        return lines

    def stmt_repeat_while(self, s, ind: int) -> List[str]:
        """`repeat { ... } while (c)` -- a do-while, which Python does not have.

        `while True:` with the test at the BOTTOM, which is the same loop: the
        body runs at least once, and that is the whole reason a model writes a
        completion poll this way rather than as a `while`.
        """
        pad = self.pad(ind)
        inner = self.pad(ind + 1)
        lines = [f"{pad}while True:"]
        lines += self.block(s.body, ind + 1)
        cond = self.expr(s.condition)
        # The condition's own hoisted reads belong INSIDE the loop, immediately
        # before the test they feed -- the test runs once per iteration, and
        # `render_stmt` would otherwise put them in front of the whole loop,
        # where they would be read once and then spun on.
        lines += [f"{self.indent}{h}" for h in self._take_pending()]
        lines.append(f"{inner}if not ({cond}):")
        lines.append(f"{inner}{self.indent}break")
        return lines

    def stmt_for(self, s, ind: int) -> List[str]:
        """`repeat ([i :] n) { ... }` (LRM 20.7.6) -> `for i in range(n):`.

        `range(n)` evaluates the count once, which is what the LRM's "iterated
        the number of times specified" means, and runs no iteration for zero.
        A count that crosses the seam hoists in front of the loop, where
        `render_stmt` puts it -- the one evaluation it is owed.

        The index is scoped to the loop in PSS and to the function here. The
        front end gives an index that shadows a local or a parameter a name of
        its own (`ast2ir._bind_local`), so the loop cannot overwrite either.

        What it cannot rename away is a WRITE to the index: `range` supplies
        the next value regardless, where PSS reads the variable. A body that
        assigns its index is refused rather than lowered to a loop that runs a
        different number of times.
        """
        pad = self.pad(ind)
        count = self.expr(s.iter)
        name = getattr(getattr(s, "target", None), "name", None)
        if name is None:
            idx = f"_v{self._tmp_n}"
            self._tmp_n += 1
            return ([f"{pad}for {idx} in range({count}):"]
                    + self.block(s.body, ind + 1))
        if _assigns_local(s.body, name):
            raise ValueError(
                f"cannot lower `repeat ({name} : ...)`: its body assigns the "
                f"index '{name}', and a Python `for` would not see the write.")
        with self._loop_var(name, INT):
            body = self.block(s.body, ind + 1)
        return [f"{pad}for {self.expr(s.target)} in range({count}):"] + body

    @contextmanager
    def _loop_var(self, name, t):
        """*name* is a loop variable of type *t* for the extent of the block."""
        had = name in self.loop_vars
        prev = self.loop_vars.get(name)
        self.loop_vars[name] = t
        try:
            yield
        finally:
            if had:
                self.loop_vars[name] = prev
            else:
                del self.loop_vars[name]

    def stmt_break(self, s, ind: int) -> List[str]:
        return [f"{self.pad(ind)}break"]

    def stmt_continue(self, s, ind: int) -> List[str]:
        return [f"{self.pad(ind)}continue"]

    def stmt_foreach(self, s, ind: int) -> List[str]:
        """`foreach (a[i])` -> `for i in range(N):`; `foreach (v : a)` binds
        `v = a[i]` at the top of each iteration (LRM 20.7.8).

        The bound is folded at generation time rather than taken as `len(...)`.
        A sub-component array IS a Python list here and `len()` would work, but
        the collection may also be a register array, which has no object to
        measure -- so the model's own stated size is the one answer that covers
        both.

        Two things are refused rather than lowered to a different loop, as
        `stmt_for` refuses them: a body that assigns the INDEX, which `range`
        would not see, and one that assigns the ITERATOR, which in PSS is an
        alias to the element (20.7.8 c) and here would be a copy. The iterator
        form is lowered over a data array only: an element of a component or
        register array has no value to bind.
        """
        pad = self.pad(ind)
        dtype = self._iter_dtype(s)
        n = _array_size(dtype)
        if n is None:
            raise ValueError(
                f"cannot lower `foreach` over '{self.expr(s.iter)}': its size "
                f"is not known at generation time.")
        index = getattr(s, "index_var", None)
        it = None if index is s.target else s.target
        for var, what in ((index, "index"), (it, "iterator")):
            if var is not None and _assigns_local(s.body, var.name):
                raise ValueError(
                    f"cannot lower `foreach` over '{self.expr(s.iter)}': its "
                    f"body assigns the {what} '{var.name}'"
                    + (", and a Python `for` would not see the write."
                       if what == "index" else
                       ", which aliases the element (LRM 20.7.8 c); here it "
                       "would be a copy. Assign the element by index."))
        if index is not None:
            idx = self.expr(index)
        else:
            idx = f"_v{self._tmp_n}"
            self._tmp_n += 1
        if it is None:
            with self._loop_var(index.name, INT):
                body = self.block(s.body, ind + 1)
            return [f"{pad}for {idx} in range({n}):"] + body
        field = getattr(s.iter, "attr", None)
        if field in self.subs or field in self.reg_fields \
                or field in self.chan_fields:
            raise ValueError(
                f"cannot lower `foreach ({it.name} : {field})`: '{field}' is "
                f"an array of components, and this target binds an iterator "
                f"to an element's value. Use the index form, "
                f"`foreach ({field}[i])`.")
        elem = self.types.of_datatype(getattr(dtype, "element_type", None))
        with self._loop_var(it.name, elem), \
                (self._loop_var(index.name, INT) if index is not None
                 else _nullctx()):
            body = self.block(s.body, ind + 1)
        bind = f"{self.pad(ind + 1)}{self.expr(it)} = {self.expr(s.iter)}[{idx}]"
        return [f"{pad}for {idx} in range({n}):", bind] + body

    def _iter_dtype(self, s):
        it = s.iter
        if (_dt_name(it) == "ExprAttribute"
                and _dt_name(it.value) == "TypeExprRefSelf"):
            for f in getattr(self.comp, "fields", []):
                if f.name == it.attr:
                    return f.datatype
        return None

    def stmt_match(self, s, ind: int) -> List[str]:
        """PSS `match` -> an if/elif chain over a bound subject.

        `match`/`case` exists in Python 3.10+ and is deliberately not used: it
        would put a floor under the interpreter a generated model runs on, and
        a generated driver is exactly the kind of code that ends up on whatever
        Python the lab machine has.

        The subject is bound to a temporary FIRST. A PSS subject may be a
        register read, and re-evaluating it per arm would issue one bus
        transaction per arm -- on a status register whose read clears bits, that
        is not merely wasteful.

        PSS makes an unmatched subject an error (§22.7.9). Where the model
        states no default this emits one that says so, rather than falling
        through in silence.
        """
        pad = self.pad(ind)
        var = f"_subject{self._match_depth or ''}"
        self._match_depth += 1
        try:
            lines = [f"{pad}{var} = {self.expr(s.subject)}"]
            keyword = "if"
            default = None
            for case in s.cases:
                labels = self._pattern_labels(case.pattern)
                if not labels:
                    default = case
                    continue
                test = " or ".join(f"{var} == {lb}" for lb in labels)
                lines.append(f"{pad}{keyword} {test}:")
                lines += self.block(case.body, ind + 1)
                keyword = "elif"
            lines.append(f"{pad}else:")
            if default is not None:
                lines += self.block(default.body, ind + 1)
            else:
                lines.append(
                    f"{pad}{self.indent}raise ValueError("
                    f"{py_string_literal(f'{self.fn.name}: unmatched match subject')}"
                    f' + " (%r)" % (' + var + ",))")
        finally:
            self._match_depth -= 1
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

    def stmt_yield(self, s, ind: int) -> List[str]:
        """`yield` -- the wait primitive, whose cost is the form's to say.

        LOWERED, NOT REJECTED, in both forms.

        Sync: a comment. `yield` is a hint to a scheduler and this form has
        none, so the honest cost of "let something else run" is zero and the
        surrounding loop becomes a poll -- which is what a model reaching
        `yield` asked for. `block()` supplies the `pass` when this is a body's
        only statement.

        Async: a call on the platform, because now there IS something to yield
        to and only the platform knows what that costs -- `asyncio.sleep(0)`, a
        clock edge, a watchdog kick. Still a HINT and never an interrupt wait:
        the surrounding poll decides when it is done
        (`docs/op-model-export-design.md` §4.4). The C and C++ targets spell the
        same seam `--yield import` and call the same `yield_()`.
        """
        pad = self.pad(ind)
        if self.awaits and self.await_style == "async":
            self.awaited_any = True
            return [f"{pad}await self.{IMPORTS_ATTR}.yield_()"]
        return [f"{pad}# yield: nothing to yield to on this target"]

    def stmt_super(self, s, ind: int) -> List[str]:
        """`super;` -- the base type's exec block of the same kind (20.1.4).

        What resolved it says which block that is, in `metadata["super"]`:
        the method holding a base action's body (`export_action`), or the
        methods holding a base component's blocks of this kind, in order
        (`comp_inherit`). None when the base declares none, and the statement
        does nothing. Unresolved, it is refused.
        """
        md = getattr(self.fn, "metadata", None) or {}
        pad = self.pad(ind)
        kind = exec_kind(self.fn)
        if self.native and kind in INIT_EXEC_KINDS:
            # The base's blocks of this kind, by method resolution; nothing
            # when no base declares one.
            if self.super_base is not None and self.model.init_blocks(
                    self.super_base, kind):
                return [f"{pad}super().{init_kind_method(kind)}()"]
            return [f"{pad}# super: no base declares an exec {kind}"]
        if "super" not in md:
            kind = exec_kind(self.fn)
            where = f"exec {kind}" if kind else repr(getattr(self.fn, "name", "?"))
            raise ValueError(
                f"`super;` in {where} is not lowered by this target: "
                f"only an exported action's `exec body` runs its base's")
        names = md["super"]
        if not names:
            return [f"{pad}# super: the base declares no such exec block"]
        if isinstance(names, str):
            names = [names]
        if self.awaits and self.await_style == "async":
            self.awaited_any = True
            return [f"{pad}await self.{n}()" for n in names]
        return [f"{pad}self.{n}()" for n in names]


class _CtorMixin:
    """The three forms that only ever appear in a constructor.

    Not a separate emitter: a constructor assigns attributes, loops and calls
    built-ins exactly as an operation does, so all of that is inherited. What is
    added is the part with no runtime representation -- binding a register group
    and folding a group offset both become constants here, which is why a
    generated model carries no register objects at all.
    """

    #: A constructor body is a SOLVE context. The registry refuses a target-only
    #: call there, which is how `write32(...)` in a constructor becomes a
    #: diagnostic rather than code that runs before the platform exists.
    call_context = Ctx.SOLVE

    #: And therefore nothing here is awaited, in either form. `__init__` cannot
    #: be `async def` -- Python constructs before it can await -- and it does
    #: not need to be: the registry has already refused every call that could
    #: consume time. The design's claim that constructors stay synchronous is
    #: not a rule this backend enforces separately; it is a consequence of
    #: where a constructor body runs.
    awaits = False

    def call_structural(self, call) -> Optional[str]:
        if self.callee_name(call) == "set_executor":
            return self._set_executor(call)
        return self._group_call(call)

    def _set_executor(self, call) -> str:
        """`set_executor(tap)` -> `self._pss_xtr = self.tap` (LRM 21.7.2.6).

        It records THIS component's executor. A component that records none
        inherits its parent's, which is resolved over the whole tree once every
        init block has run (`_pss_bind`) -- an `init_up` may assign one after
        the children's blocks, and a later call overrides an earlier one.
        """
        kind = exec_kind(self.fn)
        if kind not in INIT_EXEC_KINDS:
            raise ValueError(
                f"'set_executor' is called in "
                f"{'exec ' + kind if kind else repr(self.fn.name)}; a component "
                f"states its executor in its exec init_down or init_up (LRM "
                f"21.7.2.6)")
        tm = self.types._tm
        if xtr.is_executor(self.comp, tm):
            raise ValueError(
                f"'set_executor' in executor '{getattr(self.comp, 'name', '?')}'"
                f": an executor is its own executor, and assigning another to "
                f"it is an error (LRM 21.7.2.6)")
        if len(call.args) != 1:
            raise ValueError(
                f"'set_executor' takes one executor, got {len(call.args)}")
        arg = call.args[0]
        dt = self._component_at(arg)
        if dt is None or not xtr.is_executor(dt, tm):
            raise ValueError(
                f"'set_executor' needs a sub-component of this component that "
                f"is an executor (derived from executor_base_c); got "
                f"{getattr(dt, 'name', None) or _dt_name(arg)}")
        return f"self.{EXECUTOR_ATTR} = {self.expr(arg)}"

    def _component_at(self, e):
        """The component type `self.<sub>[i]...` names, or None."""
        chain = self._chain(e)
        if not chain:
            return None
        dt = self.comp
        for name, _ in chain:
            sub = next((s for s in sub_components(dt) if s.name == name), None)
            if sub is None:
                return None
            dt = sub.dtype
        return dt

    #: A group offset is folded at generation time, by the same method: both are
    #: calls on a register GROUP, which has no object in the generated Python.
    call_fold = call_structural

    def call_subcomp_ctor(self, call) -> Optional[str]:
        return self._sub_ctor_call(call)

    def _group_call(self, call) -> Optional[str]:
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.reg_groups:
            return None
        group = self.reg_groups[chain[0][0]]
        m = func.attr
        args = call.args
        if m == "set_handle":
            # The group has no object: binding it IS setting its base, and
            # every accessor into it folds its offset from there. An
            # ASSIGNMENT, like `_sub_ctor_call`'s -- `set_handle` returns void,
            # so it can only ever appear as a statement.
            return f"self.{group_base(chain[0][0])} = {self.expr(args[0])}"
        if m == "get_offset_of_instance":
            return f"0x{scalar_offset(group, _str_const(args[0], m)):x}"
        if m == "get_offset_of_instance_array":
            base, stride = array_base_stride(group, _str_const(args[0], m))
            return f"(0x{base:x} + 0x{stride:x} * {self._operand(args[1])})"
        raise ValueError(
            f"unsupported register-group method '{m}'. This target folds group "
            f"offsets at generation time; a method it does not know would "
            f"become a call on a group object that does not exist.")

    def _sub_ctor_call(self, call) -> Optional[str]:
        """`ch[i].initialize(...)` -> constructing the sub-component object.

        A sub-component is a real object here, unlike in C where it is embedded
        storage that `_init` writes into. So the constructor CALL is the
        construction, and the list element is assigned rather than addressed.
        """
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.subs:
            return None
        sub = self.subs[chain[0][0]]
        if func.attr not in (self.ctor_names or ()):
            raise ValueError(
                f"'{sub.name}.{func.attr}()' is called during construction, but "
                f"only a sub-component's constructor may be. An operation is a "
                f"target function and cannot run in a solve exec.")
        fn = next((f for f in getattr(sub.dtype, "functions", None) or ()
                   if f.name == func.attr), None)
        args = [f"self.{IMPORTS_ATTR}"] + self.call_args(call.args, fn)
        ctor = f"{class_name(getattr(sub.dtype, 'name', ''))}(" + \
               ", ".join(args) + ")"
        idx = chain[0][1]
        target = (f"self.{mangle(sub.name)}[{self.expr(idx)}]"
                  if idx is not None else f"self.{mangle(sub.name)}")
        # An ASSIGNMENT, not a call, which is why this reaches the output only
        # through `stmt_expr`: PSS states sub-component construction as a call
        # statement, and it is never an operand of anything.
        return f"{target} = {ctor}"


class _CtorEmitter(_CtorMixin, _BodyEmitter):
    """The constructor emitter: the ctor-only forms over the default body."""


def _str_const(e, method: str) -> str:
    """The string-literal argument of an offset query."""
    if (_dt_name(e) != "ExprConstant"
            or not isinstance(getattr(e, "value", None), str)):
        raise ValueError(
            f"'{method}' needs a literal instance name so its offset can be "
            f"folded at generation time; got {_dt_name(e)}.")
    return e.value
