"""The async half of the runtime: a blocking channel, and a stub to drive.

Copied beside the generated module by `op-model-py --py-await async`, alongside
`pssc_rt.py` -- which is still copied, because `check_import_api` and the sparse
memory it wraps are wanted in either form.

WHAT THE ASYNC FORM ADDS is one thing: a channel whose `get()`/`put()` BLOCK.
Everything else about the two forms is the same program with a colour on it. The
sync `Chan1` raises on `get()` because a plain method has no scheduler to
suspend to; here there is one, so `get()` means what PSS says it means -- wait
until someone posts.

THE SCHEDULER IS NOT NAMED. `Chan1` takes an event FACTORY and calls it; it
never imports `asyncio` unless it has to build the default. The protocol it
needs is three methods -- `await ev.wait()`, `ev.set()`, `ev.clear()` -- which
is satisfied by `asyncio.Event` and by `cocotb.triggers.Event` alike. That
compatibility is the entire reason the type is left unnamed: a generated model
should run under a cocotb simulation and under `asyncio.run()` without being
regenerated, and naming either scheduler here would decide which.
"""
from __future__ import annotations

__all__ = ["AsyncMemoryBus", "Chan1", "ChannelEmpty", "ChannelFull"]

from pssc_rt import ChannelEmpty, ChannelFull      # noqa: F401  (re-exported)


def _default_event():
    """An `asyncio.Event`, imported only if it is actually needed.

    Lazily, and that is not micro-optimisation: a cocotb harness passes its own
    event factory and should not drag asyncio into a simulator process that has
    its own event loop.
    """
    import asyncio

    return asyncio.Event()


class AsyncMemoryBus:
    """`pssc_rt.MemoryBus`, with the seam coloured. Same log, same addresses.

    The async counterpart, and it is a WRAPPER rather than a copy so that the
    two cannot drift: a test that drives the same operations through both and
    compares `log` is asserting that the async lowering issues exactly the
    accesses the sync one does -- same width, same address, same data, same
    order. That test is what catches an `await` placed where it changes when
    something is evaluated, which no golden snapshot can see.
    """

    def __init__(self, wordsize: int = 4, event=None):
        from pssc_rt import MemoryBus

        self._sync = MemoryBus(wordsize)
        self._event = event or _default_event

    @property
    def mem(self):
        return self._sync.mem

    @property
    def log(self):
        return self._sync.log

    async def read8(self, addr):    return self._sync.read8(addr)

    async def read16(self, addr):   return self._sync.read16(addr)

    async def read32(self, addr):   return self._sync.read32(addr)

    async def read64(self, addr):   return self._sync.read64(addr)

    async def write8(self, addr, data):    self._sync.write8(addr, data)

    async def write16(self, addr, data):   self._sync.write16(addr, data)

    async def write32(self, addr, data):   self._sync.write32(addr, data)

    async def write64(self, addr, data):   self._sync.write64(addr, data)

    def message(self, text):
        """NOT `async def`, in either form: writing a line consumes no time.

        The generated Protocol says the same thing, and the two agreeing is
        what `check_import_api` checks.
        """
        self._sync.message(text)

    async def yield_(self):
        """What a PSS `yield` costs here: one trip through the event loop.

        `asyncio.sleep(0)` is the minimum honest implementation -- it gives
        every other ready task a turn and returns. A real platform charges what
        waiting actually costs it (a clock edge, a watchdog kick).
        """
        import asyncio

        await asyncio.sleep(0)

    def event(self):
        """A fresh event for a generated channel. See the module docstring."""
        return self._event()


class Chan1(object):
    """A depth-1 `sync_pkg::channel_c` whose `get`/`put` suspend.

    Depth 1 only, matching every other backend's channel
    (`share/c/pssc_chan.h`, `share/cpp/pssc_chan.hpp`, `pssc_rt.Chan1`): a
    deeper channel is a ring buffer, which is a different type per capacity, and
    the generator refuses one rather than quietly widening it.

    TWO EVENTS, not one. A depth-1 channel has two conditions to wait on --
    "something arrived" and "room appeared" -- and one event shared between them
    would wake a waiting `put()` when a `get()` should have been woken.

    The waits are `while`, not `if`, and that is what makes two waiters safe. A
    woken `get()` re-checks the flag, so if another task took the value first it
    goes back to waiting instead of returning a value nobody posted. Two
    concurrent `get()`s on a depth-1 channel is a modelling error either way --
    the model states one consumer -- but the runtime's job when the model is
    wrong is to block, not to hand the same token to both.
    """

    __slots__ = ("value", "full", "_arrived", "_drained")

    def __init__(self, event=None):
        make = event or _default_event
        self.value = 0
        self.full = False
        self._arrived = make()
        self._drained = make()

    # -- non-blocking: identical to the sync runtime's ------------------------

    def try_put(self, value) -> bool:
        if self.full:
            return False
        self.value = value
        self.full = True
        self._arrived.set()
        self._drained.clear()
        return True

    def try_get(self, out):
        """Receive into *out*, a ONE-ELEMENT LIST; return whether anything came.

        The cell is how a PSS output argument reaches Python -- an int is
        immutable and a name rebound inside a call is not rebound outside it --
        and it is the same protocol the sync runtime uses, deliberately: the two
        forms lower `try_get` identically, so the shape a body reads has one
        explanation and not two. See `pssc_rt.Chan1.try_get`.
        """
        if not self.full:
            return False
        self.full = False
        out[0] = self.value
        self._drained.set()
        self._arrived.clear()
        return True

    # -- blocking: what the async form exists for ----------------------------

    async def put(self, value):
        while self.full:
            self._drained.clear()
            await self._drained.wait()
        self.try_put(value)

    async def get(self):
        while not self.full:
            self._arrived.clear()
            await self._arrived.wait()
        out = [0]
        self.try_get(out)
        return out[0]
