"""The C backend's style policy: every naming and layout decision it makes.

`CStylePolicy` is the default and reproduces today's output character for
character -- `test_style_policy.py::test_default_policy_is_identity` and the
golden snapshots both hold it to that. A house style subclasses it and
overrides the two or three methods it cares about.

What is HERE is spelling. What is not here, and never will be, is the address a
register sits at, which registers exist, or what a masked write does; those
come off the `OpModel` and are the model's statements about the device. The
split is not a matter of taste -- a policy that could move an address would be
a supported way to generate firmware that talks to the wrong offsets.

Design: docs/design/generator-style-extensions-design.md §2.5.
"""
from __future__ import annotations

import dataclasses as dc
from typing import List, Optional, Sequence, Tuple

from ..style import StylePolicy
from .mem_access import MemAccess


@dc.dataclass(frozen=True)
class CSettings:
    """The knobs the banner and the include block are functions of.

    Frozen and passed whole rather than unpacked into arguments: the banner's
    job is to report the settings that produced the file, so a policy that can
    only see the four fields today's banner happens to print cannot do that job
    after the fifth knob is added.

    It is also the C backend's whole configuration (P6a.T1) -- `generate()` used
    to take eighteen keywords and pass fourteen of them down by hand, which is
    the shape that makes adding a knob a six-file edit and makes a subclass
    overriding one method have to re-declare all of them.
    """
    prefix: str
    seam_include: str
    link_style: str = "vtable"
    mem_access: Optional[str] = None
    reg_style: str = "bitfields"
    lifecycle: str = "malloc"
    addr_bits: int = 64
    header_only: bool = False
    #: Address registers by following a generated layout struct, rather than by
    #: a baked `_addr` accessor per register. True for every seam that has no
    #: bus context to thread -- which is what makes the access a plain
    #: `write32(&s->regs->x, v)` with nothing to pass but the pointer.
    reg_map: bool = False
    omit_stdint: bool = False
    has_channels: bool = False
    includes: Tuple[str, ...] = ()
    includes_impl: Tuple[str, ...] = ()
    # -- knobs the bodies are a function of. Not read by any style method
    # today; here because a settings object that holds only what the banner
    # prints is one every emitter has to be handed something else alongside.
    yield_mode: str = "none"
    match_default: str = "message"
    message_style: str = "import"
    emit_stubs: bool = False
    prefix_map: Tuple[str, ...] = ()


def pssc_version() -> str:
    """The generator version, for the banner. Never fatal: a header that cannot
    name its version is still a valid header, and failing generation over a
    missing dist-info would be a worse trade than printing `(unknown)`."""
    try:
        from importlib.metadata import version
        return version("pssc")
    except Exception:
        return "(unknown version)"


class CStylePolicy(StylePolicy):
    """Today's C conventions, as overridable methods."""

    name = "default"
    target = "op-model-c"
    description = "pssc's own C conventions (snake_case, `_t` types)"

    # -- files ---------------------------------------------------------------

    def header_name(self, prefix: str) -> str:
        return f"{prefix}.h"

    def impl_name(self, prefix: str) -> str:
        return f"{prefix}.c"

    def stubs_name(self, prefix: str) -> str:
        return f"{prefix}_stubs.c"

    def include_guard(self, prefix: str) -> str:
        return f"{prefix.upper()}_H"

    # -- symbols -------------------------------------------------------------

    def symbol(self, comp_prefix: str, name: str) -> str:
        """A generated function or object: ``wb_dma`` + ``start`` ->
        ``wb_dma_start``.

        One method for every symbol the API exposes -- operations, `_init`,
        `_create`, `_destroy`, the sub-component accessors -- because a house
        style that renames one and not the others produces a header nobody
        would describe as consistent, and because the prototype and the call
        site must agree by construction rather than by both being edited.
        """
        return f"{comp_prefix}_{name}"

    def type_name(self, comp_prefix: str) -> str:
        """The component handle type: ``wb_dma`` -> ``wb_dma_t``."""
        return f"{comp_prefix}_t"

    def struct_tag(self, comp_prefix: str) -> str:
        """The struct tag behind the typedef: ``wb_dma`` -> ``wb_dma_s``."""
        return f"{comp_prefix}_s"

    def reg_symbol(self, comp_prefix: str, path, reg: str) -> str:
        """The accessor stem for one register: ``wb_dma`` + ``["regs"]`` +
        ``csr`` -> ``wb_dma_regs_csr``.

        Takes the PATH rather than a pre-joined name because the register's
        identity is the thing a house style wants -- a traced accessor spelled
        `ACME_REG(DMA, REGS, CSR)` needs the segments, and cannot recover them
        from a string that has already flattened them into underscores.
        """
        return "_".join([comp_prefix] + list(path) + [reg])

    def macro(self, comp_prefix: str, name: str) -> str:
        """An emitted macro: ``wb_dma`` + ``CH_COUNT`` -> ``WB_DMA_CH_COUNT``."""
        return f"{comp_prefix}_{name}".upper()

    # -- register / memory access --------------------------------------------
    #
    # Every hook below returns None by default, meaning "use the funnel's own
    # spelling". None rather than the default string itself, so the default
    # exists exactly once (in `mem_access.py`) instead of once here and once
    # there, drifting.
    #
    # What these may and may not decide is design §2.5.1 and is enforced in the
    # funnel, not documented here and hoped for: the direction rules and the
    # masked-write read are checked before and after a policy is consulted, and
    # a violation raises.

    def mem_access(self) -> "MemAccess":
        """The funnel this policy renders through. One per policy instance."""
        cached = self.__dict__.get("_mem_access")
        if cached is None:
            cached = self.__dict__["_mem_access"] = self.mem_access_cls(self)
        return cached

    #: Swap in a whole funnel subclass instead of overriding hooks one at a
    #: time -- for a style that changes the shape of every access rather than
    #: its spelling.
    mem_access_cls = MemAccess

    def reg_accessor_form(self) -> str:
        """`inline` (today's baked `static inline` set) or `macro`.

        Under `macro` NO accessor block is emitted and every register access in
        a body is rendered by `render_reg_*` -- which is the point: a house
        macro mandate is not satisfied by generated code that calls a generated
        inline that calls the macro, because what a lint rule and a reviewer
        read is the body.
        """
        return "inline"

    def render_mem_read(self, width, bus, addr):
        return None

    def render_mem_write(self, width, bus, addr, value):
        return None

    def render_reg_read(self, acc, handle, idx_args, raw):
        return None

    def render_reg_write(self, acc, handle, idx_args, value, raw):
        return None

    def render_reg_masked_write(self, acc, handle, idx_args, mask, val):
        return None

    def overrides_bus(self, link_style: str) -> bool:
        """True when this style supplies the memory MECHANISM, not just its
        spelling.

        Derived from `seam_headers` rather than declared separately, because
        two ways to say the same thing is two things to keep in agreement.
        Everything that follows from it -- no seam headers copied, no
        `pssc_bus` macro emitted, and the refusal to combine with
        `--link-style vtable` -- reads this one answer.
        """
        headers = self.seam_headers(link_style)
        return headers is not None and not headers

    def seam_headers(self, link_style: str) -> Optional[Sequence[str]]:
        """The core headers to copy beside the generated files.

        `None` keeps the target's own list. `()` means "this style supplies its
        own memory mechanism" -- a policy rendering `ACME_REG_WRITE32` is
        choosing the mechanism, not just the spelling, and copying pssc's seam
        headers beside it would ship a second one nothing calls.
        """
        return None

    # -- layout --------------------------------------------------------------

    def banner(self, model, s: CSettings) -> List[str]:
        """The comment block above the include guard.

        Every line here answers a question a consumer of the header asks about
        an artefact they did not generate: which pssc built it, from which
        root, and under which ABI-affecting settings. The settings line is
        deliberately COMPLETE and always present rather than listing only
        non-defaults -- a reader diffing two headers cannot tell "absent" from
        "default" unless they already know the defaults.
        """
        out = [
            f"/* Generated by pssc {pssc_version()} (c-progseq) -- do not edit. */",
            f"/* Root component: {model.tree.name}; link-style: {s.link_style}. */",
            f"/* ABI-affecting settings: {self.abi_line(s)} */",
            "/* A caller built with different values above is NOT compatible"
            " with this header. */",
            "/* Register value layouts assume little-endian bitfield allocation"
            " (gcc/clang, x86/ARM, LP64/LLP64). */",
            # C2.3. The component struct is COMPLETE, not opaque, and
            # deliberately: a caller on this class of part places the object in
            # static storage, which needs its size. The cost is an ABI, and a
            # header that does not say so is a header someone will ship against
            # a stale object file.
            "/* Component structs are COMPLETE types: their layout is part of"
            " this header's ABI. */",
            "/* Rebuild all callers when the model changes. */",
        ]
        if s.has_channels:
            # C3.4. Stated HERE and not only in pssc_chan.h: a firmware author
            # reads the driver header, and the constraint they can violate --
            # calling a generated operation from an ISR -- is a property of
            # THIS API.
            out += [
                "/* NOT ISR-SAFE: the channel members below are read-modify-"
                "written without a critical */",
                "/* section, so no function in this header may be called from "
                "an interrupt handler */",
                "/* concurrently with foreground code. Define PSSC_CHAN_ENTER/"
                "PSSC_CHAN_EXIT (pssc_chan.h) */",
                "/* to lift that restriction. */",
            ]
        return out

    def abi_line(self, s: CSettings) -> str:
        """The knob settings that change this header's ABI, as one stable
        string. A struct-layout mismatch between a caller and a separately
        built object is otherwise undiagnosable from the artefacts: both files
        look generated and correct, and the only evidence is a wrong offset at
        run time. Emitting the settings makes `diff` the diagnostic."""
        return (f"addr-bits={s.addr_bits}, lifecycle={s.lifecycle}, "
                f"reg-style={s.reg_style}, "
                f"mem-access={s.mem_access or 'from-link-style'}, "
                f"struct-args=value")

    def include_order(self, model, s: CSettings) -> List[str]:
        """The header's `#include` block, in the one order that works.

        The ORDER is load-bearing and is the reason this is a single method
        rather than a list a policy appends to:

          * `PSSC_ADDR_BITS` before the seam include -- pssc_mem.h reads it to
            pick the address typedef.
          * user `--include`s before the seam headers -- the hooks a platform
            overrides (PSSC_MEM_BARRIER, PSSC_CHAN_ENTER/EXIT,
            PSSC_UNREACHABLE) are `#ifndef` guards inside those headers, so a
            definition arriving after them is silently ignored -- and after
            `<stdint.h>`, so a platform header may use `uint32_t`.
          * `pssc_chan.h` unconditionally, not only when the model holds a
            channel: an include list that depends on a model detail makes a
            build that works today fail on a model edit nobody thought was
            API-visible.
        """
        out: List[str] = []
        if not s.omit_stdint:
            out.append("#include <stdint.h>")
            out.append("#include <stdbool.h>")   # PSS `bool` is two-valued too
        if s.addr_bits != 64:
            # Emitted only when it differs from the seam header's own default,
            # so a 64-bit generation stays byte-identical to what it produced
            # before this knob existed.
            out.append(f"#define PSSC_ADDR_BITS {s.addr_bits}")
        if s.reg_map:
            # offsetof, for the _Static_asserts that pin the layout. Before the
            # seam headers rather than after: they are what a platform
            # overrides, and an include that arrives late has already missed
            # its #ifndef.
            out.append("#include <stddef.h>")
        out += [self.include_line(inc) for inc in s.includes]
        if s.header_only and s.lifecycle == "malloc":
            # ONLY for malloc/free, which only `_create`/`_destroy` call. Under
            # `--lifecycle static` the #include is dropped rather than left
            # harmless: on a freestanding target <stdlib.h> may not exist, and
            # an unused include is the kind of thing that fails the build on
            # the part rather than on the workstation.
            out.append("#include <stdlib.h>")
        out.append(f'#include "{s.seam_include}"')
        if s.reg_map:
            # The pointer-in primitives (write32/read32). Alongside the address
            # seam, not instead of it: `_mem_call` still lowers the model's own
            # `write32(addr, ...)` into system RAM, which is an address and not
            # a register.
            out.append('#include "pssc_mem_reg.h"')
        # The environment seam: `message`, and PSSC_UNREACHABLE. Separate from
        # the memory seam because it is optional, and it is what an integrator
        # is most likely to redirect.
        out.append('#include "pssc_env.h"')
        out.append('#include "pssc_chan.h"')
        return out

    def impl_includes(self, model, s: CSettings) -> List[str]:
        """The `.c` file's includes. After the generated header, not before:
        these are for what the BODIES need, and a caller including the header
        must not be made to require them."""
        out = [f'#include "{self.header_name(s.prefix)}"']
        if s.lifecycle == "malloc":
            out.append("#include <stdlib.h>")
        out += [self.include_line(inc) for inc in s.includes_impl]
        return out

    def include_line(self, spec: str) -> str:
        """One `#include` directive from a user-supplied header name.

        `<foo.h>` is passed through; anything else is quoted. The two forms are
        not interchangeable -- quoted searches the generated directory first,
        which is where the seam headers live, and angled does not. Guessing
        from the name (does it end in `.h`? is it a known system header?) would
        be wrong for exactly the cases a user reaches for this flag to handle,
        so the SPELLING is the instruction.
        """
        spec = spec.strip()
        if spec.startswith("<") and spec.endswith(">"):
            return f"#include {spec}"
        if spec.startswith('"') and spec.endswith('"'):
            return f"#include {spec}"
        return f'#include "{spec}"'


#: The policy used unless a target is handed another. Stateless and shared.
DEFAULT = CStylePolicy()


def coerce(style) -> CStylePolicy:
    """`None` -> the default policy; anything else passed through.

    Every emitter takes `style=None`, so this is the one place the fallback
    lives -- rather than `style or DEFAULT` at thirty call sites, where the
    thirty-first is the one that gets forgotten.
    """
    return DEFAULT if style is None else style
