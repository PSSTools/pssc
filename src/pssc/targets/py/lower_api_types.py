"""The enums and plain structs a generated Python API mentions.

The Python analogue of `c/lower_api_types.py`, and like it, it reuses
`sv.lower_api_types.collect_api_types`: WHICH types an API mentions is a
property of the model rather than of the output language, and a second
collector would be a second answer to one question. Only the rendering is here.

Register VALUE structs are `lower_reg_model`'s -- they have a bit layout, and
that layout is the whole of what they are for. What is left here is the other
half: the enums and aggregates that appear in operation signatures, in local
declarations and as component attributes.
"""
from __future__ import annotations

from typing import List

from ..progseq_model import _dt_name
from ..sv.lower_api_types import collect_api_types
from .naming import mangle

__all__ = ["emit_enum", "emit_struct", "emit_struct_base", "lower_api_types",
           "py_annotation"]

_DT_INT = "DataTypeInt"
_DT_BOOL = "DataTypeBool"
_DT_ENUM = "DataTypeEnum"
_DT_CHANDLE = "DataTypeChandle"
_DT_STRUCT = "DataTypeStruct"
_DT_STRING = "DataTypeString"


def py_annotation(dtype) -> str:
    """A PSS type as a Python annotation, or ``""`` where there is no answer.

    `cpp_type`'s counterpart, and it is DELIBERATELY coarser. C++ needs a width
    to allocate storage; a Python annotation exists to be read and to be checked,
    and `int` is the whole truth about a `bit[32]` here -- the generated code
    masks where the width matters (`expr_cast`), so annotating it `uint32` would
    claim a guarantee the language does not make.

    An empty string where the mapping has no answer, and the caller then emits no
    annotation at all. `Any` would be the other option and is worse: an
    unannotated parameter says "not stated", `Any` says "anything goes", and only
    the first of those is true.
    """
    cn = _dt_name(dtype)
    if cn == _DT_INT:
        # A one-bit unsigned field is a flag in the model and reads as one at a
        # call site; `int` would still accept it, but `bool` is what the
        # signature means. Matches `cpp_type`'s same special case.
        if not getattr(dtype, "signed", False) \
                and int(getattr(dtype, "bits", 32) or 32) == 1:
            return "bool"
        return "int"
    if cn == _DT_BOOL:
        return "bool"
    if cn == _DT_STRING:
        return "str"
    if cn == _DT_ENUM:
        # An enum reaches Python as module-level `int` constants
        # (`emit_enum`), so `int` is what a caller can actually pass.
        return "int"
    if cn == _DT_CHANDLE:
        return "int"
    if cn == _DT_STRUCT:
        from .lower_reg_model import value_class_name

        nm = (getattr(dtype, "name", "") or "").split("::")[-1]
        if nm == "addr_handle_t":
            return "int"
        return value_class_name(dtype)
    return ""

#: The base of every plain struct class. Emitted once per module, for the same
#: reason `_RegValue` is: repeating six lines per type buries what each type
#: actually says.
_STRUCT_BASE = '''\
class _Struct(object):
    """Base of every plain (non-register) struct the API mentions.

    A subclass declares `FIELDS` -- names in declaration order -- and the
    matching `__slots__`. Fields default to 0 and may be set positionally or by
    keyword, so a caller writes `wb_dma_ch_cfg_s(prio=2)` and gets defined
    values for everything else.
    """

    # Annotated for the reason `_RegValue`'s are: a subclass assigns a
    # populated tuple, and an unannotated `()` is inferred as a type only `()`
    # satisfies.
    __slots__: tuple = ()

    FIELDS: tuple = ()

    def __init__(self, *args, **kwargs):
        if len(args) > len(self.FIELDS):
            raise TypeError("%s takes at most %d positional arguments" % (
                type(self).__name__, len(self.FIELDS)))
        for name in self.FIELDS:
            setattr(self, name, 0)
        for name, value in zip(self.FIELDS, args):
            setattr(self, name, value)
        for name, value in kwargs.items():
            if name not in self.FIELDS:
                raise TypeError("%s has no field %r; it has: %s" % (
                    type(self).__name__, name, ", ".join(self.FIELDS)))
            setattr(self, name, value)

    def __eq__(self, other):
        return type(other) is type(self) and all(
            getattr(other, n) == getattr(self, n) for n in self.FIELDS)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, ", ".join(
            "%s=%r" % (n, getattr(self, n)) for n in self.FIELDS))
'''


def emit_struct_base() -> str:
    return _STRUCT_BASE


def emit_enum(enum_dtype) -> List[str]:
    """A PSS enum as module-level integer constants, with EXPLICIT values.

    The model's numbers are register field encodings and cross-language
    contract, so they are written out rather than left to any numbering scheme.
    Names are emitted verbatim, as the C and SV projections emit them: an
    enumerator is how the model, the datasheet and the other generated
    languages all refer to the value.

    Plain `int` constants rather than `enum.IntEnum`: a generated module that
    imports nothing can be dropped anywhere, and an IntEnum would buy repr at
    the cost of that. A field read back off the bus is an int either way.
    """
    name = (getattr(enum_dtype, "name", "") or "").split("::")[-1]
    out = [f"# {name}"]
    out += [f"{mangle(k)} = {v}" for k, v in enum_dtype.items.items()]
    return out


def emit_struct(struct_dtype) -> List[str]:
    """A plain struct as a class, in DECLARATION order.

    A `packed_s<>` base means the PSS type has a bit layout, which a plain class
    does not reproduce and does not attempt: such a type reaching here means it
    is being used as an aggregate, and the register path -- which is where its
    layout matters -- packs it there.
    """
    from .lower_reg_model import (_docstring, tuple_lines,
                                  value_class_name)

    name = value_class_name(struct_dtype)
    fields = [f.name for f in struct_dtype.fields]
    lines = [f"class {name}(_Struct):"]
    lines += _docstring(getattr(struct_dtype, "doc", None), "    ") or [
        '    """Data type used by the export API."""']
    lines.append("")
    lines += tuple_lines("__slots__", fields, "    ")
    lines += tuple_lines("FIELDS", fields, "    ")
    return lines


def lower_api_types(components, reg_value_structs=(), ctor_names=None
                    ) -> List[str]:
    """The enums and structs the API mentions, as module-level definitions."""
    enums, structs = collect_api_types(components, reg_value_structs,
                                       ctor_names)
    if not enums and not structs:
        return []
    out: List[str] = ["# ----- Data types used by the export API. -----"]
    for e in enums:
        out += emit_enum(e)
        out.append("")
    if structs:
        out.append(emit_struct_base().rstrip("\n"))
        for s in structs:
            out += emit_struct(s)
            out.append("")
    return out


def api_struct_names(components, reg_value_structs=(), ctor_names=None):
    """`{id(dtype): class name}` for every plain struct this API declares."""
    from .lower_reg_model import value_class_name

    _, structs = collect_api_types(components, reg_value_structs, ctor_names)
    return {id(s): value_class_name(s) for s in structs}
