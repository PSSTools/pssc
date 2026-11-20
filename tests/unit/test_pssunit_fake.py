"""Tier 0 register fake, storage (P1.10a, T1.10a) and field kinds (P1.10b).

``pv.RegisterFake`` gives each register instance of a debug image its own
storage, from the image's ``registers`` (P1.7): reset values, READONLY /
WRITEONLY, aliases (``ch[i].regs`` is bound to ``regs.bank[i]``), and the
platform's sparse memory for every other address. Runs on the WB DMA of
``examples/op_model``.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources
import pssc.testing.vm as pv

OP_MODEL = Path(__file__).resolve().parents[2] / "examples" / "op_model" / "pss"
BASE = 0x10000000


@pytest.fixture(scope="module")
def wb_dma():
    files = [str(OP_MODEL / re.sub(r"^src/pss/", "", ln.strip()))
             for ln in (OP_MODEL / "files.f").read_text().splitlines()
             if ln.strip() and not ln.startswith("#")]
    ctx = driver.translate([str(x) for x in sources.all_sources(files)])
    assert not ctx.errors
    return ctx, bc_session.lower(ctx, "wb_dma_c", functions=["*"], debug=True)


def _session(model, fake, **kw):
    s = pv.Session(model, platform=fake, **kw)
    s.call(s.construct(), "initialize", BASE)
    return s


def _cfg(model, **vals):
    return [vals.get(n.split(".")[-1], 0)
            for n, _ in model.entries["wb_dma_ch_c::configure_channel"].params]


def test_writes_land_in_the_register_and_read_back(wb_dma):
    _, model = wb_dma
    fake = pv.RegisterFake()
    s = _session(model, fake)
    ch = s.instance("ch[1]")
    s.call(ch, "configure_channel",
           *_cfg(model, src=0x1234, dst=0x5678, tot_sz=16, inc_src=1))
    assert fake.peek_reg("ch[1].regs.adr0") == 0x1234
    assert fake.peek_reg("ch[1].regs.adr1") == 0x5678
    # the channel's group is the device's bank[1]: one register, once an
    # access to a device register has placed the device's registers
    assert "" not in fake.bases
    s.call(s.instance(""), "pause_engine",
           *[1] * len(model.entries["wb_dma_c::pause_engine"].params))
    assert fake.bases[""] == BASE
    assert fake.peek_reg("regs.bank[1].adr0") == 0x1234
    fake.poke_reg("regs.bank[1].adr0", 0x99)
    assert fake.peek_reg("ch[1].regs.adr0") == 0x99
    fake.poke_reg("regs.bank[1].adr0", 0x1234)
    assert fake.peek_reg("ch[0].regs.adr0") == 0
    n = len(fake.log)
    s.call(ch, "probe_status")
    reads = [e for e in fake.log[n:] if e[0] == "read"]
    assert reads
    csr = fake.peek_reg("ch[1].regs.csr")
    assert all(e[3] == csr for e in reads if e[2] == fake.log[n][2])


def test_plain_accesses_are_sparse_memory(wb_dma):
    _, model = wb_dma
    fake = pv.RegisterFake()
    s = _session(model, fake)
    root = s.instance("")
    n = len(model.entries["wb_dma_c::write_descriptor"].params)
    s.call(root, "write_descriptor", *[0x40 + i for i in range(n)])
    assert fake.mem                      # descriptor words went to memory
    regs = set(fake.reg_at)
    assert not regs & {a for a in fake.mem}


def test_readonly_and_writeonly(wb_dma):
    _, model = wb_dma
    fake = pv.RegisterFake()
    _session(model, fake)
    ro = fake.by_path["regs.int_src_a"]
    fake.poke_reg("regs.int_src_a", 0x5)
    fake.note_access(ro, 0)
    fake.write32(0x40, 0xFF)
    assert fake.peek_reg("regs.int_src_a") == 0x5
    fake.note_access(ro, 0)
    assert fake.read32(0x40) == 0x5
    reg = fake.registers[ro]
    fake.registers[ro] = dict(reg, access="WRITEONLY", fields=[
        dict(f, kind="WO") for f in reg["fields"]])
    fake.reset()
    fake.poke_reg("regs.int_src_a", 0x5)
    fake.note_access(ro, 0)
    assert fake.read32(0x40) == 0
    fake.write32(0x40, 0x7)               # the address is known: the register
    assert fake.peek_reg("regs.int_src_a") == 0x7
    assert 0x40 not in fake.mem


def test_reset_values(wb_dma):
    _, model = wb_dma

    class Board(pv.RegisterFake):
        resets = {"regs.bank[2].csr": 0x40, "ch[3].regs.sz": 0x1FFF}

    fake = Board()
    _session(model, fake)
    assert fake.peek_reg("regs.bank[2].csr") == 0x40
    assert fake.peek_reg("regs.csr") == 0
    fake.poke_reg("regs.bank[2].csr", 1)
    fake.reset()
    assert fake.peek_reg("regs.bank[2].csr") == 0x40

    class Typo(pv.RegisterFake):
        resets = {"regs.bank[2].cs": 1}

    with pytest.raises(ValueError, match=r"regs\.bank\[2\]\.cs"):
        pv.Session(model, platform=Typo())


def test_needs_a_debug_image(wb_dma):
    ctx, _ = wb_dma
    release = bc_session.lower(ctx, "wb_dma_c", functions=["*"])
    with pytest.raises(ValueError, match="debug image"):
        pv.Session(release, platform=pv.RegisterFake())


def test_user_sink_sees_only_what_it_watched(wb_dma):
    from zuspec.be.bc.trace import MemorySink
    _, model = wb_dma
    sink = MemorySink()
    fake = pv.RegisterFake()
    s = _session(model, fake, events=sink)
    s.call(s.instance("ch[0]"), "configure_channel", *_cfg(model, src=9))
    assert fake.peek_reg("ch[0].regs.adr0") == 9
    assert not [e for e in sink.events if e.kind == "access"]


FIELD_ACCESS_PSS = """
package t_pkg {
    import addr_reg_pkg::*;
    struct st_s : packed_s<LITTLE_ENDIAN> {
        bit done;
        bit err;
        bit irq;
        bit[29] rest;
    }
    @pssunit_pkg::field_access {.field = "done", .kind = pssunit_pkg::access_kind_e::RC}
    pure component st_c : reg_c<st_s, READWRITE, 32> {}
    pure component ro_c : reg_c<st_s, READONLY, 32> {}

    @pssunit_pkg::field_access {.reg = "ctl", .field = "err", .kind = pssunit_pkg::access_kind_e::W1S}
    pure component grp_c : reg_group_c {
        @pssunit_pkg::field_access {.field = "err", .kind = pssunit_pkg::access_kind_e::W1C}
        st_c st;
        st_c ctl;
        ro_c sts;
        pure function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["st"]: return 0x0;
                ["ctl"]: return 0x4;
                ["sts"]: return 0x8;
                default: return -1;
            }
        }
        pure function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { default: return -1; }
        }
    }
    component dev_c {
        @pssunit_pkg::field_access {.reg = "sts", .field = "irq", .kind = pssunit_pkg::access_kind_e::W1C}
        grp_c regs;
        solve function void initialize(addr_handle_t base) { regs.set_handle(base); }
        target function bit[32] rd_st() { return regs.st.read_val(); }
        target function void wr_st(bit[32] v) { regs.st.write_val(v); }
        target function void wr_ctl(bit[32] v) { regs.ctl.write_val(v); }
        target function void wr_sts(bit[32] v) { regs.sts.write_val(v); }
    }
}
"""


def test_field_kinds_from_field_access(tmp_path):
    src = tmp_path / "t.pss"
    src.write_text(FIELD_ACCESS_PSS)
    fake = pv.RegisterFake()
    s = pv.load([src], "t_pkg::dev_c", platform=fake)
    kinds = {r["path"]: {f["name"]: f["kind"] for f in r["fields"]}
             for r in s.model.registers.values()}
    # register type; group type naming its register; register instance;
    # the group's instance naming one of its registers
    assert kinds["regs.st"] == {"done": "RC", "err": "W1C", "irq": "RW", "rest": "RW"}
    assert kinds["regs.ctl"] == {"done": "RC", "err": "W1S", "irq": "RW", "rest": "RW"}
    assert kinds["regs.sts"] == {"done": "RO", "err": "RO", "irq": "W1C", "rest": "RO"}
    root = s.construct()
    s.call(root, "initialize", BASE)

    fake.poke_reg("regs.st", 0b111)
    s.call(root, "wr_st", 0b011)            # err W1C: cleared; irq RW: 0
    assert fake.peek_reg("regs.st") == 0b001
    assert s.call(root, "rd_st") == 0b001   # done RC: read, then cleared
    assert fake.peek_reg("regs.st") == 0

    fake.poke_reg("regs.ctl", 0)
    s.call(root, "wr_ctl", 0b010)           # err W1S: set
    s.call(root, "wr_ctl", 0b100)           # ... and kept by a 0
    assert fake.peek_reg("regs.ctl") == 0b110

    fake.poke_reg("regs.sts", 0b111)
    s.call(root, "wr_sts", 0b100 | 0b001)   # RO bits kept, irq cleared
    assert fake.peek_reg("regs.sts") == 0b011


def test_field_access_names_a_field(tmp_path):
    src = tmp_path / "t.pss"
    src.write_text(FIELD_ACCESS_PSS.replace('.field = "done"', '.field = "dne"'))
    with pytest.raises(ValueError, match=r"regs\.st.*'dne'"):
        pv.load([src], "t_pkg::dev_c")


def test_bus_log_is_decoded(tmp_path):
    src = tmp_path / "t.pss"
    src.write_text(FIELD_ACCESS_PSS)
    fake = pv.RegisterFake()
    s = pv.load([src], "t_pkg::dev_c", platform=fake)
    root = s.construct()
    s.call(root, "initialize", BASE)
    s.call(root, "wr_ctl", 0b110)
    fake.write32(0x2000, 7)                 # memory, no note: no source
    e, m = fake.bus[-2:]
    assert (e["op"], e["addr"], e["data"], e["reg"]) == ("write", BASE + 4, 0b110, "regs.ctl")
    assert e["fields"] == {"done": 0, "err": 1, "irq": 1, "rest": 0}
    i = FIELD_ACCESS_PSS.index("regs.ctl.write_val")
    line = FIELD_ACCESS_PSS[:i].count("\n") + 1
    col = i - FIELD_ACCESS_PSS.rfind("\n", 0, i)
    assert e["src"] == f"{src}:{line}:{col}"
    assert m == {"op": "write", "width": 32, "addr": 0x2000, "data": 7,
                 "reg": None, "src": None}
    assert len(fake.bus) == len(fake.log)


def test_back_door(tmp_path):
    src = tmp_path / "t.pss"
    src.write_text(FIELD_ACCESS_PSS)
    fake = pv.RegisterFake()
    s = pv.load([src], "t_pkg::dev_c", platform=fake)
    n = len(fake.log)
    fake.poke_field("regs.sts", "irq", 1)   # RO register: the back door sets it
    fake.poke_field("regs.sts", "rest", 3)
    assert fake.peek_reg("regs.sts") == 0b100 | (3 << 3)
    assert fake.peek_field("regs.sts", "rest") == 3
    fake.check_reg("regs.sts", irq=1, done=0)
    with pytest.raises(AssertionError,
                       match=r"register regs\.sts: irq: expected 0x0, got 0x1; "
                             r"rest: expected 0x1, got 0x3"):
        fake.check_reg("regs.sts", irq=0, rest=1)
    with pytest.raises(AssertionError, match=r"value: expected 0x0"):
        fake.check_reg("regs.sts", 0)
    with pytest.raises(KeyError, match="no field 'nope'"):
        fake.peek_field("regs.sts", "nope")
    with pytest.raises(KeyError, match="no register"):
        fake.peek_field("regs.x", "irq")
    fake.mem_poke(0x3000, 0x11223344)
    assert fake.mem_peek(0x3001, 16) == 0x2233
    assert fake.read32(0x3000) == 0x11223344
    assert len(fake.log) == n + 1


class HookedFake(pv.RegisterFake):
    def __init__(self):
        super().__init__()
        self.seen = []

    @pv.on_write("grp_c.ctl", field="irq")      # group type and register
    def armed(self, ctx, acc):
        self.seen.append(("armed", acc.reg, acc.group, acc.index, acc.old,
                          acc.new, acc.fields["irq"], acc.src is not None,
                          ctx is not None))
        self.poke_field("regs.st", "done", 1)   # the device finishes

    @pv.on_read("t_pkg::grp_c.st")              # package-qualified
    def status(self, ctx, acc):
        self.seen.append(("status", acc.data))

    @pv.on_write("regs.sts")                    # instance path
    def any_sts(self, ctx, acc):
        self.seen.append(("sts", acc.data))


def test_hooks(tmp_path):
    src = tmp_path / "t.pss"
    src.write_text(FIELD_ACCESS_PSS)
    fake = HookedFake()
    s = pv.load([src], "t_pkg::dev_c", platform=fake)
    root = s.construct()
    s.call(root, "initialize", BASE)
    s.call(root, "wr_ctl", 0b010)           # irq 0: not armed
    assert fake.seen == []
    s.call(root, "wr_ctl", 0b100)
    assert fake.seen == [("armed", "regs.ctl", "regs", None, 0b010, 0b110,
                          1, True, True)]
    assert s.call(root, "rd_st") == 1       # the hook ran before the read
    assert fake.seen[-1] == ("status", 1)
    assert fake.peek_reg("regs.st") == 0    # done is RC
    s.call(root, "wr_sts", 5)
    assert fake.seen[-1] == ("sts", 5)
    n = len(fake.seen)
    fake.poke_reg("regs.ctl", 0b100)        # the back door runs no hook
    fake.peek_reg("regs.st")
    assert len(fake.seen) == n


def test_a_hook_names_a_register(tmp_path):
    class Typo(pv.RegisterFake):
        @pv.on_write("grp_c.ctrl")
        def h(self, ctx, acc):
            pass

    class BadField(pv.RegisterFake):
        @pv.on_write("grp_c.ctl", field="ire")
        def h(self, ctx, acc):
            pass

    src = tmp_path / "t.pss"
    src.write_text(FIELD_ACCESS_PSS)
    with pytest.raises(ValueError, match=r"Typo\.h: on_write\('grp_c\.ctrl'\) names no register"):
        pv.load([src], "t_pkg::dev_c", platform=Typo())
    with pytest.raises(KeyError, match="no field 'ire'"):
        pv.load([src], "t_pkg::dev_c", platform=BadField())


def test_scripts_are_library_hooks(tmp_path):
    """status_sequence/complete_after/fail_on_access are hooks of the test:
    dispatched with the mocks' hooks, dropped by ``reset``."""
    src = tmp_path / "t.pss"
    src.write_text(FIELD_ACCESS_PSS)
    fake = pv.RegisterFake()
    s = pv.load([src], "t_pkg::dev_c", platform=fake)
    root = s.construct()
    s.call(root, "initialize", BASE)
    fake.poke_field("regs.st", "irq", 1)
    fake.status_sequence("regs.st", "rest", 5)
    fake.status_sequence("regs.st", "rest", 6)  # same queue, same hook
    fake.complete_after("regs.st", "irq", 2)
    assert [h[0] for h in fake._hooks] == ["value", "value"]
    assert s.call(root, "rd_st") == 0b100 | (5 << 3)
    assert s.call(root, "rd_st") == 0b100 | (6 << 3)   # then irq cleared
    assert [h[0] for h in fake._hooks] == ["value"]    # the countdown is gone
    assert s.call(root, "rd_st") == 0                  # queue empty: as stored
    fake.fail_on_access("regs.sts")
    with pytest.raises(AssertionError,
                       match=r"register regs\.sts accessed \(fail_on_access\)"):
        s.call(root, "wr_sts", 1)
    fake.reset()
    assert fake._hooks == [] and fake.seq == {}
    s.call(root, "wr_sts", 1)
