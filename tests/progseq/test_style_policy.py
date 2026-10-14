"""Phase 5b: the C style policy.

Two claims, and the second is the one that actually needs testing. That the
DEFAULT policy changes nothing is the golden set's job, and it is checked here
once directly. That a CUSTOM policy reaches every site is not something a
snapshot can say anything about: a partial wiring produces output that is
internally inconsistent -- a renamed definition and an un-renamed call -- and
the only thing that catches it is asking whether the old spelling survives
anywhere at all.
"""
from __future__ import annotations

import pytest

from pssc import testing
from pssc.targets import style as style_reg
from pssc.targets.c.style import CSettings, CStylePolicy


@pytest.fixture
def registered_style():
    """Register throwaway policies and take them back out."""
    added = []

    def add(policy):
        style_reg.register(policy)
        added.append((policy.target, policy.name))
        return policy.name

    yield add
    for key in added:
        style_reg._REGISTRY.pop(key, None)


def _c_output(**opts) -> str:
    """The generated header and .c, concatenated -- one string to search.

    Concatenated deliberately: the defect a style refactor produces is a
    prototype and a definition that disagree, and they live in different files.
    """
    with testing.compile_op_model("op-model-c", **opts) as out:
        return "\n".join(out.read(n) for n in out.names if n.startswith("dma_"))


# -- the default is the identity ---------------------------------------------

def test_default_policy_is_identity():
    """`--style default` and no `--style` are the same generation."""
    assert _c_output() == _c_output(c_style="default")


def test_the_default_policy_is_what_the_backend_uses_unasked():
    from pssc.targets.c.style import DEFAULT, coerce
    assert coerce(None) is DEFAULT
    assert style_reg.get("op-model-c", "default") is not None
    assert "default" in style_reg.list_styles("op-model-c")


@pytest.mark.parametrize("method,args,expected", [
    ("header_name", ("wb_dma",), "wb_dma.h"),
    ("impl_name", ("wb_dma",), "wb_dma.c"),
    ("stubs_name", ("wb_dma",), "wb_dma_stubs.c"),
    ("include_guard", ("wb_dma",), "WB_DMA_H"),
    ("symbol", ("wb_dma", "start"), "wb_dma_start"),
    ("type_name", ("wb_dma_c",), "wb_dma_c"),
    ("struct_tag", ("wb_dma_c",), "wb_dma_c"),
    ("macro", ("wb_dma", "ch_COUNT"), "WB_DMA_CH_COUNT"),
    ("enum_item", ("op_e", "RD"), "OP_RD"),
    ("enum_item", ("wb_dma_status_e", "WB_DMA_DONE"),
     "WB_DMA_STATUS_WB_DMA_DONE"),
    ("enum_item", ("mode", "X"), "MODE_X"),
])
def test_the_default_spellings_are_pinned(method, args, expected):
    assert getattr(CStylePolicy(), method)(*args) == expected


def test_the_include_line_rule():
    """Quoted vs angled is the user's instruction, not a guess from the name:
    quoted searches the generated directory, where the seam headers live."""
    p = CStylePolicy()
    assert p.include_line("board.h") == '#include "board.h"'
    assert p.include_line("<stdio.h>") == "#include <stdio.h>"
    assert p.include_line(' "x.h" ') == '#include "x.h"'


# -- a custom policy reaches every site --------------------------------------

class _ShoutySymbols(CStylePolicy):
    """Renames every name the API exposes. Nothing else.

    `reg_symbol` is a SEPARATE hook from `symbol` and is overridden here too,
    because a house style that renames its operations and leaves its register
    accessors alone is a real thing to want -- which is why the two are not one
    method. The test below leans on both being overridden so that "no default
    spelling survives" is a meaningful claim.
    """
    name = "shouty"

    def symbol(self, comp_prefix, name):
        return f"ACME_{comp_prefix}__{name}"

    def reg_symbol(self, comp_prefix, path, reg):
        return "_".join(["ACME", comp_prefix] + list(path) + [reg])

    def type_name(self, type_name):
        return f"ACME_{type_name}_handle"

    def struct_tag(self, type_name):
        return f"ACME_{type_name}_struct"


def test_a_symbol_policy_reaches_prototypes_and_call_sites(registered_style):
    """The assertion that catches a PARTIAL wiring.

    Checking that the new spelling appears proves only that one site was
    routed. Checking that the OLD spelling is gone is what proves they all
    were -- a definition renamed without its callers is C that compiles as far
    as the linker and then does not.
    """
    registered_style(_ShoutySymbols())
    text = _c_output(c_style="shouty", c_link_style="vtable")

    assert "ACME_dma_engine__mem_to_mem_copy(" in text
    assert "ACME_dma_engine_c_handle" in text
    assert "ACME_dma_engine_c_struct" in text

    assert "ACME_dma_engine_regs_channels_CSR_write(" in text

    # ...and nothing kept the old spelling. `dma_engine_` still appears inside
    # the new names, so this looks for it where a symbol would START. Comment
    # lines are skipped: they carry the PSS type name (`dma_engine_pkg::
    # dma_engine_c`), which is the model's and not a C symbol at all. `#include`
    # lines are skipped too: a FILENAME (`dma_engine_regs.h`) comes from
    # `header_name`/`regs_header_name`/..., never from `symbol`/`reg_symbol`,
    # and `_ShoutySymbols` overrides neither -- a style that renames only its
    # symbols and not its filenames is exactly the "renames its operations and
    # leaves its register accessors alone" kind of partial policy this test's
    # own class comment says is a real thing to want, just for files instead.
    import re
    stale = [ln for ln in text.splitlines()
             if re.search(r"(?<![A-Za-z0-9_])dma_engine_[a-z]", ln)
             and "ACME_" not in ln
             and not ln.lstrip().startswith(("/*", "*", "//", "#include"))]
    assert not stale, "sites that kept the old symbol spelling:\n" + "\n".join(stale)


def test_the_lifecycle_symbols_move_too(registered_style):
    """`_init`/`_create`/`_destroy` are generated from three separate sites --
    prototype, definition, and the call inside `_create`. All three or none."""
    registered_style(_ShoutySymbols())
    text = _c_output(c_style="shouty")
    for kind in ("init", "create", "destroy"):
        assert f"ACME_dma_engine__{kind}(" in text, kind
    # the call `_create` makes to `_init` is the site that hides
    assert "if (_self) ACME_dma_engine__init(_self" in text


def test_a_file_naming_policy_renames_the_files(registered_style):
    class _Dotted(CStylePolicy):
        name = "dotted"

        def header_name(self, prefix):
            return f"{prefix}_api.h"

        def impl_name(self, prefix):
            return f"{prefix}_api.c"

    registered_style(_Dotted())
    with testing.compile_op_model("op-model-c", c_style="dotted") as out:
        assert "dma_engine_api.h" in out.names
        assert "dma_engine.h" not in out.names
        # the .c must include the header under the name the policy gave it,
        # not the one the default would have
        assert '#include "dma_engine_api.h"' in out.read("dma_engine_api.c")


def test_a_banner_policy_changes_only_the_banner(registered_style):
    class _Quiet(CStylePolicy):
        name = "quiet"

        def banner(self, model, s):
            return ["/* proprietary */"]

    registered_style(_Quiet())
    text = _c_output(c_style="quiet")
    assert text.startswith("/* proprietary */")
    assert "ABI-affecting settings" not in text
    assert "dma_engine_mem_to_mem_copy(" in text     # the API is untouched


# -- settings ----------------------------------------------------------------

def test_the_abi_line_names_every_knob():
    """A consumer diffing two headers needs the line comparable, so every knob
    is present whether or not it is at its default."""
    line = CStylePolicy().abi_line(
        CSettings(prefix="p", seam_include="x.h", addr_bits=32,
                  lifecycle="static", reg_style="accessors"))
    for part in ("addr-bits=32", "lifecycle=static", "reg-style=accessors",
                 "mem-access=from-link-style", "struct-args=value"):
        assert part in line


def test_a_policy_sees_the_settings_it_reports():
    """`banner` takes the whole settings object, not the four fields today's
    banner happens to print -- a policy that cannot see a knob cannot report
    it, and the next knob is always about to be added."""
    seen = {}

    class _Nosy(CStylePolicy):
        def banner(self, model, s):
            seen.update(dc_asdict(s))
            return []

    import dataclasses
    def dc_asdict(s):
        return {f.name: getattr(s, f.name) for f in dataclasses.fields(s)}

    from pssc.targets.c.style import CSettings as _S
    _Nosy().banner(None, _S(prefix="p", seam_include="x.h", addr_bits=32))
    assert seen["addr_bits"] == 32 and seen["prefix"] == "p"


# -- style discovery ---------------------------------------------------------

def test_an_unknown_style_lists_the_available_ones():
    with pytest.raises(style_reg.StyleError) as exc:
        style_reg.get("op-model-c", "nope")
    assert "nope" in str(exc.value) and "default" in str(exc.value)
    assert "op-model-c" in str(exc.value)


def test_an_unknown_style_is_fatal_not_a_silent_default():
    """Falling back to pssc's conventions when a house style was asked for is
    the exact failure `--style` exists to prevent, and it is invisible in a
    diff of the generated files alone."""
    with pytest.raises(Exception) as exc:
        testing.compile_op_model("op-model-c", c_style="no-such-style")
    assert "no-such-style" in str(exc.value)


def test_styles_are_keyed_by_target(registered_style):
    """A style for one target must not resolve for another: the two share no
    method contract, so the failure would be an AttributeError deep inside a
    generator."""
    class _SvStyle(style_reg.StylePolicy):
        name = "shouty"
        target = "op-model-sv"

    registered_style(_SvStyle())
    assert style_reg.get("op-model-sv", "shouty") is not None
    with pytest.raises(style_reg.StyleError):
        style_reg.get("op-model-c", "shouty")


def test_a_style_collision_is_refused(registered_style):
    class _Dup(CStylePolicy):
        name = "default"

    with pytest.raises(style_reg.StyleError) as exc:
        style_reg.register(_Dup())
    assert "already" in str(exc.value) and "replaces=True" in str(exc.value)


def test_a_style_without_a_target_is_refused():
    class _Homeless(style_reg.StylePolicy):
        name = "x"

    with pytest.raises(style_reg.StyleError) as exc:
        style_reg.register(_Homeless())
    assert "target" in str(exc.value)


# -- access hooks (P5b.T3) ---------------------------------------------------

class _Acme(CStylePolicy):
    """A house macro mandate: `ACME_RD32(addr)` / `ACME_WR32(addr, val)`.

    Deliberately the SMALLEST policy that satisfies such a mandate -- if this
    needs more than a few methods, the tier is not doing its job.
    """
    name = "acme"

    def reg_accessor_form(self):
        return "macro"

    def render_mem_read(self, width, bus, addr):
        return f"ACME_RD{width}({addr})"

    def render_mem_write(self, width, bus, addr, value):
        return f"ACME_WR{width}({addr}, {value})"

    # The ADDRESS comes from the `_addr` accessor pssc emits under every form.
    # A house macro is handed both the register's identity (`acc.base`) and its
    # address, and computes neither.
    @staticmethod
    def _addr(acc, handle, idx_args):
        return f"{acc.base}_addr({handle}{idx_args})"

    def render_reg_read(self, acc, handle, idx_args, raw):
        return f"ACME_RD{acc.prim}({self._addr(acc, handle, idx_args)})"

    def render_reg_write(self, acc, handle, idx_args, value, raw):
        return (f"ACME_WR{acc.prim}({self._addr(acc, handle, idx_args)}, "
                f"{value})")

    def render_reg_masked_write(self, acc, handle, idx_args, mask, val):
        cur = self.render_reg_read(acc, handle, idx_args, True)
        return (f"ACME_WR{acc.prim}({self._addr(acc, handle, idx_args)}, "
                f"({cur} & ~{mask}) | ({val} & {mask}))")

    def seam_headers(self, link_style):
        return ()

    def include_order(self, model, s):
        return ['#include "acme_regs.h"']


def test_macro_form_emits_no_accessor_block(registered_style):
    """Under a macro mandate the read/write accessors are not merely
    unnecessary -- emitting inline functions nothing calls would read to a
    reviewer as though the mandate had not been applied.

    The ADDRESS accessors survive, and that is not a compromise: the folded
    offsets are the model's statement about the device, and the alternative is
    a house macro recomputing them, which is a supported way to point firmware
    at the wrong register.
    """
    registered_style(_Acme())
    text = _c_output(c_style="acme", c_link_style="direct")
    assert "Baked inline register accessors" not in text
    for suffix in ("_read(", "_write(", "_read_val(", "_write_val(",
                   "_write_masked("):
        assert f"static inline{suffix}" not in text
        assert f"dma_engine_regs_CSR{suffix}" not in text
    assert "static inline pssc_addr_t dma_engine_regs_CSR_addr(" in text


def test_custom_macro_reaches_every_access_site(registered_style):
    """P5b.T3's accept criterion, and the assertion that catches a missed site.

    Not "the ACME macro appears" -- that passes with one site routed. The claim
    is that NO pssc spelling survives anywhere, which is what a company
    mandating its own macros is actually buying.
    """
    registered_style(_Acme())
    text = _c_output(c_style="acme", c_link_style="direct")
    assert "ACME_WR32(" in text and "ACME_RD32(" in text
    import re
    leaks = [ln for ln in text.splitlines()
             if re.search(r"pssc_[rw]\d|pssc_bus\s*\(", ln)]
    assert not leaks, "sites still using pssc's seam:\n" + "\n".join(leaks)


def test_a_bus_overriding_style_copies_no_seam_headers(registered_style):
    """The other half of the accept criterion: zero copied seam headers.

    A directory holding both ACME macros and pssc's seam is a directory whose
    contents contradict each other.
    """
    registered_style(_Acme())
    with testing.compile_op_model("op-model-c", c_style="acme",
                                  c_link_style="direct") as out:
        assert not [n for n in out.names if n.startswith("pssc_")], out.names


# -- enforcement (P5b.T4) ----------------------------------------------------

def _acc(**kw):
    from pssc.targets.c.lower_reg_model import _Acc
    base = dict(base="r", c_type="uint32_t", prim=32, is_struct=False,
                access="READWRITE", const_off=0, strides=[])
    base.update(kw)
    return _Acc(**base)


def test_readonly_register_never_written():
    """The direction rules are the MODEL's statement about the device, so the
    policy is not asked -- a plausible rendering for an access the register
    does not have is worse than no rendering at all."""
    from pssc.targets.c.mem_access import DEFAULT, LegalityError
    asked = []

    class _Nosy(CStylePolicy):
        def render_reg_write(self, acc, handle, idx_args, value, raw):
            asked.append(acc.base)
            return "SHOULD_NEVER_APPEAR"

    mem = _Nosy().mem_access()
    with pytest.raises(LegalityError) as exc:
        mem.reg_write(_acc(access="READONLY"), "s", "", "v")
    assert "READONLY" in str(exc.value)
    assert not asked, "the policy was consulted for an access that cannot exist"

    with pytest.raises(LegalityError) as exc:
        mem.reg_read(_acc(access="WRITEONLY"), "s", "")
    assert "WRITEONLY" in str(exc.value)


def test_masked_write_read_cannot_be_dropped():
    """§21.14.1's read is part of the operation. A policy may re-spell the
    read-modify-write; a policy that writes `val` straight through silently
    clears every bit outside the mask."""
    from pssc.targets.c.mem_access import LegalityError

    class _Dropper(CStylePolicy):
        def render_reg_read(self, acc, handle, idx_args, raw):
            return f"ACME_RD({acc.base})"

        def render_reg_masked_write(self, acc, handle, idx_args, mask, val):
            return f"ACME_WR({acc.base}, {val})"        # no read

    with pytest.raises(LegalityError) as exc:
        _Dropper().mem_access().reg_masked_write(_acc(), "s", "", "m", "v")
    assert "21.14.1" in str(exc.value) and "dropped the read" in str(exc.value)


def test_a_masked_write_that_does_read_is_accepted():
    """The control: the check must not reject every custom rendering, only the
    ones that lost the read."""
    class _Honest(CStylePolicy):
        def render_reg_read(self, acc, handle, idx_args, raw):
            return f"ACME_RD({acc.base})"

        def render_reg_masked_write(self, acc, handle, idx_args, mask, val):
            return (f"ACME_WR({acc.base}, (ACME_RD({acc.base}) & ~{mask}) "
                    f"| ({val} & {mask}))")

    out = _Honest().mem_access().reg_masked_write(_acc(), "s", "", "m", "v")
    assert "ACME_RD(r)" in out


def test_policy_cannot_alter_address(registered_style):
    """Addresses are never handed to a policy for COMPUTATION.

    There is no hook that produces one: the offsets are folded onto the
    `OpModel` and rendered by `lower_reg_model`, and a policy sees either an
    address expression it may only place, or the register's identity. So the
    strongest check available is also the right one -- an aggressive style
    changes every spelling in the file and the folded addresses are still
    exactly the default's.
    """
    import re
    registered_style(_Acme())

    def addrs(text):
        return re.findall(r"_self->base_\w+ \+ 0x[0-9a-f]+u(?: \+ [^;)]+)?", text)

    default = addrs(_c_output(c_link_style="vtable"))
    acme = addrs(_c_output(c_style="acme", c_link_style="direct"))
    assert default and default == acme


def test_the_offsets_survive_a_policy_that_tries_to_move_them(registered_style):
    """And a policy that WANTS to move an address has nowhere to say so: the
    `_Acc` it is handed is the folded result, so returning a different offset
    means writing one into a string it did not compute."""
    class _Liar(CStylePolicy):
        name = "liar"

        def render_reg_write(self, acc, handle, idx_args, value, raw):
            # The only address available here is the accessor whose body
            # `lower_reg_model` wrote. There is no offset to bend.
            assert not hasattr(acc, "set_offset")
            return None

    registered_style(_Liar())
    assert _c_output(c_style="liar") == _c_output()


# -- incompatible combinations (P5b.T5) --------------------------------------

def test_vtable_plus_bus_policy_rejected(registered_style):
    """Two answers to one question. vtable puts a struct of function pointers
    in the handle and reaches the bus through it; a bus-overriding style never
    calls it, so the caller hands over a vtable that is silently unused."""
    registered_style(_Acme())
    with pytest.raises(Exception) as exc:
        testing.compile_op_model("op-model-c", c_style="acme",
                                 c_link_style="vtable")
    msg = str(exc.value)
    assert "--style acme" in msg and "--link-style vtable" in msg
    assert "function pointers" in msg


def test_the_same_style_is_fine_with_a_non_vtable_link(registered_style):
    """The control -- the rejection must be about the CONFLICT, not the style."""
    registered_style(_Acme())
    with testing.compile_op_model("op-model-c", c_style="acme",
                                  c_link_style="direct") as out:
        assert out.names
