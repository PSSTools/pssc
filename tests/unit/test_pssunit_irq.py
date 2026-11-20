"""P2.6a: interrupt delivery. ``ctx.raise_irq`` runs the root's interrupt
handler (``notify_irq`` unless the run names another) as a thread of its
own at the next scheduling point, so a test blocked on what the handler
posts wakes up; the ``notify`` event says whether it was delivered.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

import pssc.testing.vm as pv
from zuspec.be.bc.trace.events import EV_NOTIFY

IRQ = """\
import sync_pkg::*;
import pssunit_pkg::*;

import function void kick();

component i_f : fixture_c {
    rt_c rt;
    channel_c<bit, 1> wake;
    bit[32] seen;
    function void notify_irq() {
        seen = seen + 1;
        wake.try_put(1);
    }
    function void other_isr() {
        seen = seen + 10;
        wake.try_put(1);
    }
    @pssunit_pkg::test
    function void t_wait() {
        kick();
        wake.get();
        rt.check_eq_int(seen, 1, "handler ran once");
    }
    @pssunit_pkg::test
    function void t_no_wait() {
        kick();
    }
}
"""


def _kick(r: pv.Registry) -> pv.Registry:
    @r.implements("kick")
    def kick(ctx):
        ctx.raise_irq(2)
    return r


@pytest.fixture(scope="module")
def src(tmp_path_factory):
    f = tmp_path_factory.mktemp("irq") / "irq.pss"
    f.write_text(IRQ)
    return f


def _notify(sink):
    return [e.detail for e in sink.events if e.kind == "notify"]


def test_the_handler_wakes_a_blocked_test(src):
    from zuspec.be.bc.trace import MemorySink

    sink = MemorySink()
    s = pv.load([src], root="i_f", use=[_kick(pv.Registry())], events=sink,
                watch=EV_NOTIFY)
    assert s.irq_entry is not None
    assert s.run_test("i_f::t_wait") == (1, 0, 0)
    assert s.irqs == [2]
    assert _notify(sink) == [{"irq": 2, "delivered": True}]


def test_the_handler_runs_before_the_run_ends(src):
    # nothing waits for it; it still runs (the drain runs every thread)
    s = pv.load([src], root="i_f", use=[_kick(pv.Registry())])
    assert s.run_test("i_f::t_no_wait") == (0, 0, 0)
    assert s.comp_obj.get_field_name("seen") == 1


def test_the_run_names_the_handler(src):
    s = pv.load([src], root="i_f", use=[_kick(pv.Registry())],
                irq_handler="i_f::other_isr")
    assert s.run_test("i_f::t_wait") == (0, 1, 0)      # seen == 10


def test_no_handler_is_not_delivered(src):
    from zuspec.be.bc.trace import MemorySink

    sink = MemorySink()
    s = pv.load([src], root="i_f", use=[_kick(pv.Registry())], events=sink,
                watch=EV_NOTIFY, irq_handler=None)
    assert s.irq_entry is None
    # undelivered: the unconsumed-irq sanitizer fails the test (P2.6b)
    assert s.run_test("i_f::t_no_wait") == (0, 1, 0)
    assert _notify(sink) == [{"irq": 2, "delivered": False}]
    # a name the image has no function entry for is no handler either
    s = pv.load([src], root="i_f", irq_handler="no_such_isr")
    assert s.irq_entry is None
