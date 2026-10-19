"""`_<digit>` register-group members are ordinary members on every target.

A reserved placeholder is a member named `_` followed by a NON-digit
(`_reserved0`, `_rsvd`, `_pad`, `_`): it holds address space and has no API.
`_0`, `_12`, `_3a` are names a register generator writes for an indexed
series -- an identifier cannot start with a digit -- and they are registers
like any other. The rule has one home, `reg_layout.is_reserved_name`; the
behaviour (one trace on all four targets) is the matrix case "numbered
members and a strided register array" in `matrix/test_m_registers.py`, and
an access to a reserved member is refused there too
(`matrix/test_m_refusals.py`). This holds the API each target surfaces.
"""
import json
import re

import pytest

from pssc.targets import progseq_model
from pssc.targets.reg_layout import is_reserved_name
from pssc.testing import conformance

from . import trace_harness as th

_MODEL = """
pure component pr_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> _0;
  reg_c<bit[32], READONLY, 32> _1;
  reg_c<bit[32], READWRITE, 32> _2[2];
  reg_c<bit[32], READONLY, 32> _reserved0;
  reg_c<bit[32], READONLY, 32> _rsvd;
  function bit[64] get_offset_of_instance(string name) {
    match (name) {
      ["_0"]: return 0x0; ["_1"]: return 0x4;
      ["_reserved0"]: return 0x8; ["_rsvd"]: return 0xC;
    }
    return 0xFFFFFFFFFFFFFFFF;
  }
  function bit[64] get_offset_of_instance_array(string name, int index) {
    match (name) { ["_2"]: return 0x10 + 4 * index; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
component pss_top {
  pr_c g;
  solve function void initialize(addr_handle_t base) { g.set_handle(base); }
  target function void run() {
    bit[32] v;
    g._0.write_val(1);
    v = g._1.read_val();
    g._2[1].write_val(v);
  }
}
"""


@pytest.mark.parametrize("name, reserved", [
    ("_reserved0", True), ("_rsvd", True), ("_pad", True), ("_", True),
    ("_x1", True), ("_A", True),
    ("_0", False), ("_12", False), ("_3a", False), ("CSR", False),
    ("x_0", False),
])
def test_the_reserved_rule(name, reserved):
    assert is_reserved_name(name) is reserved


def test_one_rule_for_every_reader():
    """The SV/C++ group lowering and the conformance kit ask the same
    predicate as the register walk."""
    class F:
        def __init__(self, name):
            self.name = name
    for n in ("_0", "_reserved0", "_", "_9z", "_z9"):
        assert progseq_model._is_reserved(F(n)) is is_reserved_name(n)
    src = conformance.__file__
    text = open(src).read()
    assert 'startswith("_")' not in text


@pytest.mark.parametrize("target", th.TARGETS)
def test_numbered_members_are_in_the_manifest_and_reserved_ones_are_not(
        tmp_path, target):
    man = tmp_path / "manifest.json"
    th.compile_model(tmp_path, _MODEL, target, progseq_manifest=str(man))
    doc = json.loads(man.read_text())
    (root,) = [c for c in doc["components"] if c["name"] == "pss_top"]
    regs = {tuple(r["path"]): r for r in root["registers"]}
    assert set(regs) == {("g", "_0"), ("g", "_1"), ("g", "_2")}
    assert regs[("g", "_2")]["offset"] == 0x10
    assert regs[("g", "_2")]["strides"] == [4]


def test_cpp_members_keep_their_names_and_no_identifier_gains_a_double_underscore(
        tmp_path):
    """C++ reserves every identifier containing `__`. The group keeps the
    member as a class member (`g._0`, no joined name); the one generated
    name built from a member, the array's factory, is `make_2`, not
    `make__2`."""
    out = th.compile_model(tmp_path, _MODEL, "cpp")
    text = "\n".join(p.read_text() for p in sorted(out.glob("*.hpp")))
    cls = text[text.index("class pr_c {"):]
    cls = cls[:cls.index("};") + 2]
    assert re.search(r"pssc::reg<std::uint32_t[^>]*> _0;", cls)
    assert re.search(r"pssc::reg<std::uint32_t[^>]*> _1;", cls)
    assert "> _2;" in cls
    assert "_reserved0" not in cls and "_rsvd" not in cls
    assert "make_2(" in cls and "make_2_impl(" in cls
    assert not re.findall(r"\b\w*[A-Za-z0-9]__\w*\b|\b\w+__\b", cls), cls


def test_python_surfaces_accessors_for_numbered_members_only(tmp_path):
    out = th.compile_model(tmp_path, _MODEL, "py")
    (mod,) = [p for p in out.glob("*.py") if p.name != "pssc_rt.py"]
    text = mod.read_text()
    for m in ("g__0_write_val", "g__1_read_val", "g__2_write_val"):
        assert f"def {m}(" in text, m
    assert "_reserved0" not in text and "g__rsvd" not in text


def test_sv_group_class_declares_numbered_members(tmp_path):
    out = th.compile_model(tmp_path, _MODEL, "sv")
    text = "\n".join(p.read_text() for p in sorted(out.glob("*.sv*")))
    cls = text[text.index("class pr_c;"):]
    cls = cls[:cls.index("endclass")]
    assert re.search(r"\b_0;", cls) and re.search(r"\b_1;", cls)
    assert re.search(r"\b_2\[2\];", cls)
    assert "_reserved0" not in cls and "_rsvd" not in cls
