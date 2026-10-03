"""Reduce the masked / field-wise register writes to one primitive.

PSS 3.1 §21.14.1 gives four spellings of the same operation:

    regs.csr.write_field("ch_en", 1);
    regs.csr.write_fields({"use_ed", "ch_en"}, {1, 1});
    regs.csr.write_masked({.ch_en=1}, {.ch_en=1});
    regs.csr.write_val_masked(32'h1, 32'h1);

and one meaning::

    REG_VAL(new) = (REG_VAL(current) & ~mask) | (val & mask)

So they reduce to a single IR call -- ``write_val_masked`` -- with the mask and
value folded to constants wherever the source made that possible. Two things
follow, and both are the point of doing it here rather than per backend:

* **No field name survives into the IR.** ``"ch_en"`` is a reference to a
  declared field that happens to be spelled as a string because the LRM's
  signature is ``write_field(string, bit[SZ])``. Resolving it is the compiler's
  job (``reg_field_resolve``), so no backend needs a name table and no two
  backends can disagree about what a name means.
* **A backend implements one primitive, not four.** :func:`expand` goes one step
  further for a backend that has no RMW primitive at all, rewriting
  ``write_val_masked`` into the read/modify/write pair the equation describes.

The read is *not* optional. §21.14.1 defines these methods as read-modify-write,
so a masked write to a register whose read has side effects (a channel CSR that
clears its status bits on read, say) still has them. Nothing here removes a bus
read; it only stops the model from spelling one out by hand.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Any, Dict, List, Optional, Tuple

import zuspec.ir.core as ir

from .reg_field_resolve import (
    FieldSlice, RegFieldError, field_map, is_readable, reg_name, reg_size_bits,
    resolve_field,
)

#: The three forms that carry a field name or a struct-shaped mask. Each is
#: reduced away; none may reach a backend.
_REDUCED_METHODS = ("write_masked", "write_field", "write_fields")

#: The primitive everything reduces to.
_PRIMITIVE = "write_val_masked"

#: Every register method a masked/field-wise call can become or already be.
_RMW_METHODS = _REDUCED_METHODS + (_PRIMITIVE,)

_DT_REGISTER = "DataTypeRegister"
_DT_REGISTER_GROUP = "DataTypeRegisterGroup"
_DT_ARRAY = "DataTypeArray"
_DT_REF = "DataTypeRef"


def _dt_name(o) -> str:
    return type(o).__name__


# --- diagnostics ------------------------------------------------------------

def _fail(ctx, call, msg: str) -> None:
    """Record a diagnostic against ``call`` and mark it as already reported.

    The mark is what keeps :func:`check_reduced` quiet about a call that has
    just been rejected for a better reason: an unreduced call is normally a
    silent-wrong-code hazard, but not when the user has already been told why.
    """
    ctx.add_error(f"{_where(call)}{msg}")
    if not hasattr(ctx, "_reg_rmw_failed"):
        ctx._reg_rmw_failed = set()
    ctx._reg_rmw_failed.add(id(call))


def _where(call) -> str:
    """``file:line:col: `` for a call that carries a location, else ``''``."""
    loc = getattr(call, "loc", None)
    if loc is None:
        return ""
    f = loc.file or "<unknown>"
    return f"{f}:{loc.line}:{loc.pos}: "


# --- constant folding -------------------------------------------------------

_UNARY_FOLD = {
    ir.UnaryOp.Invert: lambda x: ~x,
    ir.UnaryOp.USub: lambda x: -x,
    ir.UnaryOp.UAdd: lambda x: x,
    ir.UnaryOp.Not: lambda x: int(not x),
}


def _const(e) -> Optional[int]:
    """The integer value of ``e`` if it is an integer constant, else ``None``.

    A unary operator over a constant folds, because the LRM's own mask idiom is
    ``{.f=~0}`` -- an ``ExprUnary``, not an ``ExprConstant``. Without this the
    mask still lowers to correct bits, but it reaches the backend as an
    unfolded expression, which breaks the invariant that a resolved mask is a
    literal (`test_reg_ir_equivalence.py`). ``~0`` folds to -1 here and is
    normalised by the field-width AND in `_and_lit`.
    """
    name = _dt_name(e)
    if name == "ExprUnary":
        v = _const(e.operand)
        fold = _UNARY_FOLD.get(e.op)
        return None if (v is None or fold is None) else fold(v)
    if name != "ExprConstant":
        return None
    v = e.value
    if isinstance(v, bool):
        return int(v)
    return v if isinstance(v, int) else None


def _str_const(e) -> Optional[str]:
    if _dt_name(e) != "ExprConstant":
        return None
    return e.value if isinstance(e.value, str) else None


def _lit(v: int) -> ir.Expr:
    return ir.ExprConstant(value=int(v))


def _bin(lhs, op, rhs) -> ir.Expr:
    return ir.ExprBin(lhs=lhs, op=op, rhs=rhs)


def _and_lit(e: ir.Expr, m: int) -> ir.Expr:
    c = _const(e)
    if c is not None:
        return _lit(c & m)
    return _bin(e, ir.BinOp.BitAnd, _lit(m))


def _shl_lit(e: ir.Expr, n: int) -> ir.Expr:
    if n == 0:
        return e
    c = _const(e)
    if c is not None:
        return _lit(c << n)
    return _bin(e, ir.BinOp.LShift, _lit(n))


def _or_all(parts: List[ir.Expr]) -> ir.Expr:
    """OR the parts, folding the constant ones into a single literal.

    Constants are accumulated separately rather than folded pairwise so the
    result of an all-constant list is one ``ExprConstant`` regardless of the
    order the fields were written in -- which is what makes two spellings of the
    same write compare equal.
    """
    if not parts:
        return _lit(0)
    acc = 0
    dyn: List[ir.Expr] = []
    for p in parts:
        c = _const(p)
        if c is not None:
            acc |= c
        else:
            dyn.append(p)
    if not dyn:
        return _lit(acc)
    out = dyn[0]
    for d in dyn[1:]:
        out = _bin(out, ir.BinOp.BitOr, d)
    if acc:
        out = _bin(out, ir.BinOp.BitOr, _lit(acc))
    return out


def _place(value: ir.Expr, fs: FieldSlice, size_bits: int) -> ir.Expr:
    """``value`` truncated to the field width and shifted to its position.

    A non-constant value is cast to the register's width first. §21.14.1's
    equation is defined on ``bit[SZ]``, and saying so explicitly is what keeps
    the generated code honest about width: a one-bit `enable` shifted left by 6
    is zero in SystemVerilog unless something widens it, and relying on the mask
    literal to do that implicitly is a correctness argument that holds only by
    accident of the constant's type.
    """
    if _const(value) is None:
        value = ir.ExprCast(
            target_type=ir.DataTypeInt(name="bit", bits=size_bits, signed=False),
            value=value)
    return _shl_lit(_and_lit(value, (1 << fs.width) - 1), fs.lsb)


# --- receiver resolution ----------------------------------------------------

def _chain(e) -> Optional[List[str]]:
    """Flatten a ``self``-rooted attribute path to its names, or ``None``.

    Subscripts are dropped: an array of registers has one element type, so the
    index does not change which register type is named.
    """
    cn = _dt_name(e)
    if cn == "TypeExprRefSelf":
        return []
    if cn == "ExprAttribute":
        base = _chain(e.value)
        if base is None:
            return None
        return base + [e.attr]
    if cn == "ExprSubscript":
        return _chain(e.value)
    return None


def _deref(dtype, ctx):
    if dtype is not None and _dt_name(dtype) == _DT_REF:
        return ctx.get_type(getattr(dtype, "ref_name", None)) or dtype
    return dtype


def _field_dtype(dtype, name: str, ctx):
    for f in getattr(dtype, "fields", []) or []:
        if f.name == name:
            dt = _deref(f.datatype, ctx)
            if _dt_name(dt) == _DT_ARRAY:
                dt = _deref(getattr(dt, "element_type", None), ctx)
            return dt
    return None


def _resolve_receiver(comp, recv_expr, ctx, via: Optional[str] = None):
    """The ``DataTypeRegister`` a call's receiver names, or ``None``.

    ``None`` covers both "not a register" and "not a path this pass can follow"
    -- the caller distinguishes them, because the first is an error and the
    second is a call on something that merely shares a method name.

    ``via`` names the leading path element that stands for ``comp`` rather than
    for a field of it. An action reaches its component's register model as
    ``comp.regs.csr``, and the ``comp`` handle is not a field of the action, so
    without this the walk stops at the first element and a perfectly ordinary
    exec body gets reported as unreducible.
    """
    names = _chain(recv_expr)
    if names is None:
        return None
    if via is not None and names and names[0] == via:
        names = names[1:]
    dtype = comp
    for n in names:
        dtype = _field_dtype(dtype, n, ctx)
        if dtype is None:
            return None
    return dtype if _dt_name(dtype) == _DT_REGISTER else None


# --- the reduction ----------------------------------------------------------

def _mask_and_val(reg, call, ctx) -> Optional[Tuple[ir.Expr, ir.Expr]]:
    """(mask, value) for one masked/field-wise call, or ``None`` on error."""
    method = call.func.attr
    err = lambda msg: _fail(ctx, call, msg)
    resolve = ctx.get_type

    if method == "write_field":
        if len(call.args) != 2:
            err(f"write_field takes (string, value); got {len(call.args)} argument(s)")
            return None
        name = _str_const(call.args[0])
        if name is None:
            err("write_field: the field name must be a string literal "
                "(PSS 3.1 §21.14.1) -- it is resolved at compile time")
            return None
        try:
            fs = resolve_field(reg, name, resolve)
        except RegFieldError as e:
            err(str(e))
            return None
        return _lit(fs.mask), _place(call.args[1], fs, reg_size_bits(reg))

    if method == "write_fields":
        if len(call.args) != 2:
            err(f"write_fields takes (names, values); got {len(call.args)} argument(s)")
            return None
        names_e, vals_e = call.args
        if _dt_name(names_e) != "ExprList" or _dt_name(vals_e) != "ExprList":
            err("write_fields takes two list literals; a runtime list cannot be "
                "resolved at compile time (PSS 3.1 §21.14.1)")
            return None
        names = [_str_const(n) for n in names_e.elts]
        if any(n is None for n in names):
            err("write_fields: every field name must be a string literal "
                "(PSS 3.1 §21.14.1)")
            return None
        if len(names) != len(vals_e.elts):
            err(f"write_fields: {len(names)} name(s) but {len(vals_e.elts)} value(s)")
            return None
        seen = set()
        for n in names:
            if n in seen:
                err(f"write_fields: duplicate field name '{n}' "
                    "(PSS 3.1 §21.14.1)")
                return None
            seen.add(n)
        masks, vals = [], []
        for n, v in zip(names, vals_e.elts):
            try:
                fs = resolve_field(reg, n, resolve)
            except RegFieldError as e:
                err(str(e))
                return None
            masks.append(_lit(fs.mask))
            vals.append(_place(v, fs, reg_size_bits(reg)))
        # One write_val_masked, not N: coalescing the fields into a single bus
        # read-modify-write is the whole reason the plural form exists.
        return _or_all(masks), _or_all(vals)

    if method == "write_masked":
        if len(call.args) != 2:
            err(f"write_masked takes (mask, value); got {len(call.args)} argument(s)")
            return None
        mask = _pack_struct(reg, call.args[0], ctx, call, "mask")
        val = _pack_struct(reg, call.args[1], ctx, call, "value")
        if mask is None or val is None:
            return None
        return mask, val

    return None


def _pack_struct(reg, e, ctx, call, role: str) -> Optional[ir.Expr]:
    """Pack a ``write_masked`` argument (a value of the register's type) to bits."""
    err = lambda msg: _fail(ctx, call, msg)
    resolve = ctx.get_type
    cn = _dt_name(e)

    if cn != "ExprStructLiteral":
        # A whole-struct expression (a local, a function result). It is already
        # the register's value type, so its bit pattern is the packed word; the
        # backends pack it the same way they pack a `write()` argument.
        return e

    parts: List[ir.Expr] = []
    seen = set()
    for sf in e.fields:
        if sf.name in seen:
            err(f"write_masked: duplicate field '{sf.name}' in the {role} literal")
            return None
        seen.add(sf.name)
        try:
            fs = resolve_field(reg, sf.name, resolve)
        except RegFieldError as e2:
            err(str(e2))
            return None
        placed = _place(sf.value, fs, reg_size_bits(reg))
        if role == "mask" and _const(placed) == 0:
            # A field named in the mask literal but contributing no bits writes
            # nothing -- there is no reason to name it, so this is far more
            # likely a mistake than an intent. Refuse it rather than emit a
            # write that does nothing. (This guard was introduced to contain
            # pssparser defect D5, which dropped the `~` in `~0`; D5 is fixed,
            # but a zero mask is still meaningless on its own terms.)
            err(f"write_masked: field '{sf.name}' is given a zero mask, so the "
                f"{role} selects none of its bits. Write the mask bits "
                f"explicitly, use `~0` to select all of them, or use "
                f"write_field('{sf.name}', ...)")
            return None
        parts.append(placed)
    return _or_all(parts)


def _reduce_call(comp, call, ctx, via=None) -> Optional[ir.Expr]:
    """Rewrite one masked/field-wise call to ``write_val_masked``.

    Returns the replacement expression, or ``None`` if ``call`` is not one of
    the four forms (in which case it is left alone).
    """
    func = call.func
    if _dt_name(func) != "ExprAttribute" or func.attr not in _RMW_METHODS:
        return None

    reg = _resolve_receiver(comp, func.value, ctx, via)
    if reg is None:
        # Not a register access path this pass can follow. Left alone here and
        # caught by check_reduced(), which refuses to let any of these names
        # reach a backend unresolved.
        return None

    if not is_readable(reg):
        _fail(ctx, call,
              f"cannot use {func.attr}() on register '{reg_name(reg)}': it is "
              f"WRITEONLY, and the masked forms are defined as "
              f"read-modify-write (PSS 3.1 §21.14.1)")
        return None

    if func.attr == _PRIMITIVE:
        if len(call.args) != 2:
            _fail(ctx, call, "write_val_masked takes (mask, value); "
                  f"got {len(call.args)} argument(s)")
            return None
        # A constant written out becomes the literal the other spellings
        # build, so `write_val_masked(0xe, 4)` is the IR of
        # `write_field("prio", 2)`. Both are bits of the register's
        # `bit[SZ]` parameter: the literal's own type (0xe is a `bit[32]`)
        # decides nothing once converted to it.
        mask, val = (_lit(_const(a)) if _const(a) is not None else a
                     for a in call.args)
    else:
        mv = _mask_and_val(reg, call, ctx)
        if mv is None:
            return None
        mask, val = mv

    out = ir.ExprCall(
        func=ir.ExprAttribute(value=func.value, attr=_PRIMITIVE),
        args=[mask, val])
    out.loc = getattr(call, "loc", None)
    return out


# --- expansion (--reg-rmw=expand) ------------------------------------------

class _Expander:
    """Rewrite ``write_val_masked`` into the read/modify/write it is defined as."""

    def __init__(self, comp, ctx, via=None):
        self.comp = comp
        self.ctx = ctx
        self.via = via
        self.n = 0
        #: Declarations for the temporaries, to be placed at the top of the
        #: body rather than at the expansion site. SystemVerilog allows a
        #: variable declaration only at the start of a block, so a temporary
        #: declared where the masked write happened to sit would be illegal the
        #: moment any statement preceded it. Hoisting also keeps the expansion
        #: legal inside an `if` arm or a loop body.
        self.decls: List[ir.Stmt] = []

    def stmt(self, s) -> Optional[List[ir.Stmt]]:
        if _dt_name(s) != "StmtExpr":
            return None
        call = s.expr
        if _dt_name(call) != "ExprCall":
            return None
        func = call.func
        if _dt_name(func) != "ExprAttribute" or func.attr != _PRIMITIVE:
            return None
        reg = _resolve_receiver(self.comp, func.value, self.ctx, self.via)
        if reg is None:
            return None

        sz = reg_size_bits(reg)
        full = (1 << sz) - 1
        mask, val = call.args[0], call.args[1]
        tmp = f"_rmw{self.n}"
        self.n += 1

        t = ir.ExprRefLocal(name=tmp)
        self.decls.append(ir.StmtAnnAssign(
            target=t,
            annotation=ir.DataTypeInt(name="bit", bits=sz, signed=False),
            value=None))
        read = ir.StmtAssign(
            targets=[t],
            value=ir.ExprCall(
                func=ir.ExprAttribute(value=func.value, attr="read_val"), args=[]))
        read.loc = getattr(s, "loc", None)

        mc = _const(mask)
        keep = _and_lit(t, full & ~mc) if mc is not None else _bin(
            t, ir.BinOp.BitAnd, _bin(_lit(full), ir.BinOp.BitXor, mask))
        new = _bin(keep, ir.BinOp.BitOr,
                   _and_lit(val, mc) if mc is not None
                   else _bin(val, ir.BinOp.BitAnd, mask))
        write = ir.StmtExpr(expr=ir.ExprCall(
            func=ir.ExprAttribute(value=func.value, attr="write_val"), args=[new]))
        write.loc = getattr(s, "loc", None)
        return [read, write]


# --- IR traversal -----------------------------------------------------------

def _stmt_list_fields(node) -> List[str]:
    """Names of ``node``'s attributes that hold a list of statements."""
    out = []
    if not dc.is_dataclass(node):
        return out
    for f in dc.fields(node):
        v = getattr(node, f.name, None)
        if isinstance(v, list) and v and all(isinstance(x, ir.Stmt) for x in v):
            out.append(f.name)
    return out


def _sub_nodes(node) -> List[Any]:
    """Dataclass children of ``node`` that may themselves hold statement lists."""
    out = []
    if not dc.is_dataclass(node):
        return out
    for f in dc.fields(node):
        v = getattr(node, f.name, None)
        if isinstance(v, list):
            out.extend(x for x in v if dc.is_dataclass(x) and not isinstance(x, ir.Stmt))
        elif dc.is_dataclass(v) and not isinstance(v, ir.Stmt):
            out.append(v)
    return out


def _walk_exprs(node, fn, seen=None):
    """Apply ``fn(parent, attr, expr)`` to every expression under ``node``."""
    if seen is None:
        seen = set()
    if not dc.is_dataclass(node) or id(node) in seen:
        return
    seen.add(id(node))
    for f in dc.fields(node):
        v = getattr(node, f.name, None)
        if isinstance(v, ir.Expr):
            fn(node, f.name, v)
            _walk_exprs(v, fn, seen)
        elif isinstance(v, list):
            for i, x in enumerate(v):
                if isinstance(x, ir.Expr):
                    fn(v, i, x)
                    _walk_exprs(x, fn, seen)
                elif dc.is_dataclass(x):
                    _walk_exprs(x, fn, seen)
        elif dc.is_dataclass(v):
            _walk_exprs(v, fn, seen)


def _reduce_body(comp, body: List[ir.Stmt], ctx, via=None) -> None:
    """Rewrite every masked/field-wise call under ``body`` in place.

    One walk covers the whole tree: :func:`_walk_exprs` descends through nested
    statements as well as expressions, so a call inside an ``if`` arm or a
    ``foreach`` is reached without a second traversal.
    """
    def visit(parent, attr, e):
        if not isinstance(e, ir.ExprCall):
            return
        new = _reduce_call(comp, e, ctx, via)
        if new is None:
            return
        if isinstance(parent, list):
            parent[attr] = new
        else:
            setattr(parent, attr, new)

    seen = set()
    for s in body:
        _walk_exprs(s, visit, seen)


def _expand_body(body: List[ir.Stmt], expander) -> List[ir.Stmt]:
    """Replace each ``write_val_masked`` statement with its read/write pair.

    A separate pass from :func:`_reduce_body` because this one changes the
    *shape* of a statement list -- one statement becomes two -- so it has to
    rebuild each list rather than rewrite in place.
    """
    out: List[ir.Stmt] = []
    for s in body:
        for name in _stmt_list_fields(s):
            setattr(s, name, _expand_body(getattr(s, name), expander))
        for sub in _sub_nodes(s):
            for name in _stmt_list_fields(sub):
                setattr(sub, name, _expand_body(getattr(sub, name), expander))
        rep = expander.stmt(s)
        out.extend(rep if rep is not None else [s])
    return out


@dc.dataclass(frozen=True)
class _Scope:
    """One type whose bodies may hold a register access.

    ``owner`` is the type the bodies belong to; ``comp`` is the component whose
    register model those bodies reach; ``via`` is the leading path element that
    stands for ``comp``. For a component all three collapse (``comp is owner``,
    ``via is None``). For an action they do not: the exec body belongs to the
    action, the registers belong to its component, and the source says
    ``comp.regs.csr``.
    """
    owner: Any
    comp: Any
    via: Optional[str] = None


def _scopes(ctx) -> List[_Scope]:
    """Every scope to walk.

    Actions are included, and that is not incidental: an exec body reaching a
    register through ``comp`` is ordinary PSS, and leaving it unwalked would
    make :func:`check_reduced` reject a legal model.

    Actions reach the IR as ``DataTypeClass``, so ``ctx.parent_comp_names`` --
    which maps a qualified action name to its component -- is what identifies
    them.
    """
    out: List[_Scope] = []
    parents = getattr(ctx, "parent_comp_names", {}) or {}
    for name, dt in ctx.type_map.items():
        if _dt_name(dt) in ("DataTypeComponent", _DT_REGISTER_GROUP):
            out.append(_Scope(owner=dt, comp=dt))
        elif name in parents:
            comp = ctx.get_type(parents[name])
            if comp is not None:
                out.append(_Scope(owner=dt, comp=comp, via="comp"))
    return out


def _bodies_of(dtype) -> List[Tuple[Any, str]]:
    """(owner, attribute) pairs naming every statement list ``dtype`` owns."""
    out = []
    for fn in getattr(dtype, "functions", []) or []:
        out.append((fn, "body"))
    for eb in getattr(dtype, "exec_blocks", []) or []:
        for name in _stmt_list_fields(eb):
            out.append((eb, name))
    return out


def reduce(ctx, *, expand: bool = False) -> None:
    """Reduce every masked / field-wise register write in ``ctx``.

    Runs at the end of translation so *every* consumer -- SV, C, C++, the SW
    backend, an IR dump -- sees the same reduced form. Errors are recorded on
    ``ctx`` in the usual way, so the driver reports them alongside the other
    translation errors and refuses to emit.
    """
    def do(sc, owner, attr):
        _reduce_body(sc.comp, getattr(owner, attr), ctx, sc.via)
        if expand:
            _expand_one(owner, attr, _Expander(sc.comp, ctx, sc.via))

    _for_each_body(ctx, do)
    check_reduced(ctx)


def expand_all(ctx) -> None:
    """Rewrite every ``write_val_masked`` as the read/modify/write pair.

    ``--reg-rmw=expand``. For a backend with no RMW primitive: the construct
    still reaches it, spelled in the ``read_val``/``write_val`` it already
    implements. Behaviour is identical either way -- the LRM defines the masked
    forms *as* this sequence -- which is what makes the two modes comparable on
    the same bus trace.
    """
    _for_each_body(ctx, lambda sc, owner, attr: _expand_one(
        owner, attr, _Expander(sc.comp, ctx, sc.via)))


def _expand_one(owner, attr, expander) -> None:
    """Expand one body, with the temporaries' declarations hoisted to its top."""
    body = _expand_body(getattr(owner, attr), expander)
    setattr(owner, attr, expander.decls + body)


def _for_each_body(ctx, action) -> None:
    done = set()
    for sc in _scopes(ctx):
        for owner, attr in _bodies_of(sc.owner):
            key = (id(owner), attr)
            if key in done:
                continue
            done.add(key)
            action(sc, owner, attr)


def check_reduced(ctx) -> None:
    """Refuse to hand a backend a field name.

    The reduction only follows ``self``-rooted paths into a component's own
    register model, which is every shape the LRM's own examples use. This walk
    is what makes that restriction safe: anything it could not reduce is
    reported here rather than reaching a backend, where an unrecognised register
    method becomes a generic call that compiles and is wrong.
    """
    bad: List[str] = []

    def visit(parent, attr, e):
        if not isinstance(e, ir.ExprCall):
            return
        f = e.func
        if isinstance(f, ir.ExprAttribute) and f.attr in _REDUCED_METHODS:
            if id(e) in getattr(ctx, "_reg_rmw_failed", ()):
                return          # already reported, for a better reason
            bad.append(f"{_where(e)}{f.attr}() could not be resolved to a "
                       f"register field access; the receiver is not a register "
                       f"reachable from the enclosing component")

    for dt in list(ctx.type_map.values()):
        _walk_exprs(dt, visit)
    for msg in dict.fromkeys(bad):
        ctx.add_error(msg)
