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
from typing import Callable, Dict, List, Optional, Set

from .comments import LINE, append_trailing, comment_lines
from .progseq_model import _dt_name

__all__ = ["BodyWalker", "CallDispatch", "hook_name", "scan_write_only",
           "scan_output_locals"]

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
                raise ValueError(f"{what} takes one output argument")
            arg = n.args[0]
            if _dt_name(arg) != "ExprRefLocal":
                raise ValueError(
                    f"{what} writes through a pointer, so its argument must be "
                    f"a local variable; got {_dt_name(arg)}. Assign to a local "
                    f"and copy it out.")
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
        out: List[str] = []
        for s in body:
            out += self.stmt(s, ind)
        return out

    def stmt(self, s, ind: int) -> List[str]:
        """Emit *s*, wrapped in whatever the PSS source wrote around it.

        Every nesting level comes through here -- a nested body is emitted by
        `stmts`, which calls back into this -- so one hook covers the whole
        tree. A statement that lowers to no lines gets no comment either; prose
        with nothing to describe is worse than none.
        """
        lines = self.render_stmt(s, ind)
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
