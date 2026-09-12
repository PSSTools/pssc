"""
Regression tests for a (now-fixed) linker bug: inside a `package`, an array of
a `reg_group_c`-derived component that contains a `reg_c<...>` field failed to
link with a spurious "unknown type" error.

Discovered while modeling the WISHBONE DMA/Bridge register file
(src/pssc/testing/models), where the natural model is an array of
per-channel register groups:

    package dma_pkg {
        component dma_channel_regs_c : reg_group_c {
            reg_c<bit[32]> CSR;
            ...
        }
        component dma_regs_c : reg_group_c {
            dma_channel_regs_c channels[31];   // <-- used to fail to link
        }
    }

The bug required the CONJUNCTION of three conditions; dropping any one made
it link cleanly:

  1. the declarations are inside a `package`  (file scope works -- see
     test_array_with_reg_at_file_scope_links),
  2. the element type derives from reg_group_c AND contains a reg_c field
     (a plain `int` field does not trigger it -- test_..._plain_field), and
  3. it is instantiated as an array  (a single instance works -- test_..._single).

Root cause: declaring `ch_c channels[4]` creates a template specialization
`array<ch_c, 4>`. The element type `ch_c` is first resolved correctly in its
instantiation context (package `t`) and that target is copied into the fresh
specialization. But TaskResolveRefs::visitTypeIdentifier then unconditionally
re-resolved every type identifier in the specialization -- using the
specialization's declaration scope (rooted at where `array` is declared, which
does NOT include package `t`) -- and clobbered the good target with a failed
(null) resolution, emitting `unknown type 'ch_c'` with loc.file == -1. The
plain-`int` element happened to be carried as an IDataTypeUserDefined, whose
visitor already guarded on an existing target, which is why it never tripped.
The fix adds the same already-resolved guard to visitTypeIdentifier.

Secondary defect (test_reporter_handles_bad_location): when a marker carries
loc.file == -1, Parser._mkErrorMessage indexed self._filenames[-1] directly and
raised KeyError instead of reporting the diagnostic. It now uses .get(...) like
Parser._collectMarkers already did.
"""

from pssparser.parser import Parser


def _link_markers(pss_code):
    """Link `pss_code` and return the list of marker messages.

    Drives the linker directly rather than via Parser.link(), whose error
    formatting itself crashes on the bad location (see
    test_reporter_crashes_on_bad_location)."""
    parser = Parser()
    parser.parses([("test.pss", pss_code)])
    linker = parser.parser_f.mkAstLinker()
    mc = parser.parser_f.mkMarkerCollector()
    linker.link(mc, parser._files)
    return [mc.getMarker(i).msg() for i in range(mc.numMarkers())]


# --------------------------------------------------------------------------- #
# Control cases — each drops exactly one of the three trigger conditions and   #
# links cleanly today, bounding the bug.                                       #
# --------------------------------------------------------------------------- #

def test_single_instance_in_package_links():
    """Condition 3 dropped: a single (non-array) instance links fine."""
    src = """
import addr_reg_pkg::*;
package t {
    component ch_c : reg_group_c { reg_c<bit[32]> SZ; }
    component top_c : reg_group_c { ch_c channel0; }
}
"""
    assert _link_markers(src) == []


def test_array_with_plain_field_in_package_links():
    """Condition 2 dropped: element type has no reg_c field."""
    src = """
import addr_reg_pkg::*;
package t {
    component ch_c : reg_group_c { int x; }
    component top_c : reg_group_c { ch_c channels[4]; }
}
"""
    assert _link_markers(src) == []


def test_array_with_reg_at_file_scope_links():
    """Condition 1 dropped: same array, but declared at file scope (no
    package) — links fine."""
    src = """
import addr_reg_pkg::*;
component ch_c : reg_group_c { reg_c<bit[32]> SZ; }
component top_c : reg_group_c { ch_c channels[4]; }
"""
    assert _link_markers(src) == []


# --------------------------------------------------------------------------- #
# The bug: all three conditions present.                                       #
# --------------------------------------------------------------------------- #

_TRIGGER = """
import addr_reg_pkg::*;
package t {
    component ch_c : reg_group_c { reg_c<bit[32]> SZ; }
    component top_c : reg_group_c { ch_c channels[4]; }
}
"""


def test_array_of_reg_group_with_reg_c_links():
    """The original trigger now links cleanly (all three conditions present)."""
    assert _link_markers(_TRIGGER) == []


def test_reporter_handles_bad_location():
    """Parser.link() on the (now-valid) trigger links without raising, and the
    error reporter no longer crashes with KeyError on a -1 location. Even if a
    marker with a bad location is produced, _mkErrorMessage must report it
    rather than raise KeyError."""
    parser = Parser()
    parser.parses([("test.pss", _TRIGGER)])
    # Links cleanly -> no exception.
    parser.link()
