"""Subset-gating tests for the pure-SV path (`is_pure_supported`).

The pure path must correctly recognize what it can lower and fall back to
sv-native otherwise — never emit broken SV. These are fast (no simulator).
"""
import pytest

from pssc.driver import translate
from pssc.targets.sv.lower_pure import is_pure_supported


def _supported(tmp_path, pss_text, export):
    src = tmp_path / "m.pss"
    src.write_text(pss_text)
    ctx = translate([str(src)])
    assert not ctx.errors, ctx.errors
    return is_pure_supported(ctx, [export])


_ATOMIC = """
component pss_top { action A { rand bit[8] x; constraint c { x == 5; } } }
"""

_BUFFER_FWD = """
component pss_top {
    buffer Data { rand bit[8] x; }
    pool Data dp; bind dp *;
    action P { output Data o; }
    action C { input Data i; constraint c { i.x == 5; } }
    action T { P p; C c; activity { p; c; bind p.o c.i; } }
}
"""

_COUPLED_LOCAL_RAND = """
component pss_top {
    buffer Data { rand bit[8] x; }
    pool Data dp; bind dp *;
    action P { output Data o; }
    action C { input Data i; rand bit[8] k; constraint c { i.x == k; } }
    action T { P p; C c; activity { p; c; bind p.o c.i; } }
}
"""


def test_atomic_supported(tmp_path):
    assert _supported(tmp_path, _ATOMIC, "A") is True


def test_buffer_value_forwarding_supported(tmp_path):
    assert _supported(tmp_path, _BUFFER_FWD, "T") is True


def test_input_coupled_to_local_rand_falls_back(tmp_path):
    # `i.x == k` (k is the consumer's own rand) is the residual joint case:
    # not expressible via forwarding -> must fall back to sv-native.
    assert _supported(tmp_path, _COUPLED_LOCAL_RAND, "T") is False


# Handles declared in an activity block (LRM 11.2.1) reach the IR as
# `ActivityFieldDecl`; the pure path declares them at the activity top.
_BLOCK_HANDLES = """
component pss_top {
    action A { rand bit[8] x; }
    action B { rand bit[8] y; }
    action T { activity { A a; B b; a; b; } }
}
"""

_BLOCK_HANDLE_NAME_TWICE = """
component pss_top {
    action A { rand bit[8] x; }
    action B { rand bit[8] y; }
    action T { activity { { A h; h; } { B h; h; } } }
}
"""

_BLOCK_DATA_FIELD = """
component pss_top {
    action A { rand bit[8] x; }
    action T { activity { action bit[4] n; A a; a; } }
}
"""


def test_handles_declared_in_an_activity_block_are_supported(tmp_path):
    assert _supported(tmp_path, _BLOCK_HANDLES, "T")


def test_one_handle_name_in_two_blocks_falls_back(tmp_path):
    """Declared at the activity top, the two would be one variable."""
    assert not _supported(tmp_path, _BLOCK_HANDLE_NAME_TWICE, "T")


def test_a_data_field_declared_in_an_activity_block_falls_back(tmp_path):
    assert not _supported(tmp_path, _BLOCK_DATA_FIELD, "T")
