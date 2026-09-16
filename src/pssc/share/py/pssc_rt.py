"""Implementations and helpers for a pssc-generated Python operation model.

Copied beside the generated module by `op-model-py` (suppress with
`--no-core-copy` if your project installs it another way). Nothing here is
generated, and nothing here is *required*: the generated module imports from
this file only when the model declares channels.

THE SEAM IS DECLARED BY THE GENERATED MODULE, NOT BY THIS ONE. Each generated
module emits its own `<Root>ImportApi` -- a `typing.Protocol` listing exactly
what that model's bodies call plus every import it declares. That Protocol is
the authority on what a platform must supply; this file ships things that
satisfy it (`MemoryBus`) and a way to find out whether yours does
(`check_import_api`). There is no base class to inherit, deliberately: the
object you pass may be a cocotb driver, a socket client or a register model
from another framework.

Widths are named rather than parameterised (`read32`, not `read(32, addr)`)
because that is how the PSS core library declares them (`addr_reg_pkg`, 21.12)
and how every other pssc backend renders them; a platform written against one
generated model works against them all.
"""
from __future__ import annotations

import inspect
from typing import Any, List

__all__ = ["MemoryBus", "Chan1", "ChannelEmpty", "ChannelFull",
           "check_import_api"]


class MemoryBus:
    """A flat sparse memory. Enough to bring a generated model up and to test it.

    Reads of never-written addresses return 0, which is right for a memory and
    wrong for a device: a status bit polled in a completion loop never sets, so
    a `repeat {} while` against this spins forever. That is a property of the
    stub, not of the generated code -- drive a real device model, or subclass
    and override the register in question.

    Every width is implemented, which is more than any one model needs. There is
    no base class declaring the ones a model does NOT use, and that is the
    change the generated Protocol made: what a model requires is now written in
    the model's own module, so a stub here is free to answer everything.
    """

    def __init__(self, wordsize: int = 4):
        self.mem = {}
        self.wordsize = wordsize
        #: Every access, in order: `("read", width, addr, data)`. A test asserts
        #: on this rather than on the generated source, which is what makes it a
        #: check of BEHAVIOUR and not of spelling.
        self.log = []

    # -- the seam -----------------------------------------------------------

    def read8(self, addr):    return self._read(8, addr)

    def read16(self, addr):   return self._read(16, addr)

    def read32(self, addr):   return self._read(32, addr)

    def read64(self, addr):   return self._read(64, addr)

    def write8(self, addr, data):    self._write(8, addr, data)

    def write16(self, addr, data):   self._write(16, addr, data)

    def write32(self, addr, data):   self._write(32, addr, data)

    def write64(self, addr, data):   self._write(64, addr, data)

    def message(self, text):
        self.log.append(("message", 0, 0, text))

    # -- storage ------------------------------------------------------------

    def _read(self, width, addr):
        data = self.mem.get(addr, 0) & ((1 << width) - 1)
        self.log.append(("read", width, addr, data))
        return data

    def _write(self, width, addr, data):
        data &= (1 << width) - 1
        self.mem[addr] = data
        self.log.append(("write", width, addr, data))


# --- conformance reporting --------------------------------------------------

def check_import_api(obj: Any, proto: type) -> List[str]:
    """What *obj* is missing to satisfy *proto*. Empty means it satisfies it.

    A DIAGNOSTIC, not a gate. Nothing calls this automatically: a generated
    model constructs and runs against whatever it was handed, because the
    duck-typing is the point and a mandatory check would be a way to reject an
    object that works. Call it yourself when bringing a platform up.

    `isinstance(obj, proto)` answers the same question for a `@runtime_checkable`
    Protocol and answers it worse in two ways, which are the two reasons this
    function exists:

    * it says only *no*, never *which method*;
    * it does not look at whether a method is `async`, so a synchronous platform
      passed to a model generated with `--py-await async` is reported as
      CONFORMING and then returns a coroutine object where the model expects an
      integer. That failure surfaces as a register value of the wrong type,
      arbitrarily far from its cause.

    Known limits, stated rather than worked around:

    * A plain `def` that RETURNS an awaitable is a legitimate way to implement
      an async method and is reported here as synchronous. If that is what you
      are doing, this function is not the tool.
    * Argument NAMES are not compared, only how many can be passed. A platform
      is free to call the address parameter whatever it likes.
    """
    problems: List[str] = []
    for name in sorted(_protocol_members(proto)):
        want = getattr(proto, name, None)
        got = getattr(obj, name, None)
        if got is None:
            problems.append(f"{name}: missing")
            continue
        if not callable(got):
            problems.append(f"{name}: present but not callable ({type(got).__name__})")
            continue
        if _is_async(want) and not _is_async(got):
            problems.append(
                f"{name}: must be `async def` -- this model was generated with "
                f"--py-await async")
        elif not _is_async(want) and _is_async(got):
            problems.append(
                f"{name}: must NOT be `async def` -- this model was generated "
                f"with --py-await sync")
        arity = _arity_problem(name, want, got)
        if arity:
            problems.append(arity)
    return problems


def _protocol_members(proto: type) -> List[str]:
    """The names *proto* requires.

    `__protocol_attrs__` where the interpreter provides it, and the class
    dictionary otherwise -- the attribute is not part of `typing`'s public API
    and has moved between versions, and a generated driver is exactly the code
    that ends up on whatever Python the lab machine has.
    """
    names = getattr(proto, "__protocol_attrs__", None)
    if names:
        return list(names)
    return [n for n, v in vars(proto).items()
            if not n.startswith("_") and callable(v)]


def _is_async(fn: Any) -> bool:
    """Is *fn* a coroutine function, seen through wrappers?

    `inspect.unwrap` follows `functools.wraps`; a bound method's `__func__` is
    what carries the flag. `functools.partial` is handled by `iscoroutinefunction`
    itself on modern Pythons and by the `func` walk here on older ones.
    """
    seen = set()
    while fn is not None and id(fn) not in seen:
        seen.add(id(fn))
        if inspect.iscoroutinefunction(fn):
            return True
        nxt = getattr(fn, "__func__", None) or getattr(fn, "func", None)
        if nxt is None:
            try:
                nxt = inspect.unwrap(fn)
            except Exception:
                nxt = None
            if nxt is fn:
                nxt = None
        fn = nxt
    return False


def _arity_problem(name: str, want: Any, got: Any):
    """Can *got* be called with the arguments *want* declares? Or ``None``.

    Silent on anything it cannot inspect -- a C-implemented callable, a
    `__call__` object -- because "I could not tell" and "it is wrong" are
    different answers and only one of them belongs in this list.
    """
    try:
        need = len([p for p in inspect.signature(want).parameters.values()
                    if p.name != "self"
                    and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)
                    and p.default is p.empty])
        sig = inspect.signature(got)
    except (TypeError, ValueError):
        return None
    params = [p for p in sig.parameters.values()
              if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    if any(p.kind is p.VAR_POSITIONAL for p in sig.parameters.values()):
        return None
    if len(params) < need:
        return (f"{name}: takes {len(params)} argument(s), the model calls it "
                f"with {need}")
    required = len([p for p in params if p.default is p.empty])
    if required > need:
        return (f"{name}: requires {required} argument(s), the model calls it "
                f"with {need}")
    return None


# --- channels ---------------------------------------------------------------

class ChannelEmpty(Exception):
    """A blocking `get()` on an empty channel, with no scheduler to wait in."""


class ChannelFull(Exception):
    """A blocking `put()` on a full channel, with no scheduler to wait in."""


class Chan1(object):
    """A depth-1 `sync_pkg::channel_c`.

    Depth 1 only, matching `share/c/pssc_chan.h` and `share/cpp/pssc_chan.hpp`:
    a deeper channel is a ring buffer, which is a different type per capacity,
    and the generator refuses one rather than quietly widening it.

    `get`/`put` SUSPEND in PSS, and a model generated with `--py-await sync` has
    no scheduler to suspend to, so they raise instead of blocking -- the same
    position the C and C++ targets take, and for the same reason: a `get()` that
    returned whatever was in the slot would report a completion nobody
    signalled. Use `try_get`/`try_put`, which the generator does lower. The
    async form gets `pssc_rt_async.Chan1`, where blocking has an answer.
    """

    __slots__ = ("value", "full")

    def __init__(self):
        self.value = 0
        self.full = False

    def try_put(self, value) -> bool:
        if self.full:
            return False
        self.value = value
        self.full = True
        return True

    def try_get(self, out):
        """Receive into *out*, a ONE-ELEMENT LIST; return whether anything came.

        The list is how a PSS output argument reaches Python. `try_get(tok)`
        writes through a pointer in C and cannot in Python, where an int is
        immutable and a name rebound inside a call is not rebound outside it --
        so the generator declares exactly those locals as cells (it knows which
        ones; the same analysis widens them to 64 bits in the C backend) and
        this writes into the cell.

        A tuple return would read better and would not work: the call appears
        in `if (!inflight.try_get(tok))`, where its value is a condition and
        there is nowhere to unpack a second result to.
        """
        if not self.full:
            return False
        self.full = False
        out[0] = self.value
        return True

    def put(self, value):
        raise ChannelFull(
            "put() blocks, and a model generated with --py-await sync has no "
            "scheduler to block in. Use try_put(), or generate with "
            "--py-await async.")

    def get(self):
        raise ChannelEmpty(
            "get() blocks, and a model generated with --py-await sync has no "
            "scheduler to block in. Use try_get(), or generate with "
            "--py-await async.")
