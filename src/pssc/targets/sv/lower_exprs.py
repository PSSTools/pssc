"""Lower Zuspec IR expressions to SV expression strings.

This module provides a general-purpose expression lowering used by both
constraint lowering (Phase 3) and activity/statement lowering (Phase 4).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import zuspec.ir.core as ir

if TYPE_CHECKING:
    from .context import LoweringContext


# Mapping from IR BinOp to SV operator string
_BINOP_MAP = {
    ir.BinOp.Add: "+",
    ir.BinOp.Sub: "-",
    ir.BinOp.Mult: "*",
    ir.BinOp.Div: "/",
    ir.BinOp.Mod: "%",
    ir.BinOp.BitAnd: "&",
    ir.BinOp.BitOr: "|",
    ir.BinOp.BitXor: "^",
    ir.BinOp.LShift: "<<",
    ir.BinOp.RShift: ">>",
    ir.BinOp.Eq: "==",
    ir.BinOp.NotEq: "!=",
    ir.BinOp.Lt: "<",
    ir.BinOp.LtE: "<=",
    ir.BinOp.Gt: ">",
    ir.BinOp.GtE: ">=",
    ir.BinOp.And: "&&",
    ir.BinOp.Or: "||",
}

_UNARYOP_MAP = {
    ir.UnaryOp.Not: "!",
    ir.UnaryOp.Invert: "~",
    ir.UnaryOp.USub: "-",
    ir.UnaryOp.UAdd: "+",
}

_CMPOP_MAP = {
    ir.CmpOp.Eq: "==",
    ir.CmpOp.NotEq: "!=",
    ir.CmpOp.Lt: "<",
    ir.CmpOp.LtE: "<=",
    ir.CmpOp.Gt: ">",
    ir.CmpOp.GtE: ">=",
}

_AUGOP_MAP = {
    ir.AugOp.Add: "+=",
    ir.AugOp.Sub: "-=",
    ir.AugOp.Mult: "*=",
    ir.AugOp.Div: "/=",
    ir.AugOp.Mod: "%=",
    ir.AugOp.LShift: "<<=",
    ir.AugOp.RShift: ">>=",
    ir.AugOp.BitAnd: "&=",
    ir.AugOp.BitOr: "|=",
    ir.AugOp.BitXor: "^=",
}


def lower_expr(ctx: LoweringContext, expr: ir.Expr) -> str:
    """Lower one IR expression to an SV expression string.

    This is the general-purpose expression lowering that handles all
    expression forms. Used by constraint lowering, statement lowering,
    and activity lowering.
    """
    if isinstance(expr, ir.ExprConstant):
        return _format_constant(expr)

    if isinstance(expr, ir.ExprBin):
        lhs = lower_expr(ctx, expr.lhs)
        rhs = lower_expr(ctx, expr.rhs)
        op = _BINOP_MAP.get(expr.op, "??")
        return f"({lhs} {op} {rhs})"

    if isinstance(expr, ir.ExprUnary):
        operand = lower_expr(ctx, expr.operand)
        op = _UNARYOP_MAP.get(expr.op, "!")
        return f"{op}({operand})"

    if isinstance(expr, ir.ExprCompare):
        left = lower_expr(ctx, expr.left)
        parts = [left]
        for op, comp in zip(expr.ops, expr.comparators):
            parts.append(_CMPOP_MAP.get(op, "=="))
            parts.append(lower_expr(ctx, comp))
        return f"({' '.join(parts)})"

    if isinstance(expr, ir.ExprBool):
        op = "&&" if expr.op == ir.BoolOp.And else "||"
        parts = [lower_expr(ctx, v) for v in expr.values]
        return f" {op} ".join(parts)

    if isinstance(expr, ir.ExprIn):
        val = lower_expr(ctx, expr.value)
        container = lower_expr(ctx, expr.container)
        return f"{val} inside {{{container}}}"

    if isinstance(expr, ir.ExprRangeList):
        parts = []
        for r in expr.ranges:
            parts.append(_lower_range(ctx, r))
        return ", ".join(parts)

    if isinstance(expr, ir.ExprRange):
        return _lower_range(ctx, expr)

    if isinstance(expr, ir.ExprAttribute):
        val = lower_expr(ctx, expr.value)
        if val in ("", "self", "this"):
            return expr.attr
        return f"{val}.{expr.attr}"

    if isinstance(expr, ir.ExprRefLocal):
        return expr.name

    if isinstance(expr, ir.TypeExprRefSelf):
        return "this"

    if isinstance(expr, ir.TypeExprRefTraversed):
        # Only in a traversal's `randomize() with`, where SV, like PSS
        # (13.1.4), resolves a bare name in the randomized object first.
        return ""

    if isinstance(expr, ir.ExprRefField):
        base = lower_expr(ctx, expr.base)
        if base == "" or base == "self" or base == "this":
            return f"field_{expr.index}"
        return f"{base}.field_{expr.index}"

    if isinstance(expr, ir.ExprSubscript):
        val = lower_expr(ctx, expr.value)
        if isinstance(expr.slice, ir.ExprSlice):
            lo = lower_expr(ctx, expr.slice.lower) if expr.slice.lower else "0"
            hi = lower_expr(ctx, expr.slice.upper) if expr.slice.upper else ""
            return f"{val}[{hi}:{lo}]"
        idx = lower_expr(ctx, expr.slice)
        return f"{val}[{idx}]"

    if isinstance(expr, ir.ExprSlice):
        lo = lower_expr(ctx, expr.lower) if expr.lower else "0"
        hi = lower_expr(ctx, expr.upper) if expr.upper else ""
        return f"[{hi}:{lo}]"

    if isinstance(expr, ir.ExprCall):
        # Implication: ExprCall(func=ExprRefUnresolved(name='implies'), args=[cond, body])
        if (isinstance(expr.func, ir.ExprRefUnresolved)
                and expr.func.name == 'implies'
                and len(expr.args) == 2):
            cond = lower_expr(ctx, expr.args[0])
            body = lower_expr(ctx, expr.args[1])
            return f"({cond} -> {body})"
        # Package-scope import functions (target/solve) are realised by the
        # testbench-supplied import_api_if and reached via the component's
        # import handle: doit(a) -> comp.import_if.doit(a).
        fname = None
        if isinstance(expr.func, ir.ExprRefUnresolved):
            fname = expr.func.name
        elif (isinstance(expr.func, ir.ExprAttribute)
                and isinstance(expr.func.value, ir.TypeExprRefSelf)):
            fname = expr.func.attr
        if fname and fname in ctx.import_function_names():
            args = ", ".join(lower_expr(ctx, a) for a in expr.args)
            return f"comp.import_if.{fname}({args})"
        func = lower_expr(ctx, expr.func)
        args = ", ".join(lower_expr(ctx, a) for a in expr.args)
        return f"{func}({args})"

    if isinstance(expr, ir.ExprHierarchical):
        parts = []
        for elem in expr.elements:
            part = elem.name
            if elem.subscript is not None:
                part += f"[{lower_expr(ctx, elem.subscript)}]"
            parts.append(part)
        return ".".join(parts)

    if isinstance(expr, ir.ExprNull):
        return "null"

    if isinstance(expr, ir.ExprRefUnresolved):
        return expr.name

    if isinstance(expr, ir.ExprRefParam):
        return expr.name

    if isinstance(expr, ir.ExprAwait):
        # Await maps to the inner expression in SV (no direct equivalent)
        return lower_expr(ctx, expr.value)

    if isinstance(expr, ir.ExprCast):
        val = lower_expr(ctx, expr.value)
        type_str = ctx.pss_type_to_sv_type_str(expr.target_type)
        return f"{type_str}'({val})"

    if isinstance(expr, ir.ExprStaticRef):
        return "::".join(expr.path)

    # Fallback
    return f"/* unsupported: {type(expr).__name__} */"


def _lower_range(ctx: LoweringContext, r: ir.ExprRange) -> str:
    """Format a single range expression."""
    lo = lower_expr(ctx, r.lower)
    if r.upper is not None:
        hi = lower_expr(ctx, r.upper)
        return f"[{lo}:{hi}]"
    return lo


def _format_constant(expr: ir.ExprConstant) -> str:
    """Format a constant value as an SV literal."""
    v = expr.value
    if isinstance(v, bool):
        return "1'b1" if v else "1'b0"
    if isinstance(v, int):
        if v < 0:
            return str(v)
        return str(v)
    if isinstance(v, str):
        return f'"{v}"'
    return str(v)
