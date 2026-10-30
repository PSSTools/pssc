"""Register field bit layout -- the single owner of the ``packed_s<>`` mapping.

PSS 3.1 §21.14.1 lets a register field be named by a *string literal*:
``regs.csr.write_field("ch_en", 1)``. The string is a reference to a declared
field, not data, and two different questions have to be answered about it:

* **which field does the name mean?** -- a name-binding question, answered by
  the front end. pssparser resolves it at link time against the receiver's
  ``reg_c<R, ACC, SZ>`` template argument, with a real source location, the way
  it resolves every other name in the language. By the time a name reaches this
  module it has already been proven to exist and to be scalar.
* **which bits is that field?** -- this module.

Layout is *declaration order, LSB-first* -- the opposite of SystemVerilog. That
is a target representation, not a language rule, which is why it lives here on
the compiler side rather than in the front end. It is also why there must be
exactly one implementation of it: a second one that got the direction wrong
would produce wrong register bits with no diagnostic anywhere.
``targets/c/lower_reg_model.py`` therefore calls :func:`field_layout` rather
than recomputing the running bit offset it used to keep for itself.

The lookups here still raise :class:`RegFieldError` on a name they cannot find,
but that is a backstop against a front end that let something through, not the
plan of record for how a user learns about a typo. ``reg_rmw`` reports it
rather than assuming it cannot happen.
"""
from __future__ import annotations

import dataclasses as dc
from typing import Dict, List, Optional, Tuple


_DT_STRUCT = "DataTypeStruct"
_DT_REGISTER = "DataTypeRegister"


def _dt_name(dtype) -> str:
    return type(dtype).__name__


class RegFieldError(Exception):
    """A register field reference that cannot be resolved.

    Carries the message only; the caller supplies source context, since this
    module deliberately knows nothing about statements or files.
    """


@dc.dataclass(frozen=True)
class FieldSlice:
    """Where one declared field sits in the register word."""
    name: str
    lsb: int
    width: int
    #: The value struct's field declaration, for annotations on it
    #: (``annotations.of(slice.decl, q)``). Not part of the slice's identity.
    decl: object = dc.field(default=None, compare=False, repr=False)

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lsb


# --- layout -----------------------------------------------------------------

def value_struct(reg_dtype, resolve=None):
    """The register's value struct, or ``None`` for a scalar-valued register.

    ``resolve(name) -> dtype`` is consulted when the value type reached the IR
    as an unresolved ``DataTypeRef``.
    """
    vt = getattr(reg_dtype, "register_value_type", None)
    if vt is None:
        return None
    if _dt_name(vt) == "DataTypeRef" and resolve is not None:
        vt = resolve(getattr(vt, "ref_name", None)) or vt
    return vt if _dt_name(vt) == _DT_STRUCT else None


def _field_bits(field) -> Optional[int]:
    """Declared width of ``field`` in bits, or ``None`` if it is not scalar.

    An aggregate-typed field (a nested struct, an array) has no ``bits``, which
    is exactly the §21.14.1(c) case the callers must reject.

    An enum-typed field is as wide as the enum's declared base type
    (`enum e : bit[2]`). An enum with none has no width in a packed struct
    (21.13.1); the front end refuses that, and this raises rather than lay
    the field out at some width it made up -- a zero-width field is how a
    register value once came out a bit short, with every later field moved.
    """
    dt = field.datatype
    if _dt_name(dt) == "DataTypeEnum":
        w = int(getattr(dt, "width", 0) or 0)
        if w <= 0:
            en = (getattr(dt, "name", "") or "?").split("::")[-1]
            raise ValueError(
                f"field '{field.name}' is enum '{en}', which has no base "
                f"type, so it has no width in a packed struct; declare it as "
                f"'enum {en} : bit[N]'")
        return w
    bits = getattr(dt, "bits", None)
    try:
        return int(bits) if bits is not None else None
    except (TypeError, ValueError):
        return None


def field_width(field) -> int:
    """The width of a packed struct's scalar field; raises for anything else.

    For the places that SUM a struct's fields: an aggregate counted as zero
    there would shorten the value, not refuse it.
    """
    w = _field_bits(field)
    if w is None:
        raise ValueError(
            f"field '{field.name}' of a packed struct is not a scalar, so it "
            f"has no width in the register value (21.14.1)")
    return w


def is_big_endian(struct_dtype) -> bool:
    """True if ``struct_dtype`` is `packed_s<BIG_ENDIAN>`, directly or through
    its bases (ast2ir names that base `packed_s_BIG_ENDIAN`)."""
    dt, depth = struct_dtype, 0
    while dt is not None and depth < 64:
        sup = getattr(dt, "super", None)
        nm = (getattr(sup, "ref_name", None) or getattr(sup, "name", None)
              or "").split("::")[-1]
        if nm.startswith("packed_s"):
            return nm == "packed_s_BIG_ENDIAN"
        dt, depth = (sup if _dt_name(sup) == _DT_STRUCT else None), depth + 1
    return False


def _place(fields, big: bool) -> List[FieldSlice]:
    """Lay ``fields`` out in declaration order (21.13.1.1): from the LSB
    up for little-endian; for big-endian from the MSB of the packed register
    (whole bytes) down, the don't-care bits at its LSB end. Big-endian was
    laid out little-endian (W49).

    Aggregate-typed fields get ``width == 0``, so their presence is visible
    to :func:`resolve_field` (which rejects them by name) without moving the
    fields that follow -- there is no honest offset to give them, and
    silently skipping them would shift every later field.
    """
    widths = [_field_bits(f) for f in fields]
    total = sum(w for w in widths if w)
    top = (total + 7) // 8 * 8
    out: List[FieldSlice] = []
    bit = 0
    for f, w in zip(fields, widths):
        if w is None:
            out.append(FieldSlice(name=f.name, lsb=bit, width=0, decl=f))
            continue
        lsb = top - bit - w if big else bit
        out.append(FieldSlice(name=f.name, lsb=lsb, width=w, decl=f))
        bit += w
    return out


def field_layout(reg_dtype, resolve=None) -> List[FieldSlice]:
    """Declared fields of ``reg_dtype``'s value struct, placed by
    :func:`_place` (the SV lowering reverses the declaration order on
    emission, not here)."""
    fields = list(getattr(reg_dtype, "fields", []) or [])
    vs = None
    if not fields:
        vs = value_struct(reg_dtype, resolve)
        fields = list(getattr(vs, "fields", []) or []) if vs is not None else []
    return _place(fields, vs is not None and is_big_endian(vs))


def field_map(reg_dtype, resolve=None) -> Dict[str, FieldSlice]:
    return {fs.name: fs for fs in field_layout(reg_dtype, resolve)}


def struct_layout(struct_dtype) -> List[FieldSlice]:
    """:func:`field_layout` for a bare value struct.

    The C register-model emitter walks value structs directly (it emits one
    union per struct, not per register), so it needs the layout without a
    register to hang it on. Same arithmetic, one implementation.
    """
    return _place(list(getattr(struct_dtype, "fields", []) or []),
                  is_big_endian(struct_dtype))


# --- resolution -------------------------------------------------------------

def _did_you_mean(name: str, candidates: List[str]) -> str:
    """`` did you mean 'ch_en'?`` for the nearest candidate, else ``''``.

    Deliberately the same shape as the linker's existing suggestions so the two
    read alike. The edit-distance cutoff scales with the name so a two-character
    identifier does not match everything.
    """
    best, best_d = None, None
    limit = max(1, min(3, len(name) // 3 + 1))
    for c in candidates:
        d = _edit_distance(name, c)
        if d <= limit and (best_d is None or d < best_d):
            best, best_d = c, d
    return f" -- did you mean '{best}'?" if best else ""


def _edit_distance(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def reg_name(reg_dtype) -> str:
    n = getattr(reg_dtype, "name", None)
    return (n or "<register>").split("::")[-1]


def value_type_name(reg_dtype) -> str:
    vt = getattr(reg_dtype, "register_value_type", None)
    n = getattr(vt, "name", None) if vt is not None else None
    return (n or _dt_name(vt) if vt is not None else "<none>").split("::")[-1]


def resolve_field(reg_dtype, name: str, resolve=None) -> FieldSlice:
    """Resolve ``name`` to its bit range in ``reg_dtype``, or raise.

    Raises :class:`RegFieldError` for the two §21.14.1 cases a name can fail:
    it is not a field of the value type, or it names an aggregate.
    """
    layout = field_map(reg_dtype, resolve)
    if not layout:
        raise RegFieldError(
            f"register '{reg_name(reg_dtype)}' has no named fields: its value "
            f"type '{value_type_name(reg_dtype)}' is not a struct, so "
            f"field-wise access does not apply")
    fs = layout.get(name)
    if fs is None:
        raise RegFieldError(
            f"no field '{name}' in register value type "
            f"'{value_type_name(reg_dtype)}'"
            f"{_did_you_mean(name, list(layout))}")
    if fs.width == 0:
        raise RegFieldError(
            f"field '{name}' of '{value_type_name(reg_dtype)}' has aggregate "
            f"type; field-wise register access applies to scalar fields only "
            f"(PSS 3.1 §21.14.1)")
    return fs


def field_mask(reg_dtype, name: str, resolve=None) -> int:
    return resolve_field(reg_dtype, name, resolve).mask


def reg_size_bits(reg_dtype) -> int:
    """Transaction width of the register, in bits."""
    sb = getattr(reg_dtype, "size_bits", None)
    if sb:
        return int(sb)
    layout = field_layout(reg_dtype)
    if layout:
        return sum(fs.width for fs in layout)
    vt = getattr(reg_dtype, "register_value_type", None)
    return int(getattr(vt, "bits", 32) or 32)


def is_readable(reg_dtype) -> bool:
    return (getattr(reg_dtype, "access_mode", "READWRITE") or "READWRITE") != "WRITEONLY"
