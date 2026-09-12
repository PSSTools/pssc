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

import dataclasses as dc
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set

from ..progseq_model import (
    func_kind, FuncKind, field_is_reg_group, _dt_name, CompKind,
    sub_components, channel_fields, array_base_stride, scalar_offset,
)
from .lower_reg_model import (accessor_base, c_struct_name, _prim_bits,
                             map_type_name)
from .mem_access import DEFAULT as DEFAULT_MEM, MemAccess
from .style import coerce as _style
from ..body_walker import (BodyWalker, CallDispatch, scan_output_locals,
                           scan_write_only)
from ..call_legality import Ctx
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
    return name + "_" if name in _C_KEYWORDS else name


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
    """C type name for a PSS enum: ``wb_dma_status_e`` -> ``wb_dma_status_t``.

    Same rule :func:`c_struct_name` applies to ``_s``, so one suffix convention
    covers both and a reader never has to remember which kind a name came from.
    """
    n = (getattr(enum_dtype, "name", None) or "").split("::")[-1]
    if not n:
        raise ValueError("enum type has no name; cannot emit a C typedef")
    return (n[:-2] + "_t") if n.endswith("_e") else (n + "_t")


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
    exception -- it keeps the prefix the caller asked for via ``--prefix``, so
    the generated file name and its main type still agree.

    Two types that collide after ``_c``-stripping are a generation ERROR rather
    than a last-writer-wins overwrite: the second component's operations would
    be emitted under the first one's names, and C would report it as a
    redefinition somewhere unrelated -- or, for two components with disjoint
    operation sets, not report it at all.
    """

    def __init__(self, model, root_prefix: str,
                 overrides: Optional[Dict[str, str]] = None,
                 language: str = "C"):
        #: Named in the collision diagnostic. The C++ backend shares this class
        #: -- the question ("do two component types map to one name?") and its
        #: answer are the same in both languages, and two copies could disagree.
        self._language = language
        self._by_id: Dict[int, str] = {}
        overrides = overrides or {}
        used: Dict[str, str] = {}
        for i, node in enumerate(regular_nodes(model)):
            # The QUALIFIED name identifies the type; the short one names it in
            # C and in `--prefix-map`. Comparing the short names here let
            # `p::x_c` and `q::x_c` -- two different components -- look like one
            # and share a prefix, which is precisely the collision this check is
            # for. An override may be given under either spelling.
            qname = getattr(node.dtype, "name", None) or "<anon>"
            tname = qname.split("::")[-1]
            if qname in overrides:
                p = overrides[qname]
            elif tname in overrides:
                p = overrides[tname]
            elif i == 0:
                p = root_prefix
            else:
                p = strip_c_suffix(tname)
            if p in used and used[p] != qname:
                raise ValueError(
                    f"component types '{used[p]}' and '{qname}' both map to the "
                    f"{self._language} symbol prefix '{p}', so their operations "
                    f"would be emitted under the same names. Disambiguate with "
                    f"--prefix-map {qname}=<prefix>.")
            used[p] = qname
            self._by_id[id(node.dtype)] = p

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


def _member_decl(f) -> str:
    """One struct-member declaration, arrays included.

    C puts the bound after the name, so an array member cannot be spelled by
    `c_type()` alone -- which is why this is not one more branch there.
    """
    dt = f.datatype
    if _dt_name(dt) == "DataTypeArray":
        n = _array_size(dt)
        if n is None:
            raise ValueError(
                f"array member '{f.name}' has no folded size; C needs a bound "
                "and there is nothing to derive one from.")
        return f"{c_type(dt.element_type)} {mangle(f.name)}[{n}];"
    return f"{c_type(dt)} {mangle(f.name)};"


def _field_defaults(comp, target: str = "self") -> List[str]:
    """PSS field initializers, as assignments in `_init`.

    A default is part of a field's MEANING, not a convenience:
    `wb_dma_ch_caps_s` declares every capability true, and a model that reads
    back all-false silently refuses to attempt the operations those
    capabilities gate -- a driver that reports the device cannot do things it
    can.

    Emitted as assignments rather than as a designated initializer because
    `_init` writes into caller-supplied storage: the caller's struct may be a
    static whose other members are already set, and `*self = (T){...}` would
    clear them.
    """
    out: List[str] = []
    for f in data_members(comp):
        iv = getattr(f, "initial_value", None)
        if iv is not None:
            out.append(f"    {target}->{mangle(f.name)} = {_const_expr(iv)};")
            continue
        # A struct-typed attribute carries its defaults on the STRUCT's fields
        # rather than on the instance, so they have to be walked out member by
        # member.
        if _dt_name(f.datatype) == _DT_STRUCT:
            for sf in getattr(f.datatype, "fields", []) or []:
                siv = getattr(sf, "initial_value", None)
                if siv is not None:
                    out.append(f"    {target}->{mangle(f.name)}.{sf.name} = "
                               f"{_const_expr(siv)};")
    return out


def _const_expr(e) -> str:
    """A field initializer, which is a compile-time constant by construction."""
    cn = _dt_name(e)
    if cn == "ExprConstant":
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
    dt = f.datatype
    depth = int(getattr(dt, "depth", 1) or 1)
    if depth != 1:
        raise ValueError(
            f"channel '{f.name}' has depth {depth}; the C target implements "
            f"only depth-1 channels (share/c/pssc_chan.h). A deeper channel is "
            f"a ring buffer, which is a different type per capacity and which "
            f"nothing in scope declares.")
    elem = getattr(dt, "element_type", None)
    if elem is not None and _dt_name(elem) not in (_DT_INT, _DT_BOOL, _DT_ENUM):
        raise ValueError(
            f"channel '{f.name}' carries {_dt_name(elem)}; the C target's "
            f"channel payload is a 64-bit integer, so only integral and enum "
            f"element types are supported (share/c/pssc_chan.h).")
    return f"{_CHAN_T} {mangle(f.name)};"


def _sub_member_decl(sub, prefixes, style=None) -> str:
    """A sub-component member -- BY VALUE, array bound included.

    By value rather than by pointer because the PSS tree is static
    (elaboration-time), so the C tree is too: one caller-provided object covers
    every level and there is nothing to allocate. See the design's §4.1.
    """
    t = _style(style).type_name(prefixes[sub.dtype])
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
    lines.append(f"typedef struct {style.struct_tag(prefix)} {{")
    if link_style == "vtable":
        # Every component carries its own bus pointer, not just the root. A
        # sub-component's register accessors call pssc_bus(s) with ITS handle,
        # and reaching the root's copy would need the parent back-pointer §4.1
        # rules out. One pointer per channel is the cheaper of the two.
        lines.append("    const pssc_mem_if *bus;")
    lines.append("    pssc_addr_t base;")
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
    lines.append(f"}} {style.type_name(prefix)};")
    return "\n".join(lines)


def _stem(chain, prefix: str, style=None) -> str:
    """The accessor stem a chain would have had.

    Under `reg_map` no accessor is emitted, but the `_Acc` record keyed by this
    stem still carries what the access needs and the layout does not: the
    TRANSACTION WIDTH. A 16-bit register in a 32-bit map is `read16` of a
    `uint16_t` member, and the member type alone would let `read32` compile.
    """
    return accessor_base(prefix, [c[0] for c in chain[:-1]], chain[-1][0],
                         style)


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
        lines.append(f"#define {macro}(s) ((s)->bus)")
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
    parent_t = style.type_name(parent)
    out: List[str] = []
    for sub in sub_components(comp):
        name = style.symbol(parent, mangle(sub.name))
        sub_t = style.type_name(prefixes[sub.dtype])
        if sub.is_array:
            out.append(
                f"#define {style.macro(parent, mangle(sub.name) + '_COUNT')} "
                f"{sub.size}u")
            out.append(
                f"static inline {sub_t} *{name}({parent_t} *s, unsigned i) "
                f"{{ return &s->{mangle(sub.name)}[i]; }}")
        else:
            out.append(
                f"static inline {sub_t} *{name}({parent_t} *s) "
                f"{{ return &s->{mangle(sub.name)}; }}")
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

def _op_params(fn) -> str:
    parts = [f"{c_type(a.annotation)} {mangle(a.arg)}" for a in fn.args.args]
    return (", " + ", ".join(parts)) if parts else ""


def _op_signature(fn, prefix: str, qual: str = "", style=None) -> str:
    style = _style(style)
    ret = c_type(fn.returns) if fn.returns is not None else "void"
    return (f"{qual}{ret} {style.symbol(prefix, mangle(fn.name))}"
            f"({style.type_name(prefix)} *s{_op_params(fn)})")


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


def lower_imports(imports: Dict[str, object]) -> str:
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
    """
    if not imports:
        return ""
    lines = ["/* ----- Imported functions: supplied by the PLATFORM. ----- */",
             "/* Declared by the model with `import target/solve function`;"
             " named here exactly as */",
             "/* the model names them. Implement each one, or the link fails"
             " with the PSS name. */"]
    for name in sorted(imports):
        lines.append(f"{_import_signature(imports[name])};")
    return "\n".join(lines)


def _create_params(ctor, link_style: str) -> str:
    parts = []
    if link_style == "vtable":
        parts.append("const pssc_mem_if *bus")
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
    "message", "print",
    "make_handle_from_handle", "addr_value",
}) | frozenset(_MEM_PRIMS)


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
        return getattr(func, "name", None)
    if cn == "ExprAttribute":
        return func.attr
    return None


class _BodyEmitter(CallDispatch, BodyWalker):
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

    def __init__(self, fn, comp, prefix: str, reg_style: str = "bitfields",
                 yield_mode: str = "none", match_default: str = "message",
                 message_style: str = "import", handle: str = "s",
                 prefixes=None, link_style: str = "vtable", imports=None,
                 mem: MemAccess = None, style=None, accs=None,
                 ctor_names=None, reg_map: bool = False):
        self.fn = fn
        #: Address registers by following the layout struct. See `_reg_path`.
        self.reg_map = reg_map
        #: This compile's constructor names, for the one call site that has
        #: only a name to classify (`ch[i].initialize(...)`).
        self.ctor_names = ctor_names
        #: Naming and layout. A body spells the same symbols the prototypes do
        #: -- a sibling call, a sub-component's `_init` -- so it consults the
        #: same policy rather than rebuilding the name from the prefix.
        self.style = _style(style)
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
        #: Name of the handle parameter in the emitted signature. Operations
        #: take `s`; `_init` takes `self` (the design's spelling, and the one a
        #: caller reads first). The bodies are otherwise identical, so this is a
        #: parameter rather than two emitters.
        self.h = handle
        self.prefixes = prefixes
        self.link_style = link_style
        #: Declared package-scope imports, by PSS name. A call to one lowers to
        #: a bare call under that same name -- see :func:`lower_imports`.
        self.imports = dict(imports or {})
        #: Operations of this component, so a call to a sibling operation is
        #: recognised as one and gets the handle threaded through.
        self.model_ops = {f.name for f in comp.functions}
        self.arg_rename = {a.arg: mangle(a.arg) for a in fn.args.args}
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
        ref = f"&{self.h}->{mangle(name)}"
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
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        # A register lives inside a register group, so its path is at least
        # `<group>.<reg>`. A one-element chain is a call on the GROUP itself
        # (`regs.set_handle(...)`), which is not a register access and must not
        # be judged as one.
        if not chain or len(chain) < 2 or chain[0][0] not in self.reg_fields:
            return None

        reg = chain[-1][0]
        segs = [c[0] for c in chain[:-1]]
        idx = [c[1] for c in chain if c[1] is not None]
        base = accessor_base(self.prefix, segs, reg, self.style)
        idx_args = "".join(f", {self.expr(i)}" for i in idx)
        args = [self.expr(a) for a in call.args]

        if func.attr in _REG_ACCESSORS:
            want = _REG_ACCESSORS[func.attr]
            if len(args) != want:
                raise ValueError(
                    f"register method '{func.attr}' takes {want} argument(s), "
                    f"got {len(args)}")
            if self.reg_map:
                return self._reg_map_access(chain, func.attr, args)
            return self._reg_access(base, func.attr, idx_args, args)

        raise ValueError(
            f"unsupported register method '{func.attr}' on '{'.'.join(segs + [reg])}'. "
            f"The C target emits a baked accessor per register method; a method "
            f"it does not know would otherwise become a generic call that "
            f"compiles and writes the wrong thing. Known: "
            f"{', '.join(sorted(_REG_ACCESSORS))}.")

    def _reg_path(self, chain) -> str:
        """`s->regs->bank[3].csr` for the PSS path `regs.bank[3].csr`.

        The PSS access path and the C member path are THE SAME PATH -- which is
        the whole reason the layout struct is worth having. Nothing is folded
        here and no offset appears: the struct states where each register is
        and the compiler does the arithmetic, so this walk only has to spell
        the hops.

        Only the first hop is through a pointer (the handle's layout member);
        everything after it is a member of a struct reached by value, hence
        `->` once and `.` thereafter. An index makes the element a value, so a
        subscripted first hop drops straight to `.`.
        """
        name, idx = chain[0]
        out = f"{self.h}->{mangle(name)}"
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

    def _reg_map_access(self, chain, method: str, args) -> str:
        """One register access, addressed through the layout struct.

        The primitives take a pointer and nothing else -- there is no bus
        handle on this seam and no address to pass, so the whole access is the
        member expression and a width.

        The masked write is rendered INLINE rather than as a call, because
        there is no per-register function left to put it in. It is still
        §21.14.1's read-modify-write and the read is still part of the
        definition, not an optimisation: on a register whose read clears its
        status bits, dropping it changes what the device does.
        """
        acc = self.accs.get(_stem(chain, self.prefix, self.style))
        prim = acc.prim if acc is not None else 32
        # A register whose value is a struct is carried as its value union;
        # the primitives move a plain word, so the union is opened here. The
        # `_val` spellings are already raw and must NOT be opened again.
        typed = acc is not None and acc.is_struct
        path = self._reg_path(chain)
        raw = f"&{path}"
        if method == "read":
            rd = f"read{prim}({raw})"
            # A compound literal, because this is an EXPRESSION: the accessor
            # that used to do `v.raw = ...; return v;` had a function body to
            # do it in, and there is no function here any more.
            return f"({acc.c_type}){{ .raw = {rd} }}" if typed else rd
        if method == "read_val":
            return f"read{prim}({raw})"
        if method in ("write", "write_val"):
            v = f"({args[0]}).raw" if (typed and method == "write") else args[0]
            return f"write{prim}({raw}, {v})"
        mask, val = args[0], args[1]
        return (f"write{prim}({raw}, (read{prim}({raw}) & ~({mask}))"
                f" | (({val}) & ({mask})))")

    def _reg_access(self, base: str, method: str, idx_args: str,
                    args) -> str:
        """One register access, through the funnel.

        The `_Acc` is looked up rather than reconstructed because a policy
        rendering a house macro needs the register itself -- its access mode,
        its width, its folded offset -- and none of that survives the
        underscore-joined stem. Falls back to the stem alone when the map has
        no entry, which happens for a register reached through a group whose
        accessors this component does not emit; the funnel then renders the
        plain accessor call, exactly as before.
        """
        acc = self.accs.get(base)
        if acc is None:
            return f"{self.mem.accessor(base, method)}({self.h}{idx_args}" + \
                   "".join(f", {a}" for a in args) + ")"
        if method in ("read", "read_val"):
            return self.mem.reg_read(acc, self.h, idx_args,
                                     raw=method.endswith("_val"))
        if method in ("write", "write_val"):
            return self.mem.reg_write(acc, self.h, idx_args, args[0],
                                      raw=method.endswith("_val"))
        return self.mem.reg_masked_write(acc, self.h, idx_args, args[0], args[1])

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

    def expr_constant(self, e) -> str:
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
        return self.arg_rename.get(e.name, e.name)

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
            if e.attr in self.comp_fields:
                return f"{self.h}->{mangle(e.attr)}"
            if e.attr in self.arg_names:
                return self.arg_rename[e.attr]
            # Not a member: a package-scope `static const`, which is a
            # plain identifier in C too.
            return e.attr
        if self.reg_style == "accessors":
            sf = self._struct_field(e)
            if sf is not None:
                ct, local, field = sf
                return f"{ct}_{field}_get({local})"
        return f"{self.expr(base)}.{e.attr}"

    def expr_subscript(self, e) -> str:
        return f"{self.expr(e.value)}[{self.expr(e.slice)}]"

    def expr_bin(self, e) -> str:
        op = _BINOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported binop {e.op.name}")
        return f"{self._operand(e.lhs)} {op} {self._operand(e.rhs)}"

    def expr_cast(self, e) -> str:
        # `(bit[32])x` -> `(uint32_t)x`. C widens implicitly where SV does
        # not, so this is mostly redundant here -- but dropping a cast the
        # model wrote is not this emitter's call to make, and the masked
        # register writes now emit one to state the register's width.
        return f"({c_type(e.target_type)})({self.expr(e.value)})"

    def expr_unary(self, e) -> str:
        op = _UNOP.get(e.op.name)
        if op is None:
            raise ValueError(f"unsupported unary op {e.op.name}")
        # Bracketed for the same reason `_operand` brackets binary operands:
        # the IR tree states the grouping and C precedence only sometimes
        # agrees. `~0` in particular has to reach the compiler as written.
        return f"{op}({self.expr(e.operand)})"

    # -- calls, one hook per Disposition -------------------------------------
    #
    # `expr_call` is `CallDispatch`'s: it classifies once and comes back here.
    # What used to be a four-branch chain whose ORDER was the classification is
    # now the registry's answer, and the registry is the same table the gate
    # already ran on this model.

    def call_names(self):
        """The model's own names, from the pass that vouched for these calls."""
        from ..validate_calls import model_names

        _, ops, ctors = model_names(self.comp, self.ctor_names)
        return dict(model_ops=ops, imports=frozenset(self.imports),
                    subcomps=ctors)

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
            return f"({self.expr(args[0])} + {self.expr(args[1])})"
        if name == "addr_value":
            # The handle IS the address here, so this is the identity.
            return self.expr(args[0])
        if name in _MEM_PRIMS:
            return self._mem_call(name, args)
        if name in ("message", "print"):
            return self._message_call(name, args)
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
        does, and picks up the same `volatile` access and the same
        PSSC_MEM_BARRIER. The only difference left is where the pointer comes
        from: a register's is a member of the layout struct, a descriptor's is
        an address the model computed, so it is cast here.

        Under the address seams (`vtable`, `direct`) this stays on the funnel:
        those route an address somewhere and have no pointer to dereference.
        """
        direction, width = _MEM_PRIMS[name]
        want = 1 if direction == "read" else 2
        if len(args) != want:
            raise ValueError(
                f"'{name}' takes {want} argument(s), got {len(args)}")
        rendered = [self.expr(a) for a in args]
        if self.reg_map:
            ptr = f"(volatile void *)(uintptr_t)({rendered[0]})"
            if direction == "read":
                return f"read{width}({ptr})"
            return f"write{width}({ptr}, {rendered[1]})"
        if direction == "read":
            return self.mem.read(width, self.h, rendered[0])
        return self.mem.write(width, self.h, rendered[0], rendered[1])

    def _message_call(self, name: str, args) -> str:
        if self.message_style == "none":
            # Drop the call AND its string. `.rodata` is a real budget on this
            # class of part, and a format string nothing prints is pure cost.
            return "(void)0"
        # message(verbosity, fmt, args...) -- the verbosity has no C analogue
        # and is dropped, exactly as the SV projection drops it.
        rest = args[1:] if (name == "message" and len(args) >= 2) else args
        rendered = ", ".join(self.expr(a) for a in rest)
        return f"pssc_message({rendered})"

    def _model_call(self, call) -> str:
        """A call on the model itself -- another operation of this component.

        `wait_hint()` inside `wait_completion()` is the case that matters. It
        needs the handle threaded through, which the generic path would not do.
        """
        func = call.func
        args = [self.expr(a) for a in call.args]
        name = None
        if _dt_name(func) == "ExprRefUnresolved":
            name = func.name
        elif _dt_name(func) == "ExprAttribute" and \
                _dt_name(func.value) == "TypeExprRefSelf":
            name = func.attr
        if name is not None and name in self.model_ops:
            return f"{self.style.symbol(self.prefix, mangle(name))}(" + \
                   ", ".join([self.h] + args) + ")"
        # An `import target/solve function`: a bare call, and NO handle -- an
        # import is a platform function, not a method of this component, so
        # passing `s` would invent a parameter its declaration does not have.
        # The prototype comes from the same declaration (`lower_imports`), so
        # the two cannot disagree about the signature.
        if name is not None and name in self.imports:
            return f"{mangle(name)}(" + ", ".join(args) + ")"
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
        ct = c_type(s.annotation)
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
        if getattr(s, "value", None) is not None:
            return [f"{pad}{ct} {name} = {self.expr(s.value)};"] + tail
        if _dt_name(s.annotation) == _DT_STRUCT:
            # zero reserved/padding bits
            return [f"{pad}{ct} {name} = {{0}};"] + tail
        return [f"{pad}{ct} {name};"] + tail

    def stmt_assign(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        tgt = s.targets[0]
        if self.reg_style == "accessors":
            sf = self._struct_field(tgt)
            if sf is not None:
                ct, local, field = sf
                return [f"{pad}{ct}_{field}_set(&{local}, {self.expr(s.value)});"]
        return [f"{pad}{self.expr(tgt)} = {self.expr(s.value)};"]

    def stmt_aug_assign(self, s, ind: int) -> List[str]:
        op = _BINOP.get(s.op.name)
        if op is None:
            raise ValueError(
                f"unsupported augmented-assign op {s.op.name}")
        return [f"{self.pad(ind)}{self.expr(s.target)} {op}= "
                f"{self.expr(s.value)};"]

    def stmt_expr(self, s, ind: int) -> List[str]:
        return [f"{self.pad(ind)}{self.expr(s.expr)};"]

    def stmt_return(self, s, ind: int) -> List[str]:
        pad = self.pad(ind)
        if s.value is not None:
            return [f"{pad}return {self.expr(s.value)};"]
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

    def stmt_foreach(self, s, ind: int) -> List[str]:
        """`foreach (a[i]) { ... }` -> an indexed `for`.

        The bound is the array's own size, folded at generation time. C has no
        way to ask an array its length through a pointer, and the collection
        here is always a component member with a size the model states -- so
        the alternative is a magic number in the generated body, which is the
        same number stated twice.
        """
        pad = self.pad(ind)
        idx = mangle(getattr(getattr(s, "target", None), "name", None) or "i")
        coll = self.expr(s.iter)
        n = _array_size(self._iter_dtype(s))
        if n is None:
            raise ValueError(
                f"cannot lower `foreach` over '{coll}': its size is not known "
                "at generation time. C needs a bound, and guessing one would "
                "produce a loop that runs off the end of the array.")
        lines = [f"{pad}for (unsigned {idx} = 0; {idx} < {n}u; {idx}++) {{"]
        lines += self.stmts(s.body, ind + 1)
        lines.append(f"{pad}}}")
        return lines

    def _iter_dtype(self, s):
        """Declared type of a `foreach` collection, when it is a member."""
        it = s.iter
        if _dt_name(it) == "ExprAttribute" and \
                _dt_name(it.value) == "TypeExprRefSelf":
            for f in getattr(self.comp, "fields", []):
                if f.name == it.attr:
                    return f.datatype
        return None

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

    def _default_arm(self, pad: str, ind: int) -> List[str]:
        """What an unmatched subject does when the model states no default."""
        if self.match_default == "none":
            return []
        out = [f"{pad}default:"]
        if self.match_default == "message" and self.message_style != "none":
            out.append(f'{pad}    pssc_message("{self.prefix}: unmatched '
                       f'match subject in {self.fn.name}");')
        elif self.match_default == "unreachable":
            out.append(f"{pad}    PSSC_UNREACHABLE();")
        out.append(f"{pad}    break;")
        return out

    def _pattern_labels(self, pattern) -> List[str]:
        if pattern is None:
            return []
        cn = _dt_name(pattern)
        if cn == "PatternValue":
            return [self.expr(pattern.value)]
        if cn in ("PatternOr", "PatternSequence"):
            out: List[str] = []
            for p in pattern.patterns:
                out += self._pattern_labels(p)
            return out
        raise ValueError(f"unsupported match pattern {cn}")

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

    def _group_call(self, call) -> Optional[str]:
        """A call on a register GROUP (not on a register inside one)."""
        func = call.func
        if _dt_name(func) != "ExprAttribute":
            return None
        chain = self._chain(func.value)
        if not chain or len(chain) != 1 or chain[0][0] not in self.reg_groups:
            return None
        group = self.reg_groups[chain[0][0]]
        m = func.attr
        args = call.args
        if m == "set_handle":
            # The group has no object: binding it IS setting the component's
            # base, and every accessor folds its offset from there.
            return f"({self.h}->base = {self.expr(args[0])})"
        if m == "get_offset_of_instance":
            return f"0x{scalar_offset(group, _str_const(args[0], m)):x}u"
        if m == "get_offset_of_instance_array":
            base, stride = array_base_stride(group, _str_const(args[0], m))
            return f"(0x{base:x}u + 0x{stride:x}u * {self._operand(args[1])})"
        raise ValueError(
            f"unsupported register-group method '{m}'. The C target folds "
            f"group offsets at generation time; a method it does not know "
            f"would become a call to a group object that does not exist.")

    def _sub_ctor_call(self, call) -> Optional[str]:
        """`ch[i].initialize(...)` -> `<sub>_init(&s->ch[i], ...)`."""
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
        fwd = [elem]
        if self.link_style == "vtable":
            # The child gets the parent's bus. This is the ONE piece of state
            # that flows down the tree, and it flows at construction so no
            # operation ever has to walk anywhere to find it.
            fwd.append(f"{self.h}->bus")
        fwd += [self.expr(a) for a in call.args]
        return (f"{self.style.symbol(sub_prefix, 'init')}("
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


class _CtorEmitter(_CtorMixin, _BodyEmitter):
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
    if emitter_cls is _BodyEmitter:
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

def _addr_arg(ctor) -> Optional[str]:
    """The constructor's first address-handle argument, or ``None``."""
    if ctor is None:
        return None
    for a in ctor.args.args:
        if c_type(a.annotation) == "pssc_addr_t":
            return mangle(a.arg)
    return None


def _sig_all(node, prefixes, link_style: str, qual: str, is_root: bool,
             lifecycle: str = "malloc", style=None,
             ctor_names=None) -> List[str]:
    """Every file-scope prototype for one component.

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
    sym, prefix_t = style.symbol, style.type_name(prefix)
    ctor = _ctor(comp, ctor_names)
    cp = _create_params(ctor, link_style)
    sig_params = f", {cp}" if cp else ""
    out = [f"{qual}void {sym(prefix, 'init')}({prefix_t} *self{sig_params});"]
    if is_root and lifecycle == "malloc":
        out.append(
            f"{qual}{prefix_t} *{sym(prefix, 'create')}({cp or 'void'});")
        out.append(f"{qual}void {sym(prefix, 'destroy')}({prefix_t} *self);")
    for fn in _operations(comp, ctor_names):
        # On the prototype, which is the API surface a caller reads. Repeated
        # on the definition below, which is what someone debugging reads --
        # the same deliberate duplication the SV target makes.
        blank_line(out)
        out += doc_block(getattr(fn, "doc", None), "", BLOCK)
        out.append(f"{_op_signature(fn, prefix, qual, style)};")
    return out


def lower_decls(model, prefixes, *, link_style: str = "vtable",
                static_inline: bool = False,
                lifecycle: str = "malloc", style=None) -> str:
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
                          ctor_names=model.ctor_names)
        lines.append("")
    return "\n".join(lines).rstrip()


def _lifecycle_impl(node, prefixes, link_style: str, qual: str,
                    reg_style: str, is_root: bool, lifecycle: str = "malloc",
                    reg_map: bool = False,
                    style=None, emitter_cls=None, ctor_names=None,
                    **be_kw) -> List[str]:
    style = _style(style)
    comp = node.dtype
    prefix = prefixes[comp]
    sym, prefix_t = style.symbol, style.type_name(prefix)
    ctor = _ctor(comp, ctor_names)
    cp = _create_params(ctor, link_style)
    sig_params = f", {cp}" if cp else ""

    lines = [f"{qual}void {sym(prefix, 'init')}({prefix_t} *self{sig_params}) {{"]
    if link_style == "vtable":
        lines.append("    self->bus = bus;")
    # THE DEFAULT BINDING, which the ctor body below may then override with its
    # own `regs.set_handle(...)`.
    #
    # Both exist because models legitimately do it both ways, and dropping
    # either breaks a working model. `src/pssc/testing/models` declares
    # `solve function void ctor(addr_handle_t base) { }` with an EMPTY body and
    # says so: "the binding is the generator's job, not the model's". The WB DMA
    # model states it instead. Emitting only the body's version left the example
    # with an unused parameter and a base of 0; emitting only this one ignored
    # what the real model actually said.
    #
    # The FIRST ADDRESS-TYPED argument, not args[0]: `initialize(int id,
    # addr_handle_t bank)` would otherwise bind the base to the channel number.
    addr_arg = _addr_arg(ctor)
    if addr_arg is not None:
        lines.append(f"    self->base = {addr_arg};")
    else:
        # Nothing to bind. 0 is the honest answer: every accessor then offsets
        # from 0, which is visibly wrong in a trace rather than quietly wrong.
        lines.append("    self->base = 0;")
    if reg_map:
        # The one cast in the generated driver, and it is here rather than at
        # every access for that reason. `base` is what the model's constructor
        # was handed; the layout is how this component reads it.
        for f in comp.fields:
            if field_is_reg_group(f):
                mt = map_type_name(f.datatype, style)
                lines.append(f"    self->{mangle(f.name)} = "
                             f"({mt} *)(uintptr_t)self->base;")
    for f in channel_fields(comp):
        lines.append(f"    pssc_chan1_init(&self->{mangle(f.name)});")
    lines += _field_defaults(comp)
    if ctor is not None:
        ctor_cls = ctor_emitter_cls(emitter_cls or _BodyEmitter)
        be = ctor_cls(ctor, comp, prefix, reg_style=reg_style,
                      handle="self", prefixes=prefixes,
                      link_style=link_style, style=style,
                      ctor_names=ctor_names, reg_map=reg_map, **be_kw)
        lines += be.stmts(ctor.body, 1)
    lines.append("}")

    # `_create`/`_destroy` are the ONLY things in a generated image that call an
    # allocator, so `--lifecycle static` is not a convenience: on a part with no
    # heap, linking malloc pulls in a sbrk stub and a several-KiB allocator for
    # one object whose size is known at compile time. The caller supplies the
    # storage instead -- `static wb_dma_t dma; wb_dma_init(&dma, ...)`.
    if is_root and lifecycle == "malloc":
        fwd = (["bus"] if link_style == "vtable" else []) + \
              ([mangle(a.arg) for a in ctor.args.args] if ctor else [])
        fwd_s = ", ".join(fwd)
        lines.append(
            f"{qual}{prefix_t} *{sym(prefix, 'create')}({cp or 'void'}) {{")
        lines.append(
            f"    {prefix_t} *self = ({prefix_t} *)malloc(sizeof({prefix_t}));")
        init_args = f", {fwd_s}" if fwd_s else ""
        lines.append(f"    if (self) {sym(prefix, 'init')}(self{init_args});")
        lines.append("    return self;")
        lines.append("}")
        lines.append(
            f"{qual}void {sym(prefix, 'destroy')}({prefix_t} *self) "
            f"{{ free(self); }}")
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

    def body_emitter(self, fn):
        """The emitter that renders ``fn``'s body."""
        return self.emitter_cls(fn, self.comp, self.prefix, **self.be_kw)


def lower_operation(fn, ctx: OpCtx) -> List[str]:
    """One exported operation: doc comment, signature, body, closing brace.

    Returns LINES, with no leading or trailing blank -- the caller separates.
    """
    be = ctx.body_emitter(fn)
    style = ctx.be_kw.get("style")
    lines = list(doc_block(getattr(fn, "doc", None), "", BLOCK))
    lines.append(f"{_op_signature(fn, ctx.prefix, ctx.qual, style)} {{")
    body = be.stmts(fn.body, 1)
    # An operation that touches neither a register nor a member never
    # names the handle, and `-Wextra -Werror` -- which any real firmware
    # build uses, and which the C gate uses -- rejects the unused
    # parameter. The handle stays in the signature regardless: dropping
    # it would make the API shape depend on a body detail, so a later
    # edit that started using a member would silently change every
    # caller's call.
    # Code only: the body now carries the PSS source's prose, and a
    # comment that mentions `s->` would make an operation that never
    # touches the handle look as though it did -- reinstating the
    # -Wunused-parameter error this suppresses.
    code = [ln for ln in body
            if not ln.lstrip().startswith(("/*", "*", "//"))]
    if not any("s->" in ln or "pssc_bus(s)" in ln or "(s," in ln or  # seam-ok: reads emitted text
               "(s)" in ln or "&s->" in ln for ln in code):
        lines.append("    (void)s;")
    lines += body
    lines.append("}")
    return lines


def lower_impl(model, prefixes, *, link_style: str = "vtable",
               static_inline: bool = False, reg_style: str = "bitfields",
               yield_mode: str = "none", match_default: str = "message",
               message_style: str = "import",
               lifecycle: str = "malloc", imports=None, style=None,
               accs=None, emitter_cls=None, emit_operation=None,
               reg_map: bool = False) -> str:
    """Lifecycle + operation bodies for every component, CHILDREN FIRST.

    ``emit_operation(fn, ctx)`` renders one operation; it defaults to
    :func:`lower_operation` and is how the backend inserts itself into this
    loop without owning the walk.
    """
    qual = "static inline " if static_inline else ""
    emitter_cls = emitter_cls or _BodyEmitter
    emit_operation = emit_operation or lower_operation
    be_kw = dict(yield_mode=yield_mode, match_default=match_default,
                 message_style=message_style, imports=imports, style=style,
                 accs=accs, reg_map=reg_map)
    ctor_names = model.ctor_names
    lines: List[str] = ["/* ----- Component lifecycle + operations. ----- */"]
    for node in post_order(model):
        comp = node.dtype
        prefix = prefixes[comp]
        lines.append(f"/* --- {getattr(comp, 'name', '?')} --- */")
        lines += _lifecycle_impl(node, prefixes, link_style, qual, reg_style,
                                 is_root=model.is_root(comp),
                                 lifecycle=lifecycle, ctor_names=ctor_names,
                                 emitter_cls=emitter_cls, **be_kw)
        lines.append("")
        ctx = OpCtx(comp=comp, prefix=prefix, qual=qual,
                    emitter_cls=emitter_cls,
                    be_kw=dict(reg_style=reg_style, prefixes=prefixes,
                               link_style=link_style, ctor_names=ctor_names,
                               **be_kw))
        for fn in _operations(comp, ctor_names):
            blank_line(lines)
            lines += emit_operation(fn, ctx)
            lines.append("")
    return "\n".join(lines).rstrip()
