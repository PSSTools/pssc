"""Sanitizers, first set (design §6.8; plan P1.12).

Each is a consumer of the VM's ``access`` events (``detail`` = ``{"rw",
"width", "addr", "data"}`` + ``reg_id`` for a register's access), so it runs
on whatever produced the stream: a register fake or, later, RTL.

* ``poll-budget`` -- a read of one address returned the same value more than
  ``poll_budget`` times with no progress between (no write, no read of that
  address returning something else). A spin on a fake that never completes
  stops the test, located at the polling read, instead of hanging.
* ``unmapped`` -- an access no register and no declared region covers.
  Registers are placed from the register accesses seen (a component's base is
  learned the first time one of its registers is accessed). With ``regions``
  (``[(base, size)]``) declared, any other address is unmapped; without, only
  an address inside a placed register block that no register covers is (the
  rest is taken to be plain memory).
* ``access-rule`` -- a write to a READONLY register, or a read of a WRITEONLY
  one. A register with fields is read-only when all its fields are RO
  (write-only: all WO), whatever its access says.
* ``inflight`` (P2.5a) -- a consumer of ``chan`` events, checked at the end of
  a test (``end``): a latch still holds a token, i.e. an operation started
  and never completed. A latch is a channel the model only ever uses with
  ``try_put``/``try_get`` (``latches``; a wake channel, which someone
  ``get``s, is not one). Channels are told apart by name, as the code wrote
  it, so the ``inflight`` of every instance is one count; the finding is
  located at the ``try_put`` whose token was never taken. A test that stopped
  early (a failed require, a sanitizer that stops) is not checked.
* ``rc-read`` (P2.5b) -- a read of a register with a read-to-clear (``RC``)
  field whose value the code drops: ``regs.st.read_val();`` as a statement
  clears the field and loses what it held. Debug lowering marks such reads
  (the ``access`` event says ``discarded``). A test that means the clear
  says so with ``@pssunit_pkg::allow {.what = "rc-read"}``.
* ``unbound`` (P2.5c) -- an access to a register of a group no ``set_handle``
  has placed: the group's handle is still 0, so the register's address is
  its bare offset. A group really placed at address 0 looks the same; its
  tests say ``@pssunit_pkg::allow {.what = "unbound"}``.
* ``unconsumed-irq`` (P2.6b) -- an interrupt raised (``ctx.raise_irq``) that
  nothing delivers: the run has no interrupt handler (``irq_handler``) and
  the platform no ``raise_irq`` of its own, so nothing ever sees it. Located at the access or import whose double
  raised it (the next ``access``/``import`` event: the one in progress);
  one finding per interrupt number per test.

``@pssunit_pkg::allow {.what = "<name>"}`` on a test function switches one off
for that test (``allows``). A finding is a FAIL located at the access.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple

NAMES = ("poll-budget", "unmapped", "access-rule", "inflight", "rc-read", "unbound",
         "unconsumed-irq")

#: reads without progress before ``poll-budget`` fails (calibrated in T1.12)
DEFAULT_POLL_BUDGET = 1000


@dataclass
class Finding:
    name: str
    what: str
    src_ref: int = 0
    #: ``file:line`` of the access, when the image has provenance
    where: Optional[str] = None

    def __str__(self) -> str:
        at = f" at {self.where}" if self.where else ""
        return f"sanitizer {self.name}{at}: {self.what}"


class SanitizerFailed(Exception):
    """A sanitizer that stops the test (``poll-budget``)."""

    def __init__(self, finding: Finding):
        super().__init__(str(finding))
        self.finding = finding


class Sanitizers:
    """The sanitizers of one session; ``start`` before each test, ``access``
    for each ``access`` event. ``findings`` are the last test's."""

    def __init__(self, registers: Dict[int, Dict], regions: Sequence[Tuple[int, int]] = (),
                 poll_budget: int = DEFAULT_POLL_BUDGET,
                 where: Callable[[int], Optional[str]] = lambda src_ref: None,
                 latches: Iterable[str] = ()):
        self.latches = set(latches)
        self.registers = dict(registers or {})
        self.regions = [(int(b), int(n)) for b, n in regions]
        self.poll_budget = poll_budget
        self.where = where
        self._by_comp: Dict[str, List[int]] = {}
        for i, r in self.registers.items():
            self._by_comp.setdefault(r["comp"], []).append(i)
        self.start()

    def start(self, allow: Iterable[str] = ()) -> None:
        self.off: Set[str] = set(allow)
        self.findings: List[Finding] = []
        #: address -> reg-id, of the placed register blocks
        self.reg_at: Dict[int, int] = {}
        #: (lo, hi) address span of each placed register block
        self.spans: Dict[str, Tuple[int, int]] = {}
        self._blocks: Dict[str, str] = {}
        #: address -> (last value read, reads of it without progress)
        self._polls: Dict[int, Tuple[int, int]] = {}
        #: latch name -> src_refs of the puts whose token is still held
        self._held: Dict[str, List[int]] = {}
        #: reg-ids an ``unbound`` finding has named
        self._unbound_seen: Set[int] = set()
        #: interrupts raised undelivered, not yet located; numbers named
        self._irqs: List[int] = []
        self._irq_seen: Set[int] = set()

    # -- the stream ---------------------------------------------------------

    def access(self, detail: Dict[str, Any], src_ref: int = 0) -> Optional[Finding]:
        """Take one ``access`` event; the finding it makes, if any (raises
        ``SanitizerFailed`` for one that stops the test)."""
        rw, addr = detail.get("rw"), int(detail.get("addr", 0))
        data = int(detail.get("data", 0))
        rid = detail.get("reg_id")
        unbound = None
        if rid is not None and rid in self.registers:
            if addr == self.registers[rid]["offset"]:
                unbound = self._unbound(rid, rw, addr, src_ref)
            self._place(rid, addr)
        else:
            rid = self.reg_at.get(addr)
        f = unbound or self._unmapped(rid, rw, addr, src_ref) or \
            self._access_rule(rid, rw, addr, src_ref) or \
            self._rc_read(rid, detail, addr, src_ref)
        if f is not None:
            return f
        if rw == "w":
            self._polls.clear()
            return None
        last, n = self._polls.get(addr, (None, 0))
        n = n + 1 if last == data else 1
        self._polls[addr] = (data, n)
        if n > self.poll_budget and "poll-budget" not in self.off:
            what = (f"{self._name(rid, addr)} read {n} times returning "
                    f"0x{data:x} with no progress (budget {self.poll_budget})")
            raise SanitizerFailed(self._found("poll-budget", what, src_ref))
        return None

    def chan(self, detail: Dict[str, Any], src_ref: int = 0) -> None:
        """Take one ``chan`` event."""
        name, op = detail.get("channel"), detail.get("op")
        if name not in self.latches or not detail.get("ok", True):
            return
        held = self._held.setdefault(name, [])
        if op in ("put", "try_put"):
            held.append(src_ref)
        elif op in ("get", "try_get") and held:
            held.pop()

    def irq(self, irq: int, delivered: bool) -> None:
        """An interrupt was raised; one not ``delivered`` is a finding at the
        next ``site``."""
        if not delivered and "unconsumed-irq" not in self.off \
                and irq not in self._irq_seen:
            self._irq_seen.add(irq)
            self._irqs.append(irq)

    def site(self, src_ref: int = 0) -> List[Finding]:
        """The access or import in progress returned (its ``src_ref``): the
        findings of the interrupts raised during it."""
        out = []
        for irq in self._irqs:
            what = (f"interrupt {irq} raised here is never delivered: the run "
                    f"has no interrupt handler (irq_handler)")
            out.append(self._found("unconsumed-irq", what, src_ref))
        self._irqs = []
        return out

    def end(self) -> List[Finding]:
        """The end of a test that ran to its end: the findings of the
        checks made then (``inflight``)."""
        out = self.site()
        if "inflight" not in self.off:
            for name, held in sorted(self._held.items()):
                if held:
                    n = len(held)
                    what = (f"channel {name} still holds {n} token"
                            f"{'s' if n > 1 else ''} at the end of the test: "
                            f"an operation started here never completed")
                    out.append(self._found("inflight", what, held[-1]))
        self._held = {}
        return out

    def _place(self, rid: int, addr: int) -> None:
        reg = self.registers[rid]
        comp = reg["comp"]
        base = addr - reg["offset"]
        if comp in self.spans and self.reg_at.get(addr) == rid:
            return
        lo = hi = None
        for i in self._by_comp.get(comp, ()):
            r = self.registers[i]
            a = base + r["offset"]
            self.reg_at.setdefault(a, i)
            lo = a if lo is None else min(lo, a)
            hi = a + _size(r) if hi is None else max(hi, a + _size(r))
        self.spans[comp] = (lo, hi)
        self._blocks[comp] = reg["path"].rpartition(".")[0] or comp or "the root"

    def _unbound(self, rid, rw, addr, src_ref) -> Optional[Finding]:
        # once per register and test: a poll of one goes on to poll-budget
        if rid in self._unbound_seen or "unbound" in self.off:
            return None
        self._unbound_seen.add(rid)
        reg = self.registers[rid]
        block = reg["path"].rpartition(".")[0] or reg["comp"] or "the root"
        op = "read of" if rw == "r" else "write to"
        what = (f"{op} register {reg['path']} (0x{addr:x}) before its group "
                f"{block} was placed (no set_handle; its handle is 0)")
        return self._found("unbound", what, src_ref)

    def _unmapped(self, rid, rw, addr, src_ref) -> Optional[Finding]:
        if rid is not None or "unmapped" in self.off:
            return None
        if any(b <= addr < b + n for b, n in self.regions):
            return None
        if any(a <= addr < a + _size(self.registers[i])
               for a, i in self.reg_at.items()):
            return None
        block = next((c for c, (lo, hi) in self.spans.items() if lo <= addr < hi), None)
        if block is None and not self.regions:
            return None
        op = "read of" if rw == "r" else "write to"
        what = f"{op} 0x{addr:x}, which no register" + \
            (" or declared region" if self.regions else "") + " covers" + \
            (f" (inside the registers of {self._blocks[block]})" if block is not None else "")
        return self._found("unmapped", what, src_ref)

    def _access_rule(self, rid, rw, addr, src_ref) -> Optional[Finding]:
        if rid is None or "access-rule" in self.off:
            return None
        reg = self.registers[rid]
        # a register's fields, when it has them, say what it allows (a
        # READONLY register may have a W1C field)
        kinds = {f.get("kind", "RW") for f in reg["fields"]} or \
            {{"READONLY": "RO", "WRITEONLY": "WO"}.get(reg["access"], "RW")}
        if rw == "w" and kinds == {"RO"}:
            what = f"write to read-only register {reg['path']}"
        elif rw == "r" and kinds == {"WO"}:
            what = f"read of write-only register {reg['path']}"
        else:
            return None
        return self._found("access-rule", what + f" (0x{addr:x})", src_ref)

    def _rc_read(self, rid, detail, addr, src_ref) -> Optional[Finding]:
        if rid is None or not detail.get("discarded") or "rc-read" in self.off:
            return None
        reg = self.registers[rid]
        rc = [f.get("name", "?") for f in reg["fields"] if f.get("kind") == "RC"]
        if not rc:
            return None
        what = (f"read of {reg['path']} (0x{addr:x}) discards its value, and the "
                f"read cleared read-to-clear field{'s' if len(rc) > 1 else ''} "
                f"{', '.join(rc)}, and what it held is lost")
        return self._found("rc-read", what, src_ref)

    def _found(self, name: str, what: str, src_ref: int) -> Finding:
        f = Finding(name, what, src_ref, self.where(src_ref))
        self.findings.append(f)
        return f

    def _name(self, rid, addr) -> str:
        if rid is not None:
            return f"register {self.registers[rid]['path']} (0x{addr:x})"
        return f"address 0x{addr:x}"


def latches(model) -> Set[str]:
    """The channels ``model`` (a debug image) uses only with ``try_put`` and
    ``try_get``, by name: the latches ``inflight`` checks."""
    ops: Dict[str, Set[str]] = {}
    for c in getattr(model, "coros", ()) or ():
        for _pc, _sr, name, op, *_ in getattr(c, "chans", None) or ():
            ops.setdefault(name, set()).add(op)
    return {n for n, o in ops.items() if o == {"try_put", "try_get"}}


def _size(reg: Dict) -> int:
    return max(1, reg["width"] // 8)


def allows(ctx) -> Dict[str, Set[str]]:
    """``<comp type>::<function>`` -> the sanitizers its
    ``@pssunit_pkg::allow`` annotations switch off, for each component type
    of ``ctx`` and each function it declares or inherits."""
    from pssc import annotations
    from .catalog import base_chain

    out: Dict[str, Set[str]] = {}
    tm = ctx.type_map
    for cname, dt in tm.items():
        if type(dt).__name__ != "DataTypeComponent":
            continue
        for decl in [cname] + list(base_chain(ctx, dt)):
            for fn in getattr(tm.get(decl), "functions", None) or []:
                what = {str(a.get("what", "")) for a in
                        annotations.all_of(fn, "pssunit_pkg::allow")}
                if what:
                    out.setdefault(f"{cname}::{fn.name}", set()).update(what)
    unknown = sorted({w for ws in out.values() for w in ws} - set(NAMES))
    if unknown:
        raise ValueError("@pssunit_pkg::allow names no sanitizer: "
                         + ", ".join(unknown))
    return out
