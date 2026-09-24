"""The enums and plain structs a generated Python module defines.

The Python analogue of `c/lower_api_types.py`, with its own collector:
`sv.lower_api_types.collect_api_types` answers what an exported API MENTIONS,
which is what a C or SV header declares. This module also runs package
functions, `exec` blocks and exported actions, so it needs every type any of
them uses -- see `collect_module_types`.

Register VALUE structs are `lower_reg_model`'s -- they have a bit layout, and
that layout is the whole of what they are for. What is left here is the other
half: the enums and aggregates that appear in signatures, in local
declarations and as component attributes.
"""
from __future__ import annotations

from typing import List

from ..executors import has_executors, mem_access_desc
from ..progseq_model import (_dt_name, resolve_ref, struct_base,
                             struct_fields, sub_components)
from .naming import mangle

__all__ = ["collect_module_types", "emit_enum", "emit_struct",
           "emit_struct_base", "lower_api_types", "py_annotation"]

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
    """Base of every plain (non-register) struct: a PSS value.

    A subclass declares `FIELDS` -- every field, its bases' first, in
    declaration order -- and `__slots__` for the ones it adds. Fields start at
    their declared defaults (`_pss_defaults`), and may be set positionally or by
    keyword, so a caller writes `wb_dma_ch_cfg_s(prio=2)` and gets defined
    values for everything else.

    A PSS struct is a VALUE (LRM 8.3): assigning one copies it, element by
    element. A Python object is a reference, so the generated code never binds
    a second name to a struct it does not own -- it copies with `_pss_assign`
    or `_pss_copy` instead.
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
        self._pss_defaults()
        for name, value in zip(self.FIELDS, args):
            setattr(self, name, value)
        for name, value in kwargs.items():
            if name not in self.FIELDS:
                raise TypeError("%s has no field %r; it has: %s" % (
                    type(self).__name__, name, ", ".join(self.FIELDS)))
            setattr(self, name, value)

    def _pss_defaults(self):
        """Every field at its default. A class whose fields declare other
        defaults extends this."""
        for name in self.FIELDS:
            setattr(self, name, 0)

    def _pss_assign(self, other):
        """`self = other` in PSS: element by element, IN PLACE (LRM 8.3).

        In place is what an assignment to an aggregate parameter means -- the
        parameter is the caller's instance (20.3.2) -- and it is also what
        keeps a nested struct owned by exactly one parent. A derived `other`
        contributes the fields this type has.
        """
        for name in self.FIELDS:
            value = getattr(other, name)
            mine = getattr(self, name)
            if hasattr(mine, "_pss_assign"):
                mine._pss_assign(value)
            elif isinstance(value, list):
                setattr(self, name, _pss_copy_list(value))
            else:
                setattr(self, name, value)
        return self

    @classmethod
    def _pss_copy(cls, other):
        """A new value of this type, assigned from *other*."""
        return cls()._pss_assign(other)

    def __eq__(self, other):
        return type(other) is type(self) and all(
            getattr(other, n) == getattr(self, n) for n in self.FIELDS)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, ", ".join(
            "%s=%r" % (n, getattr(self, n)) for n in self.FIELDS))


def _pss_copy_list(values):
    """A PSS array value, copied: its struct elements too."""
    return [v._pss_copy(v) if hasattr(v, "_pss_copy") else
            _pss_copy_list(v) if isinstance(v, list) else v for v in values]
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


def emit_struct(struct_dtype, type_map=None, default_of=None) -> List[str]:
    """A plain struct as a class, in DECLARATION order.

    A PSS base struct is the Python base class, so `FIELDS` lists the base's
    fields first and `__slots__` only the ones this type adds. A field whose
    default is not 0 -- an initializer, a nested struct, an enum's first item
    -- is set in `_pss_defaults`; *default_of* renders it.

    A `packed_s<>` base means the PSS type has a bit layout, which a plain class
    does not reproduce and does not attempt: such a type reaching here means it
    is being used as an aggregate, and the register path -- which is where its
    layout matters -- packs it there.
    """
    from .lower_reg_model import (_docstring, tuple_lines,
                                  value_class_name)

    name = value_class_name(struct_dtype)
    base = struct_base(struct_dtype, type_map)
    own = list(struct_dtype.fields)
    lines = [f"class {name}"
             f"({value_class_name(base) if base is not None else '_Struct'}):"]
    lines += _docstring(getattr(struct_dtype, "doc", None), "    ") or [
        '    """Data type used by the export API."""']
    lines.append("")
    lines += tuple_lines("__slots__", [f.name for f in own], "    ")
    lines += tuple_lines(
        "FIELDS", [f.name for f in struct_fields(struct_dtype, type_map)],
        "    ")
    defaults = [(f.name, default_of(f)) for f in own] if default_of else []
    defaults = [(n, d) for n, d in defaults if d != "0"]
    if defaults:
        lines += ["", "    def _pss_defaults(self):",
                  "        super()._pss_defaults()"]
        lines += [f"        self.{n} = {d}" for n, d in defaults]
    return lines


def collect_module_types(model):
    """``(enums, structs)`` the generated MODULE defines, in dependency order.

    A wider question than `collect_api_types` answers for the C and SV
    headers, which is what an exported API MENTIONS: this module also renders
    package functions, `exec` blocks and exported actions, and any of them may
    declare a local of a struct nothing else names. Every body is walked
    whole -- a declaration inside a `match` arm or a loop declares a type as
    surely as one at the top -- and a struct brings its base and its fields'
    types with it. A missing class is a NameError at run time, the first time
    the line runs.
    """
    from .lower_progseq import _nodes

    tm = getattr(getattr(model, "ctx", None), "type_map", None) or {}
    skip = {id(v) for v in model.value_structs}
    enums: List[object] = []
    structs: List[object] = []
    seen = set()

    def visit(dt):
        dt = resolve_ref(dt, tm)
        if dt is None or id(dt) in seen:
            return
        cn = _dt_name(dt)
        if cn == _DT_ENUM:
            seen.add(id(dt))
            enums.append(dt)
        elif cn == _DT_STRUCT:
            seen.add(id(dt))
            if (getattr(dt, "name", "") or "").split("::")[-1] \
                    == "addr_handle_t":
                return          # a core typedef: an address, not a struct
            visit(struct_base(dt, tm))
            for f in dt.fields:
                visit(f.datatype)
            if id(dt) not in skip:
                structs.append(dt)
        elif cn == "DataTypeArray":
            visit(getattr(dt, "element_type", None))

    def visit_fn(fn):
        visit(getattr(fn, "returns", None))
        for a in (getattr(getattr(fn, "args", None), "args", None) or []):
            visit(a.annotation)
        for n in _nodes(getattr(fn, "body", None) or []):
            if _dt_name(n) == "StmtAnnAssign":
                visit(n.annotation)

    for comp in (list(model.comp_dtypes_root_first)
                 + list(getattr(model, "base_classes", ()) or ())):
        subs = {s.name for s in sub_components(comp)}
        for f in comp.fields:
            if f.name not in subs:
                visit(f.datatype)
        for fn in comp.functions:
            visit_fn(fn)
    for e in getattr(model, "entries", ()) or ():
        for fn in e.functions:
            visit_fn(fn)
    if has_executors(model):
        # Every delegated access passes a descriptor (`_mem_call`), whether or
        # not an override names the type.
        visit(mem_access_desc(tm))
    for fn in getattr(model, "functions", ()) or ():
        visit_fn(fn)
    _check_unique_names(structs)
    return enums, structs


def _check_unique_names(structs) -> None:
    """Two distinct struct types may not share a class name.

    The name is the PSS type's, without its package (`value_class_name`), so
    two packages' `cfg_s`, or two specializations of one template, would be
    one Python class -- the second silently replacing the first. Refused,
    naming both, until the name scheme carries what distinguishes them.
    """
    from .lower_reg_model import value_class_name

    by_name = {}
    for s in structs:
        n = value_class_name(s)
        if n in by_name and by_name[n] is not s:
            raise ValueError(
                f"two different struct types would both be the Python class "
                f"'{n}': '{getattr(by_name[n], 'name', '?')}' and "
                f"'{getattr(s, 'name', '?')}'. This target names a class "
                f"after its PSS type without the package or template "
                f"arguments.")
        by_name[n] = s


def lower_api_types(model, default_of=None) -> List[str]:
    """The enums and structs the module uses, as module-level definitions."""
    enums, structs = collect_module_types(model)
    if not enums and not structs:
        return []
    tm = getattr(getattr(model, "ctx", None), "type_map", None) or {}
    out: List[str] = ["# ----- Data types used by the export API. -----"]
    for e in enums:
        out += emit_enum(e)
        out.append("")
    if structs:
        out += [emit_struct_base().rstrip("\n"), "", ""]
        for s in structs:
            out += emit_struct(s, tm, default_of)
            out.append("")
    return out
