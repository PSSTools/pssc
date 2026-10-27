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


_MEM_ACCESS = {f"{op}{w}": (op == "read", w // 8)
               for op in ("read", "write") for w in (8, 16, 32, 64)}


def _mem_access(ctx, value):
    """Return (is_read, size_bytes, args) when *value* is an addr_reg_pkg
    readN/writeN call through an addr_handle_t, else None."""
    if not getattr(ctx, "uses_addr_model", False):
        return None
    if not isinstance(value, ir.ExprCall):
        return None
    func = value.func
    if isinstance(func, ir.ExprRefUnresolved):
        name = func.name
    elif isinstance(func, ir.ExprAttribute) and isinstance(func.value, ir.TypeExprRefSelf):
        name = func.attr
    else:
        return None
    if name not in _MEM_ACCESS:
        return None
    is_read, size = _MEM_ACCESS[name]
    return is_read, size, value.args


def lower_stmts(ctx: LoweringContext, stmts: List[ir.Stmt]) -> List[str]:
    """Lower a list of IR statements to SV statement strings.

    Returns a flat list of SV lines (no trailing semicolons on block
    constructs; semicolons on simple statements).
    """
    lines: List[str] = []
    for stmt in stmts:
        lines.extend(lower_stmt(ctx, stmt))
    return lines


def lower_stmt(ctx: LoweringContext, stmt: ir.Stmt) -> List[str]:
    """Lower a single IR statement to SV statement line(s)."""

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
        mem = _mem_access(ctx, stmt.expr)
        if mem is not None:
            # writeN(h, v) -> import_api_if.mem_write(h, v, N/8); a discarded
            # readN(h) still performs the bus read.
            is_read, size, args = mem
            h = lower_expr(ctx, args[0])
            if is_read:
                return ["begin", "  bit [63:0] __rd;",
                        f"  comp.import_if.mem_read({h}, __rd, {size});", "end"]
            v = lower_expr(ctx, args[1])
            if size < 8:
                # Mask: an unsized literal such as 3735928559 is signed in SV
                # and would sign-extend into the upper bytes.
                v = f"64'({v}) & 64'h{(1 << (size * 8)) - 1:x}"
            else:
                v = f"64'({v})"
            return [f"comp.import_if.mem_write({h}, {v}, {size});"]
        expr_str = lower_expr(ctx, stmt.expr)
        if expr_str:
            return [f"{expr_str};"]
        return []

    if isinstance(stmt, ir.StmtAssign):
        reg_read = _reg_read_call(ctx, stmt.value)
        if reg_read is not None:
            # reg_c read()/read_val() are tasks with an output argument (a
            # front-door access consumes time): `x = r.read_val();` ->
            # `r.read_val(x);`. Name-matched, as in lower_progseq.
            return [f"{reg_read}({lower_expr(ctx, t)});" for t in stmt.targets]
        mem = _mem_access(ctx, stmt.value)
        if mem is not None and mem[0]:
            # `v = readN(h);` -> mem_read into a temp (task output), then assign.
            _, size, args = mem
            h = lower_expr(ctx, args[0])
            lines = ["begin", "  bit [63:0] __rd;",
                     f"  comp.import_if.mem_read({h}, __rd, {size});"]
            lines += [f"  {lower_expr(ctx, t)} = __rd[{size * 8 - 1}:0];"
                      for t in stmt.targets]
            return lines + ["end"]
        val = lower_expr(ctx, stmt.value)
        targets = [lower_expr(ctx, t) for t in stmt.targets]
        lines = []
        for t in targets:
            lines.append(f"{t} = {val};")
        return lines

    if isinstance(stmt, ir.StmtAugAssign):
        target = lower_expr(ctx, stmt.target)
        val = lower_expr(ctx, stmt.value)
        op = _AUGOP_MAP.get(stmt.op, "+=")
        return [f"{target} {op} {val};"]

    if isinstance(stmt, ir.StmtReturn):
        if stmt.value is not None:
            val = lower_expr(ctx, stmt.value)
            return [f"return {val};"]
        return ["return;"]

    if isinstance(stmt, ir.StmtIf):
        cond = lower_expr(ctx, stmt.test)
        lines = [f"if ({cond}) begin"]
        for s in stmt.body:
            for l in lower_stmt(ctx, s):
                lines.append(f"  {l}")
        if stmt.orelse:
            lines.append("end else begin")
            for s in stmt.orelse:
                for l in lower_stmt(ctx, s):
                    lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtIfChain):
        lines = []
        for k, c in enumerate(stmt.clauses):
            head = "if" if k == 0 else "end else if"
            lines.append(f"{head} ({lower_expr(ctx, c.test)}) begin")
            for s in c.body:
                for l in lower_stmt(ctx, s):
                    lines.append(f"  {l}")
        if stmt.orelse:
            lines.append("end else begin")
            for s in stmt.orelse:
                for l in lower_stmt(ctx, s):
                    lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtWhile):
        cond = lower_expr(ctx, stmt.test)
        lines = [f"while ({cond}) begin"]
        for s in stmt.body:
            for l in lower_stmt(ctx, s):
                lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtFor):
        # StmtFor maps to a foreach or counted loop depending on context
        target = lower_expr(ctx, stmt.target)
        iter_expr = lower_expr(ctx, stmt.iter)
        lines = [f"foreach ({iter_expr}[{target}]) begin"]
        for s in stmt.body:
            for l in lower_stmt(ctx, s):
                lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtForeach):
        target = lower_expr(ctx, stmt.target)
        collection = lower_expr(ctx, stmt.iter)
        lines = [f"foreach ({collection}[{target}]) begin"]
        for s in stmt.body:
            for l in lower_stmt(ctx, s):
                lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtRepeat):
        count = lower_expr(ctx, stmt.count)
        lines = [f"repeat ({count}) begin"]
        for s in stmt.body:
            for l in lower_stmt(ctx, s):
                lines.append(f"  {l}")
        lines.append("end")
        return lines

    if isinstance(stmt, ir.StmtRepeatWhile):
        cond = lower_expr(ctx, stmt.condition)
        lines = ["do begin"]
        for s in stmt.body:
            for l in lower_stmt(ctx, s):
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
            for s in case.body:
                for l in lower_stmt(ctx, s):
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
