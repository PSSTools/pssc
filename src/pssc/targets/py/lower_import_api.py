"""What the PLATFORM must supply, as a `typing.Protocol` the model generates.

The Python analogue of the C++ target's `<ns>_import_if` and the SV
projection's `import_api_if`: one object carrying memory access plus whatever
the model declared as an `import target/solve function`, so a generated body
reaches everything it needs through one reference.

A `Protocol`, not a base class. Nothing is required to inherit it -- a cocotb
driver, a socket client or a register model from another framework satisfies it
by having the methods, which is the property that made this target worth having
in the first place. What the Protocol adds is that the requirement is now
WRITTEN DOWN by the generator rather than described in prose in `pssc_rt.py`,
so a type checker can see it and `pssc_rt.check_import_api()` can report on it.

WHERE THE MEMBER LIST COMES FROM, and why it is two sources rather than one:

* **What the bodies call** is read back off the generated component text
  (`imports_used`). Deriving the demand from the OUTPUT rather than re-deriving
  it from the model is the point: a Protocol computed from a second walk can
  disagree with the accessors, and the failure mode of that disagreement is a
  platform that implements the declared set and still gets an AttributeError.
* **Every DECLARED import**, called or not. That is the C++ emitter's rule
  (`cpp/lower_progseq.emit_import_api`) and the reason is unchanged: the set is
  the platform's CONTRACT, and a platform implementing one function too many
  pays nothing while one discovering a requirement later pays a rebuild.

So the two directions are deliberately asymmetric. Anything a body calls must
appear; a declared import may appear without being called. Nothing else may.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Set

from ..progseq_model import func_kind  # noqa: F401  (see _is_solve_import)
from .lower_api_types import py_annotation
from .lower_reg_model import _docstring  # noqa: F401  (kept in one place)
from .naming import IMPORTS_ATTR, class_name, mangle

__all__ = ["lower_import_api", "import_api_name", "imports_used"]

#: Every `self._imports.<name>(` in a chunk of generated text. The seam has
#: exactly one spelling (`naming.IMPORTS_ATTR`), which is what makes reading the
#: demand back off the output a fact rather than a heuristic.
_SEAM_CALL = re.compile(r"self\." + re.escape(IMPORTS_ATTR) + r"\.(\w+)\s*\(")

#: The fixed part of the seam: PSS built-ins this target renders against the
#: import API. Name -> (params, return annotation, is_target). `is_target`
#: decides whether the member is coloured in the async form -- a memory access
#: can consume time, and `message` cannot.
_BUILTIN_MEMBERS = {
    **{f"read{w}": (["addr: int"], "int", True) for w in (8, 16, 32, 64)},
    **{f"write{w}": (["addr: int", "data: int"], "None", True)
       for w in (8, 16, 32, 64)},
    "message": (["text: str"], "None", False),
}

#: `--py-await async` only. The wait primitive a PSS `yield` lowers to: what
#: "let something else run" COSTS on this platform. See
#: `docs/op-model-export-design.md` §4.4 -- it is a scheduler hint, never an
#: interrupt wait, and a platform implementing it as one changes what the model
#: means.
_YIELD_DOC = (
    "What a PSS `yield` costs here: hand control back to the scheduler.\n"
    "\n"
    "A HINT, not a wait for anything in particular -- the surrounding\n"
    "poll decides when it is done. `await asyncio.sleep(0)` is a\n"
    "correct implementation; so is one clock edge.")


def import_api_name(model) -> str:
    """The Protocol's class name: `WbDmaImportApi`.

    ONE Protocol per model, named for the ROOT, shared by every component class.
    Not one per component: a sub-component is constructed by its parent with the
    parent's object, so per-component Protocols would be a set of types all
    satisfied by the same value and unable to diverge -- a distinction with
    nothing behind it.
    """
    return class_name(getattr(model.root, "name", "") or "op_model") + "ImportApi"


def imports_used(text: str) -> Set[str]:
    """Every import-API member the generated *text* actually calls."""
    return set(_SEAM_CALL.findall(text))


def _is_solve_import(fn) -> bool:
    """Is this declared import an `import SOLVE function`?

    Read off the IR flag rather than through `progseq_model.func_kind`, and
    that is not an oversight: `func_kind` tests `is_solve` before `is_import`,
    so a declared import solve function comes back `EXPORT_SOLVE` and the
    `IMPORT_SOLVE` arm is unreachable for the entries of `model.imports`. The
    flag is the fact; going through the classifier here would silently colour
    a solve import in the async form, which is exactly the bug R2 is about.
    """
    return bool(getattr(fn, "is_solve", False))


def _signature(fn) -> List[str]:
    """A declared import's parameters, annotated where the mapping has an answer."""
    out: List[str] = []
    for a in fn.args.args:
        ann = py_annotation(a.annotation)
        out.append(f"{mangle(a.arg)}: {ann}" if ann else mangle(a.arg))
    return out


def _returns(fn) -> str:
    if getattr(fn, "returns", None) is None:
        return "None"
    return py_annotation(fn.returns) or ""


def _member(name: str, params: List[str], ret: str, *, coloured: bool,
            doc: Optional[str] = None, pad: str = "    ") -> List[str]:
    """One Protocol member, as `def`/`async def` with an ellipsis body."""
    kw = "async def" if coloured else "def"
    sig = ", ".join(["self"] + params)
    arrow = f" -> {ret}" if ret else ""
    head = f"{pad}{kw} {name}({sig}){arrow}:"
    if not doc:
        return [head + " ..."]
    return [head] + _docstring(doc, pad + "    ") + [f"{pad}    ..."]


def lower_import_api(model, *, await_style: str = "sync",
                     used: Optional[Set[str]] = None) -> List[str]:
    """The `<Root>ImportApi` Protocol for *model*.

    *used* is the set of members the generated bodies call, normally obtained by
    running `imports_used` over the components section. It is a parameter rather
    than something computed here so that the backend passes the text it is
    actually going to emit, not a second rendering of it.
    """
    coloured_ok = await_style == "async"
    used = set(used or ())
    name = import_api_name(model)

    members: List[List[str]] = []
    unknown: List[str] = []

    # 1. The fixed seam, in a stable order, and only what is reached.
    for m, (params, ret, is_target) in _BUILTIN_MEMBERS.items():
        if m in used:
            members.append(_member(m, params, ret,
                                   coloured=coloured_ok and is_target))

    # 2. Every DECLARED import, called or not.
    declared: Dict[str, object] = dict(getattr(model, "imports", None) or {})
    for m in sorted(declared):
        fn = declared[m]
        members.append(_member(
            mangle(m), _signature(fn), _returns(fn),
            coloured=coloured_ok and not _is_solve_import(fn),
            doc=getattr(fn, "doc", None)))

    # Anything called that is neither a built-in nor a declared import would be
    # a demand the Protocol cannot describe -- and the platform would meet the
    # Protocol and still fail at the call. Raise instead.
    unknown = sorted(used - set(_BUILTIN_MEMBERS) - set(declared)
                     - {"yield_", "event"})
    if unknown:
        raise ValueError(
            f"the generated bodies call {', '.join(repr(u) for u in unknown)} "
            f"on the import API, but nothing declares them: they are neither a "
            f"PSS built-in this target renders against the seam nor a declared "
            f"`import target/solve function`. The Protocol would not mention "
            f"them and a conforming platform would still fail at the call.")

    # 3/4. The async-only members. `yield_` is a target function and coloured;
    # `event` is a FACTORY -- making an event does not consume time, and only
    # awaiting it does -- so it stays synchronous in both forms.
    if coloured_ok:
        members.append(_member("yield_", [], "None", coloured=True,
                               doc=_YIELD_DOC))
        if any(model.channels(n.dtype) for n in model.components):
            members.append(_member(
                "event", [], "", coloured=False,
                doc="A fresh scheduler event: `await ev.wait()` / `ev.set()` /"
                    " `ev.clear()`.\n\nThe TYPE is deliberately unnamed."
                    " `asyncio.Event` and\n`cocotb.triggers.Event` both satisfy"
                    " it, and naming either would\ndecide which scheduler a"
                    " generated model can run under."))

    lines = ["# ----- Supplied by the PLATFORM. -----",
             "@runtime_checkable",
             f"class {name}(Protocol):"]
    lines += _docstring(_api_docstring(model, name, bool(members)), "    ")
    if not members:
        # A Protocol with no members is satisfied by anything, which is the
        # true statement about a model that crosses the seam nowhere. `pass`
        # rather than nothing, because a class body cannot be empty.
        lines.append("    pass")
        return lines
    for m in members:
        lines.append("")
        lines += m
    return lines


def _api_docstring(model, name: str, any_members: bool) -> str:
    root = (getattr(model.root, "name", "") or "").split("::")[-1]
    if not any_members:
        return (f"What `{root}` requires of the platform: nothing.\n\n"
                f"This model reaches no register, no memory primitive and no "
                f"declared\nimport, so any object satisfies it. The parameter "
                f"still exists because\nevery component class takes one.")
    return (
        f"What `{root}` requires of the platform.\n"
        f"\n"
        f"STRUCTURAL. Nothing has to inherit this: pass any object with these\n"
        f"methods and the generated model will drive it. It is generated from\n"
        f"the model, so it lists exactly what these bodies call plus every\n"
        f"import the model declares.\n"
        f"\n"
        f"`pssc_rt.MemoryBus` is one implementation, enough to bring the model\n"
        f"up. `pssc_rt.check_import_api(obj, {name})`\n"
        f"reports what an object is missing, which `isinstance` cannot do.")
