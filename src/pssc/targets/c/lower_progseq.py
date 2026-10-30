"""Lower a PSS component TREE to the C programming API: one handle struct per
component (+ the pssc_bus shim), export-function declarations, the
lifecycle/`init`, and the operation bodies.

Bodies translate the PSS procedural subset 1:1. Unlike the SV backend, C keeps
native value-returning register reads, native ``return`` values, and native
``do...while`` (PSS ``repeat{}while``) -- so the three SV-only rewrites do not
fire here; the body is closer to the PSS source.

THE TREE, not just the root. Every regular component reachable from the root
gets its own struct, its own symbol prefix taken from its TYPE name, its own
register accessors and its own operations; a parent embeds its children BY
VALUE, so one caller-provided object covers the whole tree and there is nothing
to allocate at any level. This file emitted the root alone until C1, which is
why a model like WB DMA -- whose entire per-channel operation surface lives on a
sub-component -- generated a header that compiled cleanly and contained none of
the operations anybody wanted.

Design: docs/design/pss-c-cpp-progseq-gen-design.md (§3.5, §3.6, §3.7),
docs/design/op-model-c-embedded-design.md (§4).
"""
from __future__ import annotations

import contextlib
import dataclasses as dc
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

import zuspec.ir.core as ir

from ..array_literal import is_literal
from ..enum_names import item_of
from ..progseq_model import (
    func_kind, FuncKind, field_is_reg_group, _dt_name, CompKind,
    sub_components, channel_fields, array_base_stride, scalar_offset,
    enum_first_item, field_is_array,
)
from .lower_reg_model import (accessor_base, c_struct_name, _prim_bits,
                             map_type_name)
from .c_names import BUS, HANDLE, RESERVED
from .mem_access import DEFAULT as DEFAULT_MEM, MemAccess
from .style import coerce as _style
from ...diag import Refusal
from ..body_walker import (BodyWalker, CallDispatch, match_values,
                           foreach_parts, assigns_local, in_as_compare,
                           match_needs_chain, match_arm_test,
                           scan_output_locals,
                           scan_write_only)
from ..bit_select import bit_select
from ..call_legality import Ctx
from ..expr_types import (CONTEXT_BINARY, CONTEXT_UNARY, LEFT_TYPED,
                          RELATIONAL, INT, PssType, assign_source_type, is_integral)
from ..int_semantics import IntSemantics, _Val, _br
from .. import group_binding
from ..overridable import overridable
from ..reg_layout import collect_accessors, resolve_access
from ...reg_field_resolve import field_map
from ...reg_rmw import field_writes
from ..group_binding import group_fields
from ..comments import BLOCK, blank_line, comment_lines, doc_block

_DT_STRUCT = "DataTypeStruct"
_DT_INT = "DataTypeInt"
_DT_CHANDLE = "DataTypeChandle"
_DT_ENUM = "DataTypeEnum"
_DT_BOOL = "DataTypeBool"
_DT_ARRAY = "DataTypeArray"
_DT_CHANNEL = "DataTypeChannel"

#: The C type every depth-1 channel lowers to, and its operations. One type for
#: every element type -- see share/c/pssc_chan.h for why the payload is 64 bits.
_CHAN_T = "pssc_chan1_t"

# C reserved words that could collide with PSS identifiers (small, extend as needed).
_C_KEYWORDS = frozenset({
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if", "inline",
    "int", "long", "register", "restrict", "return", "short", "signed", "sizeof",
    "static", "struct", "switch", "typedef", "union", "unsigned", "void",
    "volatile", "while", "bool",
})


def mangle(name: str) -> str:
    """A PSS name as C spells it: a C keyword, or a name the generated code
    takes for itself (`c_names.RESERVED`), gets a trailing `_`."""
    return name + "_" if name in _C_KEYWORDS or name in RESERVED else name


def _c_int_literal(v: int, width: int) -> str:
    """A non-negative constant as the narrowest C literal that holds it: a
    plain `int` up to INT_MAX, `u` to 32 bits, `ull` beyond (or for an
    operation on a value wider than 32 bits)."""
    if width > 32 or v > 0xffffffff:
        return f"0x{v:x}ull"
    if v > 0x7fffffff:
        return f"0x{v:x}u"
    return f"0x{v:x}"


def c_select_read(sel, render) -> str:
    """A bit or part select (`bit_select`) read in C or C++: shifted down and
    masked. The mask is a plain `int` where it fits, so a select of a narrow
    value keeps the `int` an operation on it would have -- an unsigned mask
    would make `x[3] == y` a signed/unsigned comparison."""
    mask = _c_int_literal(sel.mask, sel.base_width)
    base = f"({render(sel.base)})"
    if sel.lo_const == 0:
        return f"({base} & {mask})"
    return f"(({base} >> {_select_lo(sel, render)}) & {mask})"


def c_select_write(sel, render, value: str, where: str) -> str:
    """The whole value after `x[hi:lo] = v` (8.5.x), in C or C++: the
    selected bits replaced by `v`, truncated to the select's width, and the
    rest kept. `x` is read as well as written, so a path with a call in it is
    refused rather than evaluated twice.

    *value* is `v` already rendered as the source of an assignment to the
    select's `bit[N]` (8.7.2, `select_type`): a select wider than `v` widens
    it before it is evaluated."""
    if _has_call(sel.base) or (sel.lo_const is None and _has_call(sel.lo)):
        raise ValueError(
            f"in '{where}': an assignment to a bit select of a value with a "
            f"call in its path would read it twice; assign the call to a "
            f"local first")
    sfx = "ull" if sel.base_width > 32 else "u"
    mask = f"0x{sel.mask:x}{sfx}"
    base = render(sel.base)
    v = f"(({value}) & {mask})"
    if sel.lo_const == 0:
        return f"({base} & ~{mask}) | {v}"
    lo = _select_lo(sel, render)
    return f"({base} & ~({mask} << {lo})) | ({v} << {lo})"


#: A call in rendered C or C++ (a name and its argument list).
_C_CALL = re.compile(r"[A-Za-z_]\w*\s*\(")


def _cmp_decided(op: str, a: _Val, b: _Val) -> Optional[bool]:
    """The result of comparing *a* with *b* if their ranges decide it."""
    op = {"Le": "LtE", "Ge": "GtE", "Ne": "NotEq"}.get(op, op)
    if None in (a.lo, a.hi, b.lo, b.hi):
        return None
    if op == "Lt":
        return True if a.hi < b.lo else False if a.lo >= b.hi else None
    if op == "LtE":
        return True if a.hi <= b.lo else False if a.lo > b.hi else None
    if op == "Gt":
        return True if a.lo > b.hi else False if a.hi <= b.lo else None
    if op == "GtE":
        return True if a.lo >= b.hi else False if a.hi < b.lo else None
    if op == "Eq":
        if a.hi < b.lo or a.lo > b.hi:
            return False
        return True if a.lo == a.hi == b.lo == b.hi else None
    if op == "NotEq":
        if a.hi < b.lo or a.lo > b.hi:
            return True
        return False if a.lo == a.hi == b.lo == b.hi else None
    return None


def handle_from_handle(em, args) -> str:
    """`make_handle_from_handle(h, offset, sub = false)` (21.13.4.2) in C or
    C++, where a handle is its address: `h + offset`, or `h - offset` when
    `sub` is true. The offset is a `bit[64]` parameter, so it is converted to
    that (8.7.2) -- rendered on its own, `0x20 + 4 * i` came out masked to
    32 bits after the add, truncating the address (W33)."""
    h = em.expr(args[0])
    off = (em.expr(args[1]) if _dt_name(args[1]) == "ExprConstant"
           else em.convert_to(args[1], PssType("int", 64, False)))
    off = off if _c_atomic(off) else f"({off})"
    plus, minus = f"({h} + {off})", f"({h} - {off})"
    if len(args) < 3:
        return plus
    sub = args[2]
    if _dt_name(sub) == "ExprConstant" and isinstance(sub.value, (bool, int)):
        return minus if sub.value else plus
    return f"(({em.expr(sub)}) ? {minus} : {plus})"


def select_type(sel):
    """The type a select is assigned as: `bit[N]`, N its width (Table 21)."""
    return PssType("int", sel.width, False)


def _select_lo(sel, render) -> str:
    return (str(sel.lo_const) if sel.lo_const is not None
            else f"({render(sel.lo)})")


def _c_store_exact(t) -> bool:
    """Whether storing into the C (and C++) type of PSS integer type *t*
    converts exactly as PSS does (8.7.2): modulo 2**N into an N-bit type.

    `bit[N]` is stored as `uintK_t`, exact when N is K; `int[N]` as `int` up
    to 32 bits (`int64_t` beyond), so only `int[32]` and `int[64]` are. A
    C++ `bool` (`bit`) is never: it maps 2 to true, where PSS keeps bit 0."""
    if t.signed:
        return t.width in (32, 64)
    return t.width in (8, 16, 32, 64)


#: "Not set", distinct from a stored None.
_UNSET = object()


def _c_carrier(s, t) -> Optional[str]:
    """The C type an operand of PSS type *s* is cast to before an operation
    carried out at *t*, or ``None`` if C's own conversions already do it.

    * Into the 64-bit carrier where *t* is wider than 32 bits: C would carry
      the operation out at 32 bits otherwise, and only then widen.
    * Into `uint32_t` for an unsigned operation wider than 16 bits on an
      operand C would promote to a SIGNED `int` (anything narrower than 32
      bits, a bitfield included): in `int`, `x << 31` is undefined and
      `x * y` can overflow; in `uint32_t` both are PSS's modular result.
    """
    if t.width > 32 and s.width <= 32:
        return "int64_t" if t.signed else "uint64_t"
    if not t.signed and 16 < t.width <= 32 and s.width < 32:
        return "uint32_t"
    return None


def _c_atomic(text: str) -> bool:
    """Whether C *text* can be an operand as it stands: a name, a call, a
    member or element chain, a cast of one, a non-negative number, or
    bracketed whole."""
    if not text or text[0] in "-~!&*":
        return False
    depth = 0
    quote = None
    escaped = False
    for c in text.replace("->", "."):
        if quote:
            if escaped:
                escaped = False
            elif c == "\\":
                escaped = True
            elif c == quote:
                quote = None
        elif c in "'\"":
            quote = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif depth == 0 and c in " +-*/%&|^<>=!~,?:":
            return False
    return True


def _c_unbracket(text: str) -> str:
    """*text* without the one pair of brackets that encloses all of it."""
    if text.startswith("(") and text.endswith(")"):
        depth = 0
        for i, c in enumerate(text):
            depth += c == "("
            depth -= c == ")"
            if depth == 0:
                return text[1:-1] if i == len(text) - 1 else text
    return text


def _same_struct(a, b) -> bool:
    """Whether two `DataTypeStruct`s are one type (by qualified name)."""
    return a is b or getattr(a, "name", None) == getattr(b, "name", 0)


def _has_call(e) -> bool:
    """Whether an IR expression contains a call (and so may not be read twice)."""
    if isinstance(e, (list, tuple)):
        return any(_has_call(x) for x in e)
    if not dc.is_dataclass(e) or isinstance(e, type):
        return False
    if _dt_name(e) == "ExprCall":
        return True
    return any(_has_call(getattr(e, f.name)) for f in dc.fields(e))


#: Characters a C string literal cannot carry raw, and their escapes.
_C_ESCAPES = {
    "\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t",
    "\a": "\\a", "\b": "\\b", "\f": "\\f", "\v": "\\v",
}


def c_string_literal(s: str) -> str:
    """A PSS string constant as a C string literal.

    This used to be `repr(s)`, which is PYTHON's spelling: it quotes with
    apostrophes unless the string contains one. `'abc'` in C is a
    multi-character CHARACTER constant -- implementation-defined int, not a
    `const char *` -- so `message(NONE, "...")` became a call passing an integer
    where a format string belongs. gcc rejects it; a compiler that merely warns
    would print garbage. It survived C0 because the only model bodies that
    reached the emitter had no string in them, and the one string the emitter
    produces itself (`--match-default message`) is written as a C literal here
    in the generator.

    Non-ASCII is escaped byte-wise as UTF-8: a generated header has no
    business assuming the compiler's source or execution character set.
    """
    out = ['"']
    for ch in s:
        if ch in _C_ESCAPES:
            out.append(_C_ESCAPES[ch])
        elif " " <= ch <= "~":
            out.append(ch)
        else:
            out += [f"\\{b:03o}" for b in ch.encode("utf-8")]
    out.append('"')
    return "".join(out)


# --- type mapping ----------------------------------------------------------

def c_enum_name(enum_dtype) -> str:
    """C type name for a PSS enum: its PSS name, ``wb_dma_status_e``.

    Same rule as :func:`c_struct_name`: a PSS type keeps its name.
    """
    n = (getattr(enum_dtype, "name", None) or "").split("::")[-1]
    if not n:
        raise ValueError("enum type has no name; cannot emit a C typedef")
    return n


class CEnums(object):
    """How C spells an enum item: the style's enumerator name
    (`CStylePolicy.enum_item`) for the item the linker resolved
    (`enum_names.item_of`), never a name looked up from a value."""

    def __init__(self, style=None, type_map=None):
        self.style = _style(style)
        self.type_map = type_map or {}

    def item(self, enum_dtype, item: str) -> str:
        fn = getattr(self.style, "enum_item_for", None)
        if fn is not None:
            return fn(enum_dtype, item)
        return self.style.enum_item(c_enum_name(enum_dtype), item)

    def constant(self, e) -> Optional[str]:
        """The enumerator constant *e* is, or None if it is no item."""
        found = item_of(e, self.type_map)
        return self.item(*found) if found is not None else None

    def first(self, dtype) -> Optional[str]:
        """An enum's first item, spelled, when zero-initialisation does not
        give it (`enum_first_item`); else None."""
        name = enum_first_item(dtype)
        return self.item(dtype, name) if name is not None else None


def c_type(dtype) -> str:
    cn = _dt_name(dtype)
    if cn == _DT_INT:
        bits = int(getattr(dtype, "bits", 32) or 32)
        signed = bool(getattr(dtype, "signed", False))
        if signed:
            return "int" if bits <= 32 else "int64_t"
        return f"uint{_prim_bits(bits)}_t"
    if cn == _DT_BOOL:
        # `bool` is `<stdbool.h>`'s, which the generated header includes. Not
        # `int`: a PSS bool is two-valued and C's `_Bool` is the type that says
        # so, and the two differ in what `(bool)2` means.
        return "bool"
    if cn == _DT_ENUM:
        return c_enum_name(dtype)
    if cn == _DT_CHANDLE:
        # `typedef chandle addr_handle_t` -- the typedef name is not in the IR,
        # and the address handle is the only chandle this API can reach.
        return "pssc_addr_t"
    if cn == _DT_STRUCT:
        nm = dtype.name.split("::")[-1]
        # Older stdlibs declared addr_handle_t as a placeholder struct.
        if nm == "addr_handle_t":
            return "pssc_addr_t"
        return c_struct_name(dtype)
    raise ValueError(f"unsupported C type for {cn}")


# --- the tree: nodes and per-component prefixes -----------------------------

def strip_c_suffix(name: str) -> str:
    """``wb_dma_ch_c`` -> ``wb_dma_ch``; also strips a package qualifier."""
    n = (name or "").split("::")[-1]
    return n[:-2] if n.endswith("_c") else n


def regular_nodes(model) -> List[object]:
    """Regular (non-register-group) components, PARENTS FIRST.

    Now a view onto the shared `OpModel` rather than a fourth walk. It kept its
    name because every call site here reads better for it, and its own body
    because the alternative -- every call site saying
    `model.components_root_first` -- would say less.
    """
    return list(model.components_root_first)


def post_order(model) -> List[object]:
    """Regular components, CHILDREN FIRST.

    Two things need this order and neither is cosmetic. A parent embeds its
    children by value, so the child's struct must be a COMPLETE type by the time
    the parent's is declared; and a parent's ``_init`` calls its children's, so
    in ``--header-only`` mode (everything ``static inline``, one file) the
    child's definition has to precede the call.

    Well-defined because the tree has no upward references -- which is not an
    assumption, it is checked: see :func:`_reject_upward_ref`.

    **This used to be `reversed(regular_nodes(...))`, which reverses SIBLINGS
    too.** For a root with children `al` then `be` it produced `[be, al, top]`
    where the SV backend produced `[al, be, top]` -- both correct, neither
    explicable, and nothing could see the difference because no model with two
    sibling component types was under test. `OpModel` is a true post-order and
    is now the one answer.
    """
    return list(model.components)


class Prefixes:
    """Per-component symbol prefix, keyed by component datatype.

    The prefix comes from the component's TYPE name, not from its instance
    path: ``ch[0]`` and ``ch[3]`` share one body and are told apart by the
    handle, which is the entire reason for passing one. The root is the one
    exception -- it keeps the prefix the caller asked for via ``--prefix``,
    which names the files and the functions but not the type.

    It also holds each component's TYPE name (:meth:`type_name`): the PSS name,
    unless ``--prefix-map`` renamed the type.

    Two types that collide after ``_c``-stripping are a generation ERROR rather
    than a last-writer-wins overwrite: the second component's operations would
    be emitted under the first one's names, and C would report it as a
    redefinition somewhere unrelated -- or, for two components with disjoint
    operation sets, not report it at all.
    """

    def __init__(self, model, root_prefix: str,
                 overrides: Optional[Dict[str, str]] = None,
                 language: str = "C", check_prefixes: bool = True):
        #: Named in the collision diagnostic. The C++ backend shares this class
        #: -- the question ("do two component types map to one name?") and its
        #: answer are the same in both languages, and two copies could disagree.
        self._language = language
        self._by_id: Dict[int, str] = {}
        self._types: Dict[int, str] = {}
        overrides = overrides or {}
        used: Dict[str, str] = {}
        used_t: Dict[str, str] = {}
        # Tree components, then the base classes a backend rendering
        # inheritance natively emits though nothing instantiates them
        # (`OpModel.base_classes`; none for a flattening one).
        import types
        nodes = list(regular_nodes(model)) + [
            types.SimpleNamespace(dtype=c)
            for c in (getattr(model, "base_classes", None) or [])]
        for i, node in enumerate(nodes):
            # The QUALIFIED name identifies the type; the short one names it in
            # C and in `--prefix-map`. Comparing the short names here let
            # `p::x_c` and `q::x_c` -- two different components -- look like one
            # and share a prefix, which is precisely the collision this check is
            # for. An override may be given under either spelling.
            qname = getattr(node.dtype, "name", None) or "<anon>"
            tname = qname.split("::")[-1]
            # The TYPE keeps its PSS name unless it was mapped, and a mapped
            # one takes the mapped name as its type too: the map is how two
            # types with one short name are told apart, and it would not tell
            # them apart if it renamed only their functions.
            ty = tname
            if qname in overrides:
                p = ty = overrides[qname]
            elif tname in overrides:
                p = ty = overrides[tname]
            elif i == 0:
                p = root_prefix
            else:
                p = strip_c_suffix(tname)
            if check_prefixes and p in used and used[p] != qname:
                raise ValueError(
                    f"component types '{used[p]}' and '{qname}' both map to the "
                    f"{self._language} symbol prefix '{p}', so their operations "
                    f"would be emitted under the same names. Disambiguate with "
                    f"--prefix-map {qname}=<prefix>.")
            if ty in used_t and used_t[ty] != qname:
                raise ValueError(
                    f"component types '{used_t[ty]}' and '{qname}' are both "
                    f"named '{ty}' in {self._language}. Disambiguate with "
                    f"--prefix-map {qname}=<name>.")
            used[p] = qname
            used_t[ty] = qname
            self._by_id[id(node.dtype)] = p
            self._types[id(node.dtype)] = ty

    def __getitem__(self, dtype) -> str:
        try:
            return self._by_id[id(dtype)]
        except KeyError:
            raise KeyError(
                f"no {self._language} prefix for component "
                f"'{getattr(dtype, 'name', '?')}': it is not part of the walked "
                f"tree, so nothing was emitted for it.") from None

    def __contains__(self, dtype) -> bool:
        return id(dtype) in self._by_id

    def type_name(self, dtype) -> str:
        """The component's type name: its PSS name, or its `--prefix-map`
        name. A style policy may still respell it (`CStylePolicy.type_name`)."""
        self[dtype]          # the same diagnostic for a type not in the tree
        return self._types[id(dtype)]


def parse_prefix_map(spec: Optional[Sequence[str]]) -> Dict[str, str]:
    """``["wb_dma_ch_c=chan"]`` -> ``{"wb_dma_ch_c": "chan"}``."""
    out: Dict[str, str] = {}
    for item in spec or ():
        if "=" not in item:
            raise ValueError(
                f"--prefix-map takes <component-type>=<prefix>, got {item!r}")
        k, v = item.split("=", 1)
        if not k.strip() or not v.strip():
            raise ValueError(
                f"--prefix-map takes <component-type>=<prefix>, got {item!r}")
        out[k.strip()] = v.strip()
    return out


# --- component introspection ----------------------------------------------

# `ctor_names` is passed down from the model rather than read from the ambient
# ContextVar (P6a.T5). The compile's own answer to "which solve function is the
# constructor" belongs to the compile: an emitter that asks the process instead
# gets whichever value was set last, which is right until two compiles share a
# process and then silently wrong -- an `initialize` classified as an ordinary
# operation generates an export function AND drops the base-address binding.

def _operations(comp, ctor_names=None) -> List[object]:
    return [fn for fn in comp.functions
            if func_kind(fn, ctor_names) == FuncKind.EXPORT_OP]


def _solve_operations(comp, ctor_names=None) -> List[object]:
    """The component's solve functions other than its constructor: private
    operations, called from the constructor and from each other only."""
    return [fn for fn in comp.functions
            if func_kind(fn, ctor_names) == FuncKind.EXPORT_SOLVE]


def _ctor(comp, ctor_names=None):
    for fn in comp.functions:
        if func_kind(fn, ctor_names) == FuncKind.CONSTRUCTOR:
            return fn
    return None


def _reg_group_fields(comp) -> Set[str]:
    return {f.name for f in comp.fields if field_is_reg_group(f)}


#: Register methods the C target emits a baked accessor for, and how many
#: arguments each takes. ARITY ONLY -- what the accessor is called belongs to
#: `MemAccess.accessor_suffix`, which both this module and the one that defines
#: the accessors read.
_REG_ACCESSORS = {
    "read": 0,
    "write": 1,
    "read_val": 0,
    "write_val": 1,
    "write_val_masked": 2,
}


# --- handle + shim ---------------------------------------------------------

def data_members(comp) -> List[object]:
    """Component fields that become struct members.

    Excluded, each for its own reason:

      * REGISTER GROUPS have no runtime representation -- a register access
        lowers to a baked accessor that folds the offset, so there is nothing
        to store beyond `base`.
      * SUB-COMPONENTS are handled by the tree lowering, which inlines them.
      * CHANNELS: `try_get`/`try_put` lower to the depth-1 runtime in
        `share/c/pssc_chan.h`; the blocking `get`/`put` are refused by name.

    What is left is the component's actual state -- `num_ch`, `caps`, `chan` --
    and it MUST be here. Without it `self.caps` has nowhere to resolve to, and
    the body emitter's `s->caps` becomes a compile error (which is the good
    case) or, before that fix, a silent read of an unrelated `caps`.
    """
    from ..progseq_model import sub_components, channel_fields
    subs = {s.name for s in sub_components(comp)}
    chans = {f.name for f in channel_fields(comp)}
    out = []
    for f in getattr(comp, "fields", []):
        if f.name in subs or f.name in chans or field_is_reg_group(f):
            continue
        out.append(f)
    return out


def c_declarator(dtype, name: str, what: str = "array") -> str:
    """``T name`` for a value of ``dtype``, arrays (of arrays) included:
    `uint8_t w[2]`, `p_s m[2][3]`.

    C puts the bound after the name, so an array cannot be spelled by
    `c_type()` alone -- which is why this is not one more branch there.
    """
    dims = ""
    dt = dtype
    while _dt_name(dt) == _DT_ARRAY:
        n = _array_size(dt)
        if n is None:
            raise ValueError(
                f"{what} '{name}' has no folded size; C needs a bound and "
                "there is nothing to derive one from.")
        dims += f"[{n}]"
        dt = dt.element_type
    return f"{c_type(dt)} {name}{dims}"


def _member_decl(f) -> str:
    """One struct-member declaration, arrays included."""
    return c_declarator(f.datatype, mangle(f.name), "array member") + ";"


def _array_copy(dst: str, src: str, dtype, pad: str,
                depth: int = 0) -> List[str]:
    """``dst = src`` for arrays of ``dtype``: C assigns no array, so element
    by element, one loop per dimension. Both sides are evaluated once per
    element, so neither may contain a call (the caller refuses one)."""
    if _dt_name(dtype) != _DT_ARRAY:
        return [f"{pad}{dst} = {src};"]
    n = _array_size(dtype)
    i = f"_pssc_i{depth}"
    body = _array_copy(f"{dst}[{i}]", f"{src}[{i}]", dtype.element_type,
                       pad + "    ", depth + 1)
    return ([f"{pad}for (unsigned {i} = 0; {i} < {n}u; {i}++) {{"]
            + body + [f"{pad}}}"])


def _field_defaults(comp, target: str = HANDLE,
                    enums: Optional[CEnums] = None) -> List[str]:
    """PSS field initializers, as assignments in `_init`.

    A default is part of a field's MEANING, not a convenience:
    `wb_dma_ch_caps_s` declares every capability true, and a model that reads
    back all-false silently refuses to attempt the operations those
    capabilities gate -- a driver that reports the device cannot do things it
    can.

    Emitted as assignments rather than as a designated initializer because
    `_init` writes into caller-supplied storage: the caller's struct may be a
    static whose other members are already set, and `*_self = (T){...}` would
    clear them.
    """
    out: List[str] = []
    for f in data_members(comp):
        iv = getattr(f, "initial_value", None)
        if is_literal(iv):
            out += _literal_assigns(f"{target}->{mangle(f.name)}", iv,
                                    "    ", enums)
            continue
        if iv is not None:
            out.append(f"    {target}->{mangle(f.name)} = "
                       f"{_const_expr(iv, enums)};")
            continue
        # A struct-typed attribute carries its defaults on the STRUCT's fields
        # rather than on the instance, so they have to be walked out member by
        # member -- through nested structs and arrays of them alike.
        out += _default_assigns(f"{target}->{mangle(f.name)}", f.datatype,
                                "    ", enums=enums)
    return out


def _literal_assigns(lv: str, lst, pad: str,
                     enums: Optional[CEnums] = None) -> List[str]:
    """A field's array initializer, one assignment per element: its elements
    are constants, and `_init` writes into storage the caller owns."""
    out: List[str] = []
    for i, e in enumerate(lst.elts):
        if is_literal(e):
            out += _literal_assigns(f"{lv}[{i}]", e, pad, enums)
        else:
            out.append(f"{pad}{lv}[{i}] = {_const_expr(e, enums)};")
    return out


def _default_assigns(lv: str, dtype, pad: str, depth: int = 0,
                     enums: Optional[CEnums] = None) -> List[str]:
    """Assignments giving ``lv`` (of type ``dtype``) its PSS defaults: one per
    member that declares an initial value, at any depth, and a loop over an
    array whose elements have some. Nothing for a type whose defaults are all
    zero, which `_init`'s storage already is."""
    cn = _dt_name(dtype)
    if cn == _DT_STRUCT:
        out: List[str] = []
        for sf in getattr(dtype, "fields", []) or []:
            siv = getattr(sf, "initial_value", None)
            if siv is not None:
                out.append(f"{pad}{lv}.{sf.name} = {_const_expr(siv, enums)};")
            else:
                out += _default_assigns(f"{lv}.{sf.name}", sf.datatype, pad,
                                        depth, enums)
        return out
    if cn == _DT_ENUM:
        first = (enums or CEnums()).first(dtype)
        return [f"{pad}{lv} = {first};"] if first else []
    if cn == _DT_ARRAY:
        n = _array_size(dtype)
        i = f"i{depth}"
        body = _default_assigns(f"{lv}[{i}]", dtype.element_type,
                                pad + "    ", depth + 1, enums)
        if not body or not n:
            return []
        return ([f"{pad}for (unsigned {i} = 0; {i} < {n}u; {i}++) {{"]
                + body + [f"{pad}}}"])
    return []


def _default_init(dtype, enums: Optional[CEnums] = None) -> Optional[str]:
    """A C initializer giving a value of ``dtype`` its PSS defaults, or None
    if they are all zero. Designated, so every member it does not name is
    zero (C99 6.7.8): `{.y = 7, .q = {.z = 5}}`."""
    cn = _dt_name(dtype)
    if cn == _DT_STRUCT:
        parts = []
        for sf in getattr(dtype, "fields", []) or []:
            siv = getattr(sf, "initial_value", None)
            sub = (_const_expr(siv, enums) if siv is not None
                   else _default_init(sf.datatype, enums))
            if sub is not None:
                parts.append(f".{sf.name} = {sub}")
        return "{" + ", ".join(parts) + "}" if parts else None
    if cn == _DT_ENUM:
        return (enums or CEnums()).first(dtype)
    if cn == _DT_ARRAY:
        n = _array_size(dtype)
        sub = _default_init(dtype.element_type, enums)
        if sub is None or not n:
            return None
        return "{" + ", ".join([sub] * n) + "}"
    return None


def _const_expr(e, enums: Optional[CEnums] = None) -> str:
    """A field initializer, which is a compile-time constant by construction.
    An enum item is its enumerator when *enums* says how to spell one."""
    cn = _dt_name(e)
    if cn == "ExprList":
        # An array member's initializer (`array<bit[8], 2> d = {3, 4};`).
        return "{" + ", ".join(_const_expr(x, enums) for x in e.elts) + "}"
    if cn == "ExprConstant":
        item = enums.constant(e) if enums is not None else None
        if item is not None:
            return item
        v = e.value
        if isinstance(v, bool):
            return "1" if v else "0"
        return str(v)
    if cn == "ExprRefUnresolved":
        return e.name
    if cn == "ExprAttribute":
        return e.attr           # a package-scope constant
    raise ValueError(f"unsupported field initializer {cn}")


def _chan_member(f) -> str:
    """One channel member, after checking the backend can actually implement it.

    Both rejections name the field, because the alternative is a struct that is
    missing a member the bodies go on to use -- a header that looks complete and
    does not compile, or worse, compiles against something else of that name.
    """
    from ..progseq_model import channel_count, channel_dtype
    dt = channel_dtype(f)
    depth = int(getattr(dt, "depth", 1) or 1)
    if depth != 1:
        raise Refusal(
            f"channel '{f.name}' has depth {depth}; the C target implements "
            f"only depth-1 channels (share/c/pssc_chan.h). A deeper channel is "
            f"a ring buffer, which is a different type per capacity and which "
            f"nothing in scope declares.", f)
    elem = getattr(dt, "element_type", None)
    if elem is not None and _dt_name(elem) not in (_DT_INT, _DT_BOOL, _DT_ENUM):
        raise ValueError(
            f"channel '{f.name}' carries {_dt_name(elem)}; the C target's "
            f"channel payload is a 64-bit integer, so only integral and enum "
            f"element types are supported (share/c/pssc_chan.h).")
    n = channel_count(f)
    return f"{_CHAN_T} {mangle(f.name)}{f'[{n}]' if n is not None else ''};"


def _sub_member_decl(sub, prefixes, style=None) -> str:
    """A sub-component member -- BY VALUE, array bound included.

    By value rather than by pointer because the PSS tree is static
    (elaboration-time), so the C tree is too: one caller-provided object covers
    every level and there is nothing to allocate. See the design's §4.1.
    """
    t = _style(style).type_name(prefixes.type_name(sub.dtype))
    if sub.is_array:
        if sub.size is None or sub.size < 0:
            raise ValueError(
                f"sub-component array '{sub.name}' has no folded size; C needs "
                "a bound and there is nothing to derive one from.")
        return f"{t} {mangle(sub.name)}[{sub.size}];"
    return f"{t} {mangle(sub.name)};"


def emit_handle(node, prefixes, link_style: str = "vtable",
                style=None, reg_map: bool = False) -> str:
    """One component's handle struct.

    Under ``reg_map`` it also carries one typed pointer per register-group
    field, named for the field, so a register access reads the way the PSS
    does: `regs.bank[i].csr` becomes `s->regs->bank[i].csr`. The pointer is
    what makes the access a pointer -- there is no address to pass and nothing
    to fold, because the layout already holds the arithmetic.
    """
    style = _style(style)
    comp = node.dtype
    prefix = prefixes[comp]
    lines = doc_block(getattr(comp, "doc", None), "", BLOCK)
    lines.append(f"typedef struct {style.struct_tag(prefixes.type_name(comp))} {{")
    if link_style == "vtable":
        # Every component carries its own bus pointer, not just the root. A
        # sub-component's register accessors call pssc_bus(s) with ITS handle,
        # and reaching the root's copy would need the parent back-pointer §4.1
        # rules out. One pointer per channel is the cheaper of the two.
        lines.append(f"    const pssc_mem_if *{BUS};")
    for g in group_fields(comp):
        lines.append(f"    pssc_addr_t {style.group_base(g)};")
    if reg_map:
        for f in comp.fields:
            if field_is_reg_group(f):
                lines.append(
                    f"    {map_type_name(f.datatype, style)} *{mangle(f.name)};")
    for f in data_members(comp):
        lines += comment_lines(getattr(f, "doc", None), "    ", BLOCK)
        lines.append(f"    {_member_decl(f)}")
    for f in channel_fields(comp):
        lines.append(f"    {_chan_member(f)}")
    for sub in sub_components(comp):
        lines.append(f"    {_sub_member_decl(sub, prefixes, style)}")
    lines.append(f"}} {style.type_name(prefixes.type_name(comp))};")
    return "\n".join(lines)


def _bus_macro(link_style: str, mem: MemAccess = None,
               style=None, reg_map: bool = False) -> List[str]:
    """``pssc_bus(s)``: the seam's first argument.

    A MACRO rather than a `static inline` function, and the reason is the tree.
    Every component's register accessors call `pssc_bus(s)` with their OWN
    handle type, and C has no overloading -- so the function form worked only
    while the root was the only component that existed. The alternatives were a
    per-component `pssc_bus_<prefix>()` (N spellings of a seam whose whole point
    is that there is one) or this.

    Nothing is lost to type checking: the expansion is a member access under
    `vtable`, so a wrong argument type fails there, and the result is handed
    straight to `pssc_r32(const pssc_mem_if *, ...)`, which checks it again.
    """
    style = _style(style)
    if reg_map:
        # Nothing expands it. Under the layout struct every register access is
        # a pointer and every descriptor access is a cast pointer, so there is
        # no call left that wants a bus handle -- and a macro nothing uses is a
        # macro a reader of this header has to rule out.
        return []
    if style.overrides_bus(link_style):
        # A style that renders its own accesses never expands this, and a
        # macro nothing uses is a macro a reader has to rule out.
        return []
    macro = (mem or style.mem_access()).bus_macro
    lines = [
        f"/* {macro}(s): the seam's first argument -- "
        f"the ONLY line varying by style. */"]
    if link_style == "vtable":
        lines.append(f"#define {macro}(s) ((s)->{BUS})")
    else:
        lines.append(f"#define {macro}(s) ((void)(s), (const void *)0)")
    return lines


def _sub_accessors(node, prefixes, style=None) -> List[str]:
    """``wb_dma_ch(wb_dma_t *s, unsigned i)`` + ``WB_DMA_CH_COUNT``.

    The count is emitted as a macro because a caller needs it in a `for` bound
    and in an array size, and because it is the number the model states -- the
    alternative is firmware repeating a 4 the generator already knows.
    """
    style = _style(style)
    comp = node.dtype
    parent = prefixes[comp]
    parent_t = style.type_name(prefixes.type_name(comp))
    out: List[str] = []
    for sub in sub_components(comp):
        name = style.symbol(parent, mangle(sub.name))
        sub_t = style.type_name(prefixes.type_name(sub.dtype))
        if sub.is_array:
            out.append(
                f"#define {style.macro(parent, mangle(sub.name) + '_COUNT')} "
                f"{sub.size}u")
            out.append(
                f"static inline {sub_t} *{name}({parent_t} *{HANDLE}, unsigned i) "
                f"{{ return &{HANDLE}->{mangle(sub.name)}[i]; }}")
        else:
            out.append(
                f"static inline {sub_t} *{name}({parent_t} *{HANDLE}) "
                f"{{ return &{HANDLE}->{mangle(sub.name)}; }}")
    return out


def lower_handles(model, prefixes, link_style: str = "vtable",
                  style=None, reg_map: bool = False) -> str:
    """Every component's handle, the bus shim, and the sub-component accessors.

    Structs in post-order (children first): a parent embeds its children by
    value, so their types must already be complete. The accessors come after ALL
    the structs, since each names two of them.
    """
    lines = ["/* ----- Component handles. ----- */"]
    for node in post_order(model):
        lines.append(emit_handle(node, prefixes, link_style, style, reg_map))
        lines.append("")
    lines += _bus_macro(link_style, style=style, reg_map=reg_map)
    acc: List[str] = []
    for node in regular_nodes(model):
        acc += _sub_accessors(node, prefixes, style)
    if acc:
        lines.append("")
        lines.append("/* ----- Sub-component access. ----- */")
        lines += acc
    return "\n".join(lines)


# --- signatures ------------------------------------------------------------

def by_handle_params(fn) -> Set[str]:
    """The parameters of *fn* passed BY HANDLE: every struct parameter not
    declared `const` (LRM 20.3.2; user ruling). The callee's writes are the
    caller's, so C passes a pointer; a `const` one is read-only and is
    passed by value, which lets a caller pass any value, a call's included.
    """
    const = set((getattr(fn, "metadata", None) or {}).get("const_params", ()))
    return {a.arg for a in fn.args.args
            if _dt_name(a.annotation) == "DataTypeStruct"
            and a.arg not in const}


def _op_params(fn) -> str:
    handle = by_handle_params(fn)
    parts = [f"{c_type(a.annotation)} {'*' if a.arg in handle else ''}"
             f"{mangle(a.arg)}" for a in fn.args.args]
    return (", " + ", ".join(parts)) if parts else ""


def _op_signature(fn, prefix: str, type_name: str, qual: str = "",
                  style=None) -> str:
    style = _style(style)
    ret = c_type(fn.returns) if fn.returns is not None else "void"
    return (f"{qual}{ret} {style.symbol(prefix, mangle(fn.name))}"
            f"({style.type_name(type_name)} *{HANDLE}{_op_params(fn)})")


#: One C signature: ``(return type, function name, [(type, parameter)])``.
#: A pointer type ends in `*` (`"wb_dma_c *"`) and is joined to its name
#: with no space, as the generated code has always spelled one.
Signature = Tuple[str, str, List[Tuple[str, str]]]


def op_signature_parts(fn, ctx: "OpCtx",
                       handle: Optional[str] = HANDLE) -> Signature:
    """`fn`'s C signature, in parts: the default `COpModelBackend.signature`.

    ``handle`` is the component handle's parameter name, first; `None`
    leaves it out (`COpModelBackend.handle_param`).
    """
    style = _style(ctx.be_kw.get("style"))
    ret = c_type(fn.returns) if fn.returns is not None else "void"
    params: List[Tuple[str, str]] = []
    if handle is not None:
        params.append((f"{style.type_name(ctx.type_name)} *", handle))
    by_handle = by_handle_params(fn)
    for a in fn.args.args:
        ct = c_type(a.annotation)
        params.append((f"{ct} *" if a.arg in by_handle else ct,
                       mangle(a.arg)))
    return ret, style.symbol(ctx.prefix, mangle(fn.name)), params


def render_signature(sig: Signature, qual: str = "") -> str:
    """``<qual><ret> <name>(<params>)``; no parameters is `(void)`."""
    ret, name, params = sig
    plist = ", ".join(f"{ct}{'' if ct.endswith('*') else ' '}{pn}"
                      for ct, pn in params)
    return f"{qual}{ret} {name}({plist or 'void'})"


#: What `linkage` may answer.
LINKAGES = ("extern", "static")


class DefaultOpApi:
    """The three questions an operation's C API is built from, answered as
    the target always has: every operation takes the handle, is named
    `<prefix>_<name>`, and has external linkage.

    `COpModelBackend` answers them through its `signature`, `handle_param`
    and `linkage` members, which default to these; the lowering only ever
    asks an `OpCtx.api`, so a backend subclass reaches every place an
    operation is declared, defined, called or named (`c_names`) at once.
    """

    def handle_param(self, fn, ctx: "OpCtx") -> Optional[str]:
        return HANDLE

    def signature(self, fn, ctx: "OpCtx") -> Signature:
        return op_signature_parts(fn, ctx, self.handle_param(fn, ctx))

    def linkage(self, fn, ctx: "OpCtx") -> str:
        return "extern"


DEFAULT_API = DefaultOpApi()


def _linkage(fn, ctx: "OpCtx") -> str:
    out = ctx.op_api.linkage(fn, ctx)
    if out not in LINKAGES:
        raise ValueError(f"linkage() for '{fn.name}' returned {out!r}; "
                         f"expected one of {', '.join(LINKAGES)}")
    return out


#: The qualifier of a `static` operation. `inline` as well, as a solve
#: function's is: an operation nothing in its own file calls would otherwise
#: fail `-Werror=unused-function` (W45).
STATIC_QUAL = "static inline "


# --- the import surface (C4.1) ---------------------------------------------

def import_map(ctx) -> Dict[str, object]:
    """Declared `import target/solve function`s, by PSS name.

    Package-scope only, which is where the front end surfaces them
    (`AstToIrContext.import_functions`). A component-scope `import function`
    declaration does not reach the IR at all today, so it cannot be honoured
    here -- see the note in :func:`lower_imports`.
    """
    return {f.name: f for f in getattr(ctx, "import_functions", None) or []}


def _import_signature(fn) -> str:
    ret = c_type(fn.returns) if fn.returns is not None else "void"
    parts = [f"{c_type(a.annotation)} {mangle(a.arg)}" for a in fn.args.args]
    return f"{ret} {mangle(fn.name)}({', '.join(parts) or 'void'})"


def lower_imports(imports: Dict[str, object], yield_mode: str = "none") -> str:
    """Prototypes for what the PLATFORM supplies, under their PSS names.

    THE NAME IS THE MODEL'S, not a table's. `pssc_r32` and friends are hard-coded
    because they are the memory seam -- fixed, and the same for every model. An
    `import target function void plat_delay_us(int)` is the opposite: it is
    whatever this model declared, so its C spelling is its PSS name and adding
    one is a model edit rather than a generator edit. That property is the whole
    point of C4.1, and `test_a_custom_import_needs_no_generator_change` is what
    holds it.

    Every DECLARED import gets a prototype, not only every called one: the set
    is the platform's contract, and a platform that implements one function too
    many pays nothing, while one that discovers a requirement at link time pays
    a rebuild. This matches what the SV projection puts in `import_api_if`.

    `is_solve` vs `is_target` does not survive here, and correctly: the
    distinction is about WHEN a function runs relative to solving, and this
    target has no solver (HAVE_RUNTIME_SOLVER=false). Both are plain C calls.

    `--yield import` adds `yield_()`: that mode lowers a PSS `yield` to a call
    on the platform, and a call with no prototype is an implicit declaration.
    C++ declares it on its import interface, and SV on `import_api_if`.
    """
    lines = []
    if imports:
        lines += ["/* ----- Imported functions: supplied by the PLATFORM. ----- */",
                  "/* Declared by the model with `import target/solve function`;"
                  " named here exactly as */",
                  "/* the model names them. Implement each one, or the link fails"
                  " with the PSS name. */"]
        for name in sorted(imports):
            lines.append(f"{_import_signature(imports[name])};")
    if yield_mode == "import":
        lines += ["/* `--yield import`: what a PSS `yield` costs on this platform"
                  " (a WFI, a */",
                  "/* watchdog kick, a delay). Supplied by the PLATFORM. */",
                  "void yield_(void);"]
    return "\n".join(lines)


def _create_params(ctor, link_style: str) -> str:
    parts = []
    if link_style == "vtable":
        parts.append(f"const pssc_mem_if *{BUS}")
    if ctor is not None:
        parts += [f"{c_type(a.annotation)} {mangle(a.arg)}" for a in ctor.args.args]
    return ", ".join(parts)


# --- body translation ------------------------------------------------------

_BINOP = {
    "Add": "+", "Sub": "-", "Minus": "-", "Mult": "*", "Mul": "*",
    "Div": "/", "Mod": "%", "Eq": "==", "NotEq": "!=", "Ne": "!=",
    "Lt": "<", "LtE": "<=", "Le": "<=", "Gt": ">", "GtE": ">=", "Ge": ">=",
    "And": "&&", "Or": "||", "BitAnd": "&", "BitOr": "|", "BitXor": "^",
    "LShift": "<<", "Shl": "<<", "RShift": ">>", "Shr": ">>",
}


_UNOP = {
    "Not": "!", "LogNot": "!",
    "Invert": "~", "BitNot": "~", "Neg": "-", "USub": "-", "Minus": "-",
    "UAdd": "+", "Plus": "+",
}

#: The memory primitives PSS declares on an address space, as (direction,
#: width). What each one is SPELLED as belongs to the funnel in
#: `mem_access.py`, so that a style changing the seam changes these too -- they
#: reach the same memory as a register accessor does, by the same route.
_MEM_PRIMS = {
    "read8": ("read", 8), "read16": ("read", 16),
    "read32": ("read", 32), "read64": ("read", 64),
    "write8": ("write", 8), "write16": ("write", 16),
    "write32": ("write", 32), "write64": ("write", 64),
}

#: PSS built-ins the C target CLAIMS a rendering for. This set is the target's
#: half of the contract in `targets/call_legality.py`: the registry says which
#: names a target claims, and every claimed name must be rendered HERE. A name
#: claimed there with no rendering here falls through to verbatim emission,
#: which is the exact defect the registry exists to prevent.
C_BUILTINS = frozenset({
    "message", "print", "error", "fatal",
    "make_handle_from_handle", "addr_value",
}) | frozenset(_MEM_PRIMS)


def with_defaults(args, fn) -> list:
    """A call's arguments, with each one it leaves out replaced by its
    parameter's default (20.2.5; W6). C has no default arguments, and C++'s
    do not mix with the virtual operations, so the default is written at
    the call. A default that refers to anything is refused rather than
    evaluated where the call is."""
    args = list(args)
    params = list(getattr(getattr(fn, "args", None), "args", None) or ())
    defaults = list(getattr(getattr(fn, "args", None), "defaults", None) or ())
    first = len(params) - len(defaults)
    for i in range(len(args), len(params)):
        if i < first:
            break
        d = defaults[i - first]
        if any(_dt_name(n) in ("ExprRefLocal", "ExprAttribute", "ExprCall",
                               "TypeExprRefSelf")
               for n in _walk_nodes(d)):
            raise ValueError(
                f"the default of '{params[i].arg}' in '{fn.name}' refers to "
                f"something; only a constant default is written at a call")
        args.append(d)
    return args


def _walk_nodes(node):
    if isinstance(node, (list, tuple)):
        for n in node:
            yield from _walk_nodes(n)
        return
    if not dc.is_dataclass(node) or isinstance(node, type):
        return
    yield node
    for f in dc.fields(node):
        yield from _walk_nodes(getattr(node, f.name, None))


def _prefixed(kind: str, rest) -> list:
    """``rest`` (a format and its arguments) with the format prefixed
    ``<kind>: ``, for `_report_call`."""
    if not rest or _dt_name(rest[0]) != "ExprConstant" or \
            not isinstance(getattr(rest[0], "value", None), str):
        raise ValueError(f"'{kind}' needs a string literal as its format")
    return [ir.ExprConstant(value=f"{kind}: {rest[0].value}")] + list(rest[1:])


def _array_size(dtype) -> Optional[int]:
    """Folded element count of an array type, or ``None`` if unresolved.

    ``None`` matters: the IR has carried ``size: -1`` for an array whose bound
    is a package constant, and a lowering that treated that as a number would
    emit `for (i = 0; i < -1; i++)`.
    """
    if dtype is None or _dt_name(dtype) != "DataTypeArray":
        return None
    n = getattr(dtype, "size", None)
    try:
        n = int(n)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


def _builtin_name(func) -> Optional[str]:
    """The built-in a call targets, or ``None``.

    Recognises a bare `message(...)` (`ExprRefUnresolved`) and a qualified
    `pkg.message(...)` / `self.read32(...)` (`ExprAttribute`) alike -- which
    spelling reaches the IR depends on the imports in force at the call site,
    and a lowering that handled only one would work file by file.
    """
    cn = _dt_name(func)
    if cn == "ExprRefUnresolved":
        # `std_pkg::message` is `message` (`validate_calls.callee_name`).
        from ..validate_calls import callee_name
        return callee_name(func)
    if cn == "ExprAttribute":
        return func.attr
    return None


class CIntSemantics(IntSemantics):
    """`IntSemantics` spelled in C -- and in C++, whose integer rules are C's.

    Every operation is carried out at its PSS type (8.7). C's own types are
    the trap twice over: an operand narrower than `int` is PROMOTED to a
    signed `int` before any operator sees it (`~` of a `uint8_t` 1 is -2,
    and `(~p) == q` over `bit[4]` compares ints), and an operation on two
    `uint32_t`s is carried out in 32 bits however wide its context (a
    `bit[32]` inverted for a 64-bit register loses its upper half). So an
    operand is widened to the carrier its PSS type needs (`_c_carrier`), and
    a result is wrapped back into range where it may have left it.

    The emitter supplies `expr`, `types`, `fn`, and `_final` (a dict).
    """

    def int_type_name(self, ct: str) -> str:
        """A fixed-width integer type's spelling (`uint32_t`)."""
        return ct

    def int_cast_type(self, dtype) -> str:
        """The type a cast the model wrote is spelled as."""
        return c_type(dtype)

    def int_cast_text(self, ct: str, text: str, atom: bool) -> str:
        """*text* converted to the type spelled *ct*."""
        return f"({ct}){text if atom else f'({text})'}"

    def int_atomic(self, text: str) -> bool:
        return _c_atomic(text)

    def int_const(self, c: int, t, hexa: bool = False) -> _Val:
        t = t.as_int() if t is not None else None
        wide = t is not None and t.width > 32
        if c < 0:
            if c == -(1 << 63):
                text = "(-9223372036854775807ll - 1)"
            elif c == -(1 << 31) and not wide:
                text = "(-2147483647 - 1)"
            else:
                text = f"{c}ll" if wide else str(c)
            return _Val(text, c, c, atom=text.startswith("("), const=c)
        text = f"0x{c:x}" if hexa else str(c)
        if wide:
            text += "ll" if t.signed else "ull"
        elif c > 0x7fffffff:
            text += "u"
        return _Val(text, c, c, atom=True, const=c)

    def int_mask_text(self, v: _Val, hi: int) -> str:
        return f"{_br(v)} & {_c_int_literal(hi, 0)}"

    def int_sint_text(self, v: _Val, width: int) -> str:
        # Two's complement into a signed type is modular in every compiler
        # this targets (and C23 makes it so); `>>` of a negative value is
        # arithmetic in the same ones.
        cw = 32 if width <= 32 else 64
        sh = cw - width
        it = self.int_type_name(f"int{cw}_t")
        ut = self.int_type_name(f"uint{cw}_t")
        if sh == 0:
            return f"(({it})({ut})({v.text}))"
        return f"(({it})(({ut})({v.text}) << {sh}) >> {sh})"

    def int_ternary_text(self, test: str, a: _Val, b: _Val) -> str:
        return f"(({test}) ? {_br(a)} : {_br(b)})"

    def int_binop_text(self, op: str, a: _Val, b: _Val) -> str:
        return f"{_br(a)} {_BINOP[op]} {_br(b)}"

    #: The seam's integer power (`pssc_env.h`; C++ overrides it).
    POW_FN = "pssc_pow"

    def _int_pow(self, e, t) -> _Val:
        """`a ** b` (8.5.1): the left operand's type, the exponent
        self-determined. Folded when both are constant; otherwise the seam's
        `pssc_pow`, whose 64-bit result is wrapped to the type like any
        other value of unknown range (E4)."""
        from ..py.pss_int import _pss_pow
        a, b = self._int_opnd(e.lhs), self._int_self(e.rhs)
        if a.const is not None and b.const is not None:
            try:
                v = _pss_pow(a.const, b.const, t.width)
            except ZeroDivisionError:
                raise ValueError(
                    f"in '{getattr(self.fn, 'name', '?')}': 0 ** a negative "
                    f"exponent is undefined (LRM 8.5.1, Table 13)") from None
            if t.signed and v >> (t.width - 1):
                v -= 1 << t.width
            return self.int_const(v, t)
        return _Val(f"{self.POW_FN}((unsigned long long)({a.text}), "
                    f"(long long)({b.text}))", None, None)

    def int_shift_text(self, op: str, a: _Val, b: _Val, t, e) -> str:
        """C's shift by the width of the (promoted) left operand or more is
        undefined -- x86 masks the count, so `1 << 33` was 2. PSS's is 0, or
        a signed `>>`'s sign (ruling R1). The width C shifts in is 32, or 64
        where the operation is carried out in 64 bits (`_c_carrier`)."""
        cw = 64 if t.width > 32 else 32
        plain = self.int_binop_text(op, a, b)
        arith = op == "RShift" and t.signed
        if b.lo is not None and b.hi is not None and 0 <= b.lo and b.hi < cw:
            return plain
        if b.const is not None:
            if arith:
                return f"{_br(a)} >> {cw - 1}"
            return f"((void)({a.text}), 0)" if _has_call(e.lhs) else "0"
        if _has_call(e.rhs):
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': the amount of "
                f"`{self.expr(e)}` contains a call, and a shift whose amount "
                f"may reach the width is guarded by testing the amount "
                f"first, which would call it twice. Read the amount into a "
                f"local first.")
        # Compared unsigned: a negative amount is as far out of range.
        big = f"(uint64_t)({b.text}) >= {cw}u"
        if arith:
            return f"{_br(a)} >> ({big} ? {cw - 1} : {_br(b)})"
        return f"({big} ? 0 : {_br(a)} {_BINOP[op]} {_br(b)})"

    def int_signed_div_text(self, op: str, a: _Val, b: _Val) -> str:
        """C's `/` and `%` truncate toward zero, which is PSS's (8.5.1). But
        the most negative value of C's type divided by -1 overflows it, which
        C leaves undefined (x86 traps), where PSS wraps: -2**31 / -1 is
        -2**31 and the remainder 0 (W29). Where that pair can occur, a 32-bit
        dividend is divided in 64 bits -- the quotient is then wrapped like
        any value outside its type -- and a 64-bit one is tested for -1."""
        plain = f"({_br(a)} {_BINOP[op]} {_br(b)})"
        if b.lo is not None and b.hi is not None and not b.lo <= -1 <= b.hi:
            return plain
        if a.lo is not None and a.lo > -(1 << 63) and (
                a.lo > -(1 << 31) or a.lo < -(1 << 31)):
            return plain
        if a.lo == -(1 << 31) and a.hi is not None and a.hi < (1 << 31):
            wide = self.int_cast_text(self.int_type_name("int64_t"),
                                      a.text, a.atom)
            return f"({wide} {_BINOP[op]} {_br(b)})"
        if _C_CALL.search(b.text):
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': a 64-bit signed "
                f"`{_BINOP[op]}` whose divisor may be -1 tests the divisor "
                f"first, so the divisor would be evaluated twice, and it "
                f"contains a call. Read it into a local first.")
        if op == "Mod":
            return f"({_br(b)} == -1 ? 0 : {plain})"
        neg = self.int_cast_text(
            self.int_type_name("int64_t"),
            f"0ull - {self.int_cast_text(self.int_type_name('uint64_t'), a.text, a.atom)}",
            False)
        return f"({_br(b)} == -1 ? {neg} : {plain})"

    def int_cmp_text(self, op: str, a: _Val, b: _Val) -> str:
        """A comparison whose result the operands' ranges decide is that
        result: `-x < 0` of an unsigned x is false, and C's
        `-Werror=type-limits` refuses to compile it spelled out (W30). An
        operand with a call is still evaluated."""
        known = _cmp_decided(op, a, b)
        if known is None:
            return f"{_br(a)} {_BINOP[op]} {_br(b)}"
        side = [x.text for x in (a, b) if _C_CALL.search(x.text)]
        if not side:
            return str(int(known))
        return ("(" + ", ".join(f"(void)({t})" for t in side)
                + f", {int(known)})")

    def int_leaf_text(self, e, s, t) -> str:
        """A primary, in the C type its operation is carried out in
        (`_c_carrier`). The whole expression is left as it is: C converts it
        to its target -- an assignment, an argument, a comparison's other
        side -- with nothing in between to overflow."""
        text = self.expr(e)
        if e is getattr(self, "_int_root_e", None):
            return text
        ct = _c_carrier(s, t.as_int())
        if ct is not None:
            return self.int_cast_text(self.int_type_name(ct), text,
                                      _c_atomic(text))
        return text

    def _int_leaf(self, e, ring: bool = False) -> _Val:
        """A cast the model wrote, as an OPERAND, keeps its C type: the shared
        rule folds `(bit[32])f` into the converted value of `f`, which is
        right about the value and wrong about C's type for it -- a one-bit
        field is promoted to `int`, and `<< 31` on an `int` is undefined."""
        v = IntSemantics._int_leaf(self, e, ring)
        if _dt_name(e) != "ExprCast" or v.const is not None \
                or e is getattr(self, "_int_root_e", None):
            return v
        own = self.types.type_of(e)
        own = own.as_int() if is_integral(own) else None
        ct = _c_carrier(PssType("int", 1, False), self._final[id(e)].as_int())
        if ct is not None:
            ct = self.int_type_name(ct)
        elif own is not None and own.width > 16:
            ct = self.int_cast_type(e.target_type)
        if ct is None:
            return v
        return dc.replace(v, text=self.int_cast_text(ct, v.text, v.atom),
                          atom=True)

    def _int_root(self, e, final, ring: bool = False) -> _Val:
        saved = getattr(self, "_int_root_e", None)
        self._int_root_e = e
        try:
            return IntSemantics._int_root(self, e, final, ring)
        finally:
            self._int_root_e = saved

    def convert_to(self, e, target) -> str:
        """*e* in an assignment-like context whose target type is *target*
        (8.7.2). Where the C store holds exactly the target's width -- a
        `uint32_t` for a `bit[32]` (`_c_store_exact`) -- the store itself is
        the conversion, so only the evaluation width is the source's
        concern."""
        if _dt_name(e) == "ExprStructLiteral" and target is not None \
                and target.kind == "struct":
            return self.struct_literal_text(e, self.types.resolve(target.dtype))
        s = self.types.type_of(e)
        if target is None or target.kind != "int" or not is_integral(s):
            return self.expr(e)
        if _dt_name(e) == "ExprConstant" and isinstance(e.value, int) \
                and not isinstance(e.value, bool):
            # A constant the target cannot hold is folded here: C would
            # wrap it too, and warn (`-Woverflow`), and C++ refuses one in
            # braces. `bit[8] x = 300;` is 44 (8.7.2).
            bits, v = target.width, e.value
            folded = v & ((1 << bits) - 1)
            if target.signed and folded >> (bits - 1):
                folded -= 1 << bits
            if folded != v:
                e = dc.replace(e, value=folded, width=0, signed=None)
                s = self.types.type_of(e)
        if _c_store_exact(target.as_int()):
            return self._int_root(e, assign_source_type(target, s),
                                  ring=True).text
        return IntSemantics.convert_to(self, e, target)


class _LazyArgs:
    """`LoweredRegAccess.args`: a tuple, or a no-argument callable that
    renders it, run on the first read and kept. A composed masked write that
    sets `RegAccess.fields` never reads the packed value and mask, so they
    are never rendered (and cannot refuse)."""

    def __set_name__(self, owner, name):
        self.slot = "_" + name + "_v"

    def __get__(self, obj, owner=None):
        if obj is None:
            # No class-level default: the dataclass field stays required.
            raise AttributeError(self.slot)
        v = obj.__dict__[self.slot]
        if callable(v):
            v = tuple(v())
            object.__setattr__(obj, self.slot, v)
        return v

    def __set__(self, obj, v):
        object.__setattr__(obj, self.slot, v)


@dc.dataclass(frozen=True)
class LoweredRegAccess:
    """A `reg_layout.RegAccess` in C: what `CBodyEmitter.reg_access` and the
    masked-write primitives spell it from."""
    #: The C accessor record (`lower_reg_model._Acc`): stem, value type,
    #: transaction width, and ``layout``, the `RegAccessor`.
    acc: Any
    #: Each of the access's ``index_exprs``, rendered.
    indices: Tuple[str, ...]
    #: The arguments, rendered and converted to the register's width.
    #: Rendered on first read; `dc.replace(lw, args=(...))` sets them.
    args: Tuple[str, ...] = _LazyArgs()
    #: The member path of the sub-components crossed (`sub[i].inner`), or
    #: None for a register of this component's own group.
    sub_path: Optional[str] = None
    #: The emitter's own path, for its default rendering. Not published.
    _chain: Any = dc.field(default=None, compare=False, repr=False)


def _c_word(v: int, bits: int) -> str:
    """An unsigned constant of a ``bits``-wide register word."""
    return f"0x{v:X}{'u' if bits <= 32 else 'ull'}"


class CBodyEmitter(CIntSemantics, CallDispatch, BodyWalker):
    """Translate one operation body to C lines.

    The walk, the comment attachment and the two scans are
    `targets/body_walker.py`'s; everything here is the C rendering. A node kind
    is handled by a hook named after it -- `StmtForeach` -> `stmt_foreach` --
    so what this class contains is exactly the list of constructs the C target
    lowers, with no cascade to read past.

    A CALL is dispatched on its `Disposition` from `call_legality.py`, the same
    table the legality gate consults -- see `CallDispatch`.
    """

    indent = "    "
    comment_style = BLOCK

    legality_target = "op-model-c"
    call_context = Ctx.TARGET

    #: "statement" when the bodies were sequenced
    #: (`OpModelTarget.reg_read_form`): a read that is a declaration's or a
    #: local assignment's whole value is rendered by `reg_read_into`, never
    #: as an initializer or a right-hand side. Set by the backend.
    reg_read_form = "expression"

    #: Whether a composed masked write may set a PART of a field through
    #: `reg_value_field_set` (its ``mask`` then is not all of ``fs.mask``).
    #: False: such a write is spelled by `reg_value_merge` from its mask and
    #: value instead (`reg_rmw.field_writes`). The default hook masks.
    partial_field_writes = True

    def __init__(self, fn, comp, prefix: str, reg_style: str = "bitfields",
                 yield_mode: str = "none", match_default: str = "message",
                 message_style: str = "import", handle: str = HANDLE,
                 prefixes=None, link_style: str = "vtable", imports=None,
                 mem: MemAccess = None, style=None, accs=None,
                 ctor_names=None, reg_map: bool = False, ctx=None):
        self.fn = fn
        #: The compile context, for `ExprTypes` (the type map, and the
        #: functions a call's result type comes from).
        self.ctx = ctx
        #: `IntSemantics`' per-root final types.
        self._final = {}
        #: Address registers by following the layout struct. See `_reg_path`.
        self.reg_map = reg_map
        #: This compile's constructor names, for the one call site that has
        #: only a name to classify (`ch[i].initialize(...)`).
        self.ctor_names = ctor_names
        #: Naming and layout. A body spells the same symbols the prototypes do
        #: -- a sibling call, a sub-component's `_init` -- so it consults the
        #: same policy rather than rebuilding the name from the prefix.
        self.style = _style(style)
        #: How an enum item is spelled: the style's enumerator name.
        self.enums = CEnums(self.style, getattr(ctx, "type_map", None))
        #: `{accessor stem: _Acc}`, from `lower_reg_model.accessor_map`.
        self.accs = dict(accs or {})
        #: Every memory access this body emits goes through here -- register
        #: accessor calls included, so that they agree with the definitions
        #: `lower_reg_model` emits from the same object.
        self.mem = mem or self.style.mem_access()
        self.comp = comp
        self.prefix = prefix
        self.reg_style = reg_style
        self.yield_mode = yield_mode
        self.match_default = match_default
        self.message_style = message_style
        #: Name of the handle parameter in the emitted signature: `HANDLE`,
        #: for an operation and `_init` alike. `None` when the operation
        #: has none (`COpModelBackend.handle_param`); reading `h` is then a
        #: refusal, since only component state is reached through it.
        self.h = handle
        #: The `OpCtx` this body is rendered in, set by `OpCtx.body_emitter`;
        #: None for a body rendered outside one (a constructor).
        self.op_ctx = None
        self.prefixes = prefixes
        self.link_style = link_style
        #: Declared package-scope imports, by PSS name. A call to one lowers to
        #: a bare call under that same name -- see :func:`lower_imports`.
        self.imports = dict(imports or {})
        #: Operations of this component, so a call to a sibling operation is
        #: recognised as one and gets the handle threaded through.
        self.model_ops = {f.name for f in comp.functions}
        #: A by-handle parameter is a pointer; the body reads it as `(*v)`.
        handle = by_handle_params(fn)
        self.arg_rename = {a.arg: (f"(*{mangle(a.arg)})" if a.arg in handle
                                   else mangle(a.arg)) for a in fn.args.args}
        self.arg_names = set(self.arg_rename)
        self.reg_fields = _reg_group_fields(comp)
        self.reg_groups = {f.name: f.datatype for f in getattr(comp, "fields", [])
                           if field_is_reg_group(f)}
        self.chan_fields = {f.name for f in channel_fields(comp)}
        self.subs = {s.name: s for s in sub_components(comp)}
        #: Locals used as the OUTPUT of a channel `try_get`. They are declared
        #: as `uint64_t` rather than their PSS width, because `try_get` takes a
        #: pointer and the channel payload is 64 bits -- passing `&tok` where
        #: `tok` is a `uint8_t` would be a type error at best and an
        #: out-of-bounds write at worst. See `_chan_call`.
        self.chan_out_locals: Set[str] = scan_output_locals(
            fn.body, lambda n: self._is_chan_method(n, "try_get"),
            what="channel try_get()")
        #: Locals the model assigns and never reads, stated as `(void)x;`.
        self.write_only_locals: Set[str] = scan_write_only(fn.body)
        #: Every data member of the component. `self.<name>` for one of these
        #: is `s-><name>`; anything else is a package-scope constant and keeps
        #: its bare spelling. Getting this wrong is SILENT -- see `expr`.
        self.comp_fields = {f.name for f in getattr(comp, "fields", [])}
        # struct-typed locals (name -> C value-union type), for --reg-style
        # accessors field get/set rewriting.
        self.struct_locals: Dict[str, str] = {}
        for s in fn.body:
            if _dt_name(s) == "StmtAnnAssign" and _dt_name(s.annotation) == _DT_STRUCT:
                nm = s.annotation.name.split("::")[-1]
                if nm != "addr_handle_t":
                    self.struct_locals[s.target.name] = c_type(s.annotation)

    @property
    def h(self) -> str:
        if self._h is None:
            raise Refusal(
                f"'{getattr(self.fn, 'name', '?')}' is generated with no "
                f"component handle (handle_param), but this reaches the "
                f"component's state, which only the handle can")
        return self._h

    @h.setter
    def h(self, value: Optional[str]) -> None:
        self._h = value

    def callee_ctx(self, comp, prefix: str) -> "OpCtx":
        """The `OpCtx` of an operation of ``comp``, as a call reaches it."""
        ctx = self.op_ctx
        if ctx is None:
            # A body rendered outside any backend (a test's emitter): the
            # default answers.
            tn = (self.prefixes.type_name(comp) if self.prefixes is not None
                  else getattr(comp, "name", ""))
            return OpCtx(comp=comp, prefix=prefix, qual="", emitter_cls=None,
                         be_kw={"style": self.style}, type_name=tn)
        if comp is ctx.comp:
            return ctx
        return ctx.for_comp(comp, prefix, self.prefixes.type_name(comp))

    @staticmethod
    def callee_handle(callee, ctx: "OpCtx") -> bool:
        """Whether a call to ``callee`` passes a handle: whether its own
        `handle_param` names one."""
        return ctx.op_api.handle_param(callee, ctx) is not None

    @overridable(since='0.3', stability='provisional')
    def model_call(self, callee, args: List[str], ctx: "OpCtx",
                   handle: Optional[str]) -> str:
        """A call to ``callee``, an operation of this component or of a
        sub-component, as a C expression.

        ``args`` are the PSS arguments, already rendered and converted to
        their parameters' types (a by-handle struct as a pointer). ``ctx``
        is the CALLEE's `OpCtx`: its component, prefix and the backend's
        answers. ``handle`` is the expression for the callee's handle
        (`_self`, `&_self->sub[i]`), or None when the callee takes none
        (its `handle_param` is None). The default calls the name the
        callee's `signature` gives, with the handle first; an override that
        changes a `signature`'s parameters must pass what they now are.
        """
        name = ctx.op_api.signature(callee, ctx)[1]
        return f"{name}(" + ", ".join(([handle] if handle else []) + args) + ")"

    # channels ---------------------------------------------------------------

    def _chan_name(self, call) -> Optional[str]:
        """The channel field a call is made on, or ``None``."""
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.chan_fields:
            return None
        return chain[0][0]

    def _chan_ref(self, call) -> str:
        """`&_self->ch`, or `&_self->ch[i]` for an element of an array of
        channels (E10)."""
        name, idx = self._chain(call.func.value)[0]
        ref = f"&{self.h}->{mangle(name)}"
        return ref if idx is None else f"{ref}[{self.expr(idx)}]"

    def _is_chan_method(self, call, method: str) -> bool:
        return (_dt_name(getattr(call, "func", None)) == "ExprAttribute"
                and call.func.attr == method
                and self._chan_name(call) is not None)

    def _chan_call(self, call) -> Optional[str]:
        """`inflight.try_get(tok)` / `wake.try_put(1)` -> the pssc_chan1 runtime.

        Blocking `get()`/`put()` are REJECTED by name rather than lowered. They
        are the only two channel operations that need a scheduler, this target
        has none, and a `get()` that returned whatever was in the struct would
        report a completion nobody signalled. A model reaches here having
        already been told, by HAVE_EVENT_WAIT=false, that it cannot wait on an
        event -- so a blocking call surviving into the C means the model asked
        anyway.
        """
        name = self._chan_name(call)
        if name is None:
            return None
        ref = self._chan_ref(call)
        m = call.func.attr
        args = call.args
        if m == "try_put":
            if len(args) != 1:
                raise ValueError("channel try_put() takes one argument")
            return f"pssc_chan1_try_put({ref}, {self.expr(args[0])})"
        if m == "try_get":
            return f"pssc_chan1_try_get({ref}, &{self.expr(args[0])})"
        if m in ("get", "put"):
            raise ValueError(
                f"'{name}.{m}()' is a BLOCKING channel operation, and the C "
                f"target has no scheduler to suspend to (HAVE_EVENT_WAIT is "
                f"false for this target -- see targets/target_cfg.py). Use "
                f"try_{m}(), or guard the call with "
                f"`compile if (target_cfg_pkg::HAVE_EVENT_WAIT)` as "
                f"src/pss/wb_dma_ch_c/functions/wait_hint.pss does.")
        raise ValueError(
            f"unsupported channel method '{m}' on '{name}'. The C channel "
            f"runtime implements try_put/try_get (share/c/pssc_chan.h).")

    def _struct_field(self, e):
        """If ``e`` is ``<struct-local>.<FIELD>``, return (ctype, local, field);
        else None. Used only in --reg-style accessors mode."""
        if _dt_name(e) != "ExprAttribute" or _dt_name(e.value) != "ExprRefLocal":
            return None
        local = e.value.name
        ct = self.struct_locals.get(local)
        if ct is None:
            return None
        return ct, local, e.attr

    # register access chain -------------------------------------------------

    def _chain(self, e):
        """Flatten self.<reg-group>...<reg>[idx] to [[name, index|None], ...],
        rooted at self; None if not a self-rooted access path."""
        cn = _dt_name(e)
        if cn == "TypeExprRefSelf":
            return []
        if cn == "ExprAttribute":
            b = self._chain(e.value)
            if b is None:
                return None
            b.append([e.attr, None])
            return b
        if cn == "ExprSubscript":
            b = self._chain(e.value)
            if not b:
                return None
            b[-1][1] = e.slice
            return b
        return None

    def _reg_call(self, call) -> Optional[str]:
        """If ``call`` is a register access, return the baked-accessor call.

        ``None`` means "not a register access" and lets the generic call path
        have it. An *unrecognised method on a register* is not that: it raises.

        The order matters, and it is the fix for a real trap. This used to test
        the method name first and return ``None`` for anything outside
        ``("read", "write")`` -- so `regs.csr.write_val(x)` fell through to the
        generic path and emitted `regs.csr.write_val(x)`, C that names a struct
        field which does not exist... except that it does compile wherever a
        matching name happens to be in scope. Silence was the bug; the accessor
        set being short was only the occasion for it.
        """
        parts = self._reg_parts(call)
        return None if parts is None else self.reg_access(*parts)

    def _reg_parts(self, call):
        """`_reg_call`'s recognition: ``(RegAccess, LoweredRegAccess)``, or
        None when ``call`` is not a register access."""
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        # A register lives inside a register group, so its path is at least
        # `<group>.<reg>`. A one-element chain is a call on the GROUP itself
        # (`regs.set_handle(...)`), which is not a register access and must not
        # be judged as one.
        prefix, sub_path = self.prefix, None
        if chain and chain[0][0] in self.subs:
            # `s.a.STS.write_val(1)`: a register of a sub-component, reached
            # through ITS accessor and ITS handle -- the accessor already adds
            # that component's group base, so nothing is folded here.
            sub_path, prefix, comp, chain = self._sub_walk(chain)
            if not chain or len(chain) < 2 or \
                    chain[0][0] not in _reg_group_fields(comp):
                return None
        elif not chain or len(chain) < 2 or chain[0][0] not in self.reg_fields:
            return None

        reg = chain[-1][0]
        segs = [c[0] for c in chain[:-1]]
        idx = [c[1] for c in chain if c[1] is not None]
        base = accessor_base(prefix, segs, reg, self.style)
        indices = tuple(self.expr(i) for i in idx)
        # The register itself, from the one walk that says which registers
        # exist (`reg_layout.collect_accessors`, every component's, via
        # `accessor_map`). There is no fallback: guessing "untyped, 32 bits"
        # for a register the walk did not surface is what dropped a value
        # struct's `.raw` and called accessors nothing defined. A reserved
        # member is refused in the gate (`reserved_access`), so a miss here
        # is the compiler's bug.
        if func.attr in _REG_ACCESSORS:
            acc = self.accs.get(base)
            if acc is None:
                raise ValueError(
                    f"internal error: register '{'.'.join(segs + [reg])}' "
                    f"(accessor stem '{base}') is accessed in a body, but "
                    f"the register walk surfaced no such register")
            # A value written is converted to the register's width, like
            # any argument to a `bit[N]` parameter; a value struct is passed
            # whole.
            val_t = PssType("int", acc.value_bits, False)
            want = _REG_ACCESSORS[func.attr]
            if len(call.args) != want:
                raise ValueError(
                    f"register method '{func.attr}' takes {want} argument(s), "
                    f"got {len(call.args)}")
            ra = resolve_access(self.comp, call,
                                getattr(self.ctx, "type_map", None),
                                accessors=self._accessors_of)
            if ra is None:
                raise ValueError(
                    f"internal error: '{'.'.join(segs + [reg])}' is a "
                    f"register access to the C emitter and not to "
                    f"reg_layout.resolve_access")
            return ra, LoweredRegAccess(acc=acc, indices=indices,
                                        args=lambda: [self.convert_to(a, val_t)
                                                      for a in call.args],
                                        sub_path=sub_path,
                                        _chain=chain)

        raise ValueError(
            f"unsupported register method '{func.attr}' on '{'.'.join(segs + [reg])}'. "
            f"The C target emits a baked accessor per register method; a method "
            f"it does not know would otherwise become a generic call that "
            f"compiles and writes the wrong thing. Known: "
            f"{', '.join(sorted(_REG_ACCESSORS))}.")

    def _accessors_of(self, comp):
        """`collect_accessors`, once per component per body."""
        memo = self.__dict__.setdefault("_acc_memo", {})
        if id(comp) not in memo:
            memo[id(comp)] = (comp, collect_accessors(comp))
        return memo[id(comp)][1]

    # -- register access: the published hooks -------------------------------
    #
    # `reg_access` is every register access as an expression. A masked
    # write is a STATEMENT, and its shape is pssc's (ruling R-H8): the
    # default is one call (`reg_masked_write_inline`); returning None from
    # that composes it from three primitives -- read into a local, set each
    # field (or merge the mask), write the local -- which an override
    # spells. The read is written by pssc, so it cannot be dropped.

    @overridable(since='0.3', stability='provisional')
    def reg_access(self, ra, lw: "LoweredRegAccess") -> str:
        """One register access, as a C expression.

        ``ra`` is the `reg_layout.RegAccess`: the register (its
        `RegAccessor`: path, offset, strides, width, access mode), the
        sub-components crossed, the group, the method and, for a reduced
        masked write, the fields its source named. ``lw`` is the same access
        in C: the `_Acc` (``acc``: accessor stem and value type), each index
        and argument already rendered, the arguments converted to the
        register's width (`write_val_masked`: mask, then value), and
        ``sub_path``, the member path of the sub-components crossed. The
        default is pssc's own: a baked accessor, or under `--c-reg-map` the
        layout struct's member and a `pssc_read<N>`-family primitive.
        """
        if self.reg_map:
            base_expr, base_is_ptr = ((f"{self.h}->{lw.sub_path}", False)
                                      if lw.sub_path else (None, True))
            return self._reg_map_access(lw._chain, ra.method, list(lw.args),
                                        lw.acc, base_expr, base_is_ptr)
        handle = f"&{self.h}->{lw.sub_path}" if lw.sub_path else self.h
        idx_args = "".join(f", {i}" for i in lw.indices)
        return self._reg_access(lw.acc.base, ra.method, idx_args,
                                list(lw.args), handle)

    @overridable(since='0.3', stability='provisional')
    def reg_masked_write_inline(self, ra, lw: "LoweredRegAccess"
                                ) -> Optional[str]:
        """A masked write (`write_val_masked`, which every field-wise write
        reduces to) as ONE expression statement, or None to compose it from
        `reg_read_into`, `reg_value_field_set` / `reg_value_merge` and
        `reg_write_from` into a local of `reg_value_local`. The default
        is `reg_access`'s single call."""
        return self.reg_access(ra, lw)

    @overridable(since='0.3', stability='provisional')
    def reg_value_local(self, ra, lw: "LoweredRegAccess") -> Tuple[str, str]:
        """``(C type, name)`` of the local a composed masked write reads the
        register into. Declared where `reg_value_local_scope` says: by
        default once, at the top of the function, where two composed writes
        asking for one name share the local when the types agree. The
        default is the register's transaction width."""
        return f"uint{lw.acc.prim}_t", "_pss_reg"

    @overridable(since='0.3', stability='provisional')
    def reg_value_local_scope(self, ra, lw: "LoweredRegAccess") -> str:
        """Where a composed masked write declares its locals (the
        `reg_value_local` word and the `_pss_fv<i>` field-value temps):
        ``"function"``, once at the top of the function (the default), or
        ``"block"``, in a ``{ }`` block of its own around the write's
        statements, so each write's locals end with it. A block's locals
        shadow any of the function's of the same name, which a field value
        of the write must then not name."""
        return "function"

    @overridable(since='0.3', stability='provisional')
    def reg_read_into(self, ra, lw: "LoweredRegAccess", local: str) -> str:
        """The statement (without its `;`) that reads the register into
        ``local``: the first step of a composed masked write, and, under
        ``reg_read_form = "statement"``, every read the model makes. It
        must read (PSS 3.1 §21.14.1). ``ra.method`` is the read's (`read`,
        `read_val`); for a composed write it is the write's, and the read
        is a `read_val`. The default is ``local = <read>``."""
        if ra.method not in ("read", "read_val"):
            ra, lw = (dc.replace(ra, method="read_val", args=()),
                      dc.replace(lw, args=()))
        return f"{local} = {self.reg_access(ra, lw)}"

    def _read_into(self, local: str, value, target, pad: str
                   ) -> Optional[List[str]]:
        """``local = value`` as `reg_read_into` when ``value`` is a whole
        register read and the reads are statements; else None. A read the
        target type converts is read into the register's own local first."""
        if self.reg_read_form != "statement" or _dt_name(value) != "ExprCall":
            return None
        parts = self._reg_parts(value)
        if parts is None or parts[0].method not in ("read", "read_val"):
            return None
        rd, conv = self.reg_access(*parts), self.convert_to(value, target)
        if conv == rd:
            return [f"{pad}{self.reg_read_into(*parts, local)};"]
        if conv.count(rd) != 1:
            raise ValueError(f"internal error: the conversion of '{rd}' "
                             f"to '{local}' is '{conv}'")
        t = self.function_local(*self.reg_value_local(*parts))
        return [f"{pad}{self.reg_read_into(*parts, t)};",
                f"{pad}{local} = {conv.replace(rd, t)};"]

    def _read_word(self, s, name: str, pad: str) -> Optional[List[str]]:
        """The sequencing pass's local for a value-struct `read()`
        (`effect_seq.TEMP`) when the target spells that read as the
        register's word (`reg_access` gives `read()` and `read_val()` one
        spelling, so it has no value struct in C): held as the word,
        declared as `reg_value_local`'s type and read by `reg_read_into`,
        so a field of it is `reg_value_field_get` -- as it is of the read
        in expression form. None for any other declaration, which is then
        a local of the value struct."""
        from ..effect_seq import TEMP
        if self.reg_read_form != "statement" or \
                not getattr(s, TEMP, False) or \
                _dt_name(s.value) != "ExprCall":
            return None
        parts = self._reg_parts(s.value)
        if parts is None or parts[0].method != "read" or \
                parts[0].accessor.value_struct is None:
            return None
        rd = self.reg_access(*parts)
        if rd != self.reg_access(dc.replace(parts[0], method="read_val"),
                                 parts[1]):
            return None
        whole = self.convert_to(s.value, self.types.of_datatype(s.annotation))
        if whole.count(rd) != 1:
            raise ValueError(f"internal error: the conversion of '{rd}' "
                             f"to '{name}' is '{whole}'")
        self._words = getattr(self, "_words", {})
        self._words[name] = (parts, whole.replace(rd, name))
        return [f"{pad}{self.reg_value_local(*parts)[0]} {name};",
                f"{pad}{self.reg_read_into(*parts, name)};"]

    def _word(self, e):
        """``(parts, whole)`` when ``e`` names a `_read_word` local or a
        word-held value local (`_value_local`)."""
        if _dt_name(e) != "ExprRefLocal":
            return None
        vl = self._value_local(e)
        if vl is not None:
            return vl, self.arg_rename.get(e.name) or mangle(e.name)
        return getattr(self, "_words", {}).get(
            self.arg_rename.get(e.name) or mangle(e.name))

    def _word_spelled(self, parts) -> bool:
        """Whether the target spells this register's `read()` as its word:
        `reg_access` gives `read()` and `read_val()` one spelling."""
        ra, lw = (dc.replace(parts[0], args=()), dc.replace(parts[1], args=()))
        return ra.accessor.value_struct is not None and \
            self.reg_access(dc.replace(ra, method="read"), lw) == \
            self.reg_access(dc.replace(ra, method="read_val"), lw)

    def _value_local(self, e):
        """The binding access ``(RegAccess, LoweredRegAccess)`` when ``e``
        names a local of a register's value struct that the target holds as
        the register's word (`_word_spelled`), else None. Bound once per
        function by `reg_value_locals.bind`, on first use (the indices
        render through the emitter); only its accessor and layout may be
        relied on."""
        if _dt_name(e) != "ExprRefLocal":
            return None
        if "_value_locals" not in self.__dict__:
            self._value_locals = self._bind_value_locals()
        lb = self._value_locals.get(e.name)
        return None if lb is None else lb.access

    def _bind_value_locals(self):
        if self.reg_read_form != "statement":
            return {}
        from ..effect_seq import TEMP
        from ..ir_walk import walk
        from ..reg_value_locals import bind

        def access_of(call):
            f = call.func
            if _dt_name(f) != "ExprAttribute" or f.attr not in ("read",
                                                                 "write"):
                return None
            parts = self._reg_parts(call)
            return None if parts is None else (parts, f.attr)

        # The value structs whose registers the body reads or writes and
        # the target spells as words: a local of one is word-held.
        words = []
        for n in walk(self.fn.body):
            if _dt_name(n) == "ExprCall":
                acc = access_of(n)
                if acc is not None and self._word_spelled(acc[0]):
                    words.append(acc[0][0].accessor.value_struct)

        def candidate(s) -> bool:
            return not getattr(s, TEMP, False) and \
                _dt_name(s.annotation) == _DT_STRUCT and \
                any(_same_struct(s.annotation, w) for w in words)

        def same_layout(a, b) -> bool:
            return a[0].accessor.value_struct is b[0].accessor.value_struct \
                and a[1].acc.prim == b[1].acc.prim
        return bind(self.fn.body, access_of, candidate, same_layout)

    @overridable(since='0.3', stability='provisional')
    def reg_value_field_set(self, local: str, fs, value: str, ra,
                            lw: "LoweredRegAccess", mask: int) -> str:
        """The statement (without its `;`) that sets field ``fs`` (a
        `FieldSlice`) of ``local`` to ``value``: one per field the source
        named (`ra.fields`), in source order. ``value`` is as the model
        wrote it, NOT narrowed to the field; ``mask`` is the register bits
        written for this field (all of ``fs.mask`` unless the source masked
        it), and only those may change."""
        ct = self.reg_value_local(ra, lw)[0]
        bits = lw.acc.prim
        return (f"{local} = ({local} & ~{_c_word(mask, bits)}) | "
                f"((({ct})({value}) << {fs.lsb}) & {_c_word(mask, bits)})")

    @overridable(since='0.3', stability='provisional')
    def reg_value_merge(self, local: str, mask: str, val: str, ra,
                        lw: "LoweredRegAccess") -> str:
        """The modify step (without its `;`) when the source named no field
        (`ra.fields` is None): ``(local & ~mask) | (val & mask)``. ``mask``
        and ``val`` are rendered and converted to the register's width."""
        return f"{local} = ({local} & ~({mask})) | (({val}) & ({mask}))"

    @overridable(since='0.3', stability='provisional')
    def reg_write_from(self, ra, lw: "LoweredRegAccess", local: str) -> str:
        """The statement (without its `;`) that writes ``local`` to the
        register: the last step of a composed masked write. The default is
        `reg_access` of a `write_val`."""
        return self.reg_access(dc.replace(ra, method="write_val", args=()),
                               dc.replace(lw, args=(local,)))

    @overridable(since='0.3', stability='provisional')
    def reg_value_field_get(self, value: str, fs, ra,
                            lw: "LoweredRegAccess") -> str:
        """Field ``fs`` (a `FieldSlice`) of a register's value read whole
        (`regs.r.read().f`), as an expression. ``value`` is the read,
        rendered by `reg_access`. The default names the value union's
        member, `<value>.f`."""
        return f"{value}.{fs.name}"

    def function_local(self, ctype: str, name: str) -> str:
        """Declare a local at the top of the function being rendered, once,
        and return its name; a name already taken with another type gets a
        suffix."""
        locs = self.__dict__.setdefault("_fn_locals", {})
        base, n = name, 1
        while name in locs and locs[name] != ctype:
            name = f"{base}_{n}"
            n += 1
        locs[name] = ctype
        return name

    def function_local_lines(self, ind: int) -> List[str]:
        """The declarations `function_local` gathered, for the top of the
        function body."""
        pad = self.pad(ind)
        return [f"{pad}{ct} {nm};"
                for nm, ct in self.__dict__.get("_fn_locals", {}).items()]

    def _composed_masked_write(self, ra, lw, ind: int) -> List[str]:
        scope = self.reg_value_local_scope(ra, lw)
        if scope not in ("function", "block"):
            raise ValueError(f"reg_value_local_scope gave '{scope}', not "
                             f"'function' or 'block'")
        decls = []                      # the block's, in declaration order

        def local(ctype: str, nm: str) -> str:
            if scope == "function":
                return self.function_local(ctype, nm)
            decls.append(f"{ctype} {nm}")
            return nm

        ct, name = self.reg_value_local(ra, lw)
        t = local(ct, name)
        fields = field_writes(ra.fields, self.partial_field_writes)
        # A field value with a call runs before the register is read, in
        # source order, as the model's one write would have run it: each is
        # first a temp at the field's width (the width it is written at).
        out, vals = [], []
        for i, fw in enumerate(fields or ()):
            if not _has_call(fw.value):
                vals.append(self.expr(fw.value))
                continue
            ft = PssType("int", fw.slice.width, False)
            v = local(f"uint{_prim_bits(fw.slice.width)}_t", f"_pss_fv{i}")
            out.append(f"{v} = {self.convert_to(fw.value, ft)}")
            vals.append(v)
        out.append(self.reg_read_into(ra, lw, t))
        if fields:
            for fw, val in zip(fields, vals):
                out.append(self.reg_value_field_set(
                    t, fw.slice, val, ra, lw, fw.mask))
        else:
            out.append(self.reg_value_merge(t, lw.args[0], lw.args[1], ra, lw))
        out.append(self.reg_write_from(ra, lw, t))
        pad = self.pad(ind)
        if scope == "function":
            return [f"{pad}{x};" for x in out]
        inner = self.pad(ind + 1)
        return [f"{pad}{{"] + [f"{inner}{x};" for x in decls + out] + \
            [f"{pad}}}"]

    def _reg_path(self, chain, base_expr: Optional[str] = None,
                 base_is_ptr: bool = True) -> str:
        """`s->regs->bank[3].csr` for the PSS path `regs.bank[3].csr`.

        The PSS access path and the C member path are THE SAME PATH -- which is
        the whole reason the layout struct is worth having. Nothing is folded
        here and no offset appears: the struct states where each register is
        and the compiler does the arithmetic, so this walk only has to spell
        the hops.

        ``base_expr``/``base_is_ptr`` are the hop ``chain`` starts from -- the
        root's own handle (a pointer, the default) or a sub-component reached
        by value through `_through_subs` (`s->sub[i].inner`, not a pointer:
        the sub-component is embedded, not allocated). Only the FIRST hop
        depends on this; everything after it is a member of a struct reached
        by value, hence `->` once and `.` thereafter. An index makes the
        element a value, so a subscripted first hop drops straight to `.`.
        """
        base = self.h if base_expr is None else base_expr
        name, idx = chain[0]
        out = f"{base}{'->' if base_is_ptr else '.'}{mangle(name)}"
        deref = True                      # the next hop crosses the pointer
        if idx is not None:
            out += f"[{self.expr(idx)}]"
            deref = False
        for nm, ix in chain[1:]:
            out += ("->" if deref else ".") + nm
            deref = False
            if ix is not None:
                out += f"[{self.expr(ix)}]"
        return out

    def _reg_map_access(self, chain, method: str, args, acc,
                        base_expr: Optional[str] = None,
                        base_is_ptr: bool = True) -> str:
        """One register access, addressed through the layout struct.

        The primitives take a pointer and nothing else -- no seam that uses the
        layout struct has a bus handle, and the address is the member's, so the
        whole access is the member expression and a width. What the access
        then does (a `volatile` store, a call to the platform) is the seam's,
        decided in pssc_mem_reg.h. ``acc`` is looked up by the caller
        (which already has the right component's prefix in hand for a
        sub-component access) rather than re-derived here.

        The masked write calls `pssc_write_masked<N>` -- still §21.14.1's
        read-modify-write, now a named primitive instead of being spelled out
        at every call site. The read is still part of the definition, not an
        optimisation: on a register whose read clears its status bits,
        dropping it changes what the device does.
        """
        prim = acc.prim
        # A register whose value is a struct is carried as its value union;
        # the primitives move a plain word, so the union is opened here. The
        # `_val` spellings are already raw and must NOT be opened again.
        typed = acc.is_struct
        path = self._reg_path(chain, base_expr, base_is_ptr)
        if acc.strided:
            # An element of a register array whose stride exceeds its width
            # is a struct spanning the stride, the register its `v` member
            # (`lower_reg_model._member_lines`; one predicate decides both,
            # `reg_layout.is_strided`).
            path += ".v"
        raw = f"&{path}"
        if method == "read":
            rd = self.mem.reg_ptr_read(prim, raw)
            # A compound literal, because this is an EXPRESSION: the accessor
            # that used to do `v.raw = ...; return v;` had a function body to
            # do it in, and there is no function here any more.
            return f"({acc.c_type}){{ .raw = {rd} }}" if typed else rd
        if method == "read_val":
            return self.mem.reg_ptr_read(prim, raw)
        if method in ("write", "write_val"):
            v = f"({args[0]}).raw" if (typed and method == "write") else args[0]
            return self.mem.reg_ptr_write(prim, raw, v)
        mask, val = args[0], args[1]
        return self.mem.reg_ptr_masked_write(prim, raw, mask, val)

    def _through_subs(self, chain):
        """Follow the sub-component hops at the front of *chain*:
        ``(handle, prefix, component, rest)``, the handle being
        `&s->sub[i].inner` for the component the rest of the path is in."""
        path, prefix, comp, rest = self._sub_walk(chain)
        return ((f"&{self.h}->{path}" if path else self.h), prefix, comp,
                rest)

    def _sub_walk(self, chain):
        """`_through_subs` with the member path in place of the handle
        (`sub[i].inner`, or None for none): the hops, without reading it."""
        comp, prefix, path, k = self.comp, self.prefix, None, 0
        subs = self.subs
        while k < len(chain) and chain[k][0] in subs:
            sub = subs[chain[k][0]]
            if self.prefixes is None:
                raise ValueError("a sub-component access needs the prefix map")
            m = mangle(sub.name)
            if chain[k][1] is not None:
                m += f"[{self.expr(chain[k][1])}]"
            path = m if path is None else f"{path}.{m}"
            comp = sub.dtype
            prefix = self.prefixes[comp]
            subs = {s.name: s for s in sub_components(comp)}
            k += 1
        return path, prefix, comp, chain[k:]

    def _reg_access(self, base: str, method: str, idx_args: str,
                    args, handle: Optional[str] = None) -> str:
        """One register access, through the funnel.

        The `_Acc` is looked up rather than reconstructed because a policy
        rendering a house macro needs the register itself -- its access mode,
        its width, its folded offset -- and none of that survives the
        underscore-joined stem. `_reg_call` has already refused a stem with
        no entry: the map holds every component's registers (`accessor_map`),
        sub-components' included.
        """
        h = handle or self.h
        acc = self.accs[base]
        if method in ("read", "read_val"):
            return self.mem.reg_read(acc, h, idx_args,
                                     raw=method.endswith("_val"))
        if method in ("write", "write_val"):
            return self.mem.reg_write(acc, h, idx_args, args[0],
                                      raw=method.endswith("_val"))
        return self.mem.reg_masked_write(acc, h, idx_args, args[0], args[1])

    # expressions -----------------------------------------------------------

    def _operand(self, e) -> str:
        """An operand of a binary expression, parenthesised if it is one too.

        The IR tree already says how the expression groups; C precedence only
        happens to agree. Where it does not, the generated code is wrong, and
        where it does, gcc still refuses `a & b | c` under
        ``-Wparentheses -Werror`` -- which the C build tests use. Printing the
        tree's own structure settles both, and costs a pair of brackets.
        """
        s = self.expr(e)
        return f"({s})" if _dt_name(e) == "ExprBin" else s

    # -- expressions, one hook per node kind ---------------------------------

    def expr_in(self, e) -> str:
        """`v in [...]` (8.5.9), as the comparisons it means (E3)."""
        return self.expr(in_as_compare(e, self.types,
                                       getattr(self.fn, "name", "?")))

    def expr_null(self, e) -> str:
        """`null` (7.7): the empty chandle. A chandle is an address on this
        target, as `addr_handle_t` (a `typedef chandle`) is, so it is 0 (E12)."""
        return "0"

    def expr_constant(self, e) -> str:
        item = self.enums.constant(e)
        if item is not None:
            return item
        v = e.value
        if isinstance(v, bool):
            return "1" if v else "0"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, str):
            return c_string_literal(v)
        raise ValueError(
            f"unsupported constant of type {type(v).__name__}")

    def expr_ref_local(self, e) -> str:
        # A local is renamed as a parameter is: `int char;` does not compile.
        word = self._word(e)
        if word is not None:
            return word[1]      # a register word, used whole
        return self.arg_rename.get(e.name) or mangle(e.name)

    def expr_attribute(self, e) -> str:
        base = e.value
        if _dt_name(base) == "TypeExprRefSelf":
            # A COMPONENT DATA MEMBER reaches C through the handle. This
            # arm used to return the bare attribute name for everything,
            # which is the one defect in this emitter that no compiler
            # catches: `caps.ars` became `caps.ars`, and C happily resolves
            # that against any `caps` in scope -- an unrelated file-scope
            # object, or a same-named parameter. The generated driver then
            # reads the wrong memory and reports it as device behaviour.
            # Hence the behavioural gate (a register write trace) rather
            # than a compile gate: -Werror sees nothing wrong here.
            # A parameter shadows a field of the same name: the front end
            # spells both `self.a`, so scope order decides (as `ExprTypes`).
            if e.attr in self.arg_names:
                return self.arg_rename[e.attr]
            if e.attr in self.comp_fields:
                return f"{self.h}->{mangle(e.attr)}"
            # Not a member: a package-scope `static const`, which is a
            # plain identifier in C too.
            return e.attr
        if self.reg_style == "accessors":
            sf = self._struct_field(e)
            if sf is not None:
                ct, local, field = sf
                return f"{ct}_{field}_get({local})"
        word = self._word(base)
        if word is not None:
            parts = word[0]
            tm = getattr(self.ctx, "type_map", None) or {}
            fs = field_map(parts[0].accessor.dtype, tm.get).get(e.attr)
            if fs is not None:
                return self.reg_value_field_get(
                    self.arg_rename.get(base.name) or mangle(base.name), fs,
                    *parts)
        if _dt_name(base) == "ExprCall" and \
                _dt_name(base.func) == "ExprAttribute" and \
                base.func.attr == "read":
            parts = self._reg_parts(base)
            tm = getattr(self.ctx, "type_map", None) or {}
            fs = (field_map(parts[0].accessor.dtype, tm.get).get(e.attr)
                  if parts is not None else None)
            if fs is not None:
                return self.reg_value_field_get(self.reg_access(*parts), fs,
                                                *parts)
        text = self.expr(base)
        if text.startswith("(*") and text.endswith(")") and \
                text[2:-1].isidentifier():
            return f"{text[2:-1]}->{e.attr}"    # a by-handle parameter
        return f"{text}.{e.attr}"

    @property
    def types(self):
        """Static PSS types of this body's expressions (`ExprTypes`)."""
        if getattr(self, "_types", None) is None:
            from ..expr_types import ExprTypes
            self._types = ExprTypes(self.fn, self.comp, self.ctx)
        return self._types

    def expr_subscript(self, e) -> str:
        sel = bit_select(e, self.types)
        if sel is None:
            return (checked_index(self, e)
                    or f"{self.expr(e.value)}[{self.expr(e.slice)}]")
        return c_select_read(sel, self.expr)

    def _select_assign_value(self, sel, value) -> str:
        return c_select_write(sel, self.expr,
                              self.convert_to(value, select_type(sel)),
                              getattr(self.fn, "name", "?"))

    def expr_bin(self, e) -> str:
        op = e.op.name
        if op in ("Eq", "NotEq"):
            t = self.types.type_of(e.lhs)
            if t is not None and t.kind in ("array", "struct"):
                text = self._aggregate_eq(e, t.dtype)
                return text if op == "Eq" else f"!{text}"
        if op in RELATIONAL:
            text = self._int_compare(e)
            if text is not None:
                return text
        elif op in CONTEXT_BINARY or op in LEFT_TYPED:
            t = self.types.type_of(e)
            if is_integral(t):
                return self._int_root(e, t.as_int()).text
        cop = _BINOP.get(op)
        if cop is None:
            raise ValueError(f"unsupported binop {op}")
        return f"{self._operand(e.lhs)} {cop} {self._operand(e.rhs)}"

    #: The most scalar comparisons one aggregate `==` is spelled out as.
    _EQ_LEAVES = 64

    def struct_literal_text(self, e, dtype) -> str:
        """A C99 compound literal naming every member: a member the PSS
        literal leaves out has its PSS default, which zero-filling would
        lose (E5)."""
        parts = []
        for f, v in self.struct_literal_members(e, dtype):
            if v is not None:
                text = self.convert_to(v, self.types.of_datatype(f.datatype))
            else:
                iv = getattr(f, "initial_value", None)
                text = (_const_expr(iv, self.enums) if iv is not None
                        else _default_init(f.datatype, self.enums) or "0")
            parts.append(f".{f.name} = {text}")
        return f"(({c_type(dtype)}){{{', '.join(parts)}}})"

    def _aggregate_eq(self, e, dtype) -> str:
        """`a == b` of two arrays or structs (8.5.3): every element and member
        equal. C has no `==` for either -- on arrays it compares ADDRESSES,
        which -Werror refuses (W7) -- so it is spelled out, member by member.
        """
        if _has_call(e.lhs) or _has_call(e.rhs):
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': an aggregate `==` "
                f"reads each operand once per member, and an operand contains "
                f"a call; compare locals")
        leaves: List[str] = []

        def walk(dt, a, b):
            dt = self.types.resolve(dt)
            cn = _dt_name(dt)
            if len(leaves) > self._EQ_LEAVES:
                return
            if cn == "DataTypeArray":
                for i in range(int(getattr(dt, "size", 0) or 0)):
                    walk(dt.element_type, f"{a}[{i}]", f"{b}[{i}]")
            elif cn == "DataTypeStruct":
                for f in dt.fields:
                    walk(f.datatype, f"{a}.{f.name}", f"{b}.{f.name}")
            else:
                leaves.append(f"{a} == {b}")

        # `s == {...}` (8.5.3, Example 33): the literal is typed by the struct
        # it is compared with (E13); a compound literal's member is readable.
        rhs = (self.convert_to(e.rhs, self.types.type_of(e.lhs))
               if _dt_name(e.rhs) == "ExprStructLiteral" else self.expr(e.rhs))
        walk(dtype, self.expr(e.lhs), rhs)
        if len(leaves) > self._EQ_LEAVES:
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': an aggregate `==` of "
                f"more than {self._EQ_LEAVES} scalars is not spelled out")
        return "(" + " && ".join(leaves or ["1"]) + ")"

    def expr_cast(self, e) -> str:
        # `(bit[32])x` -> `(uint32_t)x`. A cast is an assignment-like context
        # (8.7.2): the value is converted to the target's width, which a C
        # cast alone does only when that width is a C type's -- `(bit[4])x`
        # keeps four bits, not eight. Dropping a cast the model wrote is not
        # this emitter's call to make, so the C type is stated as well.
        target = self.types.of_datatype(e.target_type)
        return f"({c_type(e.target_type)})({self.convert_to(e.value, target)})"

    def expr_unary(self, e) -> str:
        if e.op.name in CONTEXT_UNARY:
            t = self.types.type_of(e)
            if is_integral(t):
                return self._int_root(e, t.as_int()).text
        op = _UNOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported unary op {e.op.name}")
        # Bracketed for the same reason `_operand` brackets binary operands:
        # the IR tree states the grouping and C precedence only sometimes
        # agrees. `~0` in particular has to reach the compiler as written.
        return f"{op}({self.expr(e.operand)})"

    def expr_if_exp(self, e) -> str:
        """`c ? a : b` (8.5.6). Each arm is evaluated only if chosen, which C's
        own `?:` guarantees, so an arm that reads a register stays one."""
        t = self.types.type_of(e)
        if t is not None and t.kind == "int":
            return self._int_root(e, t).text
        return (f"(({self.expr(e.test)}) ? ({self.expr(e.body)}) : "
                f"({self.expr(e.orelse)}))")

    def _c_args(self, args, fn) -> List[str]:
        """Call arguments, each converted to its parameter's declared type
        (8.7.2, "function call parameters")."""
        params = list(getattr(getattr(fn, "args", None), "args", None) or ())
        handle = by_handle_params(fn) if params else set()
        args = with_defaults(args, fn) if params else args
        out = []
        for i, a in enumerate(args):
            ann = getattr(params[i], "annotation", None) \
                if i < len(params) else None
            if i < len(params) and params[i].arg in handle:
                out.append(self._handle_arg(a, fn, params[i].arg))
                continue
            out.append(self.convert_to(a, self.types.of_datatype(ann))
                       if ann is not None else self.expr(a))
        return out

    def _handle_arg(self, a, fn, param: str) -> str:
        """The argument of a by-handle struct parameter: the address of the
        caller's struct, which must therefore be a variable (a local, a
        field, an element, or a by-handle parameter passed on)."""
        k = a
        while _dt_name(k) in ("ExprAttribute", "ExprSubscript"):
            if _dt_name(k) == "ExprAttribute" and \
                    _dt_name(k.value) == "TypeExprRefSelf":
                break
            k = k.value
        if _dt_name(k) not in ("ExprRefLocal", "ExprAttribute"):
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': '{param}' of "
                f"'{fn.name}' is a struct passed by handle (LRM 20.3.2), so "
                f"its argument must be a variable the callee can write; pass "
                f"a local holding the value, or declare the parameter "
                f"`const`.")
        text = self.expr(a)
        if text.startswith("(*") and text.endswith(")") and \
                text[2:-1].isidentifier():
            return text[2:-1]
        return f"&{text}"

    # -- calls, one hook per Disposition -------------------------------------
    #
    # `expr_call` is `CallDispatch`'s: it classifies once and comes back here.
    # What used to be a four-branch chain whose ORDER was the classification is
    # now the registry's answer, and the registry is the same table the gate
    # already ran on this model.

    def call_names(self):
        """The model's own names, from the pass that vouched for these calls."""
        from ..validate_calls import model_names, solve_ops

        _, ops, ctors = model_names(self.comp, self.ctor_names)
        return dict(model_ops=ops, imports=frozenset(self.imports),
                    subcomps=ctors,
                    solve_ops=solve_ops(self.comp, self.ctor_names))

    def call_reg(self, call) -> Optional[str]:
        return self._reg_call(call)

    def call_channel(self, call) -> Optional[str]:
        return self._chan_call(call)

    def call_mem(self, call) -> Optional[str]:
        return self._builtin_call(call)

    #: The address built-ins are arithmetic here, and `message`/`print` are the
    #: language's rather than the model's -- both rendered by the same method,
    #: which dispatches on the built-in's own name.
    call_addr = call_mem
    call_utility = call_mem

    def call_model_op(self, call) -> Optional[str]:
        return self._model_call(call)

    def call_offset_fold(self, call) -> Optional[str]:
        """`g.get_offset_of_instance("R")` / `..._array("A", i)`: a register
        group's offset, folded at generation time (21.14.6) -- in an
        operation as in a constructor (E9). ``None`` for any other call."""
        func = call.func
        if _dt_name(func) != "ExprAttribute" or func.attr not in (
                "get_offset_of_instance", "get_offset_of_instance_array"):
            return None
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.reg_groups:
            return None
        group = self.reg_groups[chain[0][0]]
        m, args = func.attr, call.args
        if m == "get_offset_of_instance":
            return f"0x{scalar_offset(group, _str_const(args[0], m)):x}u"
        base, stride = array_base_stride(group, _str_const(args[0], m))
        return f"(0x{base:x}u + 0x{stride:x}u * {self._operand(args[1])})"

    #: A group offset is folded wherever it is called.
    call_fold = call_offset_fold

    call_import = call_model_op

    def expr_ref_bottom_up(self, e) -> str:
        _reject_upward_ref(self.comp, e)        # always raises

    # calls -----------------------------------------------------------------

    def _builtin_call(self, call) -> Optional[str]:
        """PSS built-ins, which have no definition to call.

        `message(...)` is part of the LANGUAGE, not of the generated API, so
        emitting it verbatim produces C that names a function nothing declares.
        The address built-ins are not calls at all here -- `addr_handle_t` is an
        opaque handle in PSS and a plain integer address in C, so deriving a
        handle from another is an add.
        """
        name = _builtin_name(call.func)
        if name is None or name not in C_BUILTINS:
            return None
        args = call.args
        if name == "make_handle_from_handle":
            return handle_from_handle(self, args)
        if name == "addr_value":
            # The handle IS the address here, so this is the identity.
            return self.expr(args[0])
        if name in _MEM_PRIMS:
            return self._mem_call(name, args)
        if name in ("message", "print"):
            return self._message_call(name, args)
        if name in ("error", "fatal"):
            return self._report_call(name, args)
        raise ValueError(f"built-in '{name}' is claimed by the C target but "
                         f"has no rendering; see targets/c/lower_progseq.py")

    def _mem_call(self, name: str, args) -> str:
        """`read32(h)` / `write32(h, v)` -> the memory seam.

        These are NOT register accesses -- `write_descriptor` uses them to put
        a DMA descriptor into system RAM -- but under `reg_map` they get the
        SAME primitives, and deliberately.

        A descriptor is not ordinary memory. The DMA engine reads it while the
        CPU is running and writes back into it (SZ_WB, the DONE bits of a
        linked list), so it changes without this program touching it -- which
        is the definition of `volatile`. Reached through a plain pointer the
        compiler is entitled to cache a field across a loop, sink a store past
        the write that arms the channel, or drop a re-read whose value it
        thinks it already has. Every one of those is correct C and a broken
        driver.

        So the descriptor goes through `write32`/`read32` exactly as a register
        does, and picks up whatever the seam does with a register: the same
        `volatile` access and PSSC_MEM_BARRIER under the pointer seam, the same
        call to the platform's function under the functions seam. The only
        difference left is where the pointer comes from: a register's is a
        member of the layout struct, a descriptor's is an address the model
        computed, so it is cast here (losslessly -- pssc_mem_reg.h asserts
        that `pssc_addr_t` fits in a pointer).

        Under `vtable` this stays on the funnel: its primitives need the bus
        handle, which `pssc_write32` has no argument for.
        """
        direction, width = _MEM_PRIMS[name]
        want = 1 if direction == "read" else 2
        if len(args) != want:
            raise ValueError(
                f"'{name}' takes {want} argument(s), got {len(args)}")
        rendered = [self.expr(a) for a in args]
        if direction == "write":
            # The value is an argument of a `bit[width]` parameter (8.7.2):
            # converted to it, so `write8(h, 0x1FF)` writes 0xFF (W8).
            rendered[1] = self.convert_to(args[1],
                                          PssType("int", width, False))
        if self.reg_map:
            ptr = f"(volatile void *)(uintptr_t)({rendered[0]})"
            if direction == "read":
                return self.mem.reg_ptr_read(width, ptr)
            return self.mem.reg_ptr_write(width, ptr, rendered[1])
        if direction == "read":
            return self.mem.read(width, self.h, rendered[0])
        return self.mem.write(width, self.h, rendered[0], rendered[1])

    def _report_call(self, name: str, args) -> str:
        """`error(fmt, ...)` / `fatal(status, fmt, ...)` (LRM 21.3) as the
        message seam's text, prefixed `error: ` or `fatal: ` -- this target's
        spelling of a run-time error (ruling R2), as an unmatched `match`'s
        is. An `error` continues (21.3 lets it); a `fatal` then calls the
        platform's `pssc_fatal`, which does not return."""
        rest = args[1:] if name == "fatal" else args
        text = self._message_call("print", _prefixed(name, rest))
        if name == "error":
            return text
        return f"({text}, pssc_fatal({self.expr(args[0])}))"

    def _message_call(self, name: str, args) -> str:
        if self.message_style == "none":
            # Drop the call AND its string. `.rodata` is a real budget on this
            # class of part, and a format string nothing prints is pure cost.
            return "(void)0"
        # message(verbosity, fmt, args...) -- the verbosity has no C analogue
        # and is dropped, exactly as the SV projection drops it.
        rest = args[1:] if (name == "message" and len(args) >= 2) else args
        if not rest:
            raise ValueError(f"'{name}' needs a format string")
        fmt_e, vals = rest[0], rest[1:]
        if name == "print" and _dt_name(fmt_e) == "ExprConstant" and \
                isinstance(fmt_e.value, str) and fmt_e.value.endswith("\n"):
            # `print` adds no newline (21.1.2) and ends its own line with
            # `\n`; the message seam is line-oriented and ends every call's
            # line itself, so the format's last newline is that one.
            fmt_e = ir.ExprConstant(value=fmt_e.value[:-1])
        fmt = getattr(fmt_e, "value", None)
        if _dt_name(fmt_e) != "ExprConstant" or not isinstance(fmt, str):
            raise Refusal(
                f"'{name}' needs a string literal as its format, so the "
                f"format can be checked when the model is generated; got "
                f"{_dt_name(fmt_e)}", fmt_e)
        # Formatted here, not by the platform's printf (`message_format`).
        from ..c.message_format import CFormat, lower_format
        parts = lower_format(name, fmt, vals, self.types, self.expr,
                             CFormat(), c_string_literal)
        return f"pssc_message({', '.join(parts)})"

    def _model_call(self, call) -> str:
        """A call on the model itself -- another operation of this component.

        `wait_hint()` inside `wait_completion()` is the case that matters. It
        needs the handle threaded through, which the generic path would not do.
        """
        func = call.func
        name = None
        own = {f.name: f for f in getattr(self.comp, "functions", []) or []}
        if _dt_name(func) == "ExprRefUnresolved":
            name = func.name
        elif _dt_name(func) == "ExprAttribute" and \
                _dt_name(func.value) == "TypeExprRefSelf":
            name = func.attr
        if name is not None and name in self.model_ops:
            args = (self._c_args(call.args, own[name]) if name in own
                    else [self.expr(a) for a in call.args])
            callee = own[name]
            ctx = self.callee_ctx(self.comp, self.prefix)
            return self.model_call(callee, args, ctx,
                                   self.h if self.callee_handle(callee, ctx)
                                   else None)
        # `s.poke()` / `ch[i].status()`: an operation of a sub-component, on
        # that sub-component's handle. Its function is already emitted with
        # the sub-component's API.
        chain = (self._chain(func.value)
                 if _dt_name(func) == "ExprAttribute" else None)
        if chain and chain[0][0] in self.subs:
            path, prefix, comp, rest = self._sub_walk(chain)
            callee = next((f for f in getattr(comp, "functions", []) or []
                           if f.name == func.attr), None)
            if not rest and callee is not None:
                ctx = self.callee_ctx(comp, prefix)
                h = (f"&{self.h}->{path}" if self.callee_handle(callee, ctx)
                     else None)
                return self.model_call(callee, self._c_args(call.args, callee),
                                       ctx, h)
        # An `import target/solve function`: a bare call, and NO handle -- an
        # import is a platform function, not a method of this component, so
        # passing `s` would invent a parameter its declaration does not have.
        # The prototype comes from the same declaration (`lower_imports`), so
        # the two cannot disagree about the signature.
        if name is not None and name in self.imports:
            return (f"{mangle(name)}("
                    + ", ".join(self._c_args(call.args, self.imports[name]))
                    + ")")
        raise ValueError(
            f"call to '{name or _dt_name(func)}' has no lowering in the C "
            f"target. It is not a register access, not a PSS built-in the C "
            f"target claims ({', '.join(sorted(C_BUILTINS))}), not an "
            f"operation of '{getattr(self.comp, 'name', '?')}', and not a "
            f"declared `import target/solve function`. Emitting it verbatim "
            f"would produce C naming a function nothing declares -- which is "
            f"the defect targets/call_legality.py exists to prevent.")

    # -- statements, one hook per node kind ----------------------------------
    #
    # The walk, and carrying each statement's PSS comment into the output, are
    # `BodyWalker`'s. What is here is the rendering.

    def stmt_ann_assign(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        ct = (None if _dt_name(s.annotation) == _DT_ARRAY
              else c_type(s.annotation))
        name = self.expr(s.target)
        # `(void)x;` immediately after the declaration, which is what
        # silences -Wunused-but-set-variable (the placement matters: after
        # the assignment, gcc has already decided).
        tail = ([f"{pad}(void){name};   /* PSS assigns it and never reads it */"]
                if getattr(s.target, "name", None) in self.write_only_locals
                else [])
        if getattr(s.target, "name", None) in self.chan_out_locals:
            # Widened deliberately -- `pssc_chan1_try_get` writes through a
            # `uint64_t *`, and this local's address is what it is given.
            # Stated in the output because `bit tok` becoming `uint64_t` is
            # otherwise an unexplained discrepancy with the PSS source.
            init = (f" = {self.expr(s.value)}"
                    if getattr(s, "value", None) is not None else "")
            return [f"{pad}uint64_t {name}{init};"
                    f"   /* PSS: {ct} -- widened: channel try_get output */"
                    ] + tail
        if _dt_name(s.annotation) == _DT_ARRAY:
            decl = c_declarator(s.annotation, name, "local array")
            if is_literal(getattr(s, "value", None)):
                # C99 6.7.8: an automatic array's initializer list may hold
                # any expressions, each evaluated once.
                return ([f"{pad}{decl} = "
                         f"{self._literal(s.value, s.annotation)};"] + tail)
            if getattr(s, "value", None) is not None:
                return ([f"{pad}{decl};"]
                        + self._array_assign(name, s.value, s.annotation, pad)
                        + tail)
            # Each element its type's default (LRM 8.1); `{0}` zeroes all.
            init = _default_init(s.annotation, self.enums) or "{0}"
            return [f"{pad}{decl} = {init};"] + tail
        vl = self._value_local(s.target)
        if vl is not None:
            if getattr(s, "value", None) is None and \
                    _default_init(s.annotation, self.enums) is not None:
                raise Refusal(
                    f"local '{s.target.name}' is held as its register's "
                    f"word, which has no spelling for the value struct's "
                    f"non-zero defaults; initialize it from a read", s)
            init = ([] if getattr(s, "value", None) is None
                    else self._word_assign(name, s.value, pad))
            return ([f"{pad}{self.reg_value_local(*vl)[0]} {name}"
                     f"{'' if init else ' = 0'};"] + init + tail)
        if getattr(s, "value", None) is not None:
            word = self._read_word(s, name, pad)
            if word is not None:
                return word
            rd = self._read_into(name, s.value,
                                 self.types.of_datatype(s.annotation), pad)
            if rd is not None:
                return [f"{pad}{ct} {name};"] + rd + tail
            value = self.convert_to(s.value,
                                    self.types.of_datatype(s.annotation))
            return [f"{pad}{ct} {name} = {value};"] + tail
        if _dt_name(s.annotation) == _DT_STRUCT:
            # The struct's PSS defaults, every other member (and the reserved
            # and padding bits) zero. C has no default member initializer, so
            # a declaration that said only `{0}` lost `bit[8] y = 7;`.
            init = _default_init(s.annotation, self.enums) or "{0}"
            return [f"{pad}{ct} {name} = {init};"] + tail
        if _dt_name(s.annotation) in (_DT_INT, _DT_BOOL, _DT_ENUM,
                                      _DT_CHANDLE):
            # PSS gives a declared variable a value (0, false, an enum's
            # first item); C leaves an automatic one indeterminate.
            init = _default_init(s.annotation, self.enums) or "0"
            if _dt_name(s.annotation) == _DT_ENUM and s.annotation.items:
                init = self.enums.item(            # the item, even if 0
                    s.annotation, next(iter(s.annotation.items)))
            return [f"{pad}{ct} {name} = {init};"] + tail
        return [f"{pad}{ct} {name};"] + tail

    def _word_assign(self, name: str, value, pad: str) -> List[str]:
        """A word-held value local assigned whole: from a register's read
        (`reg_read_into`, that read's own register) or from another such
        local (`reg_value_locals.bind` allows nothing else)."""
        if _dt_name(value) == "ExprCall":
            return [f"{pad}{self.reg_read_into(*self._reg_parts(value), name)};"]
        return [f"{pad}{name} = {self.expr(value)};"]

    def _word_field(self, tgt):
        """``(local, FieldSlice, parts)`` when ``tgt`` is a field of a
        word-held value local, else None."""
        if _dt_name(tgt) != "ExprAttribute":
            return None
        vl = self._value_local(tgt.value)
        if vl is None:
            return None
        tm = getattr(self.ctx, "type_map", None) or {}
        fs = field_map(vl[0].accessor.dtype, tm.get).get(tgt.attr)
        if fs is None:
            raise ValueError(f"internal error: '{tgt.attr}' is not a field "
                             f"of register '{vl[0].accessor.name}'")
        return self.expr(tgt.value), fs, vl

    def stmt_assign(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        tgt = s.targets[0]
        if self._value_local(tgt) is not None:
            return self._word_assign(self.expr(tgt), s.value, pad)
        tt = self.types.type_of(tgt)
        if tt is not None and tt.kind == "array":
            return self._array_assign(self.expr(tgt), s.value, tt.dtype, pad,
                                      tgt)
        sel = bit_select(tgt, self.types)
        if sel is not None:
            tgt, value = sel.base, self._select_assign_value(sel, s.value)
        else:
            value = self.convert_to(s.value, self.types.type_of(tgt))
        wf = self._word_field(tgt)
        if wf is not None:
            local, fs, vl = wf
            return [f"{pad}"
                    f"{self.reg_value_field_set(local, fs, value, *vl, fs.mask)};"]
        if self.reg_style == "accessors":
            sf = self._struct_field(tgt)
            if sf is not None:
                ct, local, field = sf
                return [f"{pad}{ct}_{field}_set(&{local}, {value});"]
        if sel is None:
            rd = self._read_into(self.expr(tgt), s.value,
                                 self.types.type_of(tgt), pad)
            if rd is not None:
                return rd
        return [f"{pad}{self.expr(tgt)} = {value};"]

    def _array_assign(self, dst: str, value, dtype, pad: str,
                      target=None) -> List[str]:
        """An array assigned whole (`v = w;`), element by element.

        A literal is first a temporary array, so every element is read
        before any is written: `w = {w[1], w[0]}` swaps."""
        if is_literal(value):
            if target is not None and _has_call(target):
                raise ValueError(
                    f"in '{getattr(self.fn, 'name', '?')}': an array "
                    "assigned from `{...}` into an expression with a call is "
                    "not supported in C: it is copied element by element, "
                    "and the call would run once per element.")
            tmp = "_pssc_lit"
            inner = pad + "    "
            return ([f"{pad}{{",
                     f"{inner}{c_declarator(dtype, tmp, 'array')} = "
                     f"{self._literal(value, dtype)};"]
                    + _array_copy(dst, tmp, dtype, inner)
                    + [f"{pad}}}"])
        if _has_call(value) or (target is not None and _has_call(target)):
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': an array assigned "
                "from or into an expression with a call is not supported in "
                "C: it is copied element by element, and the call would run "
                "once per element.")
        if _array_size(dtype) is None:
            raise ValueError(
                f"cannot assign array '{dst}': its size is not known at "
                "generation time.")
        return _array_copy(dst, self.expr(value), dtype, pad)

    def _literal(self, lst, dtype) -> str:
        """`{a, b}` as a C initializer list of *dtype*, each element converted
        to the element type (8.7.2); `array_literal.shape_error` has checked
        that it fits."""
        elem = self.types.resolve(dtype.element_type)
        if _dt_name(elem) == _DT_ARRAY:
            parts = [self._literal(e, elem) for e in lst.elts]
        else:
            et = self.types.of_datatype(elem)
            parts = [self.convert_to(e, et) for e in lst.elts]
        return "{" + ", ".join(parts) + "}"

    def stmt_aug_assign(self, s, ind: int) -> List[str]:
        """`x op= e` is `x = x op e` (8.3), converted back to x's type.

        Rendered `x op= ...` whenever that conversion needs nothing, which is
        the common case and reads as the source does; otherwise spelled out,
        which evaluates the target twice -- so a target with a call in it is
        refused rather than read twice.
        """
        op = _BINOP.get(s.op.name)
        if op is None:
            raise ValueError(
                f"unsupported augmented-assign op {s.op.name}")
        pad = self.pad(ind)
        whole = ir.ExprBin(lhs=s.target, op=s.op, rhs=s.value)
        if bit_select(s.target, self.types) is not None or \
                self._word_field(s.target) is not None:
            # A select is written by read-modify-write (W17); a word-held
            # local's field by `reg_value_field_set`.
            return self.stmt_assign(
                ir.StmtAssign(targets=[s.target], value=whole), ind)
        target = self.types.type_of(s.target)
        if target is None or target.kind != "int" \
                or not is_integral(self.types.type_of(whole)):
            return [f"{pad}{self.expr(s.target)} {op}= "
                    f"{self.expr(s.value)};"]
        tgt = self.expr(s.target)
        value = self.convert_to(whole, target)
        head = f"{tgt} {op} "
        if value.startswith(head):
            return [f"{pad}{tgt} {op}= {_c_unbracket(value[len(head):])};"]
        if _has_call(s.target):
            raise ValueError(
                f"in '{getattr(self.fn, 'name', '?')}': `{tgt} {op}= ...` "
                f"needs its result converted back to the target's type, "
                f"which evaluates the target twice, and the target contains "
                f"a call. Read the index into a local first.")
        return [f"{pad}{tgt} = {value};"]

    def stmt_expr(self, s, ind: int) -> List[str]:
        e = s.expr
        if _dt_name(e) == "ExprCall" and \
                _dt_name(e.func) == "ExprAttribute" and \
                e.func.attr == "write_val_masked":
            parts = self._reg_parts(e)
            if parts is not None:
                one = self.reg_masked_write_inline(*parts)
                if one is None:
                    return self._composed_masked_write(*parts, ind)
                return [f"{self.pad(ind)}{one};"]
        if _dt_name(e) == "ExprCall" and \
                _dt_name(e.func) == "ExprAttribute" and \
                e.func.attr == "write" and len(e.args) == 1 and \
                self._value_local(e.args[0]) is not None:
            parts = self._reg_parts(e)
            return [f"{self.pad(ind)}"
                    f"{self.reg_write_from(*parts, self.expr(e.args[0]))};"]
        return [f"{self.pad(ind)}{self.expr(e)};"]

    def stmt_return(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        if s.value is not None:
            rt = getattr(self.fn, "returns", None)
            value = (self.convert_to(s.value, self.types.of_datatype(rt))
                     if rt is not None else self.expr(s.value))
            return [f"{pad}return {value};"]
        return [f"{pad}return;"]

    def stmt_if(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        lines = [f"{pad}if ({self.expr(s.test)}) {{"]
        lines += self.stmts(s.body, ind + 1)
        if getattr(s, "orelse", None):
            lines.append(f"{pad}}} else {{")
            lines += self.stmts(s.orelse, ind + 1)
        lines.append(f"{pad}}}")
        return lines

    def stmt_if_chain(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        lines: List[str] = []
        for k, c in enumerate(s.clauses):
            head = "if" if k == 0 else "} else if"
            lines.append(f"{pad}{head} ({self.expr(c.test)}) {{")
            lines += self.stmts(c.body, ind + 1)
        if s.orelse:
            lines.append(f"{pad}}} else {{")
            lines += self.stmts(s.orelse, ind + 1)
        lines.append(f"{pad}}}")
        return lines

    def stmt_repeat_while(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        lines = [f"{pad}do {{"]
        lines += self.stmts(s.body, ind + 1)
        lines.append(f"{pad}}} while ({self.expr(s.condition)});")
        return lines

    def stmt_while(self, s, ind: int) -> List[str]:
        # `.test`, not `.condition`. `StmtRepeatWhile` above uses `.condition`
        # and this arm was copied from it, so every `while` in a model crashed
        # the C target with an AttributeError -- which is why nothing caught
        # it: the flat example has no `while`, and the real model could not
        # reach the emitter at all.
        pad = self.pad(ind)
        lines = [f"{pad}while ({self.expr(s.test)}) {{"]
        lines += self.stmts(s.body, ind + 1)
        lines.append(f"{pad}}}")
        return lines

    def stmt_break(self, s, ind: int) -> List[str]:
        return [f"{self.pad(ind)}break;"]

    def stmt_continue(self, s, ind: int) -> List[str]:
        return [f"{self.pad(ind)}continue;"]

    # compound statements ---------------------------------------------------

    def stmt_for(self, s, ind: int) -> List[str]:
        """`repeat ([i :] n) { ... }` (LRM 20.7.6) -> a counted `for`.

        The count is evaluated once, before the first iteration, and a count
        of zero or less runs none: it is held in a local, because a `for`
        condition is re-evaluated. The index is an `int` counting from 0,
        scoped to the loop.
        """
        pad = self.pad(ind)
        count = self.expr(s.iter)
        k = self._repeat_n = getattr(self, "_repeat_n", -1) + 1
        n = f"_pssc_n{k}"
        name = getattr(getattr(s, "target", None), "name", None)
        if name is None:
            return ([f"{pad}for (int64_t {n} = {count}; {n} > 0; {n}--) {{"]
                    + self.stmts(s.body, ind + 1) + [f"{pad}}}"])
        idx = self.expr(s.target)
        had, prev = name in self.types.loop_vars, self.types.loop_vars.get(name)
        self.types.loop_vars[name] = INT
        try:
            body = self.stmts(s.body, ind + 2)
        finally:
            if had:
                self.types.loop_vars[name] = prev
            else:
                del self.types.loop_vars[name]
        return ([f"{pad}{{",
                 f"{pad}    int64_t {n} = {count};",
                 f"{pad}    for (int {idx} = 0; {idx} < {n}; {idx}++) {{"]
                + body + [f"{pad}    }}", f"{pad}}}"])

    def stmt_foreach(self, s, ind: int) -> List[str]:
        """`foreach (a[i]) { ... }` -> an indexed `for`.

        The bound is the array's own size, folded at generation time. C has no
        way to ask an array its length through a pointer, and the collection
        here is always a component member with a size the model states -- so
        the alternative is a magic number in the generated body, which is the
        same number stated twice.
        """
        pad = self.pad(ind)
        coll = self.expr(s.iter)
        dtype = self._iter_dtype(s)
        n = _array_size(dtype)
        if n is None:
            raise ValueError(
                f"cannot lower `foreach` over '{coll}': its size is not known "
                "at generation time. C needs a bound, and guessing one would "
                "produce a loop that runs off the end of the array.")
        index, it = foreach_parts(s)
        idx = mangle(index.name) if index is not None else \
            f"_pssc_i{self._next_tmp()}"
        head = [f"{pad}for (unsigned {idx} = 0; {idx} < {n}u; {idx}++) {{"]
        if it is None:
            return head + self.stmts(s.body, ind + 1) + [f"{pad}}}"]
        # `foreach (v : a[i])`: v is the ELEMENT (20.7.8), bound by value at
        # the top of each iteration. It aliases the element in PSS, so a
        # body writing v is refused rather than silently writing a copy.
        elem = getattr(dtype, "element_type", None)
        if elem is None or self.types.of_datatype(elem) is None or \
                assigns_local(s.body, it.name):
            raise Refusal(
                f"cannot lower `foreach ({it.name} : {coll})`: "
                + ("its body assigns the iterator, which aliases the element "
                   "(LRM 20.7.8 c); assign the element by index"
                   if elem is not None else
                   "the iterator form is lowered over an array of data only; "
                   "use the index form"))
        with self._loop_vars({it.name: self.types.of_datatype(elem),
                              **({index.name: INT} if index else {})}):
            body = self.stmts(s.body, ind + 1)
        bind = f"{pad}    {c_type(elem)} {mangle(it.name)} = {coll}[{idx}];"
        return head + [bind] + body + [f"{pad}}}"]

    def _next_tmp(self) -> int:
        self._tmp_k = getattr(self, "_tmp_k", -1) + 1
        return self._tmp_k

    @contextlib.contextmanager
    def _loop_vars(self, names):
        """Type the loop variables *names* (name -> type) while the body is
        rendered, restoring whatever an enclosing scope had."""
        saved = {k: self.types.loop_vars.get(k, _UNSET) for k in names}
        self.types.loop_vars.update(names)
        try:
            yield
        finally:
            for k, v in saved.items():
                if v is _UNSET:
                    self.types.loop_vars.pop(k, None)
                else:
                    self.types.loop_vars[k] = v

    def _iter_dtype(self, s):
        """Declared type of a `foreach` collection: a field, a local, an
        element of either."""
        t = self.types.type_of(s.iter)
        return t.dtype if t is not None and t.kind == "array" else None

    def stmt_match(self, s, ind: int) -> List[str]:
        """PSS `match` -> C `switch`.

        Every arm `break`s. PSS arms do not fall through, so omitting the break
        would silently change the model's meaning into C's -- the one rewrite
        in this emitter where the two languages' defaults disagree.

        An arm with no pattern is the `default`. When the model supplies none,
        `--match-default` decides what an unmatched subject does; the choice is
        real, because a `switch` with no default is legal C that silently does
        nothing, and PSS says an unmatched `match` is an error (§22.7.9).
        """
        pad = self.pad(ind)
        if match_needs_chain(s):
            return self._match_chain(s, ind, self._unmatched())
        lines = [f"{pad}switch ({self.expr(s.subject)}) {{"]
        saw_default = False
        for case in s.cases:
            labels = self._pattern_labels(case.pattern)
            if labels:
                for lb in labels:
                    lines.append(f"{pad}case {lb}:")
            else:
                saw_default = True
                lines.append(f"{pad}default:")
            lines += self.stmts(case.body, ind + 1)
            lines.append(f"{pad}    break;")
        if not saw_default:
            lines += self._default_arm(pad, ind)
        lines.append(f"{pad}}}")
        return lines

    def _match_chain(self, s, ind: int, unmatched) -> List[str]:
        """A `match` as an `if` chain (`match_needs_chain`): a range is two
        comparisons, and a `break` in an arm leaves the enclosing loop, as
        PSS means. The subject is read once per arm, so one containing a
        call is first held in a local. ``unmatched`` is what no arm and no
        `default` matching does (the `switch` form's default arm)."""
        import zuspec.ir.core as ir
        pad = self.pad(ind)
        lines: List[str] = []
        subj, inner = s.subject, ind
        t = self.types.type_of(subj)
        held = None
        if _has_call(subj):
            if t is None or t.kind not in ("int", "enum", "bool"):
                raise ValueError(
                    f"in '{getattr(self.fn, 'name', '?')}': a `match` on a "
                    f"call's value needs its subject held in a local")
            held = f"_pssc_m{self._next_match()}"
            ct = "int64_t" if t.as_int().signed else "uint64_t"
            lines += [f"{pad}{{",
                      f"{pad}    {ct} {held} = {self.expr(subj)};"]
            subj, inner = ir.ExprRefLocal(name=held), ind + 1
            self.types.loop_vars[held] = t
        ipad = self.pad(inner)
        kw, default = "if", None
        for case in s.cases:
            vals = match_values(case.pattern)
            if not vals:
                default = case
                continue
            test = self.expr(match_arm_test(subj, vals))
            lines.append(f"{ipad}{kw} ({test}) {{")
            lines += self.stmts(case.body, inner + 1)
            kw = "} else if"
        tail = (self.stmts(default.body, inner + 1) if default is not None
                else [self.pad(inner + 1) + ln for ln in unmatched])
        if kw == "if":                      # only a default arm
            lines += [self.pad(inner) + ln.strip() for ln in tail]
        else:
            if tail:
                lines.append(f"{ipad}}} else {{")
                lines += tail
            lines.append(f"{ipad}}}")
        if held is not None:
            self.types.loop_vars.pop(held, None)
            lines.append(f"{pad}}}")
        return lines

    def _next_match(self) -> int:
        self._match_k = getattr(self, "_match_k", -1) + 1
        return self._match_k

    def _unmatched(self) -> List[str]:
        """The statements an unmatched subject runs (`--match-default`)."""
        return [ln.strip() for ln in self._default_arm("", 0)[1:-1]]

    def _default_arm(self, pad: str, ind: int) -> List[str]:
        """What an unmatched subject does when the model states no default."""
        if self.match_default == "none":
            return []
        out = [f"{pad}default:"]
        if self.match_default == "message" and self.message_style != "none":
            out.append(f'{pad}    pssc_message("error: {self.prefix}: '
                       f'unmatched match subject in {self.fn.name}");')
        elif self.match_default == "unreachable":
            out.append(f"{pad}    PSSC_UNREACHABLE();")
        out.append(f"{pad}    break;")
        return out

    def _pattern_labels(self, pattern) -> List[str]:
        """The arm's labels, rendered; none for `default` (`match_values`)."""
        return [self.expr(v) for v in match_values(pattern)]

    def stmt_yield(self, s, ind: int) -> List[str]:
        """`yield` -- the polling wait primitive.

        LOWERED, NOT REJECTED, and the default rendering is NOTHING. `yield` is
        a hint to a scheduler, and this target has no scheduler: on a
        bare-metal single-threaded part the honest cost of "let something else
        run" is zero, and the surrounding loop becomes a tight poll, which is
        exactly right there.

        Rejecting it was considered and withdrawn. A model reaches `yield` on
        this profile precisely BECAUSE it has no event to wait on -- see
        `wb_dma_ch_c/functions/wait_hint.pss` -- so refusing to lower it would
        refuse the one wait every firmware target can actually perform.

        `--yield import` gives the platform a hook, for a target that wants to
        charge for a spin (a WFI, a watchdog kick, a delay).
        """
        pad = self.pad(ind)
        if self.yield_mode == "import":
            return [f"{pad}yield_();"]
        # A comment rather than nothing at all: a reader comparing the PSS to
        # the C needs to see that the wait point is still here and is free.
        return [f"{pad}/* yield: nothing to yield to on this target */"]


# --- the constructor -------------------------------------------------------

#: The emitter's name before it was published (0.3); kept for a release.
_BodyEmitter = CBodyEmitter


def _reject_upward_ref(comp, expr) -> None:
    raise ValueError(
        f"component '{getattr(comp, 'name', '?')}' refers UPWARD to its parent "
        f"({_dt_name(expr)}). The C lowering embeds sub-components by value and "
        f"emits NO parent back-pointer (design §4.1), so there is nothing for "
        f"this to resolve to. Move the shared state down, or pass it as an "
        f"argument.")


class _CtorMixin:
    """The `initialize` body, which is ordinary procedural code plus three
    forms that only ever appear in a constructor.

    Not a separate emitter: `initialize` assigns component attributes, loops,
    and calls built-ins exactly as an operation does, so all of that is
    inherited. What is added is the part that has no runtime representation --
    register-group binding and offset folding both become constants here, which
    is the whole reason a C model needs no register objects at all.
    """

    def _sub_group_set_handle(self, call, chain) -> Optional[str]:
        """`sub.g.set_handle(h)` / `sub[i].g.set_handle(h)`: an enclosing
        component binding a sub-component's register group (LRM 21.14.1; the
        gate counts it, `group_binding`). The sub-component is embedded by
        value, so its group's base is a member of that storage."""
        if (len(chain) != 2 or chain[0][0] not in self.subs
                or chain[1][1] is not None
                or call.func.attr != "set_handle"):
            return None
        sub = self.subs[chain[0][0]]
        g = chain[1][0]
        gf = next((f for f in getattr(sub.dtype, "fields", None) or []
                   if f.name == g and f.name in group_fields(sub.dtype)), None)
        if gf is None or field_is_array(gf):
            return None
        idx = chain[0][1]
        elem = (f"{self.h}->{mangle(sub.name)}[{self.expr(idx)}]"
                if idx is not None else f"{self.h}->{mangle(sub.name)}")
        base = f"{elem}.{self.style.group_base(g)}"
        h = self.expr(call.args[0])
        if self.reg_map:
            mt = map_type_name(gf.datatype, self.style)
            return (f"({base} = {h}, {elem}.{mangle(g)} = "
                    f"({mt} *)(uintptr_t){base})")
        return f"({base} = {h})"

    def _group_call(self, call) -> Optional[str]:
        """A call on a register GROUP (not on a register inside one)."""
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if chain and len(chain) == 2:
            return self._sub_group_set_handle(call, chain)
        if not chain or len(chain) != 1 or chain[0][0] not in self.reg_groups:
            return None
        group = self.reg_groups[chain[0][0]]
        m = func.attr
        args = call.args
        if m == "set_handle":
            # The group has no object: binding it IS setting the group's
            # base, and every accessor in it folds its offset from there.
            g = chain[0][0]
            base = f"{self.h}->{self.style.group_base(g)}"
            if self.reg_map:
                mt = map_type_name(group, self.style)
                return (f"({base} = {self.expr(args[0])}, "
                        f"{self.h}->{mangle(g)} = "
                        f"({mt} *)(uintptr_t){base})")
            return f"({base} = {self.expr(args[0])})"
        folded = self.call_offset_fold(call)
        if folded is not None:
            return folded
        raise ValueError(
            f"unsupported register-group method '{m}'. The C target folds "
            f"group offsets at generation time; a method it does not know "
            f"would become a call to a group object that does not exist.")

    def _sub_ctor_call(self, call) -> Optional[str]:
        """`ch[i].initialize(...)` -> `<sub>_pss_ctor(&_self->ch[i], ...)`."""
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.subs:
            return None
        sub = self.subs[chain[0][0]]
        if func_kind_name(func.attr,
                          self.ctor_names) is not FuncKind.CONSTRUCTOR:
            raise ValueError(
                f"'{sub.name}.{func.attr}()' is called during initialization, "
                f"but only a sub-component's constructor may be. An operation "
                f"is a target function and cannot run in a solve exec.")
        if self.prefixes is None:
            raise ValueError("sub-component construction needs the prefix map")
        sub_prefix = self.prefixes[sub.dtype]
        idx = chain[0][1]
        elem = (f"&{self.h}->{mangle(sub.name)}[{self.expr(idx)}]"
                if idx is not None else f"&{self.h}->{mangle(sub.name)}")
        # The child was CONSTRUCTED with its parent (bus, channels, initial
        # values; `_pss_construct`), so this runs its constructor body only:
        # re-constructing it here would undo whatever the parent's body
        # already wrote into it.
        fwd = [elem] + [self.expr(a) for a in call.args]
        return (f"{self.style.symbol(sub_prefix, 'pss_ctor')}("
                + ", ".join(fwd) + ")")

    #: An `initialize` body is a SOLVE context. The registry refuses a
    #: target-only call there, which is how `write32(...)` in a constructor
    #: becomes a diagnostic rather than C that runs before the bus exists.
    call_context = Ctx.SOLVE

    def call_structural(self, call) -> Optional[str]:
        """`regs.set_handle(h)` -- consumed by the lowering, not emitted."""
        return self._group_call(call)

    #: A group offset is folded at generation time, by the same method: both
    #: are calls on a register GROUP, which has no object in the generated C.
    call_fold = call_structural

    def call_subcomp_ctor(self, call) -> Optional[str]:
        return self._sub_ctor_call(call)


class _CtorEmitter(_CtorMixin, CBodyEmitter):
    """The default ctor emitter: the ctor-only forms over the default body."""


def ctor_emitter_cls(emitter_cls):
    """The ctor emitter that goes with a given body emitter.

    A subclass swapping `body_emitter_cls` must get its rendering in `_init`
    bodies too. Those are emitted by a different class, so without this a
    backend that (say) reindents every statement would produce operations in
    the new shape and constructors in the old one -- the pair-of-overrides
    failure of design §2.6.3, and one nobody would notice until they read the
    generated file.
    """
    if emitter_cls is CBodyEmitter:
        return _CtorEmitter
    return type("_CtorEmitter", (_CtorMixin, emitter_cls), {})


def _str_const(e, method: str) -> str:
    """The string literal argument of an offset query."""
    if _dt_name(e) != "ExprConstant" or not isinstance(getattr(e, "value", None), str):
        raise ValueError(
            f"'{method}' needs a literal instance name so its offset can be "
            f"folded at generation time; got {_dt_name(e)}.")
    return e.value


def func_kind_name(name: str, ctor_names=None):
    """FuncKind for a function reached by NAME only (a call site).

    The callee's IR node is not to hand at a call site -- only the attribute
    name -- so this answers the one question the ctor lowering asks: is this
    the constructor?

    ``ctor_names`` is this compile's set. It falls back to the ambient one for
    a caller outside a compile (a test, a tool poking at one function), which
    is the only place that value is still the authority.
    """
    from ..progseq_model import current_ctor_names
    names = current_ctor_names() if ctor_names is None else ctor_names
    return FuncKind.CONSTRUCTOR if name in names else FuncKind.EXPORT_OP


# --- emission --------------------------------------------------------------

def _sig_all(node, prefixes, link_style: str, qual: str, is_root: bool,
             lifecycle: str = "malloc", style=None,
             ctor_names=None, api=None, lifecycle_decls_fn=None) -> List[str]:
    """Every file-scope prototype for one component: the lifecycle's
    (``lifecycle_decls_fn(node, ctx)``, default :func:`lifecycle_decls`),
    then the operations'."""
    style = _style(style)
    comp = node.dtype
    prefix = prefixes[comp]
    ctx = OpCtx(comp=comp, prefix=prefix, qual=qual, emitter_cls=None,
                be_kw={"style": style}, type_name=prefixes.type_name(comp),
                api=api)
    if lifecycle_decls_fn is not None:
        out = list(lifecycle_decls_fn(node, ctx))
    else:
        out = lifecycle_decls(node, prefixes, link_style, qual, is_root,
                              lifecycle, style, ctor_names)
    for fn in _operations(comp, ctor_names):
        # A `static` operation is not on the API; `lower_impl` declares it.
        if not qual and _linkage(fn, ctx) == "static":
            continue
        # On the prototype, which is the API surface a caller reads. Repeated
        # on the definition below, which is what someone debugging reads --
        # the same deliberate duplication the SV target makes.
        blank_line(out)
        out += doc_block(getattr(fn, "doc", None), "", BLOCK)
        out.append(f"{render_signature(ctx.op_api.signature(fn, ctx), qual)};")
    return out


def lifecycle_decls(node, prefixes, link_style: str, qual: str,
                    is_root: bool, lifecycle: str = "malloc", style=None,
                    ctor_names=None) -> List[str]:
    """The lifecycle prototypes of one component: `_init`, and on the root
    under ``lifecycle="malloc"`` `_create`/`_destroy`.

    `_create`/`_destroy` are ROOT-ONLY. A sub-component lives inside its
    parent's storage, so a `wb_dma_ch_create()` returning a malloc'd channel
    would hand back an object no `wb_dma_t` contains -- an allocation that
    compiles, runs, and is bound to nothing.

    Under ``lifecycle="static"`` they are absent entirely and `_init` is the
    whole lifecycle: the caller owns the storage. That is not merely a smaller
    API -- it is what keeps `malloc` out of the image. See :func:`_lifecycle_impl`.
    """
    style = _style(style)
    comp = node.dtype
    prefix = prefixes[comp]
    sym = style.symbol
    prefix_t = style.type_name(prefixes.type_name(comp))
    ctor = _ctor(comp, ctor_names)
    cp = _create_params(ctor, link_style)
    sig_params = f", {cp}" if cp else ""
    out = [f"{qual}void {sym(prefix, 'init')}({prefix_t} *{HANDLE}{sig_params});"]
    if is_root and lifecycle == "malloc":
        out.append(
            f"{qual}{prefix_t} *{sym(prefix, 'create')}({cp or 'void'});")
        out.append(f"{qual}void {sym(prefix, 'destroy')}({prefix_t} *{HANDLE});")
    return out


def lower_decls(model, prefixes, *, link_style: str = "vtable",
                static_inline: bool = False,
                lifecycle: str = "malloc", style=None, api=None,
                lifecycle_decls=None) -> str:
    """Prototypes for the whole tree.

    Emitted in ALL modes, including ``--header-only``. They used to be skipped
    there on the grounds that `static inline` definitions are self-declaring --
    true for one function, false for a set that calls each other: a parent's
    `_init` calls its children's and an end-to-end operation calls its own
    `*_start`, and C requires a declaration before the call, not merely a
    definition somewhere in the file.
    """
    qual = "static inline " if static_inline else ""
    lines = ["/* ----- Export API + lifecycle. ----- */"]
    for node in regular_nodes(model):
        lines += _sig_all(node, prefixes, link_style, qual,
                          is_root=model.is_root(node.dtype),
                          lifecycle=lifecycle, style=style,
                          ctor_names=model.ctor_names, api=api,
                          lifecycle_decls_fn=lifecycle_decls)
        lines.append("")
    return "\n".join(lines).rstrip()


def lifecycle_lines(node, ctx: OpCtx, model,
                    lifecycle: str = "malloc") -> List[str]:
    """One component's lifecycle definitions (`_pss_construct`, `_pss_ctor`,
    `_init`, and `_create`/`_destroy` on the root): the default
    `COpModelBackend.emit_lifecycle`. ``ctx`` is the component's `OpCtx`
    as `lower_impl` builds it."""
    kw = dict(ctx.be_kw)
    prefixes, link_style = kw.pop("prefixes"), kw.pop("link_style")
    reg_style, ctor_names = kw.pop("reg_style"), kw.pop("ctor_names")
    comp = node.dtype
    return _lifecycle_impl(node, prefixes, link_style, ctx.qual, reg_style,
                           is_root=model.is_root(comp), lifecycle=lifecycle,
                           ctor_names=ctor_names, emitter_cls=ctx.emitter_cls,
                           binding=(model.binding(comp)
                                    if hasattr(model, "binding") else None),
                           api=ctx.api, **kw)


def _lifecycle_impl(node, prefixes, link_style: str, qual: str,
                    reg_style: str, is_root: bool, lifecycle: str = "malloc",
                    reg_map: bool = False,
                    style=None, emitter_cls=None, ctor_names=None,
                    binding=None, api=None, **be_kw) -> List[str]:
    style = _style(style)
    comp = node.dtype
    prefix = prefixes[comp]
    sym = style.symbol
    prefix_t = style.type_name(prefixes.type_name(comp))
    ctor = _ctor(comp, ctor_names)
    cp = _create_params(ctor, link_style)
    sig_params = f", {cp}" if cp else ""

    # CONSTRUCTION IS TWO STEPS (LRM 20.1.2), and so is `_init`. Every
    # instance in the tree is constructed -- bus, channels, field initial
    # values -- whether or not a constructor reaches it; then the root's
    # constructor body runs, calling its children's as it chooses. One
    # function doing both meant a sub-component whose parent never called
    # its `initialize` had neither: `int a = 5;` read 0, with no error. The
    # two halves are file-local; `_init` is the API and keeps its signature.
    helper = "static inline " if qual else "static "
    construct, ctor_fn = sym(prefix, "pss_construct"), sym(prefix, "pss_ctor")
    bus_p = f", const pssc_mem_if *{BUS}" if link_style == "vtable" else ""
    body: List[str] = []
    if link_style == "vtable":
        body.append(f"    {HANDLE}->{BUS} = {BUS};")
    from ..progseq_model import channel_count
    for f in channel_fields(comp):
        n = channel_count(f)
        if n is None:
            body.append(f"    pssc_chan1_init(&{HANDLE}->{mangle(f.name)});")
        else:
            body.append(f"    for (unsigned i = 0; i < {n}u; i++) "
                        f"pssc_chan1_init(&{HANDLE}->{mangle(f.name)}[i]);")
    body += _field_defaults(
        comp, enums=CEnums(style, getattr(be_kw.get("ctx"), "type_map", None)))
    for sub in sub_components(comp):
        sub_construct = sym(prefixes[sub.dtype], "pss_construct")
        fwd = f", {HANDLE}->{BUS}" if link_style == "vtable" else ""
        if sub.size is not None:
            body.append(f"    for (unsigned i = 0; i < {sub.size}u; i++) "
                        f"{sub_construct}(&{HANDLE}->{mangle(sub.name)}[i]{fwd});")
        else:
            body.append(f"    {sub_construct}(&{HANDLE}->{mangle(sub.name)}{fwd});")
    lines = [f"{helper}void {construct}({prefix_t} *{HANDLE}{bus_p}) {{"]
    lines += body or [f"    (void){HANDLE};"]
    lines.append("}")

    # THE IMPLICIT BINDING (`group_binding`): a component with one register
    # group and a constructor that binds none binds it to the constructor's
    # first address parameter. `src/pssc/testing/models` declares
    # `solve function void ctor(addr_handle_t base) { }` with an EMPTY body and
    # says so: "the binding is the generator's job, not the model's". Every
    # other group is bound by the `set_handle` the body states -- a group
    # nothing binds was refused before generation started.
    # The model's table (`OpModel.bindings`), which the gate read: computed
    # for this component alone, it missed a parent's `sub.g.set_handle(...)`
    # and bound the group implicitly as well.
    if binding is None:
        binding = group_binding.bindings([comp], ctor_names)[id(comp)]
    implicit = binding.implicit
    if ctor is not None:
        ctor_p = "".join(f", {c_type(a.annotation)} {mangle(a.arg)}"
                         for a in ctor.args.args)
        body = []
        if implicit is not None:
            g, arg = implicit
            base = f"{HANDLE}->{style.group_base(g)}"
            body.append(f"    {base} = {mangle(arg)};")
            if reg_map:
                # The one cast in the generated driver, and it is here (or at
                # a `set_handle`) rather than at every access for that reason.
                gf = next(f for f in comp.fields if f.name == g)
                mt = map_type_name(gf.datatype, style)
                body.append(f"    {HANDLE}->{mangle(g)} = "
                            f"({mt} *)(uintptr_t){base};")
        ctor_cls = ctor_emitter_cls(emitter_cls or CBodyEmitter)
        be = ctor_cls(ctor, comp, prefix, reg_style=reg_style,
                      handle=HANDLE, prefixes=prefixes,
                      link_style=link_style, style=style,
                      ctor_names=ctor_names, reg_map=reg_map, **be_kw)
        be.op_ctx = OpCtx(comp=comp, prefix=prefix, qual=helper,
                          emitter_cls=ctor_cls, be_kw={"style": style},
                          type_name=prefixes.type_name(comp), api=api)
        stmts = be.stmts(ctor.body, 1)
        body = be.function_local_lines(1) + body + stmts
        code = [ln for ln in body
                if not ln.lstrip().startswith(("/*", "*", "//"))]
        if not any(re.search(rf"\b{HANDLE}\b", ln) for ln in code):
            body.insert(0, f"    (void){HANDLE};")
        lines.append(f"{helper}void {ctor_fn}({prefix_t} *{HANDLE}{ctor_p}) {{")
        lines += body
        lines.append("}")

    lines.append(f"{qual}void {sym(prefix, 'init')}({prefix_t} *{HANDLE}{sig_params}) {{")
    lines.append(f"    {construct}({HANDLE}{', ' + BUS if bus_p else ''});")
    if ctor is not None:
        args = "".join(f", {mangle(a.arg)}" for a in ctor.args.args)
        lines.append(f"    {ctor_fn}({HANDLE}{args});")
    lines.append("}")

    # `_create`/`_destroy` are the ONLY things in a generated image that call an
    # allocator, so `--lifecycle static` is not a convenience: on a part with no
    # heap, linking malloc pulls in a sbrk stub and a several-KiB allocator for
    # one object whose size is known at compile time. The caller supplies the
    # storage instead -- `static wb_dma_t dma; wb_dma_init(&dma, ...)`.
    if is_root and lifecycle == "malloc":
        fwd = ([BUS] if link_style == "vtable" else []) + \
              ([mangle(a.arg) for a in ctor.args.args] if ctor else [])
        fwd_s = ", ".join(fwd)
        lines.append(
            f"{qual}{prefix_t} *{sym(prefix, 'create')}({cp or 'void'}) {{")
        lines.append(
            f"    {prefix_t} *{HANDLE} = "
            f"({prefix_t} *)malloc(sizeof({prefix_t}));")
        init_args = f", {fwd_s}" if fwd_s else ""
        lines.append(f"    if ({HANDLE}) "
                     f"{sym(prefix, 'init')}({HANDLE}{init_args});")
        lines.append(f"    return {HANDLE};")
        lines.append("}")
        lines.append(
            f"{qual}void {sym(prefix, 'destroy')}({prefix_t} *{HANDLE}) "
            f"{{ free({HANDLE}); }}")
    return lines


@dc.dataclass(frozen=True)
class OpCtx:
    """Everything rendering ONE operation needs, besides the function itself.

    A record rather than eight keyword arguments, so that
    `COpModelBackend.emit_operation(fn, ctx)` -- the seam a mid-weight
    extension wraps to put a trace call, a lock or a prologue around every
    generated operation -- has a signature that a new lowering detail does not
    change. Adding a field here does not break an override; adding a keyword
    argument would break every one of them.
    """
    #: The component datatype the operation belongs to, and its symbol prefix.
    comp: Any
    prefix: str
    #: `"static inline "` under `--header-only`, else empty.
    qual: str
    #: The body-emitter class in force (`body_emitter_cls`, resolved).
    emitter_cls: Any
    #: What the body emitter is constructed with. Opaque on purpose: it is the
    #: lowering's business, and an override that reads it is reaching past the
    #: seam rather than through it.
    be_kw: Mapping[str, Any]
    #: The component's type name, before the style respells it
    #: (`Prefixes.type_name`).
    type_name: str = ""
    #: What answers `signature`/`handle_param`/`linkage`: the backend, or
    #: `None` for `DefaultOpApi`'s answers. Read it as `op_api`.
    api: Any = None

    @property
    def op_api(self):
        return self.api if self.api is not None else DEFAULT_API

    def for_comp(self, comp, prefix: str, type_name: str) -> "OpCtx":
        """This context, for another component: a callee's, at a call."""
        return dc.replace(self, comp=comp, prefix=prefix, type_name=type_name)

    def body_emitter(self, fn):
        """The emitter that renders ``fn``'s body."""
        be = self.emitter_cls(fn, self.comp, self.prefix,
                              handle=self.op_api.handle_param(fn, self),
                              **self.be_kw)
        be.op_ctx = self
        return be


def lower_operation(fn, ctx: OpCtx) -> List[str]:
    """One exported operation: doc comment, signature, body, closing brace.

    Returns LINES, with no leading or trailing blank -- the caller separates.
    """
    be = ctx.body_emitter(fn)
    api = ctx.op_api
    qual = ctx.qual or (STATIC_QUAL if _linkage(fn, ctx) == "static" else "")
    handle = api.handle_param(fn, ctx)
    lines = list(doc_block(getattr(fn, "doc", None), "", BLOCK))
    lines.append(f"{render_signature(api.signature(fn, ctx), qual)} {{")
    body = be.stmts(fn.body, 1)
    # An operation that touches neither a register nor a member never
    # names the handle, and `-Wextra -Werror` -- which any real firmware
    # build uses, and which the C gate uses -- rejects the unused
    # parameter. The handle stays in the signature regardless: dropping
    # it would make the API shape depend on a body detail, so a later
    # edit that started using a member would silently change every
    # caller's call.
    # Code only: the body now carries the PSS source's prose, and a
    # comment that mentions the handle would make an operation that never
    # touches it look as though it did -- reinstating the
    # -Wunused-parameter error this suppresses. A user name is never
    # `HANDLE` (`mangle`), so a whole-word match is a use of the handle.
    # With no handle (`handle_param` is None) there is nothing to suppress:
    # a body that needed one has been refused by the emitter.
    code = [ln for ln in body
            if not ln.lstrip().startswith(("/*", "*", "//"))]
    used = re.compile(rf"\b{re.escape(handle or '')}\b")
    lines += be.function_local_lines(1)
    if handle is not None and not any(used.search(ln) for ln in code):
        lines.append(f"    (void){handle};")
    lines += body
    lines.append("}")
    return lines


def _solve_impl(comp, prefix, prefixes, emitter_cls, qual, be_kw,
                style, api=None) -> List[str]:
    """The component's solve functions (`_solve_operations`), file-local.

    Not on the API: PSS lets only solve context call one (22.2.3), and the
    platform calls the generated code in target context. So each is
    `static inline`,
    declared first -- they may call each other, and the constructor below calls
    them -- and its body is rendered as a constructor's is, in solve context.
    """
    fns = _solve_operations(comp, be_kw.get("ctor_names"))
    if not fns:
        return []
    # `inline` as well: a component that inherits a solve function nothing
    # of its own calls still renders it (the flattened view), and an unused
    # `static` function fails `-Werror=unused-function` (W45).
    helper = "static inline "
    ctx = OpCtx(comp=comp, prefix=prefix, qual=helper,
                type_name=prefixes.type_name(comp),
                emitter_cls=ctor_emitter_cls(emitter_cls), be_kw=be_kw,
                api=api)
    lines = [f"{render_signature(ctx.op_api.signature(fn, ctx), helper)};"
             for fn in fns]
    for fn in fns:
        lines.append("")
        lines += lower_operation(fn, ctx)
    lines.append("")
    return lines


def lower_impl(model, prefixes, *, link_style: str = "vtable",
               static_inline: bool = False, reg_style: str = "bitfields",
               yield_mode: str = "none", match_default: str = "message",
               message_style: str = "import",
               lifecycle: str = "malloc", imports=None, style=None,
               accs=None, emitter_cls=None, emit_operation=None,
               reg_map: bool = False, api=None, lifecycle_impl=None) -> str:
    """Lifecycle + operation bodies for every component, CHILDREN FIRST.

    ``emit_operation(fn, ctx)`` renders one operation; it defaults to
    :func:`lower_operation` and is how the backend inserts itself into this
    loop without owning the walk. ``api`` answers each operation's
    signature, handle and linkage (`OpCtx.api`).

    A `static` operation is declared here, ahead of every definition, as a
    solve function is: it is not on the header, and any operation may call
    it.
    """
    qual = "static inline " if static_inline else ""
    emitter_cls = emitter_cls or CBodyEmitter
    emit_operation = emit_operation or lower_operation
    be_kw = dict(yield_mode=yield_mode, match_default=match_default,
                 message_style=message_style, imports=imports, style=style,
                 accs=accs, reg_map=reg_map, ctx=getattr(model, "ctx", None))
    ctor_names = model.ctor_names
    lines: List[str] = ["/* ----- Component lifecycle + operations. ----- */"]

    def op_ctx(comp) -> OpCtx:
        return OpCtx(comp=comp, prefix=prefixes[comp], qual=qual,
                     type_name=prefixes.type_name(comp),
                     emitter_cls=emitter_cls,
                     be_kw=dict(reg_style=reg_style, prefixes=prefixes,
                                link_style=link_style, ctor_names=ctor_names,
                                **be_kw), api=api)

    if not qual:
        static = [f"{render_signature(c.op_api.signature(fn, c), STATIC_QUAL)};"
                  for node in post_order(model)
                  for c in [op_ctx(node.dtype)]
                  for fn in _operations(node.dtype, ctor_names)
                  if _linkage(fn, c) == "static"]
        if static:
            lines += static + [""]
    for node in post_order(model):
        comp = node.dtype
        prefix = prefixes[comp]
        lines.append(f"/* --- {getattr(comp, 'name', '?')} --- */")
        lines += _solve_impl(comp, prefix, prefixes, emitter_cls, qual,
                             dict(reg_style=reg_style, prefixes=prefixes,
                                  link_style=link_style,
                                  ctor_names=ctor_names, **be_kw),
                             style, api)
        ctx = op_ctx(comp)
        lines += (lifecycle_impl(node, ctx) if lifecycle_impl is not None
                  else lifecycle_lines(node, ctx, model, lifecycle))
        lines.append("")
        for fn in _operations(comp, ctor_names):
            blank_line(lines)
            lines += emit_operation(fn, ctx)
            lines.append("")
    return "\n".join(lines).rstrip()


def checked_index(emitter, e) -> Optional[str]:
    """``a[i]`` with ``i`` checked against ``a``'s size (`index_bounds`), in
    C or C++: an index out of range reports the model's run-time error and
    reads element 0 instead (`error()` may continue, 21.3). None when the
    index needs no check."""
    from ..index_bounds import index_bound, MESSAGE
    n = index_bound(e)
    if n is None:
        return None
    idx = emitter.expr(e.slice)
    err = emitter.expr(ir.ExprCall(
        func=ir.ExprAttribute(value=ir.TypeExprRefSelf(), attr="error"),
        args=[ir.ExprConstant(value=MESSAGE)], keywords=[]))
    return (f"{emitter.expr(e.value)}[((unsigned long long)({idx}) < {n}u "
            f"? ({idx}) : ({err}, 0))]")
