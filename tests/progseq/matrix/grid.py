"""The expression context grid (00-plan.md §4.3): integer operators x operand
types x contexts, covered pairwise, each row's value from `ref8_7`.

Five axes: the operator, the left and right operand types, the context the
expression is evaluated in, and the context's target type. Every pair of
values of any two axes that a well-formed row can hold appears in at least one
row; that is far fewer rows than the full product and still puts, say, every
operator in every context and every type pair under every operator.

Each row is one function taking its operands as PARAMETERS (so no target can
fold the expression away), called from ``run()`` with literal arguments. Its
result is printed with ``message(LOW, "g<k> %x", ...)`` -- `%x` prints the
value unsigned at its PSS width; a stored result is widened to 64 bits first
(`_w`) -- or, in the register context, written to `ga_c.STS`. Operand values
are chosen so that the LRM's answer differs from the commonest wrong ones
(`_values`). The rows are grouped into one case per operator.

Generation is deterministic (a fixed seed). Changing the axes or the seed
reshuffles rows; the expectations come with them, since both are computed.
"""
from __future__ import annotations

import random
from typing import Dict, List, NamedTuple, Optional

from . import ref8_7 as r
from .ref8_7 import T

TYPES = (T(1, False), T(4, False), T(8, False), T(8, True), T(16, False),
         T(32, False), T(32, True), T(33, False), T(64, False), T(64, True))

#: name -> taxonomy tags. `neg` is unary minus, `?:` the conditional.
OPS = {
    "+": ("EX-ARITH.1",), "-": ("EX-ARITH.1",), "*": ("EX-ARITH.1",),
    "/": ("EX-ARITH.2",), "%": ("EX-ARITH.3",), "**": ("EX-ARITH.5",),
    "&": ("EX-BIT.1",), "|": ("EX-BIT.1",), "^": ("EX-BIT.1",),
    "~": ("EX-BIT.2", "EX-WIDTH.13"), "neg": ("EX-ARITH.6", "EX-WIDTH.13"),
    "<<": ("EX-SHIFT.1", "EX-WIDTH.10"), ">>": ("EX-SHIFT.2", "EX-WIDTH.10"),
    "?:": ("EX-COND.4", "EX-WIDTH.11"),
}
UNARY = ("~", "neg")

#: The assignment operators PSS has (8.1 `assign_op`).
COMPOUND = ("+", "-", "<<", ">>", "|", "&")

#: op -> what a row of the `compound` context (`v op= y`, a statement)
#: exercises besides the context's own tags.
COMPOUND_TAGS = {"+": "EX-ASSIGN.2", "-": "EX-ASSIGN.2",
                 "&": "EX-ASSIGN.3", "|": "EX-ASSIGN.3",
                 "<<": "EX-ASSIGN.4", ">>": "EX-ASSIGN.4"}

#: name -> taxonomy tags.
CONTEXTS = {
    "init": ("ST-VAR", "EX-WIDTH.5"),
    "assign": ("EX-ASSIGN.1", "EX-WIDTH.5"),
    "compound": ("EX-ASSIGN.6", "EX-WIDTH.5", "ST-ASSIGN.5"),
    "arg": ("FN-PARAM", "EX-WIDTH.5"),
    "return": ("ST-RET", "EX-WIDTH.5"),
    "cond": ("ST-IF", "EX-WIDTH.8"),
    "message": ("LIB-MSG", "EX-WIDTH.6", "LIB-MSG.9"),
    "reg": ("LIB-REG", "EX-WIDTH.5"),
    "field": ("DT-STRUCT", "EX-WIDTH.5"),
    "element": ("DT-ARR", "EX-WIDTH.5"),
    "part": ("EX-SEL", "EX-ASSIGN.9"),
}

SEED = 87
REG = T(32, False)
ZERO = r.Var("0", T(32, True), 0)       # the literal `0`: an `int`


class Row(NamedTuple):
    op: str
    a: T
    b: Optional[T]          # None for a unary operator
    ctx: str
    t: Optional[T]          # the context's target type; None if it has none


def _tag(t: T) -> str:
    return ("i" if t.signed else "b") + str(t.width)


def _feasible(rng: random.Random) -> Row:
    op = rng.choice(list(OPS))
    ctxs = [c for c in CONTEXTS if c != "compound" or op in COMPOUND]
    ctx = rng.choice(ctxs)
    a = rng.choice(TYPES)
    b = None if op in UNARY else rng.choice(TYPES)
    if ctx in ("cond", "message"):
        t = None
    elif ctx == "reg":
        t = REG
    elif ctx == "compound":
        t = a
    elif ctx == "part":
        t = rng.choice([x for x in TYPES if not x.signed])
    else:
        t = rng.choice(TYPES)
    return Row(op, a, b, ctx, t)


def _pairs(row: Row):
    v = list(row)
    return {(i, v[i], j, v[j]) for i in range(5) for j in range(i + 1, 5)}


def rows(seed: int = SEED) -> List[Row]:
    """A pairwise-covering set of rows."""
    rng = random.Random(seed)
    want = set()
    for _ in range(40000):
        want |= _pairs(_feasible(rng))
    out: List[Row] = []
    while want:
        best = max((_feasible(rng) for _ in range(400)),
                   key=lambda x: len(_pairs(x) & want))
        if not _pairs(best) & want:
            continue
        out.append(best)
        want -= _pairs(best)
    return out


# --- values ------------------------------------------------------------------

def _lo(t: T) -> int:
    return -(1 << (t.width - 1)) if t.signed else 0


def _hi(t: T) -> int:
    return (1 << (t.width - 1)) - 1 if t.signed else (1 << t.width) - 1


def _interesting(t: T) -> List[int]:
    w = t.width
    pat = int("a5" * 8, 16) & ((1 << w) - 1)
    vals = {_hi(t), _lo(t), 1, r.value(pat, t), r.value(1 << (w - 1), t)}
    if t.signed:
        vals |= {-1, -3}
    vals.add(min(_hi(t), 3))
    return sorted(vals)


def _amounts(a: T, b: T, small: bool) -> List[int]:
    """Non-negative right operands for a shift or `**` that ``b`` holds."""
    base = [0, 1, 2, 3, 5, 7] if small else \
        [0, 1, 3, a.width - 1, a.width, a.width + 3, 40]
    return sorted({n for n in base if 0 <= n <= _hi(b)})


def _candidates(row: Row, rng: random.Random):
    """Operand value pairs ``row`` may use, in a seeded order."""
    if row.b is None:
        out = [(a, None) for a in _interesting(row.a)]
    elif row.op in ("<<", ">>"):
        out = [(a, b) for a in _interesting(row.a)
               for b in _amounts(row.a, row.b, False)]
    elif row.op == "**":
        out = [(a, b) for a in (2, 3, -1, -2, _hi(row.a))
               if _lo(row.a) <= a <= _hi(row.a)
               for b in _amounts(row.a, row.b, True)]
    else:
        out = []
        for a in _interesting(row.a):
            for b in _interesting(row.b):
                if row.op in ("/", "%"):
                    j = r._join(row.a, row.b)
                    d = r.evaluate(r.Var("y", row.b, b), j)
                    # MIN / -1 overflows, and C's behaviour is undefined:
                    # a case of its own (test_m_ints), not a grid row.
                    if d == 0 or (j.signed and d == -1):
                        continue
                out.append((a, b))
    rng.shuffle(out)
    return out


def _wide(e, t: Optional[T]) -> int:
    """A host that computes everything in 64 bits and converts at the end."""
    st = r.self_type(e)
    v = r.evaluate(e, T(64, st.signed))
    return r.value(v, t or st)


def _unpropagated(e, t: Optional[T]) -> int:
    """A host that never propagates a width into an expression (8.7)."""
    v, st = r.self_value(e)
    return r.value(v, t or st)


def _values(row: Row, rng: random.Random, build):
    """Operand values for ``row``, chosen to tell the LRM's answer from the
    two commonest wrong ones (`_wide`, `_unpropagated`): a row whose values
    give the same number every way proves nothing about widths. ``build``
    gives (expression, target type or None) for a value pair."""
    best, best_score = None, -1
    for a, b in _candidates(row, rng)[:48]:
        e, t = build(a, b)
        want = r.convert(e, t) if t else r.self_value(e)[0]
        score = (want != _wide(e, t)) + (want != _unpropagated(e, t))
        if score > best_score:
            best, best_score = (a, b), score
    return best


# --- rendering ---------------------------------------------------------------

def _lit(v: int, t: T) -> str:
    """``v``'s bit pattern at ``t``, as a hex literal: an unsigned `bit[32+]`
    that the parameter's conversion (8.7.2) truncates back to ``v``."""
    return f"0x{r.bits(v, t):x}"


def _expr(op: str) -> str:
    if op == "~":
        return "~x"
    if op == "neg":
        return "-x"
    if op == "?:":
        return "c ? x : y"
    return f"x {op} y"


def _model(op: str, x, y, c):
    if op == "~":
        return r.Un("~", x)
    if op == "neg":
        return r.Un("-", x)
    if op == "?:":
        return r.Cond(c, x, y)
    return r.Bin(op, x, y)


W64 = T(64, False)


def _w(text: str) -> str:
    """A stored value, printed widened to 64 bits. `%x` prints at the
    value's own width, so printing a `bit[4]` directly would mask a target
    that stored 16 in it (W1); widening shows what was stored."""
    return f"(bit[64]){text}"


def _shown(v: int, t: T) -> str:
    """What `_w` prints for a ``t`` holding ``v`` (8.7.2: extended by its own
    sign)."""
    return f"{r.convert(r.Var('v', t, v), W64):x}"


class Rendered(NamedTuple):
    decls: List[str]        # functions of pss_top
    call: str               # the call from run()
    want: str               # its trace line


def render(k: int, row: Row, rng: random.Random, helpers: set,
           structs: set) -> Rendered:
    vc = rng.choice((True, False))
    c = r.Var("c", r.BOOL, int(vc))
    ctx_t = {"cond": None, "message": None}.get(row.ctx, row.t)

    def build(a, b):
        y = r.Var("y", row.b, b) if row.b is not None else None
        return _model(row.op, r.Var("x", row.a, a), y, c), ctx_t

    va, vb = _values(row, rng, build)
    x = r.Var("x", row.a, va)
    y = r.Var("y", row.b, vb) if row.b is not None else None
    params = [f"{row.a} x"] + ([f"{row.b} y"] if y else [])
    args = [_lit(va, row.a)] + ([_lit(vb, row.b)] if y else [])
    if row.op == "?:":
        params.insert(0, "bool c")
        args.insert(0, "true" if vc else "false")
    e, text, t = _model(row.op, x, y, c), _expr(row.op), row.t
    g = f"g{k}"
    decls = []
    if row.ctx == "init":
        body = f"{t} v = {text}; message(LOW, \"{g} %x\", {_w('v')});"
        want = _shown(r.convert(e, t), t)
    elif row.ctx == "assign":
        body = (f"{t} v = 0; v = {text}; "
                f"message(LOW, \"{g} %x\", {_w('v')});")
        want = _shown(r.convert(e, t), t)
    elif row.ctx == "compound":
        # `x op= y` is `x = x op y` at x's type (8.3).
        body = (f"{t} v = x; v {row.op}= y; "
                f"message(LOW, \"{g} %x\", {_w('v')});")
        want = _shown(r.convert(e, t), t)
    elif row.ctx == "arg":
        helpers.add(t)
        body = f"p_{_tag(t)}({k}, {text});"
        want = _shown(r.convert(e, t), t)
    elif row.ctx == "return":
        decls.append(f"  target function {t} r{k}({', '.join(params)}) "
                     f"{{ return {text}; }}")
        call = f"r{k}({', '.join(p.split()[-1] for p in params)})"
        body = f"message(LOW, \"{g} %x\", {_w(call)});"
        want = _shown(r.convert(e, t), t)
    elif row.ctx == "cond":
        body = (f"if (({text}) != 0) {{ message(LOW, \"{g} 1\"); }} "
                f"else {{ message(LOW, \"{g} 0\"); }}")
        want = str(r.self_value(r.Bin("!=", e, ZERO))[0])
    elif row.ctx == "message":
        body = f"message(LOW, \"{g} %x\", {text});"
        want = r.hex_at(*r.self_value(e))
    elif row.ctx == "reg":
        body = f"a.STS.write_val({text});"
        return Rendered(
            decls + [f"  target function void {g}({', '.join(params)}) "
                     f"{{ {body} }}"],
            f"{g}({', '.join(args)});",
            f"write 32 0x1004 0x{r.convert(e, REG):x}")
    elif row.ctx == "field":
        structs.add(t)
        body = (f"sf_{_tag(t)} s; s.f = {text}; "
                f"message(LOW, \"{g} %x\", {_w('s.f')});")
        want = _shown(r.convert(e, t), t)
    elif row.ctx == "element":
        body = (f"array<{t}, 2> v; v[1] = {text}; "
                f"message(LOW, \"{g} %x\", {_w('v[1]')});")
        want = _shown(r.convert(e, t), t)
    elif row.ctx == "part":
        lo = 4 if t.width <= 60 else 0
        body = (f"bit[64] w = 0; w[{t.width - 1 + lo}:{lo}] = {text}; "
                f"message(LOW, \"{g} %x\", w);")
        want = f"{r.convert(e, t) << lo:x}"
    else:
        raise ValueError(row.ctx)
    decls.append(f"  target function void {g}({', '.join(params)}) "
                 f"{{ {body} }}")
    return Rendered(decls, f"{g}({', '.join(args)});", f"{g} {want}")


def row_tags(row: Row) -> set:
    """What one row exercises that its operator and context alone do not
    say: a compound assignment's operator, mixed-sign arithmetic (Table 22:
    signed only if both operands are), a shift amount of another width than
    the shifted value. Read from the row, so a new seed keeps them true."""
    tags = set()
    if row.ctx == "compound":
        tags.add(COMPOUND_TAGS[row.op])
    if row.b is not None and row.op in ("+", "-", "*", "/", "%") \
            and row.a.signed != row.b.signed:
        tags.add("EX-ARITH.7")
    if row.op in ("<<", ">>") and row.b.width != row.a.width:
        tags.add("EX-SHIFT.6")
    return tags


def cases(seed: int = SEED) -> Dict[str, tuple]:
    """{case name: (pss, trace, covers)}: one per operator."""
    from ._matrix import ONE
    rng = random.Random(seed + 1)
    by_op: Dict[str, List[Row]] = {}
    for row in rows(seed):
        by_op.setdefault(row.op, []).append(row)
    out, k = {}, 0
    for op in OPS:
        helpers, structs = set(), set()
        decls, calls, want, covers = [], [], [], set(OPS[op])
        covers |= {"EX-WIDTH.2", "DT-INT"}
        for row in by_op.get(op, []):
            got = render(k, row, rng, helpers, structs)
            decls += got.decls
            calls.append(got.call)
            want.append(got.want)
            covers |= set(CONTEXTS[row.ctx]) | row_tags(row)
            k += 1
        for t in sorted(helpers):
            decls.append(f"  target function void p_{_tag(t)}(int k, {t} v) "
                         f"{{ message(LOW, \"g%d %x\", k, {_w('v')}); }}")
        pss = "".join(f"struct sf_{_tag(t)} {{ {t} f; }}\n"
                      for t in sorted(structs))
        pss += ("component pss_top {" + ONE + "\n".join(decls)
                + "\n  target function void run() {\n    "
                + "\n    ".join(calls) + "\n  }\n}")
        out[f"grid {op}"] = (pss, want, tuple(sorted(covers)))
    return out
