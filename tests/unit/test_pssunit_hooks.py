"""P2.6c/d: PSS reaction hook declarations (``@pssunit_pkg::on_write`` /
``on_read`` / ``on_event`` on a ``mock_c``'s target functions), resolved
against the fixture's registers when the image is lowered; each has an
entry, and a ``RegisterFake`` runs the register hooks."""
from __future__ import annotations

import pytest

import pssc.testing.vm as pv
from pssc.diag import Refusal

from .test_pssunit_fake import FIELD_ACCESS_PSS

MOCK = '''    import function void hooked(int id);
    component base_mock_c : pssunit_pkg::mock_c {
        @pssunit_pkg::on_read {.reg = "regs.sts"}
        target function void peeked() { }
    }
    component dma_mock_c : base_mock_c {
        @pssunit_pkg::on_write {.reg = "grp_c.ctl", .field = "err"}
        target function void armed() { hooked(2); }
        @pssunit_pkg::on_event {.name = "done"}
        target function void finish(int ch) { }
    }
    component dev_c {
        dma_mock_c mock;'''


def _src(tmp_path, *edits):
    text = FIELD_ACCESS_PSS.replace("    component dev_c {", MOCK)
    for old, new in edits:
        assert old in text
        text = text.replace(old, new)
    p = tmp_path / "t.pss"
    p.write_text(text)
    return p, text


def _line(text, needle):
    return text[:text.index(needle)].count("\n") + 1


def test_hooks_resolved(tmp_path):
    p, text = _src(tmp_path)
    s = pv.load([p], "t_pkg::dev_c", platform=pv.RegisterFake())
    regs = s.model.registers
    by_fn = {h.fn: h for h in s.model.hooks}
    assert set(by_fn) == {"t_pkg::base_mock_c::peeked",
                          "t_pkg::dma_mock_c::armed",
                          "t_pkg::dma_mock_c::finish"}
    armed = by_fn["t_pkg::dma_mock_c::armed"]
    assert (armed.op, armed.inst, armed.field) == ("write", "mock", "err")
    assert {regs[r]["path"] for r in armed.rids} == {"regs.ctl"}
    line = _line(text, "@pssunit_pkg::on_write")
    assert armed.src.startswith(f"{p}:{line}:")
    peeked = by_fn["t_pkg::base_mock_c::peeked"]
    assert (peeked.op, peeked.field) == ("read", None)
    assert {regs[r]["path"] for r in peeked.rids} == {"regs.sts"}
    finish = by_fn["t_pkg::dma_mock_c::finish"]
    assert (finish.op, finish.name, finish.rids) == ("event", "done", frozenset())


def test_no_mock_no_hooks(tmp_path):
    p = tmp_path / "t.pss"
    p.write_text(FIELD_ACCESS_PSS)
    assert pv.load([p], "t_pkg::dev_c").model.hooks == []


@pytest.mark.parametrize("old,new,msg", [
    ('"grp_c.ctl"', '"grp_c.ctrl"', r"'grp_c\.ctrl' names no register"),
    ('.field = "err"}\n', '.field = "eror"}\n', r"regs\.ctl has no field 'eror'"),
    ("target function void armed", "function void armed",
     r"must be a 'target function'"),
])
def test_bad_hook_is_located(tmp_path, old, new, msg):
    p, text = _src(tmp_path, (old, new))
    with pytest.raises(Refusal, match=msg) as ei:
        pv.load([p], "t_pkg::dev_c")
    line = _line(text, "@pssunit_pkg::on_write")
    assert str(ei.value).startswith(f"{p}:{line}:")


def _hooked(seen):
    r = pv.Registry()

    @r.implements("hooked")
    def hooked(ctx, i):
        seen.append(i)
    return r


def test_hooks_have_entries(tmp_path):
    # an empty body too ("*" skips one), and when the run names functions
    p, _ = _src(tmp_path)
    s = pv.load([p], "t_pkg::dev_c", functions=["t_pkg::dev_c::wr_ctl"])
    assert {h.entry for h in s.model.hooks} <= set(s.model.entries)
    assert "t_pkg::dma_mock_c::peeked" in s.model.entries


def test_write_hook_runs_in_the_mock(tmp_path):
    p, _ = _src(tmp_path)
    seen = []
    fake = pv.RegisterFake()
    s = pv.load([p], "t_pkg::dev_c", platform=fake, use=[_hooked(seen)])
    root = s.construct()
    s.call(root, "initialize", 0x1000)
    s.call(root, "wr_ctl", 0b100)           # field err not written with a 1
    assert seen == []
    s.call(root, "wr_ctl", 0b010)
    assert seen == [2]
    assert fake.peek_reg("regs.ctl") == 0b010   # the access is unchanged
    s.call(root, "rd_st")                   # not the hooked register
    assert seen == [2]


def test_read_hook_runs(tmp_path):
    p, _ = _src(tmp_path, ("void peeked() { }", "void peeked() { hooked(1); }"),
                ("target function void wr_sts", "target function bit[32] rd_sts() "
                 "{ return regs.sts.read_val(); }\n        target function void wr_sts"))
    seen = []
    s = pv.load([p], "t_pkg::dev_c", platform=pv.RegisterFake(),
                use=[_hooked(seen)])
    root = s.construct()
    s.call(root, "initialize", 0x1000)
    s.call(root, "rd_sts")
    s.call(root, "rd_sts")
    assert seen == [1, 1]


# ---- P2.6e: the hook library -------------------------------------------------

def test_hook_library(tmp_path):
    # a write hook counts in the mock's state, sets a field through the back
    # door (poke_field_at, index 0 of an unindexed register) and raises irq 3
    body = ("void armed() {\n"
            "            pssunit_pkg::state_set(\"n\", pssunit_pkg::state_get(\"n\") + 1);\n"
            "            hooked(pssunit_pkg::state_get(\"n\"));\n"
            "            pssunit_pkg::poke_field_at(0, \"grp_c.sts\", \"irq\", 1);\n"
            "            pssunit_pkg::raise_irq(3);\n"
            "        }")
    p, _ = _src(tmp_path, ("void armed() { hooked(2); }", body))
    seen = []
    fake = pv.RegisterFake()
    s = pv.load([p], "t_pkg::dev_c", platform=fake, use=[_hooked(seen)],
                irq_handler=None)
    root = s.construct()
    s.call(root, "initialize", 0x1000)
    s.call(root, "wr_ctl", 0b010)
    s.call(root, "wr_ctl", 0b010)
    assert seen == [1, 2]
    assert fake.peek_field("regs.sts", "irq") == 1
    assert s.irqs == [3, 3]


def test_poke_field_at_needs_the_index(tmp_path):
    fake = pv.RegisterFake()
    p, _ = _src(tmp_path)
    pv.load([p], "t_pkg::dev_c", platform=fake)
    fake.poke_field_at(0, "regs.sts", "irq", 1)
    assert fake.peek_field("regs.sts", "irq") == 1
    with pytest.raises(KeyError, match="at index 1"):
        fake.poke_field_at(1, "regs.sts", "irq", 1)


def test_access_and_schedule_after(tmp_path):
    # armed is given the access (index -1: regs.ctl is in no array) and
    # schedules finish(ch) 5 ticks on, which runs within the same call
    body = ("void armed(pssunit_pkg::access_s a) {\n"
            "            if (a.index == -1 && a.is_write) { hooked(1); }\n"
            "            hooked(a.data);\n"
            "            pssunit_pkg::schedule_after(5, \"done\", 7);\n"
            "        }")
    p, _ = _src(tmp_path, ("void armed() { hooked(2); }", body),
                ("void finish(int ch) { }", "void finish(int ch) { hooked(ch); }"))
    seen = []
    r = pv.Registry()

    @r.implements("hooked")
    def hooked(ctx, i):
        seen.append((i, ctx.time.now))
    s = pv.load([p], "t_pkg::dev_c", platform=pv.RegisterFake(), use=[r])
    root = s.construct()
    s.call(root, "initialize", 0x1000)
    t0 = s.ctx.time.now
    s.call(root, "wr_ctl", 0b010)
    assert seen == [(1, t0), (0b010, t0), (7, t0 + 5)]


def test_schedule_after_needs_a_hook(tmp_path):
    p, _ = _src(tmp_path, ("void armed() { hooked(2); }",
                           "void armed() { pssunit_pkg::schedule_after(1, \"dne\", 0); }"))
    s = pv.load([p], "t_pkg::dev_c", platform=pv.RegisterFake())
    root = s.construct()
    s.call(root, "initialize", 0x1000)
    with pytest.raises(Exception, match="no on_event hook is named 'dne'"):
        s.call(root, "wr_ctl", 0b010)
