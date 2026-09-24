"""What a generated Python symbol is called.

One module, because a name is decided in one place and reconstructed in
several: the register-model lowering DEFINES `regs_csr_read`, the body emitter
CALLS it, and the manifest reports it. Those were three hand-kept tables in the
C backend before `mem_access.py` collected them, and that history is the whole
reason this file exists at 60 lines rather than as three helpers scattered
across the backend.

Python identifiers are permissive enough that most PSS names pass through
unchanged, which is deliberate: a generated API whose members are spelled the
way the model spells them is one an author can navigate without a mapping
table. Only two things are rewritten -- a keyword collision, and a component
type name becoming a class name.
"""
from __future__ import annotations

import keyword
from typing import Sequence

__all__ = ["mangle", "class_name", "module_name", "reg_symbol", "strip_suffix",
           "IMPORTS_ATTR", "IMPORTS_PARAM", "INIT_METHOD", "EXECUTOR_ATTR",
           "CONSTRUCT_METHOD", "CTOR_METHOD", "init_kind_method",
           "field_storage", "group_base",
           "BIND_METHOD", "DEFAULT_EXECUTOR", "def_kw", "is_async"]

#: The constructor parameter carrying the import API, and the attribute it is
#: stored in. TWO names for one thing, and they are here rather than as literals
#: because the seam is spelled in four modules -- the constructor that binds it,
#: the register accessors, the body emitter's call sites, and the sub-component
#: construction that passes it down. Renaming it was a three-file sweep once;
#: the async work edits every one of those same call sites again, and a sweep
#: that half-lands leaves a model whose accessors call an attribute nothing set.
IMPORTS_PARAM = "imports"
IMPORTS_ATTR = "_imports"

#: The method running a component's `exec init_down`/`init_up` blocks over its
#: subtree (`backend.emit_init`). Private: the root's constructor calls it, and
#: calling it again would re-run initialization on a built model.
INIT_METHOD = "_pss_init"

#: A component's executor (LRM 21.7.2.6), the method that resolves it over the
#: tree once `_pss_init` has run, and the class a component with no executor is
#: bound to: the primitives' default implementation, which is the platform.
#: Emitted only for a model that has an executor (`executors.has_executors`).
EXECUTOR_ATTR = "_pss_xtr"
BIND_METHOD = "_pss_bind"
DEFAULT_EXECUTOR = "_PssDefaultExecutor"

#: A component class in an inheritance hierarchy (`comp_inherit.in_hierarchy`)
#: splits what `__init__` does, so a derived class can extend each part: its own
#: state (`_pss_construct`, which calls its base's first) and the PSS
#: constructor body (`_pss_ctor`, which `super.initialize(...)` reaches).
#: An init block kind is a method too (`_pss_init_down`), for `super;`.
CONSTRUCT_METHOD = "_pss_construct"
CTOR_METHOD = "_pss_ctor"


def group_base(group: str) -> str:
    """The attribute holding register group *group*'s base address.

    One per group, as C and C++ bind each group separately: two groups a
    constructor binds to different handles (`regs.set_handle(base)`,
    `more.set_handle(base + 0x40)`) shared one `_base` here, and every
    access through the first landed at the second's address."""
    return f"_pss_base_{group}"


def init_kind_method(kind: str) -> str:
    """The method holding a class's own `exec <kind>` blocks."""
    return f"_pss_{kind}"


def field_storage(decl_name: str, field: str) -> str:
    """Where a field lives when a base and a derived class both declare it.

    Python has one attribute namespace per object, so two PSS fields of one
    name (17.1: a field shadows, it does not replace) need two names."""
    return f"_pss_{decl_name.replace('::', '__')}_{field}"


def mangle(name: str) -> str:
    """A PSS name as a Python identifier.

    Only keywords are rewritten, with a trailing underscore -- PEP 8's own
    convention for exactly this, so `class` becomes `class_` and a reader knows
    why. PSS's identifier syntax is otherwise a subset of Python's.
    """
    return name + "_" if keyword.iskeyword(name) else name


def strip_suffix(name: str, suffix: str = "_c") -> str:
    """`wb_dma_c` -> `wb_dma`. The component-type naming convention, undone."""
    n = name.split("::")[-1]
    return n[:-len(suffix)] if suffix and n.endswith(suffix) else n


def class_name(comp_name: str) -> str:
    """A component type's Python class name: `wb_dma_ch_c` -> `WbDmaCh`.

    The one place a generated name does NOT mirror the model's spelling. PSS
    component types are lower_snake by convention and Python classes are
    CapWords by a convention just as strong, and a generated module that reads
    as foreign Python is one people wrap rather than use.
    """
    return "".join(p.title() for p in strip_suffix(comp_name).split("_") if p)


def module_name(root_name: str) -> str:
    """The generated module's name, without `.py`: `wb_dma_c` -> `wb_dma`."""
    return mangle(strip_suffix(root_name))


def is_async(await_style: str) -> bool:
    """Is *await_style* the coloured form? `--py-await` has exactly two values."""
    return await_style == "async"


def def_kw(await_style: str, coloured: bool = True) -> str:
    """`def` or `async def`.

    Every coloured definition goes through here rather than testing the mode
    inline, and the reason is auditability: `def_kw(` is greppable, so the list
    of things this backend colours can be read off the code and checked against
    the design's table. *coloured* is the second argument so a site that is
    deliberately NEVER async -- `__init__`, a register's `_addr`, a
    sub-component accessor -- can say so at the call rather than by omission.
    """
    return "async def" if (coloured and is_async(await_style)) else "def"


def reg_symbol(path: Sequence[str], reg: str, kind: str) -> str:
    """A register accessor method: `["regs"], "csr", "read"` -> `regs_csr_read`.

    The path is joined VERBATIM. `CSR` stays `CSR`, because the register's name
    is how the device's documentation refers to it and lower-casing it makes a
    reader check a datasheet against a transformation. The C backend reaches the
    same spelling by the same rule (`style.reg_symbol`).
    """
    return "_".join(list(path) + [reg, kind])
