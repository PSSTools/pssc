"""Backend-neutral model for programming-sequence generation.

Walks a PSS component tree (rooted at a named component) and classifies each
reachable component, and classifies functions by kind. Deliberately free of any
SystemVerilog/C specifics so future backends (SV now; C-embedded, C++/host
later -- design §10.6) can share it.

See docs/design/pss-programming-seq-gen-design.md (§6.2) and
docs/design/pss-programming-seq-gen-impl-plan.md (Phase 0 findings).
"""
from __future__ import annotations

import contextlib
import dataclasses as dc
import warnings
from contextvars import ContextVar
from enum import Enum
from typing import Dict, FrozenSet, List, Optional, Tuple


# --- Function kind ---------------------------------------------------------

class FuncKind(Enum):
    """How a PSS function maps to a generated artifact."""
    CONSTRUCTOR = "constructor"   # solve `ctor` -> impl constructor
    EXPORT_OP = "export_op"       # component operation -> export-API task
    EXPORT_SOLVE = "export_solve"  # export solve fn -> export-API function
    IMPORT_TASK = "import_task"   # import target fn -> import-API task
    IMPORT_SOLVE = "import_solve"  # import solve fn -> import-API function
    REG_OFFSET = "reg_offset"     # get_offset_of_instance[_array] -- evaluated, not emitted


#: Register-group offset functions are consumed by the generator (to compute
#: addresses) rather than emitted as callable API.
_REG_OFFSET_FNS = frozenset({"get_offset_of_instance", "get_offset_of_instance_array"})

#: Names a `solve function void` may use to mean "this is the constructor".
#:
#: Three spellings, because the convention differs and none is wrong:
#:
#:   ctor        this generator's own example
#:   init        the PSS coding guidelines' spelling, written `\init` -- an
#:               escaped identifier, since `init` is reserved -- and recorded in
#:               the IR under the plain name
#:   initialize  the same thing without the escape. The WB DMA model moved to
#:               this spelling precisely to stop needing the backslash, and a
#:               name that has to be escaped to be legal is a name most models
#:               will eventually stop using.
#:
#: Hard-coding `ctor` alone did not fail loudly. A model whose constructor was
#: called `init` had its constructor classified as an ordinary export function,
#: and the generated class then constructed its register model from an
#: undeclared `base` variable: code that looks right and does not compile. The
#: rename to `initialize` reproduced exactly that, which is why the third
#: spelling is here rather than left to `--ctor-name`.
DEFAULT_CTOR_NAMES = frozenset({"ctor", "init", "initialize"})

#: The set in effect for the compile currently running.
#:
#: A ContextVar entered and RESTORED by `ctor_names_scope`, not a module global
#: that each target overwrites on entry. The global version leaked across
#: compiles: `--ctor-name build` in one `pssc.compile()` call left `build` as
#: the only recognised name for every later call in the same process, so the
#: next model's `initialize` was classified as an ordinary export function --
#: which generates a class that constructs its register model from an
#: undeclared variable. Silent, and only reachable when more than one compile
#: shares a process, which is exactly what a test suite and a dv-flow run do.
#:
#: Still one value per context rather than a parameter on every call: threading
#: it through the ~20 `func_kind` call sites belongs with `OpModel`, which
#: carries it as a field (P2 of docs/design/generator-style-extensions-plan.md). A
#: ContextVar is correct under asyncio and under sequential reuse -- the two
#: ways compiles actually share a process today -- and wrong only for two
#: compiles running in different THREADS at the same time, which nothing does.
_ctor_names: "ContextVar[FrozenSet[str]]" = ContextVar(
    "pssc_ctor_names", default=DEFAULT_CTOR_NAMES)


def current_ctor_names() -> FrozenSet[str]:
    """The constructor-name set in effect for this compile."""
    return _ctor_names.get()


@contextlib.contextmanager
def ctor_names_scope(name: Optional[str]):
    """Make ``name`` the sole constructor spelling for the duration of a run.

    ``None`` means "use the defaults" -- which is a real setting, not a no-op:
    it restores the defaults inside a surrounding scope that narrowed them.
    """
    token = _ctor_names.set(frozenset({name}) if name else DEFAULT_CTOR_NAMES)
    try:
        yield
    finally:
        _ctor_names.reset(token)


def set_ctor_name(name: Optional[str]) -> None:
    """Override which `solve function` name means "constructor". Deprecated.

    Deprecated because it has no matching restore: the value it sets outlives
    the compile that set it and silently changes the next one. Use
    `ctor_names_scope`, which is the same thing with an end.
    """
    warnings.warn(
        "set_ctor_name() leaks its setting into the next compile in this "
        "process; use `with ctor_names_scope(name):` instead",
        DeprecationWarning, stacklevel=2)
    _ctor_names.set(frozenset({name}) if name else DEFAULT_CTOR_NAMES)


def func_kind(fn, ctor_names: Optional[FrozenSet[str]] = None) -> FuncKind:
    """Classify an ``ir.Function`` by the flags the front end already sets.

    Phase-0 finding: the IR carries ``is_import`` / ``is_target`` / ``is_solve``;
    component operations carry none of them. ``ctor`` is the solve constructor;
    the register offset functions are recognized by name.

    ``ctor_names`` defaults to the set in effect for the running compile. Pass
    it explicitly where the caller already has it to hand -- that is the form
    that survives when the ambient value goes away.
    """
    if fn.name in _REG_OFFSET_FNS:
        return FuncKind.REG_OFFSET
    if getattr(fn, "is_solve", False):
        names = current_ctor_names() if ctor_names is None else ctor_names
        return FuncKind.CONSTRUCTOR if fn.name in names else FuncKind.EXPORT_SOLVE
    if getattr(fn, "is_import", False):
        return FuncKind.IMPORT_SOLVE if getattr(fn, "is_solve", False) else FuncKind.IMPORT_TASK
    return FuncKind.EXPORT_OP


# --- Component classification ----------------------------------------------

class CompKind(Enum):
    REG_GROUP = "reg_group"     # super chain includes reg_group_c
    REGULAR = "regular"         # everything else (has operations / sub-components)


def _super_ref_name(dtype) -> Optional[str]:
    """Return the immediate super type's ``ref_name`` if present, else None."""
    sup = getattr(dtype, "super", None)
    if sup is None:
        return None
    return getattr(sup, "ref_name", None) or getattr(sup, "name", None)


def is_reg_group(dtype) -> bool:
    """True if ``dtype`` is (or derives from) ``reg_group_c``.

    The IR node type is the primary answer: the front end builds a
    ``DataTypeRegisterGroup`` for a group however deep the derivation is. The
    super-name check remains as a fallback for datatypes that reach a backend
    by another path.
    """
    if _dt_name(dtype) == _DT_REGISTER_GROUP:
        return True
    return _super_ref_name(dtype) == "reg_group_c"


def is_register(dtype) -> bool:
    """True if ``dtype`` is a register -- inline ``reg_c<...>`` or a named type
    declared as ``pure component X : reg_c<...>``. Both reach the IR as
    ``DataTypeRegister``; before the front end recovered the template arguments
    of the named form, the second silently became an ordinary component."""
    if _dt_name(dtype) == _DT_REGISTER:
        return True
    return _super_ref_name(dtype) == "reg_c"


def comp_kind(dtype) -> CompKind:
    return CompKind.REG_GROUP if is_reg_group(dtype) else CompKind.REGULAR


# --- Field / sub-component introspection -----------------------------------

# IR datatype class names (matched by name to avoid importing ir here).
_DT_REGISTER = "DataTypeRegister"
_DT_REGISTER_GROUP = "DataTypeRegisterGroup"
_DT_ARRAY = "DataTypeArray"
_DT_CHANNEL = "DataTypeChannel"


def _dt_name(dtype) -> str:
    return type(dtype).__name__


def field_is_register(field) -> bool:
    # Not array-aware on purpose: callers dispatch on scalar-register /
    # scalar-group / array in that order, and an array of registers is emitted
    # differently from one register.
    return is_register(field.datatype)


def field_is_reg_group(field) -> bool:
    return is_reg_group(field.datatype)


def field_is_array(field) -> bool:
    return _dt_name(field.datatype) == _DT_ARRAY


def field_is_channel(field) -> bool:
    """True for a ``sync_pkg::channel_c<Te, DEPTH>`` instance.

    A channel is neither a sub-component nor plain data, and it is not a type a
    backend emits: its implementation belongs to the runtime (a mailbox in SV, a
    FIFO in C). So every place that partitions a component's fields has to name
    it, or it falls into whichever bucket has the loosest test -- which is how
    it first came out as an empty component class.
    """
    return _dt_name(field.datatype) == _DT_CHANNEL


def channel_fields(comp) -> List[object]:
    """Channel instances held by ``comp``, in declaration order."""
    return [f for f in (getattr(comp, "fields", []) or []) if field_is_channel(f)]


def array_element_type(field):
    """Element datatype of an array field."""
    return field.datatype.element_type


def array_size(field) -> int:
    return int(field.datatype.size)


@dc.dataclass
class SubComp:
    """A regular sub-component instance held by a component."""
    name: str                    # field name, e.g. "ch"
    dtype: object                # the sub-component's DataTypeComponent
    size: Optional[int] = None   # element count, or None for a scalar instance

    @property
    def is_array(self) -> bool:
        return self.size is not None


def sub_components(comp) -> List[SubComp]:
    """Regular sub-component instances of ``comp``, in declaration order.

    Register groups are not sub-components -- they are the register model, which
    is never exposed. Arrays of components are reported with their size; a size
    that did not fold (-1) is reported as-is rather than skipped, so it fails
    where it is used instead of silently producing a component with one fewer
    child.
    """
    out: List[SubComp] = []
    for f in getattr(comp, "fields", []) or []:
        dt = f.datatype
        if _dt_name(dt) == "DataTypeComponent" and not is_reg_group(dt):
            out.append(SubComp(name=f.name, dtype=dt))
        elif field_is_array(f):
            elem = array_element_type(f)
            if _dt_name(elem) == "DataTypeComponent" and not is_reg_group(elem):
                out.append(SubComp(name=f.name, dtype=elem, size=int(dt.size)))
    return out


@dc.dataclass
class CompNode:
    """One component type in the walked tree."""
    name: str                       # qualified IR name
    dtype: object                   # DataTypeComponent / RegisterGroup
    kind: CompKind
    children: List["CompNode"] = dc.field(default_factory=list)


def walk_tree(root_dtype, resolve) -> CompNode:
    """Walk the component tree from ``root_dtype``.

    ``resolve(dtype) -> dtype`` maps a (possibly ref/array-element) datatype to
    its defining component datatype; callers supply it so this module stays
    independent of the type table. Register-group and regular sub-components are
    both recorded; register/array-of-register leaves are not components and are
    handled by the register-model emitter.
    """
    seen: Dict[int, CompNode] = {}

    def visit(dtype) -> CompNode:
        key = id(dtype)
        if key in seen:
            return seen[key]
        node = CompNode(
            name=getattr(dtype, "name", None) or "<anon>",
            dtype=dtype,
            kind=comp_kind(dtype),
        )
        seen[key] = node
        for f in getattr(dtype, "fields", []) or []:
            child_dt = None
            if field_is_reg_group(f):
                child_dt = f.datatype
            elif field_is_array(f) and _dt_name(array_element_type(f)) in (
                    _DT_REGISTER_GROUP, "DataTypeComponent"):
                # An ARRAY of sub-components is a child too. Following only
                # scalar instances hid every per-channel component in a model
                # that -- like every real one -- declares its channels as an
                # array.
                child_dt = array_element_type(f)
            elif _dt_name(f.datatype) == "DataTypeComponent":
                child_dt = f.datatype
            if child_dt is not None:
                node.children.append(visit(resolve(child_dt)))
        return node

    return visit(root_dtype)


# --- affine offset evaluation (language-neutral) ---------------------------
# Hoisted from sv/lower_reg_model.py so every backend (SV, C, C++) shares one
# copy. These evaluate the PSS `get_offset_of_instance[_array]` bodies, which are
# entirely language-neutral; SV, C, and C++ all need (base, stride) and scalar
# offsets. See docs/design/pss-c-cpp-progseq-gen-design.md §6.1.

_DT_STRUCT = "DataTypeStruct"


def _eval_off(expr, index: int) -> int:
    """Evaluate an affine offset expression at array ``index``."""
    cn = _dt_name(expr)
    if cn == "ExprConstant":
        return int(expr.value)
    if cn == "ExprAttribute" and getattr(expr, "attr", None) == "index":
        return index
    if cn == "ExprBin":
        l = _eval_off(expr.lhs, index)
        r = _eval_off(expr.rhs, index)
        op = expr.op.name
        if op in ("Add",):
            return l + r
        if op in ("Sub", "Minus"):
            return l - r
        if op in ("Mul", "Mult"):
            return l * r
        raise ValueError(f"unsupported offset op {op}")
    raise ValueError(f"unsupported offset expr {cn}")


def _pattern_str(pattern) -> Optional[str]:
    """The string value of a ``PatternValue`` whose value is a string constant,
    else None. (``PatternValue.value`` is an ``ExprConstant``.)"""
    if _dt_name(pattern) != "PatternValue":
        return None
    v = getattr(pattern, "value", None)
    inner = getattr(v, "value", None)
    return inner if isinstance(inner, str) else None


class OffsetFoldError(ValueError):
    """A ``get_offset_of_instance[_array]`` that cannot be evaluated.

    Carries the message only; the caller adds the source location, the same
    split ``reg_field_resolve.RegFieldError`` uses.

    Every one of these is an ERROR rather than a fallback, and the reason is
    the ``default: return -1;`` arm the generated register packages carry: -1
    is an ERROR SENTINEL, not a value. Folding it -- or emitting the call so it
    returns at run time -- puts 0xFFFF_FFFF_FFFF_FFFF into an address
    computation, where it wraps to a wild address that no simulator will
    complain about. `src/pss/wb_dma_c.pss` documents exactly this hazard for a
    renamed RDL instance; refusing to fold turns that documented footgun into a
    build failure.
    """


def _index_names(fn) -> Tuple[str, ...]:
    """Names the index parameter may appear under in an offset function body.

    The declared parameter name, plus ``index`` -- which is what the IR carries
    when the reference reaches it as an attribute rather than a local.
    """
    names = {"index"}
    args = getattr(getattr(fn, "args", None), "args", None) or []
    if len(args) >= 2:
        names.add(args[1].arg)
    return tuple(names)


def _affine(expr, index_names: Tuple[str, ...]) -> Tuple[int, int]:
    """Match ``c0 + index*c1`` structurally; return ``(c0, c1)``.

    STRUCTURAL, not sampled. The previous implementation evaluated the arm at
    index 0 and index 1 and subtracted, which ASSUMES affinity rather than
    checking it: ``0x20 + index*index*0x20`` evaluates cleanly at both sample
    points (base 0x20, stride 0x20) and is wrong from index 2 onward -- silently,
    with no diagnostic and wild addresses downstream. Multiplying two
    index-dependent terms is rejected here instead.
    """
    cn = _dt_name(expr)
    if cn == "ExprConstant":
        return int(expr.value), 0
    if cn == "ExprRefLocal" and getattr(expr, "name", None) in index_names:
        return 0, 1
    if cn == "ExprAttribute" and getattr(expr, "attr", None) in index_names:
        return 0, 1
    if cn == "ExprUnary" and expr.op.name in ("USub", "Minus"):
        c0, c1 = _affine(expr.operand, index_names)
        return -c0, -c1
    if cn == "ExprBin":
        lc0, lc1 = _affine(expr.lhs, index_names)
        rc0, rc1 = _affine(expr.rhs, index_names)
        op = expr.op.name
        if op == "Add":
            return lc0 + rc0, lc1 + rc1
        if op in ("Sub", "Minus"):
            return lc0 - rc0, lc1 - rc1
        if op in ("Mul", "Mult"):
            # Legal only if at most one operand varies with the index.
            if lc1 and rc1:
                raise OffsetFoldError(
                    "the offset expression is not affine in the array index "
                    "(the index is multiplied by itself)")
            if rc1:
                lc0, lc1, rc0, rc1 = rc0, rc1, lc0, lc1
            return lc0 * rc0, lc1 * rc0
        raise OffsetFoldError(
            f"unsupported operator '{op}' in an offset expression; "
            f"it must be affine in the array index")
    raise OffsetFoldError(
        f"unsupported expression '{cn}' in an offset expression; "
        f"it must be affine in the array index")


def _offset_fn(group_dtype, fname: str):
    """The named offset function of ``group_dtype``, or None."""
    for fn in getattr(group_dtype, "functions", []) or []:
        if fn.name == fname:
            return fn
    return None


def array_base_stride(group_dtype, field_name: str) -> Tuple[int, int]:
    """``(base, stride)`` for an instance array, from the group's own
    ``get_offset_of_instance_array``.

    Raises :class:`OffsetFoldError` naming the group and the instance when no
    arm matches -- the case a renamed RDL instance produces, which the PSS
    function itself answers with the -1 sentinel.
    """
    gname = _strip_pkg_name(group_dtype)
    fn = _offset_fn(group_dtype, "get_offset_of_instance_array")
    if fn is None:
        raise OffsetFoldError(
            f"register group '{gname}' declares no "
            f"get_offset_of_instance_array")
    if not fn.body or _dt_name(fn.body[0]) != "StmtMatch":
        raise OffsetFoldError(
            f"'{gname}.get_offset_of_instance_array' is not a match over the "
            f"instance name, so its offsets cannot be evaluated at build time")
    idx = _index_names(fn)
    for case in fn.body[0].cases:
        if _pattern_str(case.pattern) == field_name:
            return _affine(case.body[0].value, idx)
    raise OffsetFoldError(
        f"register group '{gname}' declares no instance array named "
        f"'{field_name}' (its get_offset_of_instance_array would return the "
        f"-1 error sentinel)")


def scalar_offset(group_dtype, name: str) -> int:
    """Byte offset of a scalar instance within ``group_dtype``."""
    omap = getattr(group_dtype, "offset_map", None) or {}
    if name not in omap:
        raise OffsetFoldError(
            f"register group '{_strip_pkg_name(group_dtype)}' declares no "
            f"instance named '{name}' (its get_offset_of_instance would "
            f"return the -1 error sentinel)")
    return int(omap[name])


def _strip_pkg_name(dtype) -> str:
    nm = getattr(dtype, "name", None) or _dt_name(dtype)
    return nm.split("::")[-1]


def _array_base_stride(group_dtype, field_name: str) -> Tuple[int, int]:
    """Back-compat alias for :func:`array_base_stride`."""
    return array_base_stride(group_dtype, field_name)


def _scalar_offset(group_dtype, name: str) -> int:
    """Back-compat alias for :func:`scalar_offset`.

    It used to be a bare ``offset_map[name]``, so an unknown instance escaped as
    a ``KeyError`` with no group, no instance name and no source location.
    """
    return scalar_offset(group_dtype, name)


# --- value-struct / reg-group collection walks (language-neutral) ----------
# Hoisted from sv/lower_reg_model.py; they produce the ordered type lists every
# backend emits. See design §6.2.

def _is_reserved(field) -> bool:
    """Reserved-gap fields (leading underscore) are not surfaced; their space is
    already accounted for in following siblings' offsets."""
    return field.name.startswith("_")


def collect_reg_groups(root_dtype) -> List[object]:
    """Post-order list of unique reg-group datatypes reachable from ``root``
    (nested groups before the groups that use them)."""
    out: List[object] = []
    seen = set()

    def visit(dt):
        if id(dt) in seen:
            return
        seen.add(id(dt))
        for f in getattr(dt, "fields", []) or []:
            child = None
            if field_is_reg_group(f):
                child = f.datatype
            elif field_is_array(f) and _dt_name(array_element_type(f)) == _DT_REGISTER_GROUP:
                child = array_element_type(f)
            if child is not None and is_reg_group(child):
                visit(child)
        out.append(dt)

    # Start from reg-group fields of the (possibly regular) root.
    if is_reg_group(root_dtype):
        visit(root_dtype)
    else:
        for f in getattr(root_dtype, "fields", []) or []:
            if field_is_reg_group(f) and is_reg_group(f.datatype):
                visit(f.datatype)
    return out


def collect_value_structs(groups: List[object]) -> List[object]:
    """Unique register value structs used across ``groups``, first-use order."""
    out: List[object] = []
    seen = set()

    def consider(reg_dtype):
        vt = reg_dtype.register_value_type
        if _dt_name(vt) == _DT_STRUCT and id(vt) not in seen:
            seen.add(id(vt))
            out.append(vt)

    for g in groups:
        for f in g.fields:
            if field_is_register(f):
                consider(f.datatype)
            elif field_is_array(f) and _dt_name(array_element_type(f)) == _DT_REGISTER:
                consider(array_element_type(f))
    return out
