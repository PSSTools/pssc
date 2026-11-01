"""Lower Zuspec IR statements to SV statement strings.

Translates IR statement nodes (StmtAssign, StmtIf, StmtWhile, etc.)
to SV statement strings. Used for exec block bodies (body, pre_solve,
post_solve, init_down).
"""
from __future__ import annotations

from typing import List, TYPE_CHECKING

import zuspec.ir.core as ir

from .lower_exprs import lower_expr, _AUGOP_MAP

if TYPE_CHECKING:
    from .context import LoweringContext


# ------------------------------------------------------------------
# PSS exec-block built-in function mapping
# ------------------------------------------------------------------
# PSS built-in names (ExprAttribute.attr) -> None means handle below
_PSS_BUILTINS = {"message", "print", "error", "fatal", "yield"}


def _lower_pss_call(ctx, func_attr: str, args) -> str:
    from .lower_exprs import lower_expr as _le
    if func_attr == "message":
        # message(verbosity, format_string, arg0, ...) -> $display(fmt, ...)
        if len(args) >= 2:
            fmt_and_args = ", ".join(_le(ctx, a) for a in args[1:])
            return f"$display({fmt_and_args})"
        return "$display()"
    if func_attr == "print":
        # print(format_str, args...) -> $write(...). PSS 'print' is printf-style
        # and does NOT append a newline, so $write (not $display) is faithful.
        if args:
            fmt_and_args = ", ".join(_le(ctx, a) for a in args)
            return f"$write({fmt_and_args})"
        return "$write()"
    if func_attr == "error":
        # error(format_str, args...) -> $error(...)
        fmt_and_args = ", ".join(_le(ctx, a) for a in args)
        return f"$error({fmt_and_args})"
    if func_attr == "fatal":
        # fatal(exit_code, format_str, args...) -> $fatal(...)
        fmt_and_args = ", ".join(_le(ctx, a) for a in args)
        return f"$fatal({fmt_and_args})"
    if func_attr == "yield":
        return "// yield (no-op in SV class execution)"
    return None



#: Marks a local declaration a statement produced: `lower_stmts` moves it to
#: the top of the enclosing block, as SV requires declarations before
#: statements in a `begin`/`end` block or a task/function body.
_DECL = "\x00decl:"


def _reg_read_call(ctx, value):
    """Return the lowered `<path>.read`/`<path>.read_val` callee when *value*
    is an argument-less register read in a model using pssc_reg_pkg, else None."""
    if not getattr(ctx, "uses_reg_model", False):
        return None
    if not isinstance(value, ir.ExprCall) or value.args:
        return None
    func = value.func
    if not isinstance(func, ir.ExprAttribute) or func.attr not in ("read", "read_val"):
        return None
    return f"{lower_expr(ctx, func.value)}.{func.attr}"


def lower_stmts(ctx: LoweringContext, stmts: List[ir.Stmt]) -> List[str]:
    """Lower a list of IR statements -- one block -- to SV statement strings.

    Returns a flat list of SV lines (no trailing semicolons on block
    constructs; semicolons on simple statements). Local variable
    declarations in the block come first.
    """
    decls: List[str] = []
    lines: List[str] = []
    for stmt in stmts:
        for l in lower_stmt(ctx, stmt):
            if l.startswith(_DECL):
                decls.append(l[len(_DECL):])
            else:
                lines.append(l)
    return decls + lines


def _take_single_call(ctx, val: str):
    """If *val* is exactly one hoisted task call's temp, consume it and return
    ``(call_text, tmp)``; else None."""
    pend = ctx.pending_task_calls
    if len(pend) == 1 and pend[0][0] == val:
        tmp, _, call = pend.pop()
        return call, tmp
    return None


def _call_into(call: str, tmp: str, target: str) -> str:
    """The hoisted call writing its result straight to *target*."""
    return call.replace(f"({tmp}", f"({target}", 1) + ";"


def lower_stmt(ctx: LoweringContext, stmt: ir.Stmt) -> List[str]:
    """Lower a single IR statement to SV statement line(s).

    A value-returning call of a function realised as an SV task is hoisted
    out of the statement's expressions into a temp (see `lower_exprs`); the
    statement is then wrapped in a block that declares the temps and makes
    the calls first.
    """
    saved = ctx.pending_task_calls
    ctx.pending_task_calls = []
    try:
        lines = _lower_stmt(ctx, stmt)
        pending = ctx.pending_task_calls
    finally:
        ctx.pending_task_calls = saved
    if not pending:
        return lines
    if isinstance(stmt, (ir.StmtWhile, ir.StmtRepeatWhile)):
        ctx.warn("a time-consuming function call in a loop condition is "
                 "evaluated once, before the loop", type(stmt).__name__)
    decls = [l for l in lines if l.startswith(_DECL)]
    body = [l for l in lines if not l.startswith(_DECL)]
    out = decls + ["begin"]
    out += [f"  {ty} {tmp};" for tmp, ty, _ in pending]
    out += [f"  {call};" for _, _, call in pending]
    out += [f"  {l}" for l in body]
    out.append("end")
    return out


def _lower_assign(ctx, targets: List[str], val: str) -> List[str]:
    if len(targets) == 1:
        one = _take_single_call(ctx, val)
        if one is not None:
            return [_call_into(one[0], one[1], targets[0])]
    return [f"{t} = {val};" for t in targets]


def _lower_stmt(ctx: LoweringContext, stmt: ir.Stmt) -> List[str]:

    if isinstance(stmt, ir.StmtExpr):
        # Check for PSS built-in function calls before generic lowering
        if isinstance(stmt.expr, ir.ExprCall):
            func = stmt.expr.func
            func_attr = None
            if isinstance(func, ir.ExprAttribute):
                func_attr = func.attr
            elif isinstance(func, ir.ExprRefUnresolved):
                func_attr = func.name
            if func_attr in (_PSS_BUILTINS if func_attr else ()):
                sv_stmt = _lower_pss_call(ctx, func_attr, stmt.expr.args)
                if sv_stmt is not None:
                    return [f"{sv_stmt};"]
                return []
        expr_str = lower_expr(ctx, stmt.expr)
        if any(p[0] == expr_str for p in ctx.pending_task_calls):
            return []  # the hoisted call is the statement; result discarded
        if expr_str:
            return [f"{expr_str};"]
        return []

    if isinstance(stmt, ir.StmtAnnAssign):
        # A local variable: `T name [= value];`
        name = lower_expr(ctx, stmt.target)
        sv_type = (ctx.pss_type_to_sv_type_str(stmt.annotation)
                   if stmt.annotation is not None else "int")
        lines = [f"{_DECL}{sv_type} {name};"]
        if stmt.value is not None:
            val = lower_expr(ctx, stmt.value)
            lines.extend(_lower_assign(ctx, [name], val))
        return lines

    if isinstance(stmt, ir.StmtAssign):
        reg_read = _reg_read_call(ctx, stmt.value)
        if reg_read is not None:
            # reg_c read()/read_val() are tasks with an output argument (a
            # front-door access consumes time): `x = r.read_val();` ->
            # `r.read_val(x);`. Name-matched, as in lower_progseq.
            return [f"{reg_read}({lower_expr(ctx, t)});" for t in stmt.targets]
        val = lower_expr(ctx, stmt.value)
        targets = [lower_expr(ctx, t) for t in stmt.targets]
        return _lower_assign(ctx, targets, val)

    if isinstance(stmt, ir.StmtAugAssign):
        target = lower_expr(ctx, stmt.target)
        val = lower_expr(ctx, stmt.value)
        op = _AUGOP_MAP.get(stmt.op, "+=")
        return [f"{target} {op} {val};"]

    if isinstance(stmt, ir.StmtReturn):
        if stmt.value is not None:
            val = lower_expr(ctx, stmt.value)
            if ctx.ret_out_name:
                # A value-returning function realised as a task: the value
                # goes back through its leading output argument.
                return _lower_assign(ctx, [ctx.ret_out_name], val) + ["return;"]
            return [f"return {val};"]
        return ["return;"]

    if isinstance(stmt, ir.StmtIf):
        cond = lower_expr(ctx, stmt.test)
        lines = [f"if ({cond}) begin"]
        for l in lower_stmts(ctx, stmt.body):
            lines.append(f"  {l}")
        if stmt.orelse:
            lines.append("end else begin")
            for l in lower_stmts(ctx, stmt.orelse):
                lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtIfChain):
        lines = []
        for k, c in enumerate(stmt.clauses):
            head = "if" if k == 0 else "end else if"
            lines.append(f"{head} ({lower_expr(ctx, c.test)}) begin")
            for l in lower_stmts(ctx, c.body):
                lines.append(f"  {l}")
        if stmt.orelse:
            lines.append("end else begin")
            for l in lower_stmts(ctx, stmt.orelse):
                lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtWhile):
        cond = lower_expr(ctx, stmt.test)
        lines = [f"while ({cond}) begin"]
        for l in lower_stmts(ctx, stmt.body):
            lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtFor):
        # StmtFor maps to a foreach or counted loop depending on context
        target = lower_expr(ctx, stmt.target)
        iter_expr = lower_expr(ctx, stmt.iter)
        lines = [f"foreach ({iter_expr}[{target}]) begin"]
        for l in lower_stmts(ctx, stmt.body):
            lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtForeach):
        target = lower_expr(ctx, stmt.target)
        collection = lower_expr(ctx, stmt.iter)
        lines = [f"foreach ({collection}[{target}]) begin"]
        for l in lower_stmts(ctx, stmt.body):
            lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtRepeat):
        count = lower_expr(ctx, stmt.count)
        lines = [f"repeat ({count}) begin"]
        for l in lower_stmts(ctx, stmt.body):
            lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtRepeatWhile):
        cond = lower_expr(ctx, stmt.condition)
        lines = ["do begin"]
        for l in lower_stmts(ctx, stmt.body):
            lines.append(f"  {l}")
        lines.append(f"end while ({cond});")
        return lines

    if isinstance(stmt, ir.StmtBreak):
        return ["break;"]

    if isinstance(stmt, ir.StmtContinue):
        return ["continue;"]

    if isinstance(stmt, ir.StmtPass):
        return []  # No SV equivalent; omit

    if isinstance(stmt, ir.StmtRaise):
        if stmt.exc is not None:
            msg = lower_expr(ctx, stmt.exc)
            return [f'$fatal(1, {msg});']
        return ['$fatal(1, "error");']

    if isinstance(stmt, ir.StmtAssert):
        cond = lower_expr(ctx, stmt.test)
        if stmt.msg is not None:
            msg = lower_expr(ctx, stmt.msg)
            return [f"assert ({cond}) else $error({msg});"]
        return [f"assert ({cond});"]

    if isinstance(stmt, ir.StmtMatch):
        subj = lower_expr(ctx, stmt.subject)
        lines = [f"case ({subj})"]
        for case in stmt.cases:
            pat = _lower_pattern(ctx, case.pattern)
            lines.append(f"  {pat}: begin")
            for l in lower_stmts(ctx, case.body):
                lines.append(f"    {l}")
            lines.append("  end")
        lines.append("endcase")
        return lines

    # Fallback
    if isinstance(stmt, ir.StmtCover):
        # PSS cover -> SV: immediate cover assertion, plus a trace for observability.
        # In task/function context, ``cover`` requires a procedural context;
        # we emit it here and let the simulator decide on support.
        cond = lower_expr(ctx, stmt.test)
        if stmt.msg is not None:
            msg = lower_expr(ctx, stmt.msg)
            return [
                f"`ZSP_TRACE(\"cover: {msg}\");",
                f"cover ({cond});",
            ]
        return [f"cover ({cond});"]

    ctx.warn(f"unsupported exec-body statement '{type(stmt).__name__}' — skipped",
             type(stmt).__name__)
    return [f"// unsupported stmt: {type(stmt).__name__}"]


def _lower_pattern(ctx: LoweringContext, pattern: ir.Pattern) -> str:
    """Lower a match pattern to an SV case label."""
    if isinstance(pattern, ir.PatternValue):
        return lower_expr(ctx, pattern.value)
    if isinstance(pattern, ir.PatternAs):
        if pattern.pattern is None:
            return "default"
        return _lower_pattern(ctx, pattern.pattern)
    if isinstance(pattern, ir.PatternOr):
        parts = [_lower_pattern(ctx, p) for p in pattern.patterns]
        return ", ".join(parts)
    return "default"
