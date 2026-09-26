"""Lower a component's op-model constructor (``init``) to an SV method.

A PSS operation model binds its address map in an `init` (or `ctor`) solve
function that the integrating environment calls from `exec init_down`:

    solve function void \\init (addr_handle_t base) {
        regs.set_handle(base);
        foreach (ch[i]) {
            ch[i].\\init (i, make_handle_from_handle(base,
                              WB_DMA_CH_BASE + i * WB_DMA_CH_STRIDE));
        }
    }

It becomes a method of the component class, run after construction: `new()`
has already built every sub-component and put every register group at address
0 (design D2), so here the register groups are REBUILT against their handles
and each sub-component's own constructor method is called with the arguments
the model passes it (D3). The device's own address arithmetic is what places
everything, so the map stays stated once, in the model, rather than being
re-derived by each backend.

**Anything not recognised raises.** An `init` is address binding; a statement
this cannot lower is a binding that would silently not happen, and a component
whose registers are bound to address zero is far worse than a build error.
"""
from __future__ import annotations

from typing import Callable, Dict, List, Optional

from ..progseq_model import _dt_name, SubComp


class InitLoweringError(ValueError):
    """An `init` statement that has no constructor equivalent."""


def _is_self_attr(e, attr: Optional[str] = None) -> bool:
    return (_dt_name(e) == "ExprAttribute"
            and _dt_name(e.value) == "TypeExprRefSelf"
            and (attr is None or e.attr == attr))


def _self_attr_name(e) -> Optional[str]:
    return e.attr if _is_self_attr(e) else None


def lower_init(ctor, *, members: Dict[str, str], reg_groups: List[str],
               subs: Dict[str, SubComp], bus: str,
               expr: Callable[[object], str], indent: int = 3) -> List[str]:
    """Return the SV method-body lines for ``ctor``.

    ``members`` maps a PSS field name to its generated member name, ``bus`` is
    the expression a rebuilt register group is wired to, and
    ``expr`` renders an IR expression (supplied by the caller so this module
    stays free of expression lowering).
    """
    out: List[str] = []
    for stmt in (ctor.body or []):
        out += _stmt(stmt, members, reg_groups, subs, bus, expr, indent)
    return out


def _stmt(s, members, reg_groups, subs, bus, expr, ind) -> List[str]:
    pad = "  " * ind
    cn = _dt_name(s)

    if cn == "StmtExpr" and _dt_name(s.expr) == "ExprCall":
        call = s.expr
        fn = call.func
        if _dt_name(fn) == "ExprAttribute":
            # regs.set_handle(h) -> build the register group at h
            if fn.attr == "set_handle":
                name = _self_attr_name(fn.value)
                if name in reg_groups:
                    handle = expr(call.args[0]) if call.args else bus
                    return [f"{pad}{members[name]} = new({bus}, {handle});"]
            # sub.<ctor>(args) on a scalar instance: already built by new()
            name = _self_attr_name(fn.value)
            if name in subs and not subs[name].is_array:
                args = ", ".join(expr(a) for a in call.args)
                return [f"{pad}{members[name]}.{fn.attr}({args});"]

    if cn == "StmtAssign":
        target = s.targets[0]
        name = _self_attr_name(target)
        if name in members:
            return [f"{pad}{members[name]} = {expr(s.value)};"]

    if cn == "StmtForeach":
        return _foreach(s, members, reg_groups, subs, bus, expr, ind)

    raise InitLoweringError(
        f"unsupported statement in init: {cn}. An init binds addresses; a "
        f"statement that cannot be lowered would leave part of the model "
        f"unbound, so this is an error rather than a skipped line.")


def _foreach(s, members, reg_groups, subs, bus, expr, ind) -> List[str]:
    """`foreach (ch[i]) { ch[i].init(...); }` -> an indexed loop of calls."""
    pad = "  " * ind
    iter_name = _self_attr_name(getattr(s, "iter", None))
    if iter_name not in subs or not subs[iter_name].is_array:
        raise InitLoweringError(
            f"foreach in init must iterate a sub-component array; got {iter_name!r}")

    sub = subs[iter_name]
    if sub.size is None or sub.size < 0:
        raise InitLoweringError(
            f"sub-component array '{iter_name}' has an unknown size, so the "
            f"loop cannot be bounded")

    idx = getattr(getattr(s, "target", None), "name", "i")
    body: List[str] = []
    for inner in (s.body or []):
        if (_dt_name(inner) == "StmtExpr" and _dt_name(inner.expr) == "ExprCall"
                and _dt_name(inner.expr.func) == "ExprAttribute"
                and _dt_name(inner.expr.func.value) == "ExprSubscript"
                and _self_attr_name(inner.expr.func.value.value) == iter_name):
            args = ", ".join(expr(a) for a in inner.expr.args)
            attr = inner.expr.func.attr
            body.append(f"{pad}  {members[iter_name]}[{idx}].{attr}({args});")
        else:
            body += _stmt(inner, members, reg_groups, subs, bus, expr, ind + 1)

    return ([f"{pad}for (int {idx} = 0; {idx} < {sub.size}; {idx}++) begin"]
            + body + [f"{pad}end"])
