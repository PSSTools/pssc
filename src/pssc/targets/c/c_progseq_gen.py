"""Emission entry point for the ``c-progseq`` target.

Assembles the generated C header (+ optional ``.c``) for the root component's
subtree and writes it to the output directory. `CProgSeqTarget` copies the
selected core seam headers alongside, so the directory is self-contained; this
module names them (`seam_headers`) but does not move them.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from ..progseq_model import CompKind

_log = logging.getLogger("pssc.progseq.c")

#: Core seam header(s) copied per link style. pssc_mem.h is always needed.
#: Every seam file is copied regardless of style. The set is small, the
#: directory is meant to be self-contained, and a `--mem-access selectable`
#: build DECIDES at compile time -- so the file it will pick cannot be known
#: here. Copying only the one style needed would make selectable fail to build
#: for a reason no message explains.
_CORE_HEADERS = ["pssc_mem.h", "pssc_mem_ptr.h", "pssc_mem_fn.h",
                 "pssc_mem_vtable.h", "pssc_mem_reg.h",
                 # C4.3 shims. Still copied so a directory generated today can
                 # be dropped in beside a generated header from before the
                 # split; they cost 20 lines each.
                 "pssc_mem_direct.h", "pssc_mem_mmio.h",
                 "pssc_env.h", "pssc_chan.h"]

_SEAM_HEADERS = {k: list(_CORE_HEADERS) for k in ("vtable", "direct", "mmio")}

#: The `--link-style` a build gets when it names none. `mmio` and `direct`
#: address registers through the layout struct with the generic
#: `pssc_read<N>`/`pssc_write<N>`/`pssc_write_masked<N>` primitives
#: (pssc_mem_reg.h, which routes them through the seam); `vtable` bakes a
#: family of `static inline` accessors per register, because its primitives
#: need a bus handle a register pointer cannot carry. Every place that
#: defaults the option reads this.
DEFAULT_LINK_STYLE = "mmio"


def seam_headers(link_style: str):
    """The core headers a ``--link-style`` build needs, in copy order.

    Named here rather than on the target because this file owns what the
    generated header includes; `CProgSeqTarget` does the copying (P4.T5).
    """
    return list(_SEAM_HEADERS[link_style])


#: The style-specific header the generated <prefix>.h #includes. The two
#: non-vtable entries name the SHIMS, not the new files: a header generated
#: with `--link-style mmio` should keep saying `mmio` so a regeneration is a
#: no-op diff. `--mem-access` (below) is how a caller asks for the new names.
_SEAM_INCLUDE = {
    "vtable": "pssc_mem_vtable.h",
    "direct": "pssc_mem_direct.h",
    "mmio":   "pssc_mem_mmio.h",
}

#: `--mem-access` names the MECHANISM and supersedes the direct/mmio spelling.
#: `selectable` includes the core header alone and lets pssc_mem.h choose from
#: `-DPSSC_MEM_ACCESS_FUNCTIONS` at compile time.
_MEM_ACCESS_INCLUDE = {
    "pointer":    "pssc_mem_ptr.h",
    "functions":  "pssc_mem_fn.h",
    "selectable": "pssc_mem.h",
}


def seam_include(link_style: str, mem_access) -> str:
    """The style-specific header the generated ``<prefix>.h`` includes.

    `--mem-access` wins where it is given; otherwise the link style decides,
    which keeps every existing command line byte-identical in its output.
    Deliberately NOT defaulted to "selectable" as the plan sketched: flipping a
    default changes what every current build generates, and doing that inside a
    refactor whose whole claim is that nothing changes would make the claim
    untestable. `--mem-access selectable` is one flag away.
    """
    if not mem_access:
        return _SEAM_INCLUDE[link_style]
    if link_style == "vtable":
        raise ValueError(
            "--mem-access does not apply to --link-style vtable: the vtable "
            "seam reaches the bus through a per-instance struct of function "
            "pointers, which is a third mechanism, not a choice between "
            "these two. Drop one of the flags.")
    return _MEM_ACCESS_INCLUDE[mem_access]


def settings_for(model, prefix: str, *,
                 link_style: str = DEFAULT_LINK_STYLE,
                 reg_style: str = "bitfields",
                 header_only: bool = False,
                 yield_mode: str = "none", match_default: str = "message",
                 message_style: str = "import", chan_wait: str = "reject",
                 prefix_map=None, lifecycle: str = "malloc",
                 emit_stubs: bool = False, mem_access=None,
                 includes=None, includes_impl=None,
                 omit_stdint: bool = False, addr_bits: int = 64,
                 style=None) -> "CSettings":
    """Turn the command line's keywords into the backend's `CSettings`.

    THE flag-shaped door, and the only place that knows the flag spellings.
    Separate from `generate` so that something which needs to know what WOULD
    be generated -- the differential test helper, a `--dry-run` -- can ask
    without writing anything.

    ``style`` is the policy the backend renders with (`None`: the default).
    It is read for one thing: whether it renders register accesses itself,
    which keeps the folded accessors its renderings are written against.
    """
    from .. import op_model as om
    from .style import CSettings, coerce
    from ..progseq_model import channel_fields

    tree = model.tree
    _log.info("c-progseq: root=%s prefix=%s link=%s reg=%s components: %d regular, %d reg-group",
              tree.name, prefix, link_style, reg_style,
              om.count(tree, CompKind.REGULAR),
              om.count(tree, CompKind.REG_GROUP))

    return CSettings(
        prefix=prefix, seam_include=seam_include(link_style, mem_access),
        link_style=link_style, mem_access=mem_access, reg_style=reg_style,
        lifecycle=lifecycle, addr_bits=addr_bits, header_only=header_only,
        # REGISTERS THROUGH THE LAYOUT STRUCT on every seam but vtable.
        #
        # The struct is address ARITHMETIC: generated code takes
        # `&_self->regs->LOAD` and hands it to `pssc_write32`, and never
        # dereferences it. pssc_mem_reg.h turns that pointer back into a
        # `pssc_addr_t` and calls the seam's own `pssc_w32`, so whether the
        # address becomes a `volatile` store (pointer) or a call to the
        # platform's `pssc_mem_write32` (functions) is the seam's decision, as
        # it is for every other access. `direct` is the functions seam under its
        # old name, so it takes the same shape: one seam, one register API,
        # whichever spelling selected it.
        #
        # vtable is the exception, and it is a real one: its `pssc_w32` needs
        # the instance's `const pssc_mem_if *`, which a pointer to a register
        # has no room to carry. It keeps the folded per-register accessors,
        # which take the bus handle and the address by value.
        #
        # An address wider than a pointer cannot be formed as one under any
        # seam; pssc_mem_reg.h asserts it (see `--addr-bits`).
        #
        # A style that renders register accesses itself (a house-macro
        # mandate, `reg_accessor_form() == "macro"`) or supplies its own
        # mechanism (`overrides_bus`) keeps the folded accessors too: its
        # `render_reg_*` hooks are handed an accessor (`acc`, `<base>_addr`),
        # and the layout path would bypass them without a word.
        reg_map=(link_style != "vtable"
                 and not _style_renders_registers(coerce(style), link_style)),
        omit_stdint=omit_stdint,
        has_channels=any(channel_fields(c)
                         for c in model.comp_dtypes_root_first),
        includes=tuple(includes or ()),
        includes_impl=tuple(includes_impl or ()),
        yield_mode=yield_mode, chan_wait=chan_wait,
        match_default=match_default,
        message_style=message_style, emit_stubs=emit_stubs,
        prefix_map=tuple(prefix_map or ()))


def _style_renders_registers(style, link_style: str) -> bool:
    return (style.reg_accessor_form() == "macro"
            or style.overrides_bus(link_style))


def generate(model, prefix: str, *, style=None, backend_cls=None,
             **flags) -> List[Path]:
    """Generate the C programming-sequence API for ``root``'s whole subtree.

    The walk, the component order, the register collection and the legality
    gate all happened before this was called, and copying the seam headers
    happens after -- see `OpModelTarget`. What is left here is C, and it is in
    `COpModelBackend`; ``**flags`` are `settings_for`'s, which validates them.
    Returns the list of written file paths.
    """
    from .backend import COpModelBackend
    backend = (backend_cls or COpModelBackend)(style)
    settings = settings_for(model, prefix, style=backend.style, **flags)
    return backend.generate(model, settings)
