"""The memory image of a `packed_s` struct, for `read_struct` / `write_struct`.

PSS 21.12 moves a `packed_s` struct to and from memory as its packed bit
image: fields in declaration order from the LSB (21.13.1.1, little-endian),
byte 0 of the image at the handle. This module decides HOW a target moves
that image -- which accesses, at which offsets, through which member -- so
that every target makes the same accesses and none of them recomputes a
layout. The placement of each field is `reg_field_resolve`'s, as it is for
register values; there is one layout implementation.

Access width follows the struct's size (docs/design/write-struct-plan.md):

* A struct of at most 64 bits is one value -- a target's union `raw`, an SV
  packed vector, a Python int -- moved in one access of its own size, or, for
  a size that is not 8/16/32/64 bits, in the fewest power-of-two accesses that
  cover its bytes, lowest address first. No byte past the struct is touched.
* A wider struct is moved member by member, each member at its own size: a
  scalar of exactly 8, 16, 32 or 64 bits, or a nested `packed_s` (recursively
  by the same two rules). Every access must be naturally aligned in the image.

Anything else is a :class:`PackedImageError` naming the field: the model's
layout, not a compiler bug.

The same rules decide which structs a C or C++ target declares as a union of
a raw word and bitfields (:func:`union_fields`): little-endian `packed_s`, at
most 64 bits, every field a scalar.
"""
from __future__ import annotations

import dataclasses as dc
from typing import List, Optional, Tuple

from ..diag import Refusal
from ..reg_field_resolve import _field_bits, is_big_endian, struct_layout

_DT_STRUCT = "DataTypeStruct"
_DT_BOOL = "DataTypeBool"
_DT_INT = "DataTypeInt"

#: The widest struct moved -- and, in C/C++, declared -- as a single value.
UNION_MAX_BITS = 64

#: The access widths the memory primitives provide (`read8` .. `write64`).
ACCESS_WIDTHS = (8, 16, 32, 64)


def _dt_name(dtype) -> str:
    return type(dtype).__name__


def _short(dtype) -> str:
    return (getattr(dtype, "name", "") or "?").split("::")[-1]


class PackedImageError(Refusal):
    """A struct whose image `read_struct` / `write_struct` cannot move."""


@dc.dataclass(frozen=True)
class UnionField:
    """One field of a struct declared as a union: where it sits in `raw`."""
    name: str
    lsb: int
    width: int
    signed: bool
    field: object = dc.field(compare=False, repr=False)


@dc.dataclass(frozen=True)
class Access:
    """One memory access that moves part of a struct's image.

    ``path`` is the chain of member names from the struct to the value
    accessed. ``raw`` says the value is a union's raw word -- the whole struct
    when ``path`` is empty -- of which this access moves the ``width`` bits
    from ``shift`` up; otherwise ``path`` ends at a scalar member, moved
    whole. ``offset`` is in bytes from the handle.
    """
    offset: int
    width: int
    path: Tuple[str, ...]
    raw: bool
    shift: int = 0
    raw_bits: int = 0       # the width of the raw word, when ``raw``


def is_packed(struct_dtype) -> bool:
    """True if ``struct_dtype`` derives from `packed_s<>`, directly or through
    its bases (a struct with no such base has no layout contract)."""
    dt, depth = struct_dtype, 0
    while dt is not None and depth < 64:
        sup = getattr(dt, "super", None)
        nm = (getattr(sup, "ref_name", None) or getattr(sup, "name", None)
              or "").split("::")[-1]
        if nm.startswith("packed_s"):
            return True
        dt, depth = (sup if _dt_name(sup) == _DT_STRUCT else None), depth + 1
    return False


def _fields(struct_dtype) -> list:
    return list(getattr(struct_dtype, "fields", []) or [])


def scalar_width(field) -> Optional[int]:
    """The width of a scalar field (a bool is one bit), or ``None`` for an
    aggregate. Raises ``ValueError`` for an enum without a base type."""
    if _dt_name(field.datatype) == _DT_BOOL:
        return 1
    return _field_bits(field)


def _signed(field) -> bool:
    dt = field.datatype
    return _dt_name(dt) == _DT_INT and bool(getattr(dt, "signed", False))


def struct_bits(struct_dtype, where=None) -> int:
    """Total bits of a `packed_s` struct, nested `packed_s` fields included."""
    total = 0
    for f in _fields(struct_dtype):
        w = _width(f, struct_dtype, where)
        total += w
    return total


def _width(f, owner, where) -> int:
    try:
        w = scalar_width(f)
    except ValueError as e:
        raise PackedImageError(str(e), *(where or ()))
    if w is not None:
        return w
    dt = f.datatype
    if _dt_name(dt) == _DT_STRUCT and is_packed(dt):
        return struct_bits(dt, where)
    raise PackedImageError(
        f"field '{f.name}' of '{_short(owner)}' is not a scalar or a "
        f"packed_s struct, so it has no place in a packed image (21.13.1)",
        *(where or ()))


def union_refusal(struct_dtype) -> Optional[str]:
    """Why ``struct_dtype`` cannot be declared as a raw-word union, or
    ``None`` if it can."""
    if not is_packed(struct_dtype):
        return "it is not a packed_s struct"
    if is_big_endian(struct_dtype):
        return "it is big-endian"
    total = 0
    for f in _fields(struct_dtype):
        try:
            w = scalar_width(f)
        except ValueError as e:
            return str(e)
        if w is None:
            return (f"field '{f.name}' is not a scalar; a struct of at most "
                    f"{UNION_MAX_BITS} bits cannot hold a nested struct")
        total += w
    if total == 0:
        return "it has no fields"
    if total > UNION_MAX_BITS:
        return f"it is {total} bits, wider than {UNION_MAX_BITS}"
    return None


def union_fields(struct_dtype) -> Optional[List[UnionField]]:
    """The fields of ``struct_dtype`` placed in its raw word, in declaration
    order, or ``None`` if it is not declared as a union (`union_refusal`)."""
    if union_refusal(struct_dtype) is not None:
        return None
    return [UnionField(fs.name, fs.lsb, fs.width, _signed(f), f)
            for fs, f in zip(struct_layout(struct_dtype), _fields(struct_dtype))]


def raw_bits(bits: int) -> int:
    """The raw word for a struct of ``bits`` bits: the smallest of 8/16/32/64."""
    for w in ACCESS_WIDTHS:
        if bits <= w:
            return w
    raise ValueError(f"{bits} bits do not fit a raw word")


def _split(nbytes: int) -> List[Tuple[int, int]]:
    """``(byte offset, width)`` accesses covering ``nbytes`` bytes: one if it
    is 1/2/4/8, else the fewest power-of-two pieces, largest (lowest) first."""
    out, off = [], 0
    while off < nbytes:
        w = 8
        while w * 2 <= 64 and off + (w * 2) // 8 <= nbytes:
            w *= 2
        out.append((off, w))
        off += w // 8
    return out


def _union_plan(struct_dtype, base: int, path, where) -> List[Access]:
    bits = struct_bits(struct_dtype, where)
    rb = raw_bits(bits)
    out = []
    for off, w in _split((bits + 7) // 8):
        _aligned(base + off, w, struct_dtype, path, where)
        out.append(Access(base + off, w, tuple(path), True, off * 8, rb))
    return out


def _aligned(offset: int, width: int, owner, path, where) -> None:
    if offset % (width // 8):
        what = ".".join(path) if path else _short(owner)
        raise PackedImageError(
            f"'{what}' would be a {width}-bit access at byte offset {offset} "
            f"of the image, which is not {width // 8}-byte aligned; place it "
            f"on a {width // 8}-byte boundary", *(where or ()))


def access_plan(struct_dtype, where=None) -> List[Access]:
    """The accesses that move ``struct_dtype``'s image, in address order.

    ``where`` are IR nodes a refusal is located at (the call, typically).
    """
    _check_struct(struct_dtype, (), where)
    if struct_bits(struct_dtype, where) <= UNION_MAX_BITS:
        why = union_refusal(struct_dtype)
        if why is not None:
            raise PackedImageError(
                f"'{_short(struct_dtype)}' cannot be moved by read_struct/"
                f"write_struct: {why}", *(where or ()))
        return _union_plan(struct_dtype, 0, (), where)
    return _member_plan(struct_dtype, 0, (), where)


def _check_struct(dtype, path, where) -> None:
    what = ".".join(path) if path else _short(dtype)
    if _dt_name(dtype) != _DT_STRUCT or not is_packed(dtype):
        raise PackedImageError(
            f"'{what}' is not a packed_s struct, so it has no memory image "
            f"for read_struct/write_struct (21.12)", *(where or ()))
    if is_big_endian(dtype):
        raise PackedImageError(
            f"'{what}' is packed_s<BIG_ENDIAN>; read_struct/write_struct "
            f"support little-endian packed structs only", *(where or ()))


def _member_plan(struct_dtype, base: int, path, where) -> List[Access]:
    out: List[Access] = []
    pos = 0                                    # bit position in this struct
    for f in _fields(struct_dtype):
        w = _width(f, struct_dtype, where)
        p = tuple(path) + (f.name,)
        scalar = scalar_width(f) is not None
        if pos % 8:
            raise PackedImageError(
                f"'{'.'.join(p)}' starts at bit {pos} of "
                f"'{_short(struct_dtype)}', not on a byte; in a struct wider "
                f"than {UNION_MAX_BITS} bits every member is moved on its own, "
                f"so group the narrow fields into a packed_s struct of 8, 16, "
                f"32 or 64 bits", *(where or ()))
        off = base + pos // 8
        if scalar:
            if w not in ACCESS_WIDTHS:
                raise PackedImageError(
                    f"'{'.'.join(p)}' is {w} bits; in a struct wider than "
                    f"{UNION_MAX_BITS} bits every scalar member must be 8, "
                    f"16, 32 or 64 bits, so group it with its neighbours "
                    f"into a packed_s struct", *(where or ()))
            _aligned(off, w, struct_dtype, p, where)
            out.append(Access(off, w, p, False))
        else:
            _check_struct(f.datatype, p, where)
            if w <= UNION_MAX_BITS:
                why = union_refusal(f.datatype)
                if why is not None:
                    raise PackedImageError(
                        f"'{'.'.join(p)}' cannot be moved by read_struct/"
                        f"write_struct: {why}", *(where or ()))
                out += _union_plan(f.datatype, off, p, where)
            else:
                out += _member_plan(f.datatype, off, p, where)
        pos += w
    return out


@dc.dataclass(frozen=True)
class Leaf:
    """One scalar field of a struct's image, nested structs flattened: its
    member path from the struct and its bits in the image."""
    path: Tuple[str, ...]
    lsb: int
    width: int
    field: object = dc.field(compare=False, repr=False)


def leaves(struct_dtype, where=None) -> List[Leaf]:
    """Every scalar field of ``struct_dtype``'s image, in image order.

    For a target whose own struct representation is NOT the image -- SV,
    whose enums are 32-bit ints inside a `struct packed` -- the image is
    assembled from these, a field at a time. Little-endian only, as
    everything here.
    """
    _check_struct(struct_dtype, (), where)
    return _leaves(struct_dtype, 0, (), where)


def _leaves(struct_dtype, base: int, path, where) -> List[Leaf]:
    out: List[Leaf] = []
    pos = base
    for f in _fields(struct_dtype):
        w = _width(f, struct_dtype, where)
        p = tuple(path) + (f.name,)
        if scalar_width(f) is not None:
            out.append(Leaf(p, pos, w, f))
        else:
            _check_struct(f.datatype, p, where)
            out += _leaves(f.datatype, pos, p, where)
        pos += w
    return out


# --- the call ---------------------------------------------------------------

#: The calls this module lowers.
STRUCT_CALLS = ("read_struct", "write_struct")

#: Built-ins every target renders as address arithmetic, not as a call.
_HANDLE_ARITH = ("make_handle_from_handle", "addr_value")


def callee(func) -> Optional[str]:
    """The name a call's ``func`` names: a bare `write_struct(...)` or a
    qualified `addr_reg_pkg::write_struct(...)` / `self.write_struct(...)`."""
    cn = _dt_name(func)
    if cn == "ExprRefUnresolved":
        from .validate_calls import callee_name
        return callee_name(func)
    if cn == "ExprAttribute":
        return func.attr
    return None


def struct_call(e) -> Optional[str]:
    """``'read_struct'`` / ``'write_struct'`` if ``e`` is a call of one."""
    if _dt_name(e) != "ExprCall":
        return None
    name = callee(e.func)
    return name if name in STRUCT_CALLS else None


def _reads_call(e) -> bool:
    """Whether evaluating ``e`` calls anything other than handle arithmetic."""
    if isinstance(e, (list, tuple)):
        return any(_reads_call(x) for x in e)
    if not dc.is_dataclass(e) or isinstance(e, type):
        return False
    if _dt_name(e) == "ExprCall" and callee(e.func) not in _HANDLE_ARITH:
        return True
    return any(_reads_call(getattr(e, f.name)) for f in dc.fields(e))


def check_call(name: str, args, call) -> Tuple[object, object]:
    """``(handle, struct)`` arguments of a `read_struct`/`write_struct` call,
    or a refusal located at ``call``.

    A `mem_access_desc` is refused: no target models one. Neither argument
    may contain a call (handle arithmetic aside), because a struct is moved
    in several accesses and each one reads both.
    """
    if len(args) > 2:
        raise PackedImageError(f"'{name}': a mem_access_desc argument is not "
                               f"supported", call)
    if len(args) != 2:
        raise PackedImageError(f"'{name}' takes a handle and a struct", call)
    for what, e in (("handle", args[0]), ("struct", args[1])):
        if _reads_call(e):
            raise PackedImageError(
                f"'{name}': the {what} argument contains a call, and the "
                f"struct is moved in several accesses that each read it; "
                f"assign it to a variable first", call)
    return args[0], args[1]
