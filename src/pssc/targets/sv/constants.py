"""PSS constants by name in op-model-sv.

The front end folds a reference to a `static const` or an enum item to its
value, and keeps the name it was folded from (`ExprConstant.ref`,
`DataTypeArray.size_ref`). This module decides how op-model-sv SPELLS such a
reference, and declares what it spells:

* An enum item is its enumerator (`item_name`). An SV enum literal is a
  package-scope name and PSS scopes an item by its enum, so it is prefixed
  with the enum's name (`WB_DMA_STATUS_WB_DMA_DONE`), unless
  `--sv-enum-items verbatim`. The package declares every enum whose item the
  code writes (`collect_api_types(type_map=...)`); an item of any other stays
  a number, since there is nothing to name.
* A `static const` is a `localparam` of the generated package, under its PSS
  name (`WB_DMA_MAX_CH`) and its declared type. Two constants of one name in
  different PSS scopes are both qualified with their scope
  (`wb_dma_types_pkg_WB_DMA_MAX_CH`), as is one whose name an enum item of
  the package already takes.

Only the constants the generated code references are declared, and the way
that is known is by asking: a table records every reference spelled through
it. `SvOpModelBackend.constants` renders the package once with a recording
table, then builds the final one from what was recorded.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Set

from zuspec.ir.core.expr import int_literal_type

from ..enum_names import prefixed_item
from .keywords import SV_KEYWORDS


def _split(qname: str):
    head, _, tail = qname.rpartition("::")
    return head, tail


def _strip_pkg(name: Optional[str]) -> Optional[str]:
    return name.split("::")[-1] if name else name


class SvConstants(object):
    """How op-model-sv spells a folded constant, and the declarations of the
    ones it spells by name.

    ``enums`` are the enum types the package declares. ``consts`` maps each
    referenced `static const`'s qualified name to its `ExprConstant`; it is
    None while RECORDING, when every `static const` spelled through the table
    is collected into `used` and spelled as its value.
    """

    def __init__(self, enums: Iterable[object] = (),
                 known_enums: Iterable[object] = (),
                 consts: Optional[Dict[str, object]] = None,
                 enum_items: str = "prefixed",
                 value_structs: Iterable[object] = ()):
        #: `prefixed` or `verbatim` -- `--sv-enum-items`.
        self.enum_items = enum_items
        #: Register value structs, whose members are bit vectors: an enum
        #: field of one is read as an integer (`int_storage`).
        self._value_structs: Set[int] = {id(v) for v in value_structs}
        #: Enums whose name function `%n` calls (`name_fn`).
        self.name_fns: Set[str] = set()
        #: Enum name -> its items: the enums the package DECLARES, and every
        #: enum the model knows (an item of one the package does not declare
        #: has no SV name, and stays a number).
        self._enum_items: Dict[str, Set[str]] = {
            _strip_pkg(e.name): set(e.items) for e in enums}
        self._known_enums: Dict[str, Set[str]] = {
            _strip_pkg(e.name): set(e.items) for e in known_enums}
        self._known_enums.update(self._enum_items)
        self._recording = consts is None
        #: qualified name -> the `ExprConstant` it was first seen as.
        self.used: Dict[str, object] = {}
        self._sized_only: Set[str] = set()
        self._names: Dict[str, str] = (
            {} if consts is None else self._assign_names(consts))
        self._consts = dict(consts or {})

    def final(self) -> "SvConstants":
        """The table that declares what this recording one was asked for."""
        t = SvConstants(consts=self.used)
        t.enum_items, t._value_structs = self.enum_items, self._value_structs
        t.name_fns = set(self.name_fns)
        t._enum_items, t._known_enums = self._enum_items, self._known_enums
        t._names = t._assign_names(self.used)
        return t

    # -- names ---------------------------------------------------------------

    def _assign_names(self, consts: Dict[str, object]) -> Dict[str, str]:
        taken = set()
        for enum, items in self._enum_items.items():
            taken |= {self.item_name(enum, i) for i in items}
        by_name: Dict[str, List[str]] = {}
        for q in consts:
            by_name.setdefault(_split(q)[1], []).append(q)
        names: Dict[str, str] = {}
        for name, qs in by_name.items():
            for q in qs:
                head = _split(q)[0]
                if (len(qs) > 1 or name in taken) and head:
                    names[q] = head.replace("::", "_") + "_" + name
                else:
                    names[q] = name
                if names[q] in SV_KEYWORDS:
                    names[q] += "_"
        return names

    @staticmethod
    def _item_of(table: Dict[str, Set[str]], qname: str) -> bool:
        head, item = _split(qname)
        return bool(head) and item in table.get(_split(head)[1], ())

    # -- spelling ------------------------------------------------------------

    def name_of(self, c) -> Optional[str]:
        """The SV spelling of the folded constant ``c``, or None to spell its
        value."""
        ref = getattr(c, "ref", None)
        if not ref:
            return None
        if self._item_of(self._known_enums, ref):
            if not self._item_of(self._enum_items, ref):
                return None
            head, item = _split(ref)
            return self.item_name(_split(head)[1], item)
        if self._recording:
            if ref not in self.used or ref in self._sized_only:
                self.used[ref] = c
                self._sized_only.discard(ref)
            return None
        return self._names.get(ref)

    def declared(self) -> frozenset:
        """The names the `localparam`s are declared under (none while the
        table is still recording which ones are used)."""
        return frozenset(self._names.values())

    def item_name(self, enum_name: str, item: str) -> str:
        """The enumerator for *item* of the enum named *enum_name* (without
        its package)."""
        name = (item if self.enum_items == "verbatim"
                else prefixed_item(_strip_pkg(enum_name), item))
        return name + "_" if name in SV_KEYWORDS else name

    def first_item(self, dtype) -> Optional[str]:
        """An enum's first item, spelled, when SV's 0 is not it; else None."""
        from ..progseq_model import enum_first_item
        item = enum_first_item(dtype)
        return (self.item_name(dtype.name, item) if item is not None
                else None)

    def int_storage(self, struct_dtype) -> bool:
        """Is *struct_dtype* a register value, whose members are bits?"""
        return id(struct_dtype) in self._value_structs

    def name_fn(self, enum_dtype) -> str:
        """The package function giving an item's PSS name (`%n`), which the
        package then declares (`name_fn_decl`)."""
        n = _strip_pkg(enum_dtype.name)
        self.name_fns.add(n)
        return f"pss_{n}_name"

    def name_fn_decl(self, enum_dtype) -> List[str]:
        """`%n` of an enum prints the PSS item's name (21.1.1), which is
        not the enumerator's when it is prefixed: `.name()` will not do."""
        n = _strip_pkg(enum_dtype.name)
        if n not in self.name_fns:
            return []
        lines = [f"  function automatic string pss_{n}_name(longint v);",
                 "    case (v)"]
        lines += [f'      {int(v)}: return "{k}";'
                  for k, v in enum_dtype.items.items()]
        lines += ['      default: return $sformatf("%0d", v);',
                  "    endcase", "  endfunction"]
        return lines

    def item_clashes(self) -> List[str]:
        """Two declared enums' items with one enumerator, described."""
        by_name: Dict[str, str] = {}
        out: List[str] = []
        for enum in sorted(self._enum_items):
            for item in sorted(self._enum_items[enum]):
                n = self.item_name(enum, item)
                who = f"item '{item}' of enum '{enum}'"
                if n in by_name:
                    out.append(f"'{n}' is both {by_name[n]} and {who}")
                by_name.setdefault(n, who)
        return out

    def size_name(self, dtype) -> Optional[str]:
        """The SV spelling of array type ``dtype``'s size, or None.

        A size carries the constant's name but not its declared type; a
        reference to the same constant from code, which does, replaces it.
        """
        ref = getattr(dtype, "size_ref", None)
        if not ref:
            return None
        if self._recording and ref not in self.used:
            from zuspec.ir.core import ExprConstant
            self.used[ref] = ExprConstant(value=dtype.size, ref=ref)
            self._sized_only.add(ref)
            return None
        return self.name_of(self.used.get(ref) or self._consts.get(ref))

    # -- declarations --------------------------------------------------------

    def declarations(self) -> List[str]:
        """One `localparam` per constant spelled by name, in name order."""
        lines: List[str] = []
        rows = sorted((self._names[q], q) for q in self._consts)
        tw = max((len(_sv_type(self._consts[q])) for _, q in rows), default=0)
        nw = max((len(n) for n, _ in rows), default=0)
        for name, q in rows:
            c = self._consts[q]
            lines.append(f"  localparam {_sv_type(c).ljust(tw)} {name.ljust(nw)}"
                         f" = {_sv_value(c)};  // {q}")
        return lines


def _int_type(c):
    if c.width:
        return c.width, bool(c.signed)
    return int_literal_type(c)


def _sv_type(c) -> str:
    v = c.value
    if isinstance(v, bool):
        return "bit"
    if isinstance(v, str):
        return "string"
    w, signed = _int_type(c)
    if signed and w == 32:
        return "int"
    if signed and w == 64:
        return "longint"
    return f"bit{' signed' if signed else ''} [{w - 1}:0]"


def _sv_value(c) -> str:
    v = c.value
    if isinstance(v, bool):
        return "1'b1" if v else "1'b0"
    if isinstance(v, str):
        return '"%s"' % v.replace("\\", "\\\\").replace('"', '\\"')
    w, signed = _int_type(c)
    if signed and w == 32:
        return str(v)           # an `int`
    if v < 0:
        return f"-{w}'sd{-v}"
    s = "s" if signed else ""
    if v < 256:
        return f"{w}'{s}d{v}"
    return f"{w}'{s}h{v:x}"
