"""P5.6: field kinds from a register description (``--reg-semantics``)."""
import json

import pytest

from pssc.testing import reg_semantics as rs
import pssc.testing.vm as pv

_IPXACT = """\
<ipxact:component xmlns:ipxact="http://www.accellera.org/XMLSchema/IPXACT/1685-2014">
 <ipxact:name>dma</ipxact:name>
 <ipxact:memoryMaps><ipxact:memoryMap><ipxact:name>m</ipxact:name>
  <ipxact:addressBlock><ipxact:name>regs</ipxact:name>
   <ipxact:register><ipxact:name>csr</ipxact:name>
    <ipxact:field><ipxact:name>en</ipxact:name>
     <ipxact:volatile>true</ipxact:volatile>
     <ipxact:access>read-write</ipxact:access></ipxact:field>
    <ipxact:field><ipxact:name>err</ipxact:name>
     <ipxact:access>read-only</ipxact:access>
     <ipxact:readAction>clear</ipxact:readAction></ipxact:field>
    <ipxact:field><ipxact:name>done</ipxact:name>
     <ipxact:access>read-write</ipxact:access>
     <ipxact:modifiedWriteValue>oneToClear</ipxact:modifiedWriteValue></ipxact:field>
    <ipxact:field><ipxact:name>go</ipxact:name>
     <ipxact:access>write-only</ipxact:access></ipxact:field>
   </ipxact:register>
   <ipxact:registerFile><ipxact:name>ch</ipxact:name>
    <ipxact:register><ipxact:name>csr</ipxact:name>
     <ipxact:access>read-only</ipxact:access>
     <ipxact:field><ipxact:name>busy</ipxact:name></ipxact:field>
     <ipxact:field><ipxact:name>irq</ipxact:name>
      <ipxact:fieldAccessPolicies><ipxact:fieldAccessPolicy>
       <ipxact:access>read-write</ipxact:access>
       <ipxact:modifiedWriteValue>oneToSet</ipxact:modifiedWriteValue>
      </ipxact:fieldAccessPolicy></ipxact:fieldAccessPolicies></ipxact:field>
    </ipxact:register>
   </ipxact:registerFile>
  </ipxact:addressBlock>
 </ipxact:memoryMap></ipxact:memoryMaps>
</ipxact:component>
"""


def test_ipxact_field_kinds(tmp_path):
    f = tmp_path / "dma.xml"
    f.write_text(_IPXACT)
    assert rs.load(str(f)) == {
        "csr": {"en": "VOLATILE", "err": "RC", "done": "W1C", "go": "WO"},
        "ch.csr": {"busy": "RO", "irq": "W1S"},
    }


def test_json_table(tmp_path):
    f = tmp_path / "t.json"
    f.write_text(json.dumps({"csr": {"err": "rc"}}))
    assert rs.load(str(f)) == {"csr": {"err": "RC"}}


def test_unknown_format(tmp_path):
    with pytest.raises(ValueError, match="IP-XACT"):
        rs.load(str(tmp_path / "x.txt"))


def test_kinds_for_matches_longest_suffix():
    table = {"csr": {"a": "RC"}, "ch.csr": {"a": "W1C"}, "sr": {"a": "RO"}}
    assert rs.kinds_for(table, "top.regs.csr") == {"a": "RC"}
    assert rs.kinds_for(table, "top.regs.ch[1].csr") == {"a": "W1C"}
    assert rs.kinds_for(table, "top.regs.xcsr") == {}


# --- SystemRDL (P5.6b) --------------------------------------------------

RDL = """
reg ctl_r {
    field { sw = rw; hw = r; } en[0:0] = 0;
    field { sw = r; hw = w; rclr; } irq[1:1] = 0;
    field { sw = rw; hw = w; onwrite = woclr; } err[2:2] = 0;
    field { sw = rw; hw = r; onwrite = woset; } go[3:3] = 0;
    field { sw = w; hw = r; } kick[4:4] = 0;
    field { sw = rw; hw = r; hwclr; } busy[5:5] = 0;
};
regfile ch_rf { ctl_r ctl @0x0; };
addrmap top { ctl_r ctl @0x0; ch_rf ch[2] @0x10; };
"""


def test_rdl_field_kinds(tmp_path):
    pytest.importorskip("systemrdl")
    f = tmp_path / "t.rdl"
    f.write_text(RDL)
    kinds = {"en": "RW", "irq": "RC", "err": "W1C", "go": "W1S",
             "kick": "WO", "busy": "VOLATILE"}
    assert rs.load(str(f)) == {"ctl": kinds, "ch.ctl": kinds}


def test_rdl_bench_regfile():
    """The bench's channel bank has no addrmap: its registers are keyed
    from the regfile, and the interrupt flags read-clear."""
    pytest.importorskip("systemrdl")
    import os
    path = os.path.join(os.path.dirname(__file__), "..", "..", "examples",
                        "fw_wb_dma_uvm", "src", "rdl", "wb_dma_ch_regs.rdl")
    csr = rs.load(path)["csr"]
    assert [csr[f] for f in ("int_err", "int_done", "int_chk_done")] == \
        ["RC"] * 3
    assert (csr["err"], csr["ch_en"], csr["stop"]) == ("RO", "VOLATILE", "WO")


def test_rdl_errors(tmp_path):
    pytest.importorskip("systemrdl")
    f = tmp_path / "bad.rdl"
    f.write_text("reg r { field { sw = rw; } a[0:0] = 0 }")
    with pytest.raises(ValueError, match="bad.rdl"):
        rs.load(str(f))


# --- the fake follows the file (P5.6a2) ---------------------------------

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.cli import main  # noqa: E402
from pssc.testing import cmd  # noqa: E402

from .test_pssunit_fake import FIELD_ACCESS_PSS  # noqa: E402

SUITE = """\
import addr_reg_pkg::*;
import pssunit_pkg::*;

package s_pkg {
    import addr_reg_pkg::*;
    struct st_s : packed_s<LITTLE_ENDIAN> {
        bit done;
        bit[31] rest;
    }
    pure component st_c : reg_c<st_s, READWRITE, 32> {}
    pure component grp_c : reg_group_c {
        st_c st;
        pure function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["st"]: return 0x0;
                default: return -1;
            }
        }
        pure function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { default: return -1; }
        }
    }
}

component s_f : fixture_c {
    rt_c rt;
    s_pkg::grp_c regs;
    @pssunit_pkg::test
    function void t_rc() {
        bit[32] v;
        regs.set_handle(make_handle_from_handle(0, 0x1000));
        regs.st.write_val(1);
        v = regs.st.read_val();
        rt.check_eq_int(v, 1, "first read");
        v = regs.st.read_val();
        rt.check_eq_int(v, 0, "cleared by the read");
    }
}
"""


def test_file_kinds_under_annotations(tmp_path):
    src = tmp_path / "t.pss"
    src.write_text(FIELD_ACCESS_PSS)
    sem = tmp_path / "s.json"
    sem.write_text(json.dumps({
        "regs.st": {"done": "W1S", "irq": "W1C"},  # done: the annotation wins
        "sts": {"rest": "rc"},
        "ctl": {"nope": "RC"},                     # not in PSS: dropped
    }))
    fake = pv.RegisterFake()
    s = pv.load([src], "t_pkg::dev_c", platform=fake, reg_semantics=str(sem))
    kinds = {r["path"]: {f["name"]: f["kind"] for f in r["fields"]}
             for r in s.model.registers.values()}
    assert kinds["regs.st"] == {"done": "RC", "err": "W1C", "irq": "W1C", "rest": "RW"}
    assert kinds["regs.sts"] == {"done": "RO", "err": "RO", "irq": "W1C", "rest": "RC"}
    assert kinds["regs.ctl"] == {"done": "RC", "err": "W1S", "irq": "RW", "rest": "RW"}
    root = s.construct()
    s.call(root, "initialize", 0x10000000)
    fake.poke_reg("regs.st", 0b100)
    s.call(root, "wr_st", 0b100)            # irq W1C from the file
    assert fake.peek_reg("regs.st") == 0


@pytest.fixture
def suite(tmp_path, monkeypatch):
    (tmp_path / "t.pss").write_text(SUITE)
    (tmp_path / "s.json").write_text(json.dumps({"st": {"done": "RC"}}))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cmd, "LAST_FAILED", str(tmp_path / "lastfailed"))
    return tmp_path


def test_pssc_test_reg_semantics(suite, capsys):
    base = ["test", "t.pss", "--no-ctrf", "--platform", "fake", "--seed", "1"]
    assert main(base) == 1                  # done is RW: the second read is 1
    out = capsys.readouterr().out
    assert "cleared by the read" in out
    assert main(base + ["--reg-semantics", "s.json"]) == 0
    # the replay line carries the file
    assert "--reg-semantics s.json" in " ".join(cmd.replay_options(
        cmd.argparse.Namespace(reg_semantics="s.json")))


def test_pssc_test_bad_file(suite, capsys):
    rc = main(["test", "t.pss", "--no-ctrf", "--reg-semantics", "s.txt"])
    assert rc != 0 and "IP-XACT" in capsys.readouterr().err
