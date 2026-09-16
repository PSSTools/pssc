"""A PSS register tree as Python: value classes plus folded accessor methods.

Two things are emitted, and the split matches every other backend's:

* one **value class** per register value struct, carrying the bit layout the
  model declares and nothing else -- the pack/unpack arithmetic is inherited
  from `_RegValue`, emitted once per module;
* one **accessor set** per reachable register, as methods on the component
  class, with the address folded to a constant plus one term per array index.

The addresses come from `targets/reg_layout.py`, which is also where the C
backend gets them. That is not tidiness: an address is the one thing in a
generated programming API that no golden snapshot can check, so two backends
computing offsets two ways is the worst duplication available here.
"""
from __future__ import annotations

from typing import List

from ...reg_field_resolve import struct_layout
from ..comments import HASH, append_trailing, comment_lines
from ..reg_layout import RegAccessor, collect_accessors
from .naming import IMPORTS_ATTR, def_kw, is_async, mangle, reg_symbol

__all__ = ["emit_value_base", "emit_value_class", "lower_value_classes",
           "lower_accessors", "value_class_name", "accessor_map",
           "tuple_lines"]

#: The shared pack/unpack behaviour, emitted once per module rather than
#: repeated in every value class. A generated module still imports nothing to
#: use it -- see `backend.py` for why that is worth keeping.
_VALUE_BASE = '''\
class _RegValue(object):
    """Base of every register value class: a bit layout, packed and unpacked.

    A subclass declares `LAYOUT` -- `(name, lsb, width)` in declaration order,
    LSB first -- and `BITS`. Everything else is here.

    Fields are plain attributes, so a value is built and edited the way the PSS
    body does it (`csr.CH_EN = 1`) and `__slots__` turns a misspelled field name
    into an AttributeError instead of a silently ignored assignment.
    """

    # Annotated because a subclass narrows them and an unannotated `()` is
    # inferred as the empty-tuple TYPE, which nothing can override. The
    # generated module states its platform seam as a Protocol; a type checker
    # that has to be told to ignore the rest of the file would make that claim
    # worth less.
    __slots__: tuple = ()

    LAYOUT: tuple = ()
    BITS: int = 0

    def __init__(self, **fields):
        for name, _, _ in self.LAYOUT:
            setattr(self, name, 0)
        for name, value in fields.items():
            if name not in self.__slots__:
                raise TypeError("%s has no field %r; it has: %s" % (
                    type(self).__name__, name,
                    ", ".join(n for n, _, _ in self.LAYOUT)))
            setattr(self, name, value)

    @classmethod
    def unpack(cls, raw):
        """The value a raw register word represents."""
        v = cls()
        for name, lsb, width in cls.LAYOUT:
            setattr(v, name, (raw >> lsb) & ((1 << width) - 1))
        return v

    def pack(self):
        """The raw register word this value represents.

        Each field is masked to its own width, so an out-of-range assignment
        corrupts that field rather than its neighbours.
        """
        raw = 0
        for name, lsb, width in self.LAYOUT:
            raw |= (int(getattr(self, name)) & ((1 << width) - 1)) << lsb
        return raw

    def __eq__(self, other):
        return type(other) is type(self) and other.pack() == self.pack()

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, ", ".join(
            "%s=%d" % (n, getattr(self, n))
            for n, _, _ in self.LAYOUT if getattr(self, n)))
'''


def emit_value_base() -> str:
    return _VALUE_BASE


def value_class_name(struct_dtype) -> str:
    """The Python class for a value struct: `dma_ch_csr_s`, verbatim.

    Not CapWords, unlike a component class. A value struct is a DATA layout the
    model names after the register it belongs to, its fields are upper-case
    because the datasheet's are, and renaming the type while keeping the fields
    would make the pair read as two unrelated conventions.
    """
    return mangle((getattr(struct_dtype, "name", "") or "").split("::")[-1])


def emit_value_class(struct_dtype) -> str:
    """One value class: the layout, and whatever the PSS said about each field."""
    name = value_class_name(struct_dtype)
    slices = struct_layout(struct_dtype)
    total = sum(s.width for s in slices)

    lines = [f"class {name}(_RegValue):"]
    doc = getattr(struct_dtype, "doc", None)
    lines += _docstring(doc, "    ") or ['    """Register value layout."""']
    lines.append("")
    lines += tuple_lines("__slots__", [s.name for s in slices], "    ")
    lines.append(f"    BITS = {total}")
    lines.append("    LAYOUT = (")
    # `struct_layout` emits exactly one slice per declared field, in order, so
    # the two zip: the slice carries the arithmetic, the field carries what the
    # source said about it.
    for fs, f in zip(slices, struct_dtype.fields):
        lines += comment_lines(getattr(f, "doc", None), "        ", HASH)
        trailing = (getattr(f, "doc_trailing", None)
                    or f"[{fs.lsb + fs.width - 1}:{fs.lsb}]")
        lines += append_trailing(
            [f'        ("{fs.name}", {fs.lsb}, {fs.width}),'], trailing, HASH)
    lines.append("    )")
    return "\n".join(lines)


def lower_value_classes(model) -> List[str]:
    """Every value class the model uses, in first-use order, de-duplicated.

    De-duplicated by identity across components: a sub-component's register
    group is usually also reachable from the root, and emitting its value class
    twice would leave the second definition shadowing the first -- which in
    Python is silent, unlike C's redefinition error.
    """
    out: List[str] = [emit_value_base().rstrip("\n")]
    seen = set()
    for struct in model.value_structs:
        if id(struct) in seen:
            continue
        seen.add(id(struct))
        out.append(emit_value_class(struct))
    return out


# --- accessors --------------------------------------------------------------

def accessor_map(comp_dtype):
    """`{(path, kind): RegAccessor}` for one component.

    The body emitter looks a register access up here rather than reconstructing
    the walk, so the method it calls and the method that was defined are the
    same fact read twice instead of two facts kept in step.
    """
    return {a.path: a for a in collect_accessors(comp_dtype)}


def _idx_params(n: int) -> str:
    return "".join(f", i{k}" for k in range(n))


def _addr_expr(acc: RegAccessor) -> str:
    terms = [f"self._base + 0x{acc.const_off:x}"]
    terms += [f"i{k} * 0x{stride:x}" for k, stride in enumerate(acc.strides)]
    return " + ".join(terms)


def emit_accessor(acc: RegAccessor, await_style: str = "sync") -> List[str]:
    """The methods for one register.

    `_addr` is emitted for every register regardless of direction, because the
    folded offset is the model's statement about the device and a caller
    reaching past the accessors still needs it. It is also the one accessor
    that is NEVER coloured: it is arithmetic on the component's base and reaches
    nothing, so making it `async def` would put a suspension point where there
    is no access and force every caller to await an addition.

    BOTH pairs are emitted for every register: `read`/`write` are typed, and
    `read_val`/`write_val` work in raw bits, which is what the masked forms
    need. On a scalar-valued register the two are the same access under two
    names, and they are emitted anyway -- the model calls `A0.write(src)` on a
    `reg_c<bit[32]>` exactly as it calls `CSR.write(csr)` on a struct-valued
    one, so a call site must never have to ask which kind of register it is
    holding. It is the rule the C accessors already follow.
    """
    idx = _idx_params(len(acc.strides))
    stem = lambda kind: reg_symbol(acc.segs, acc.name, kind)   # noqa: E731
    args = idx.lstrip(", ")
    sep = ", " if args else ""
    addr_call = f"self.{stem('addr')}({args})"
    width = acc.prim_bits
    d = def_kw(await_style)
    aw = "await " if is_async(await_style) else ""
    out: List[str] = [
        f"    def {stem('addr')}(self{idx}):",
        f'        """Address of `{".".join(acc.path)}`."""',
        f"        return {_addr_expr(acc)}",
    ]
    if acc.readable:
        out += [
            f"    {d} {stem('read_val')}(self{idx}):",
            f"        return {aw}self.{IMPORTS_ATTR}.read{width}({addr_call})",
        ]
    if acc.writable:
        out += [
            f"    {d} {stem('write_val')}(self{idx}, value):",
            f"        {aw}self.{IMPORTS_ATTR}.write{width}({addr_call}, value)",
        ]
    write_arg = "value"
    if acc.is_struct:
        write_arg = "value.pack()"
    if acc.readable:
        # The raw read is HOISTED before it is unpacked, for the same reason
        # every awaited call in a body is: `return cls.unpack(await self.x())`
        # would put an `await` inside an argument list, and this backend does
        # not put one there. See `lower_progseq._awaited`.
        out.append(f"    {d} {stem('read')}(self{idx}):")
        if acc.is_struct:
            out += [
                f"        raw = {aw}self.{stem('read_val')}({args})",
                f"        return {value_class_name(acc.value_struct)}"
                f".unpack(raw)",
            ]
        else:
            out.append(f"        return {aw}self.{stem('read_val')}({args})")
    if acc.writable:
        out += [
            f"    {d} {stem('write')}(self{idx}, value):",
            f"        {aw}self.{stem('write_val')}({args}{sep}{write_arg})",
        ]

    # The masked write -- PSS 3.1 §21.14.1:
    #
    #     REG_VAL(new) = (REG_VAL(current) & ~mask) | (val & mask)
    #
    # THE READ IS PART OF THE DEFINITION, not an implementation choice: on a
    # register whose read has side effects -- a CSR that clears its status bits
    # -- a masked write has them too. Which is also why it needs both
    # directions, and why a one-directional register does not get one.
    if acc.readable and acc.writable:
        out += [
            f"    {d} {stem('write_val_masked')}(self{idx}, mask, val):",
            f"        cur = {aw}self.{stem('read_val')}({args})",
            f"        {aw}self.{stem('write_val')}({args}{sep}"
            f"(cur & ~mask) | (val & mask))",
        ]
    return out


def lower_accessors(comp_dtype, await_style: str = "sync") -> List[str]:
    """Every register accessor for one component, keyed to ITS base.

    Per component rather than per model, because the address a register lives at
    depends on which component you reach it through: `WbDmaCh.regs_csr_addr()`
    is `base + 0x0`, and the same physical register reached from the root is
    `regs_bank_csr_addr(i)` = `base + 0x20 + 0x20*i`. Both are correct because
    they are methods on objects holding different bases.
    """
    out: List[str] = []
    for acc in collect_accessors(comp_dtype):
        out += emit_accessor(acc, await_style)
    return out


def tuple_lines(name: str, items, pad: str, width: int = 79) -> List[str]:
    """`name = ("a", "b", ...)`, wrapped to *width*.

    A value struct can have twenty fields, and a `__slots__` line naming all of
    them is one nobody reads to the end of. Wrapping is worth the six lines
    here: the generated module is meant to be read beside the PSS it came from.
    """
    quoted = [f'"{i}"' for i in items]
    one = f"{pad}{name} = (" + ", ".join(quoted) + ")"
    if len(one) <= width:
        return [one]
    out = [f"{pad}{name} = ("]
    cur = pad + "    "
    for q in quoted:
        piece = q + ","
        if len(cur) + len(piece) + 1 > width and cur.strip():
            out.append(cur.rstrip())
            cur = pad + "    "
        cur += piece + " "
    if cur.strip():
        out.append(cur.rstrip())
    out.append(f"{pad})")
    return out


def _docstring(text, pad: str) -> List[str]:
    """*text* as a Python docstring at indent *pad*, or ``[]``.

    A docstring rather than a comment because that is what Python has: the model
    author's prose stays reachable from `help()` and from an IDE, which is the
    whole point of carrying it through. `comments.doc_block` says the same thing
    from the other side -- HASH has no documentation form, so a generator
    wanting one emits a string literal and says so here.
    """
    if not text:
        return []
    body = text.replace("\\", "\\\\").replace('"""', '\\"\\"\\"')
    lines = body.split("\n")
    if len(lines) == 1:
        return [f'{pad}"""{lines[0]}"""']
    return ([f'{pad}"""{lines[0]}'] + [f"{pad}{l}" if l else "" for l in lines[1:]]
            + [f'{pad}"""'])
