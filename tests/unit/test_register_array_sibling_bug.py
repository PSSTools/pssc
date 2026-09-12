"""
Regression tests for a (now-fixed) linker bug: when a `reg_group_c` declared an
array of `reg_c<...>` BEFORE an array of a register-group component, member
registers of the group-array element could not be resolved -- accessing
`regs.group_array[i].SOME_REG` failed with "Failed to find elem SOME_REG".

Discovered while implementing the WISHBONE DMA driver
(src/pssc/testing/models): the register file had a 3-word reserved
pad modeled as `reg_c<bit[32]> _reserved[3]` declared before the per-channel
group array `channels[31]`, and every `regs.channels[ch].CSR` access failed.

This was DISTINCT from the "array of reg_group in a package" bug fixed in
test_register_array_bug.py: it did not require a package, the failure was at
the member-access site (not at declaration), and it was order-sensitive.

Root cause: declaring `rsvd[3]` then `channels[4]` creates two `array<>`
template specializations. `array<T,N>` carries its element type T as a *value*
parameter whose default is a type-identifier (not a generic-type parameter).
`TaskCompareParamLists::equal` -- used to decide whether an existing
specialization can be reused -- never actually compared value parameters (it
had a no-op `TODO: Compare value-type parameters` branch). So
`array<reg_c,3>` and `array<ch_c,4>` compared equal, and the second array
reused the first's specialization, making `channels[i]` resolve to a `reg_c`
element (which has no CSR). Array size also went uncompared, which is why the
sizes were irrelevant to the trigger.

Fix: TaskCompareParamLists::equal now compares value-parameter defaults
(TaskCompareParamLists::valueParamDfltEqual) -- type-identifier defaults by
resolved target, constant defaults by evaluated value
(packages/pssparser/src/TaskCompareParamLists.{h,cpp}).

The control cases below (which never triggered the bug) plus the formerly-
failing case are now all expected to link cleanly.
"""

from pssparser.parser import Parser


_HDR = """import std_pkg::*;
import addr_reg_pkg::*;
struct s_s : packed_s<> { bit[1] EN; bit[31] r; }
component ch_c : reg_group_c { reg_c<s_s, READWRITE, 32> CSR; }
"""

_ACC = """package eng_pkg {
    component dma_engine_c {
        dma_regs_c regs;
        function void start(int ch) {
            s_s csr;
            csr.EN = 1;
            regs.channels[ch].CSR.write(csr);
        }
    }
}
"""


def _errors(regs_decl):
    """Link _HDR + a dma_regs_c declaration + _ACC; return error messages."""
    src = _HDR + regs_decl + _ACC
    parser = Parser()
    parser.parses([("test.pss", src)])
    linker = parser.parser_f.mkAstLinker()
    mc = parser.parser_f.mkMarkerCollector()
    linker.link(mc, parser._files)
    return [mc.getMarker(i).msg() for i in range(mc.numMarkers())]


# --------------------------------------------------------------------------- #
# Control cases — each avoids the trigger and links cleanly today.            #
# --------------------------------------------------------------------------- #

def test_scalar_reserved_before_group_array_links():
    """Scalar reserved registers (not an array) before the group array: OK.
    This is the workaround dma_regs.pss uses."""
    decl = ("component dma_regs_c : reg_group_c { "
            "reg_c<bit[32],READWRITE,32> r0; "
            "reg_c<bit[32],READWRITE,32> r1; "
            "reg_c<bit[32],READWRITE,32> r2; "
            "ch_c channels[4]; }\n")
    assert _errors(decl) == []


def test_reg_array_after_group_array_links():
    """Order matters: a reg_c array AFTER the group array is fine."""
    decl = ("component dma_regs_c : reg_group_c { "
            "ch_c channels[4]; "
            "reg_c<bit[32],READWRITE,32> rsvd[3]; }\n")
    assert _errors(decl) == []


def test_two_group_arrays_link():
    """Two group arrays (no preceding reg_c array) link fine."""
    decl = ("component dma_regs_c : reg_group_c { "
            "ch_c a[4]; ch_c channels[4]; }\n")
    assert _errors(decl) == []


# --------------------------------------------------------------------------- #
# The bug: reg_c array before group array, then member access.               #
# --------------------------------------------------------------------------- #

_TRIGGER_DECL = ("component dma_regs_c : reg_group_c { "
                 "reg_c<bit[32],READWRITE,32> rsvd[3]; "
                 "ch_c channels[4]; }\n")


def test_reg_array_before_group_array_links():
    """Formerly the bug: a reg_c array before a reg-group array, then a member
    access on a group-array element. Now links cleanly."""
    assert _errors(_TRIGGER_DECL) == []


def test_distinct_element_types_get_distinct_specializations():
    """The underlying fix: array specializations with distinct element types
    must not be conflated. Access a member of each group-array element."""
    decl = ("component other_c : reg_group_c { reg_c<bit[16],READWRITE,16> X; } "
            "component dma_regs_c : reg_group_c { "
            "other_c others[3]; ch_c channels[4]; }\n")
    acc = """package eng_pkg {
    component dma_engine_c {
        dma_regs_c regs;
        function void start(int ch) {
            s_s csr; csr.EN = 1;
            regs.channels[ch].CSR.write(csr);
            regs.others[ch].X.write_val(0);
        }
    }
}
"""
    parser = Parser()
    parser.parses([("test.pss", _HDR + decl + acc)])
    linker = parser.parser_f.mkAstLinker()
    mc = parser.parser_f.mkMarkerCollector()
    linker.link(mc, parser._files)
    msgs = [mc.getMarker(i).msg() for i in range(mc.numMarkers())]
    assert msgs == [], msgs
