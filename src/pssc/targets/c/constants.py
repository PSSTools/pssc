"""Named PSS constants in op-model-c: kept as macros (user ruling, 2026-11-06).

A HAL or driver is often built once and compiled for several variants of a
project, whose feature parameters come from the project's own headers. So a
reference in an operation body to a `static const` or a package `const`
(ast2ir folds it, keeping its name in `ExprConstant.ref`) is spelled by NAME,
and the header defines the name only if the build has not:

    #ifndef MAX_CH
    #define MAX_CH (4)  /* cfg_pkg::MAX_CH */
    #endif

The value is the build's to change, so pssc decides nothing from it:
a use is cast to the constant's declared type (`((int32_t)(MAX_CH))`), and
`IntSemantics` treats it as an unknown value of that type -- widened, wrapped
and sign-extended as a variable of the type would be, never folded, and
compared only where the type's range alone decides it.

What is NOT symbolic (D3a): an enum item (already a name), a literal, a
`bool`, a constant of pssc's own packages (`OWN_PACKAGES`), and every value
pssc used to make generated code: a register offset, an array size, a width,
a template argument, a bit-select bound, an index whose range check was left
out, a field's initial value. Those are folded where they are used; each
constant folded so is PINNED, and its definition is followed by a static
assertion that the build kept the value pssc folded --
overriding it is a compile error naming it, never a half-reconfigured build.

`--fold-constants` restores the folded form: no table, no section.

The table records each constant a body spells (`use`), as `SvConstants`
does; the names are fixed from what was recorded (`constant_names.clashing`),
and the backend renders again only if recording found a clash that changes
one (`COpModelBackend.prepare`).
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

from ..constant_names import clashing, split
from ..enum_names import item_of
from ..progseq_model import _dt_name

__all__ = ["CConstants", "OWN_PACKAGES", "PINNED_ASSERT", "is_named_constant",
           "use_text", "value_text", "constant_refs", "pinned_closure"]

#: Packages whose constants are pssc's, not the model's: the target's own
#: configuration (`target_cfg_pkg`, which a target publishes) and the core
#: library. A build does not get to redefine what the target said it is.
OWN_PACKAGES = frozenset({"target_cfg_pkg", "std_pkg", "addr_reg_pkg",
                          "executor_pkg"})


def is_named_constant(e, type_map) -> bool:
    """Whether *e* is a reference to a named integer constant that
    op-model-c spells by name (D3a): an `ExprConstant` with a `ref`, an
    integer value and a declared width, that is not an enum item and not
    one of `OWN_PACKAGES`'."""
    if _dt_name(e) != "ExprConstant":
        return False
    ref, v = getattr(e, "ref", None), getattr(e, "value", None)
    if not ref or not isinstance(v, int) or isinstance(v, bool):
        return False
    if not getattr(e, "width", 0):
        return False                    # no declared type: a literal
    if split(ref)[0].split("::")[0] in OWN_PACKAGES:
        return False
    return item_of(e, type_map) is None


#: The assertion that follows a pinned constant's definition. C11's
#: `_Static_assert` where the build is C11 or later; the negative-array-size
#: idiom (whose type names the constant) where it is C99, as the generated
#: code is built (`pssc_mem.h`'s `PSSC_STATIC_ASSERT`, which this section
#: cannot rely on: a plugin may include no pssc header).
PINNED_ASSERT = [
    "#ifndef PSSC_PINNED_CONSTANT",
    "#  if defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L",
    "#    define PSSC_PINNED_CONSTANT(n, v, msg) _Static_assert((n) == (v), msg)",
    "#  else",
    "#    define PSSC_PINNED_CONSTANT(n, v, msg) \\",
    "       typedef char pssc_pinned_##n[((n) == (v)) ? 1 : -1]",
    "#  endif",
    "#endif",
]


def _prim(width: int) -> int:
    return next(b for b in (8, 16, 32, 64) if width <= b)


def use_text(name: str, width: int, signed: bool) -> str:
    """A use of the macro *name*, as a value of its declared type (D3d).

    The build may define the macro as any integer expression, so the use
    converts it: a cast where C's type is exactly the PSS one, and a wrap
    (masked, or sign-extended) into the type's range where it is not --
    what `IntSemantics` assumes of a value of that type.
    """
    if signed and width in (32, 64):
        return f"((int{width}_t)({name}))"
    if not signed and width in (8, 16, 32, 64):
        return f"((uint{width}_t)({name}))"
    cw = _prim(width)
    if not signed:
        mask = (1 << width) - 1
        lit = f"0x{mask:x}{'ull' if width > 32 else 'u'}"
        return f"((uint{cw}_t)(({name}) & {lit}))"
    cw = 32 if width <= 32 else 64
    sh = cw - width
    return f"((int{cw}_t)((uint{cw}_t)({name}) << {sh}) >> {sh})"


def value_text(value: int, width: int, signed: bool) -> str:
    """*value* as a C literal of a type wide enough for its declared type."""
    wide = width > 32
    if value < 0:
        if value == -(1 << 63):
            return "(-9223372036854775807ll - 1)"
        if value == -(1 << 31) and not wide:
            return "(-2147483647 - 1)"
        return f"{value}ll" if wide else str(value)
    text = str(value) if value < 0x10000 else f"0x{value:x}"
    if signed:
        return text + ("ll" if wide else "")
    return text + ("ull" if wide else "u")


def constant_refs(node) -> Set[str]:
    """Every `ref` of a constant under IR *node* (see `ast2ir.constant_refs`)."""
    from ...ast2ir import constant_refs as refs
    return refs(node)


class CConstants(object):
    """How op-model-c spells a named constant, and the definitions of the
    ones it spells.

    ``names`` maps qualified names to macro names; a name it does not hold
    is the style's unqualified one (`CStylePolicy.constant_name`).
    """

    def __init__(self, style, type_map=None,
                 names: Optional[Dict[str, str]] = None):
        self.style = style
        self.type_map = type_map or {}
        self._names: Dict[str, str] = dict(names or {})
        #: qualified name -> the `ExprConstant` first spelled.
        self.used: Dict[str, object] = {}
        #: Constants whose value the emitter used to make code (a bit-select
        #: bound, say), in place of spelling them.
        self.pinned: Set[str] = set()

    # -- what is symbolic -----------------------------------------------------

    def symbolic(self, e) -> bool:
        return is_named_constant(e, self.type_map)

    def name(self, qname: str) -> str:
        n = self._names.get(qname)
        return n if n is not None else self.style.constant_name(qname, False)

    def use(self, e) -> str:
        """The use site of the named constant *e*, recorded."""
        self.used.setdefault(e.ref, e)
        return use_text(self.name(e.ref), e.width, bool(e.signed))

    def pin(self, node) -> None:
        """Record every named constant under IR *node* as pinned: its value
        was used where it is not spelled."""
        for q in constant_refs(node):
            self.pinned.add(q)

    # -- names ---------------------------------------------------------------

    def final_names(self, taken: Iterable[str] = ()) -> Dict[str, str]:
        """The macro of every recorded constant, qualified where two share a
        name, or where *taken* (`model_identifiers`) holds it: a macro
        rewrites every token of its name, a local's and a member's
        included (`constant_names.clashing`)."""
        qualify = clashing(self.used, taken, scopeless=True)
        return {q: self.style.constant_name(q, q in qualify)
                for q in self.used}

    def renamed(self, taken: Iterable[str] = ()) -> bool:
        """Whether `final_names` spells a recorded constant other than it was
        spelled while recording (the backend then renders again)."""
        return any(self.name(q) != n
                   for q, n in self.final_names(taken).items())

    def declared(self) -> List[tuple]:
        """``(macro, qualified name)`` for every constant spelled, by macro."""
        return sorted((self.name(q), q) for q in self.used)

    # -- definitions ---------------------------------------------------------

    def definitions(self, pinned: Iterable[str] = ()) -> List[str]:
        """The `constants` section's lines: one guarded definition per
        constant spelled, in macro order; a pinned one asserted after it."""
        pinned = set(pinned)
        lines: List[str] = []
        if pinned & set(self.used):
            lines += PINNED_ASSERT
        for name, q in self.declared():
            c = self.used[q]
            v = value_text(c.value, c.width, bool(c.signed))
            lines += [f"#ifndef {name}",
                      f"#define {name} ({v})  /* {q} */",
                      "#endif"]
            if q in pinned:
                lines.append(
                    f'PSSC_PINNED_CONSTANT({name}, {v}, "pssc folded {name} = '
                    f'{c.value} into this file; regenerate to change it");')
        return lines


def model_identifiers(type_map) -> Set[str]:
    """Every name of *type_map*'s model that the generated C may spell as
    an identifier inside a scope -- a local, a parameter, a member, a
    function, a type -- which `c_names` (file scope only) does not see. A
    macro of one of these names would rewrite it."""
    from ..ir_walk import walk
    out: Set[str] = set()
    for key, t in (type_map or {}).items():
        out.add(str(key).split("::")[-1])
        for f in getattr(t, "fields", None) or ():
            # A component's `static const` is a member in C too.
            out.add(f.name)
        for fn in getattr(t, "functions", None) or ():
            out.add(fn.name)
            for a in getattr(getattr(fn, "args", None), "args", None) or ():
                out.add(a.arg)
            for n in walk(getattr(fn, "body", None) or []):
                cn = type(n).__name__
                if cn == "ExprRefLocal":
                    out.add(n.name)
                elif cn == "ExprAttribute":
                    out.add(n.attr)
    return out


def pinned_closure(roots: Iterable[str], deps: Dict[str, Set[str]],
                   spelled: Iterable[str]) -> Set[str]:
    """The constants whose value a build must not change: *roots* (pinned
    where pssc folded them), and everything a pinned or *spelled* constant's
    value was folded FROM (`B = A + 1`: B's definition holds A's value)."""
    out: Set[str] = set()
    work = list(roots)
    for q in spelled:
        work += list(deps.get(q, ()))
    while work:
        q = work.pop()
        if q in out:
            continue
        out.add(q)
        work += list(deps.get(q, ()))
    return out
