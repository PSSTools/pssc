"""An array member of a struct used in an op-model-sv export API.

Before this, `sv_member_type` (`targets/sv/lower_api_types.py`) had no branch
for `DataTypeArray`, so a struct like `struct key_s { bit[64] k[4]; }`
appearing as an operation argument crashed the whole compile with
``ValueError: unsupported SV struct member type DataTypeArray`` instead of
emitting the unpacked array member SV actually supports
(``bit [63:0] k[4];``).
"""
import argparse

import pytest

from pssc import driver

_MODEL = """
package key_pkg {
    struct key_s {
        bit[64] k[4];
    }
}

import key_pkg::*;

component root_c {
    target function key_s op(key_s kv) {
        key_s o;
        o = kv;
        return o;
    }

    export target function op;
}
"""


def _gen(tmp_path):
    src = tmp_path / "key.pss"
    src.write_text(_MODEL)
    ns = argparse.Namespace(progseq_root="root_c", output_dir=str(tmp_path))
    driver.compile([str(src)], target="op-model-sv", opts=ns)
    return (tmp_path / "root_c_pkg.sv").read_text()


def test_an_array_member_lowers_to_an_unpacked_sv_array(tmp_path):
    sv = _gen(tmp_path)
    assert "bit [63:0] k[4];" in sv
