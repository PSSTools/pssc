"""One walk of an operation body; each language renders what it finds.

The three progseq emitters (`c/`, `cpp/`, `sv/lower_progseq.py`) each carry a
`_BodyEmitter` with the same shape: a `stmts`/`stmt`/`_stmt_lines` cascade over
the same thirteen statement kinds, an `expr` cascade over the same expression
kinds, the same comment attachment, and -- in C and C++ -- a byte-for-byte copy
of the same write-only-local analysis. What actually differs between them is
the RENDERING: `{` versus `begin`, four spaces versus two, `/* */` versus `//`.

This module is the part that does not differ. It provides:

* **Dispatch.** `stmt`/`expr` map a node's class name to a hook named after it
  (`StmtAnnAssign` -> `stmt_ann_assign`, `ExprRefBottomUp` ->
  `expr_ref_bottom_up`). A subclass renders by defining hooks; the walk, the
  recursion and the "no lowering for this" error are here.
* **Comment attachment.** Every nesting level goes through `stmt`, so one site
  carries a body's prose into the generated file -- and a statement that lowers
  to no lines takes its comment with it.
* **The two scans.** `scan_write_only` and `scan_output_locals` read the PSS
  tree and answer questions about it. Neither has a target language in it, and
  the first exists twice today.

The scans are free functions rather than methods because that is what makes
them checkable independently of any emitter: `tests/progseq/test_body_walker.py`
runs them against what the C emitter computes for itself, on the real WB DMA
model, before either backend is asked to move (plan P7.T1).

Design: docs/design/generator-style-extensions-design.md I6; plan Phase 7.
"""
from __future__ import annotations

import dataclasses as dc
import re
from typing import Any, Callable, Dict, List, Optional, Set

from ..diag import Refusal
from .comments import LINE, append_trailing, comment_lines
from .progseq_model import _dt_name

__all__ = ["BodyWalker", "CallDispatch", "hook_name", "match_values",
           "scan_write_only", "scan_output_locals"]

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def hook_name(class_name: str) -> str:
    """The rendering hook a node of type *class_name* dispatches to.

    `StmtAnnAssign` -> `stmt_ann_assign`. The node's own type name IS the hook
    name, so a reader holding an IR class can find its rendering without a
    table to consult, and a table cannot fall out of step with the IR.
    """
    return _CAMEL_BOUNDARY.sub("_", class_name).lower()


# -- the scans ---------------------------------------------------------------

def scan_write_only(body) -> Set[str]:
    """Locals *body* assigns and never reads.

    These are legal PSS and mean something: `ok = inflight.try_get(tok)` in
    `wait_completion` DISCARDS the result on purpose -- the operation is
    draining a token it knows it holds, and the answer is not interesting.
    SystemVerilog accepts that silently, so nothing noticed until a C compiler
    did.

    The compiler is right to complain and the model is right to discard, so
    neither gets changed: an emitter states the discard (`(void)x;`). Not
    dropping the declaration and the assignment -- the CALL has effects (it
    takes the token), and an emitter that decides which of a model's statements
    are pointless is one that will eventually be wrong about it.

    What makes this language-neutral is that it answers a question about the
    PSS, not about the output: C and C++ ask it identically today, and a target
    that does not care simply does not ask.
    """
    declared: Set[str] = set()
    read: Set[str] = set()

    def walk(n, in_read: bool):
        if isinstance(n, (list, tuple)):
            for x in n:
                walk(x, in_read)
            return
        if not dc.is_dataclass(n):
            return
        cn = _dt_name(n)
        if cn == "ExprRefLocal" and in_read:
            read.add(n.name)
            return
        if cn == "StmtAnnAssign":
            if _dt_name(n.target) == "ExprRefLocal":
                declared.add(n.target.name)
            walk(getattr(n, "value", None), True)
            return
        if cn == "StmtAssign":
            # A subscripted or member target READS the base object.
            for t in n.targets:
                walk(t, _dt_name(t) != "ExprRefLocal")
            walk(n.value, True)
            return
        if cn == "StmtAugAssign":
            walk(n.target, True)     # `x += 1` reads x
            walk(n.value, True)
            return
        for f in dc.fields(n):
            walk(getattr(n, f.name, None), True)

    walk(body, True)
    return declared - read


def foreach_parts(s):
    """``(index, iterator)`` of a `StmtForeach`, either ``None``.

    One reading of the three forms for every rendering (20.7.8): ast2ir
    writes `foreach (a[i])` with the index as both `target` and `index_var`,
    `foreach (v : a[i])` with the ELEMENT as `target` and `i` as
    `index_var`, and `foreach (v : a)` with no `index_var`. C, C++ and SV
    each took `target` for the index, so in the two value forms `v` was the
    index and `i` undeclared (W5).
    """
    index = getattr(s, "index_var", None)
    target = getattr(s, "target", None)
    if index is not None and target is not None and (
            index is target or getattr(index, "name", None)
            == getattr(target, "name", None)):
        return index, None
    return index, target


def assigns_local(body, name: str) -> bool:
    """Whether *body* assigns the local *name* (a `foreach` iterator, which
    aliases its element -- 20.7.8 c -- so a rendering binding a copy refuses
    a write to it)."""
    for n in _walk(body):
        cn = _dt_name(n)
        if cn in ("StmtAssign", "StmtAugAssign"):
            tgts = n.targets if cn == "StmtAssign" else [n.target]
            if any(_dt_name(t) == "ExprRefLocal" and t.name == name
                   for t in tgts):
                return True
    return False


def _walk(node):
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _walk(n)
        return
    if not dc.is_dataclass(node) or isinstance(node, type):
        return
    yield node
    for f in dc.fields(node):
        yield from _walk(getattr(node, f.name, None))


def in_as_compare(e, types, where: str = "?"):
    """`v in [a, b..c]` / `v in arr` (8.5.9) as the comparisons it means:
    `v == a || (v >= b && v <= c)`, or one `==` per element of a fixed-size
    array. Every target renders comparisons already, at PSS widths, so the
    one reading here serves them all (E3).

    The value is read once per comparison, so a value containing a call is
    refused rather than called repeatedly. A range here always has both
    bounds -- an open one is a domain's form, which the parser refuses in
    an expression.
    """
    import zuspec.ir.core as ir
    v, cont = e.value, e.container
    if any(_dt_name(n) == "ExprCall" for n in _walk(v)):
        raise Refusal(
            f"in '{where}': the value of an `in` is compared once per "
            f"element or range, and it contains a call; read it into a "
            f"local first", v, e)
    terms = []
    if _dt_name(cont) == "ExprRangeList":
        for r in cont.ranges:
            if r.upper is None:
                terms.append(ir.ExprBin(lhs=v, op=ir.BinOp.Eq, rhs=r.lower))
            else:
                terms.append(ir.ExprBin(
                    lhs=ir.ExprBin(lhs=v, op=ir.BinOp.GtE, rhs=r.lower),
                    op=ir.BinOp.And,
                    rhs=ir.ExprBin(lhs=v, op=ir.BinOp.LtE, rhs=r.upper)))
    elif _dt_name(cont) == "ExprList" and not any(
            _dt_name(x) in ("ExprList", "ExprStructLiteral")
            for x in cont.elts):
        # `v in {1, 2}`: a collection literal (8.5.9) holds its elements;
        # `{}` holds none (E22).
        terms = [ir.ExprBin(lhs=v, op=ir.BinOp.Eq, rhs=x) for x in cont.elts]
    elif _dt_name(cont) == "ExprSubscript" \
            and _dt_name(cont.slice) == "ExprSlice":
        # `v in a[lo..hi]` (8.5.9.1): an element whose index the slice
        # holds; a slice whose bounds cross holds none (E16).
        base, sl = cont.value, cont.slice
        t = types.type_of(base)
        size = getattr(types.resolve(getattr(t, "dtype", None)), "size", None)
        if t is None or t.kind != "array" or not isinstance(size, int):
            raise Refusal(
                f"in '{where}': `in` over a slice of a "
                f"{getattr(t, 'kind', 'value')} is not lowered; a slice of a "
                f"fixed-size array is", cont, e)
        if any(_dt_name(n) == "ExprCall"
               for x in (base, sl.lower, sl.upper) if x is not None
               for n in _walk(x)):
            raise Refusal(
                f"in '{where}': the array and bounds of an `in` over a slice "
                f"are read once per element, and they contain a call", cont, e)
        for i in range(size):
            idx = ir.ExprConstant(value=i)
            term = ir.ExprBin(lhs=v, op=ir.BinOp.Eq,
                              rhs=ir.ExprSubscript(value=base, slice=idx))
            if sl.upper is not None:
                term = ir.ExprBin(lhs=ir.ExprBin(lhs=idx, op=ir.BinOp.LtE,
                                                 rhs=sl.upper),
                                  op=ir.BinOp.And, rhs=term)
            if sl.lower is not None:
                term = ir.ExprBin(lhs=ir.ExprBin(lhs=sl.lower,
                                                 op=ir.BinOp.LtE, rhs=idx),
                                  op=ir.BinOp.And, rhs=term)
            terms.append(term)
    else:
        t = types.type_of(cont)
        size = getattr(getattr(t, "dtype", None), "size", None)
        if t is None or t.kind != "array" or not size:
            raise Refusal(
                f"in '{where}': `in` over a {getattr(t, 'kind', 'value')} "
                f"is not lowered; a value list, a range or a fixed-size "
                f"array is", cont, e)
        if any(_dt_name(n) == "ExprCall" for n in _walk(cont)):
            raise Refusal(
                f"in '{where}': the array of an `in` is read once per "
                f"element, and it contains a call", cont, e)
        for i in range(int(size)):
            terms.append(ir.ExprBin(
                lhs=v, op=ir.BinOp.Eq,
                rhs=ir.ExprSubscript(value=cont,
                                     slice=ir.ExprConstant(value=i))))
    if not terms:
        return ir.ExprConstant(value=False)
    return any_of(terms)


def any_of(terms):
    """``terms`` joined by ``||`` as a balanced tree. A chain is one level
    deeper per term, and every recursive walker after this one (typing,
    rendering, folding) then runs out of stack on a `match` arm or an `in`
    list of a few hundred values; balanced, a million terms are 20 deep."""
    import zuspec.ir.core as ir
    if len(terms) == 1:
        return terms[0]
    mid = (len(terms) + 1) // 2
    return ir.ExprBin(lhs=any_of(terms[:mid]), op=ir.BinOp.Or,
                      rhs=any_of(terms[mid:]))


_LOOPS = ("StmtWhile", "StmtFor", "StmtForeach", "StmtRepeatWhile")


def _breaks_out(stmts, kind: str = "StmtBreak") -> bool:
    """Whether *stmts* hold a `break` (or a statement of *kind*) that acts on
    the loop around them: one not inside a loop of their own."""
    for st in stmts or ():
        cn = _dt_name(st)
        if cn == kind:
            return True
        if cn in _LOOPS:
            continue
        for f in dc.fields(st):
            v = getattr(st, f.name, None)
            if isinstance(v, list) and v and _dt_name(v[0]).startswith("Stmt"):
                if _breaks_out(v, kind):
                    return True
            if cn == "StmtMatch" and f.name == "cases":
                if any(_breaks_out(c.body, kind) for c in v):
                    return True
    return False


def continues_out(stmts) -> bool:
    """Whether *stmts* hold a `continue` that acts on the loop around them."""
    return _breaks_out(stmts, "StmtContinue")


def match_needs_chain(s) -> bool:
    """Whether a `match` cannot be a C/C++ `switch`: an arm matches a range
    (E6), or holds a `break` meant for the enclosing loop -- inside a
    `switch` it would leave the switch instead (IP-1; W23)."""
    for case in s.cases:
        if any(_dt_name(v) == "ExprRange" for v in match_values(case.pattern)):
            return True
        if _breaks_out(case.body):
            return True
    return False


def match_arm_test(subject, values):
    """The condition one `match` arm stands for: `subject == v` for a value,
    `lo <= subject && subject <= hi` for a range, joined by `||`."""
    import zuspec.ir.core as ir
    terms = []
    for v in values:
        if _dt_name(v) == "ExprRange" and v.upper is not None:
            terms.append(ir.ExprBin(
                lhs=ir.ExprBin(lhs=subject, op=ir.BinOp.GtE, rhs=v.lower),
                op=ir.BinOp.And,
                rhs=ir.ExprBin(lhs=subject, op=ir.BinOp.LtE, rhs=v.upper)))
        else:
            v = v.lower if _dt_name(v) == "ExprRange" else v
            terms.append(ir.ExprBin(lhs=subject, op=ir.BinOp.Eq, rhs=v))
    return any_of(terms)


def match_values(pattern) -> List[object]:
    """The value expressions one `match` arm matches; ``[]`` for `default`.

    One reading of a pattern for every rendering. ast2ir writes `default:` as
    a wildcard (`PatternAs` with no sub-pattern), an enum item as the
    constant it names, and `[a, b]` as an alternative of values; C, C++ and
    Python each read patterns with their own copy of this switch, and three of
    the four copies had no case for the wildcard -- so any `match` with a
    `default:` was an internal error everywhere but SV.

    A pattern that BINDS a name is refused: PSS has none, and a rendering
    that dropped the binding would leave its name undeclared.
    """
    if pattern is None:
        return []
    cn = _dt_name(pattern)
    if cn == "PatternAs" and getattr(pattern, "pattern", None) is None:
        return []
    if cn == "PatternValue":
        return [pattern.value]
    if cn in ("PatternOr", "PatternSequence"):
        out: List[object] = []
        for p in pattern.patterns:
            out += match_values(p)
        return out
    raise ValueError(f"unsupported match pattern {cn}")


def scan_output_locals(node, is_output_call: Callable[[object], bool],
                       *, what: str = "output call") -> Set[str]:
    """Every local passed as the OUTPUT argument of a call *is_output_call*
    accepts.

    A pre-pass rather than a fix-up at the declaration, because the declaration
    comes FIRST in the body and the use may be anywhere after it. Its caller in
    C is the channel `try_get` widening: the alternative -- emitting the local
    at its PSS width and casting the pointer at the call -- writes eight bytes
    into a one-byte object.

    *is_output_call* decides which calls are of interest (that is the language's
    and the target's business); the walk, and the requirement that the argument
    be something with a NAME to record, are not. *what* names the construct in
    the two errors, so the caller's diagnostic still reads in its own terms.
    """
    found: Set[str] = set()

    def walk(n):
        if isinstance(n, (list, tuple)):
            for x in n:
                walk(x)
            return
        if not dc.is_dataclass(n):
            return
        if _dt_name(n) == "ExprCall" and is_output_call(n):
            if not n.args:
                raise Refusal(f"{what} takes one output argument", n)
            arg = n.args[0]
            if _dt_name(arg) != "ExprRefLocal":
                raise Refusal(
                    f"{what} writes through a pointer, so its argument must be "
                    f"a local variable; got {_dt_name(arg)}. Assign to a local "
                    f"and copy it out.", arg, n)
            found.add(arg.name)
        for f in dc.fields(n):
            walk(getattr(n, f.name, None))

    walk(node)
    return found


# -- the walk ----------------------------------------------------------------

class BodyWalker:
    """Walks an operation body; subclasses render it.

    A subclass sets `indent` and `comment_style`, then defines one hook per
    node kind it supports:

    ```python
    class Renderer(BodyWalker):
        indent = "  "
        def stmt_break(self, s, ind):   return [self.pad(ind) + "break;"]
        def expr_constant(self, e):     return str(e.value)
    ```

    A node with no hook raises, naming the hook that is missing. That is the
    behaviour the hand-written cascades already had (`unsupported stmt X`) --
    what is new is that the message says what to write.
    """

    #: One indent level in the generated language.
    indent = "    "

    #: How `comments.py` should render an attached comment.
    comment_style = LINE

    # -- entry points --------------------------------------------------------

    def emit(self, body, ind: int = 1) -> List[str]:
        """Render a whole body. The name a caller reaches for; `stmts` is what
        the recursion uses, and they are the same walk."""
        return self.stmts(body, ind)

    def stmts(self, body, ind: int) -> List[str]:
        token = self.enter_block(body)
        out: List[str] = []
        for s in body:
            out += self.stmt(s, ind)
        return self.leave_block(token, out)

    def enter_block(self, body) -> Any:
        """Called before a block's statements are emitted, with the block.
        Whatever it returns is handed to `leave_block`. A language whose
        blocks constrain where something may be written (SV declares locals
        only at the top of one) arranges it here; the default does nothing."""
        return None

    def leave_block(self, token, lines: List[str]) -> List[str]:
        """The block's emitted lines, as they are to appear."""
        return lines

    def stmt(self, s, ind: int) -> List[str]:
        """Emit *s*, wrapped in whatever the PSS source wrote around it.

        Every nesting level comes through here -- a nested body is emitted by
        `stmts`, which calls back into this -- so one hook covers the whole
        tree. A statement that lowers to no lines gets no comment either; prose
        with nothing to describe is worse than none.
        """
        try:
            lines = self.render_stmt(s, ind)
        except Refusal as e:
            raise e.at_least(s)
        if not lines:
            return lines

        pad = self.pad(ind)
        return comment_lines(getattr(s, "comment", None), pad,
                             self.comment_style) + \
            append_trailing(lines, getattr(s, "comment_trailing", None),
                            self.comment_style)

    def render_stmt(self, s, ind: int) -> List[str]:
        """*s* rendered, with no comment attachment. The dispatch point.

        Not named `stmt_lines`: a hook is named after its node kind, and
        anything spelled `stmt_*` here would read as one.
        """
        return self.dispatch(s, "stmt", s, ind)

    def expr(self, e) -> str:
        """*e* rendered as one expression string."""
        return self.dispatch(e, "expr", e)

    def pad(self, ind: int) -> str:
        return self.indent * ind

    # -- dispatch ------------------------------------------------------------

    def dispatch(self, node, kind: str, *args):
        hook = self.hook_for(node)
        if hook is None:
            cn = _dt_name(node)
            raise ValueError(
                f"unsupported {kind} {cn}: {type(self).__name__} defines no "
                f"{hook_name(cn)}(). Every node kind this target lowers needs "
                f"a hook named after it")
        return hook(*args)

    def hook_for(self, node):
        """The bound hook for *node*, or ``None``."""
        return getattr(self, hook_name(_dt_name(node)), None)

    @classmethod
    def hooks(cls) -> Dict[str, str]:
        """`{node class name: hook name}` for every hook this walker defines.

        Introspection over the MRO, so a subclass reports its own kinds plus
        everything it inherits. Used by the tests that ask whether a ported
        emitter still covers the kinds its cascade did.
        """
        names = set()
        for klass in cls.__mro__:
            names.update(vars(klass))
        return {_camel(n): n for n in sorted(names)
                if not n.startswith("_") and _camel(n) is not None
                and callable(getattr(cls, n, None))}


class CallDispatch:
    """`expr_call` by `Disposition`, from the legality registry (P7.T4).

    A call is the one expression whose rendering depends on what it IS rather
    than on its shape, and every backend answered that with a hand-written
    chain: try the register path, then the channel path, then the built-ins,
    then the model. Meanwhile `call_legality.py` held a table saying exactly
    that -- and `validate_calls.py` consulted it on every compile to decide
    whether the call could be lowered at all.

    So the table and the chain said the same thing in two places, and the chain
    was the one that decided. This mixin makes the table decide: the call is
    classified once, and its `Disposition` names the hook. A Tier-2 entry added
    with a disposition the backend has no hook for is then a loud error at the
    first call rather than a silent fall-through to "no lowering for this".

    A backend mixes this in, sets `legality_target` and `call_context`, and
    defines `call_<disposition>` for the ones it renders.
    """

    #: The registry's name for this backend. The gate and the dispatch must ask
    #: the same table the same question.
    legality_target = None

    #: Which context this emitter's bodies are lowered into. `Ctx.SOLVE` for a
    #: constructor emitter -- the registry refuses a target-only call there,
    #: and refusing it at the call is better than rendering it.
    call_context = None

    def call_names(self) -> Dict[str, Any]:
        """`{model_ops, imports, subcomps}` -- the names the MODEL supplies,
        which are in no tier. See `validate_calls.model_names`. A backend that
        lowers package-scope functions adds `pkg_funcs` (`{name: contexts}`),
        consulted only for a call the front end resolved to one."""
        return {}

    def callee_name(self, call) -> Optional[str]:
        """The name a call is classified by (`validate_calls.callee_name`)."""
        from .validate_calls import callee_name

        return callee_name(getattr(call, "func", None))

    def expr_call(self, call) -> str:
        from .call_legality import Outcome, classify
        from .validate_calls import ops_for_call

        name = self.callee_name(call)
        if name is None:
            raise ValueError(
                f"cannot classify a call whose callee is "
                f"{_dt_name(getattr(call, 'func', None))}: it names nothing to "
                f"look up in the call registry")

        func = getattr(call, "func", None)
        names = dict(self.call_names())
        pkg = names.pop("pkg_funcs", None)
        raw = getattr(func, "name", None)
        if (pkg and raw in pkg
                and _dt_name(func) == "ExprRefUnresolved"):
            # The front end resolved this call to a package-scope function
            # (see `pkg_functions.py`); the model's own names do not apply.
            # Looked up by the name AS WRITTEN: `addr_reg_pkg::write32` is
            # classified as `write32`, and must not find a user package's
            # `write32` on the way.
            names = {"pkg_funcs": {raw: pkg[raw]}}
            name = raw
        elif "model_ops" in names:
            names["model_ops"] = ops_for_call(
                getattr(self, "comp", None), func, names["model_ops"],
                getattr(self, "ctor_names", None))
        res = classify(name, context=self.call_context,
                       target=self.legality_target, **names)
        if res.outcome is not Outcome.SUPPORTED:
            # `validate_calls` runs first and reports every one of these with a
            # location. Reaching here means the gate did not run, or the two
            # asked different questions -- a compiler bug either way, so it
            # says so rather than producing output.
            raise ValueError(
                f"call to '{name}' reached the emitter unclassified "
                f"({res.outcome.value}): {res.message}. The legality gate "
                f"should have reported this before any file was opened")

        disp = res.entry.disposition
        hook = getattr(self, f"call_{disp.value}", None)
        if hook is None:
            raise ValueError(
                f"'{name}' is classified {disp.name}, and "
                f"{type(self).__name__} defines no call_{disp.value}(). Every "
                f"disposition this target's registry entries can produce needs "
                f"a rendering; see targets/call_legality.py")

        out = hook(call)
        if out is None:
            raise ValueError(
                f"'{name}' is classified {disp.name}, but call_{disp.value}() "
                f"did not recognise it -- the NAME says one thing and the "
                f"receiver another. Classification is by name (see "
                f"call_legality.classify), so this is a call whose receiver is "
                f"not what its name implies")
        return out

    @classmethod
    def call_hooks(cls) -> Dict[str, str]:
        """`{disposition value: hook name}` for every one this class renders."""
        from .call_legality import Disposition

        return {d.value: f"call_{d.value}" for d in Disposition
                if getattr(cls, f"call_{d.value}", None) is not None}


def _camel(hook: str) -> Optional[str]:
    """The node class name a hook is named after, or ``None`` if it is not a
    node hook (`stmts`, `expr`, `pad`, ...)."""
    if not (hook.startswith("stmt_") or hook.startswith("expr_")
            or hook.startswith("type_expr_")):
        return None
    return "".join(p.title() for p in hook.split("_"))
