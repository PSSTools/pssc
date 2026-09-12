"""The memory-access funnel: every memory-touching spelling the C target emits.

Before this existed, `pssc_r32` / `pssc_w32` / `pssc_bus(s)` were written out
at six sites in `lower_reg_model.py` and two more in `lower_progseq.py`. Nothing
was wrong with any of them individually -- they agreed, and the golden snapshots
kept them agreeing -- but "the C target's memory seam" was not a thing you could
point at, only a pattern you could grep for. That makes a style extension
(phase 5b) impossible to write correctly: it would have to find all eight.

So: one object, and `test_package_layout.py::test_bus_spelling_has_one_home`
keeps it one. A subclass that changes `read`/`write` changes every memory access
the target emits, including the ones inside baked accessors, because there is
nowhere else for them to come from.

Named for the ABSTRACTION, not the transport. This funnel carries more than
memory-mapped I/O -- `_mem_call` lowers the `write32` calls that write a DMA
descriptor into system RAM -- and `mem` is already the word this codebase uses
for the abstraction (`pssc::mem_if`, `pssc_mem*.h`, `--mem-access`). `bus_expr`
keeps `bus` because it renders the HANDLE (`pssc_bus(s)`), which is the one
thing here that really is a bus.

Design: docs/design/generator-style-extensions-design.md §2.5.1.
"""
from __future__ import annotations

from typing import List

#: Primitive widths the seam headers declare. A register whose value is wider
#: than 64 bits does not reach here -- `_prim_bits` clamps first.
WIDTHS = (8, 16, 32, 64)


class MemAccess:
    """How the C target spells a memory access.

    The default rendering is today's, character for character. Every method is
    a pure string function of its arguments: it holds no model state, so one
    instance serves a whole generation run and a style can replace it wholesale.

    A `style` may be attached, in which case each rendering is offered to the
    policy first and its answer used when it gives one. The DEFAULT lives here
    rather than in the policy so that there is exactly one of it -- a policy
    that overrides nothing and a funnel with no policy at all must not be two
    separate copies of the same eight strings.
    """

    def __init__(self, style=None):
        #: The style policy, or None. Duck-typed on purpose: this module does
        #: not import `style.py`, which imports the funnel back.
        self.style = style

    #: The macro standing for "this component's bus handle". A macro rather
    #: than a function because each component passes its own handle type and C
    #: has no overloading -- see `lower_progseq._bus_macro`, which defines it.
    bus_macro = "pssc_bus"

    #: `%d` is the width in bits. Short spelling, not `pssc_read32`: it is what
    #: the seam headers have declared since they were written, and a second
    #: spelling would be a second seam.
    read_prim = "pssc_r%d"
    write_prim = "pssc_w%d"

    #: PSS register method -> the suffix of the baked accessor it lowers to.
    #: Both halves of the target read this: `lower_reg_model` to DEFINE the
    #: accessors and `lower_progseq` to CALL them. They were two tables, agreeing
    #: by hand -- a mismatch on either side emits a call to a function that was
    #: never defined, which -Werror does catch, but only after the fact and only
    #: for a model that happens to use that method. `addr` has no PSS spelling;
    #: it is the accessor the others are built on.
    accessor_suffix = {
        "addr": "addr",
        "read": "read", "write": "write",
        "read_val": "read_val", "write_val": "write_val",
        "write_val_masked": "write_masked",
    }

    # -- pieces ---------------------------------------------------------------

    def bus_expr(self, handle: str) -> str:
        """The seam's first argument: ``pssc_bus(s)``."""
        return f"{self.bus_macro}({handle})"

    def read_fn(self, width: int) -> str:
        return self.read_prim % self._width(width)

    def write_fn(self, width: int) -> str:
        return self.write_prim % self._width(width)

    def accessor(self, base: str, kind: str) -> str:
        """The baked accessor for one register method: ``wb_dma_regs_csr_read``.

        An unknown `kind` raises rather than falling back to `base_<kind>`,
        because that fallback names a function nothing defines, and the failure
        surfaces in a C compiler's output rather than pssc's.
        """
        try:
            return f"{base}_{self.accessor_suffix[kind]}"
        except KeyError:
            raise ValueError(
                f"no accessor for register method '{kind}'; known: "
                f"{', '.join(sorted(self.accessor_suffix))}") from None

    # -- accesses -------------------------------------------------------------

    def read(self, width: int, handle: str, addr: str) -> str:
        """``pssc_r32(pssc_bus(s), <addr>)`` -- an expression, not a statement."""
        self._width(width)
        bus = self.bus_expr(handle)
        out = self._ask("render_mem_read", width, bus, addr)
        return out if out is not None else f"{self.read_fn(width)}({bus}, {addr})"

    def write(self, width: int, handle: str, addr: str, value: str) -> str:
        """``pssc_w32(pssc_bus(s), <addr>, <value>)``.

        Also an expression: the seam's write primitives return void, so the
        caller supplies the semicolon and can place the call anywhere a void
        expression goes.
        """
        self._width(width)
        bus = self.bus_expr(handle)
        out = self._ask("render_mem_write", width, bus, addr, value)
        return (out if out is not None else
                f"{self.write_fn(width)}({bus}, {addr}, {value})")

    def masked_write(self, value_type: str, base: str, args: str, *,
                     mask: str = "mask", val: str = "val") -> str:
        """PSS 3.1 §21.14.1: ``(current & ~mask) | (val & mask)``.

        Composed from the register's own raw accessors rather than from `read`
        and `write` directly, because the address has already been folded into
        them and recomputing it here would be a second copy of that arithmetic.

        THE READ IS PART OF THE DEFINITION, not an implementation choice. A
        style may change how the read and the write are spelled; it may not
        drop the read, and `test_mem_access.py::test_masked_write_always_reads`
        holds every rendering to that.
        """
        return (f"{value_type} cur = {self.accessor(base, 'read_val')}({args}); "
                f"{self.accessor(base, 'write_val')}({args}, "
                f"(cur & ~{mask}) | ({val} & {mask}));")

    # -- register access ------------------------------------------------------
    #
    # The direction rules are enforced HERE, before the policy is consulted, and
    # that ordering is the point. A policy asked to render a write for a
    # READONLY register might well return something plausible; the register
    # still has no writer, and the generated firmware would be writing to a
    # device that ignores it -- or does not. So the funnel does not ask.

    def reg_read(self, acc, handle: str, idx_args: str = "",
                 raw: bool = False) -> str:
        """One register read, as an expression."""
        self._require(acc, "read")
        kind = "read_val" if raw else "read"
        out = self._ask("render_reg_read", acc, handle, idx_args, raw)
        return (out if out is not None else
                f"{self.accessor(acc.base, kind)}({handle}{idx_args})")

    def reg_write(self, acc, handle: str, idx_args: str, value: str,
                  raw: bool = False) -> str:
        """One register write, as an expression."""
        self._require(acc, "write")
        kind = "write_val" if raw else "write"
        out = self._ask("render_reg_write", acc, handle, idx_args, value, raw)
        return (out if out is not None else
                f"{self.accessor(acc.base, kind)}({handle}{idx_args}, {value})")

    def reg_masked_write(self, acc, handle: str, idx_args: str,
                         mask: str, val: str) -> str:
        """PSS 3.1 §21.14.1, and the read is not negotiable.

        A policy may override this wholesale -- a house macro may well do the
        read-modify-write itself -- but what it returns must still READ. The
        check is a substring test against the rendering the same policy gives
        for a read, which is the strongest thing available without parsing C,
        and it catches the failure that matters: a `render_reg_masked_write`
        that writes `val` straight through, silently clearing every bit outside
        the mask on a register whose other fields were live.
        """
        self._require(acc, "read")
        self._require(acc, "write")
        out = self._ask("render_reg_masked_write", acc, handle, idx_args,
                        mask, val)
        if out is None:
            return f"{self.accessor(acc.base, 'write_val_masked')}(" \
                   f"{handle}{idx_args}, {mask}, {val})"
        probe = self.reg_read(acc, handle, idx_args, raw=True)
        if _call_head(probe) not in out:
            raise LegalityError(
                f"style {type(self.style).__name__}.render_reg_masked_write "
                f"dropped the read for '{acc.base}': PSS 3.1 §21.14.1 defines "
                f"a masked write as (current & ~mask) | (val & mask), so the "
                f"read is part of the operation -- and on a status CSR that "
                f"clears on read it is a side effect the model asked for. "
                f"Rendered: {out!r}")
        return out

    def _require(self, acc, direction: str) -> None:
        mode = getattr(acc, "access", "READWRITE") or "READWRITE"
        if direction == "read" and mode == "WRITEONLY":
            raise LegalityError(
                f"'{acc.base}' is WRITEONLY: there is no read to render. The "
                f"access mode is the model's statement about the device")
        if direction == "write" and mode == "READONLY":
            raise LegalityError(
                f"'{acc.base}' is READONLY: there is no write to render. The "
                f"access mode is the model's statement about the device")

    def _ask(self, hook: str, *args):
        """Offer a rendering to the policy. `None` means 'use the default'."""
        fn = getattr(self.style, hook, None)
        return None if fn is None else fn(*args)

    # -- internals ------------------------------------------------------------

    @staticmethod
    def _width(width: int) -> int:
        if width not in WIDTHS:
            raise ValueError(
                f"no memory primitive for a {width}-bit access; the seam "
                f"declares {', '.join(str(w) for w in WIDTHS)}")
        return width


class LegalityError(Exception):
    """A style asked for something the model does not permit.

    Fatal, never a warning. The whole boundary in design §2.5.1 is that a
    policy decides SPELLING and the model decides semantics; a violation that
    only logged would be a supported way to generate firmware that reads a
    write-only register or writes without reading.
    """


def _call_head(expr: str) -> str:
    """`foo(a, b)` -> `foo(`. What a rendering must contain to be that call."""
    return expr.split("(", 1)[0] + "("


#: The rendering used unless a target hands over its own. Shared and stateless.
DEFAULT = MemAccess()


def prim_names(mem: MemAccess = DEFAULT) -> List[str]:
    """Every primitive name this funnel can emit -- for the seam headers and
    for tests that want to assert over the whole set rather than a sample."""
    return ([mem.read_fn(w) for w in WIDTHS]
            + [mem.write_fn(w) for w in WIDTHS])
