"""Register watches: ``ch[3].regs.csr.err`` -> the stub's access watches.

A watch names a register instance by its path from the root, as the image's
``registers`` (debug lowering, ``bc_session._registers``) spell it, and
optionally one of its fields after it (``ch[3].regs.csr.err``). It stops on
a read, a write or either (``rw``), and with ``value`` only when the
register (or the field) has that value: on a write the value written, on a
read the value read.

The stub watches addresses (``set_access_watch``), and a register's address
is its component's register base plus its offset; the base is known only at
run time, from the first access to one of that component's registers (as
:class:`pssc.testing.fake.RegisterFake` learns it). :class:`BaseLearner`
learns bases from the accesses the VM notes (``note_access``, with the
``access`` event on) and tells the set, which sends the stub the watches it
can now place. A watch whose base is not known yet is unverified, with a
message; while one is, the set keeps an inert watch (:data:`INERT`) in the
stub so its memory wrapper is in place and the access that gives the base
is matched too. Two instances at one address (``ch[0].regs`` bound to
``regs.bank[0]``) are one register: a watch on either sees both.

Conditions and hit counts are decided here, as for breakpoints
(:meth:`WatchSet.on_stop`); a field's value is matched by the engine with
the field's mask, and checked again here when the access does not cover the
whole register (a byte access).
"""

from __future__ import annotations

import dataclasses as dc
from typing import Callable, Dict, List, Mapping, Optional, Sequence, Tuple

from .breakpoints import Evaluate, parse_hit_condition

#: the stub watch id kept while a base is unknown: it matches no address
INERT = {"id": 0, "addr": -2, "size": 1, "rw": "rw"}


@dc.dataclass
class Watch:
    """One user watch. ``reg_id`` is the register instance (an index into
    ``model.components.paths``); ``lsb``/``width`` the field's bits, or the
    whole register's."""

    id: int
    path: str
    rw: str = "w"
    value: Optional[int] = None
    condition: Optional[str] = None
    hit_condition: Optional[str] = None
    reg: str = ""
    field: Optional[str] = None
    reg_id: Optional[int] = None
    lsb: int = 0
    width: int = 0
    addr: Optional[int] = None
    verified: bool = False
    message: str = ""
    hits: int = 0

    @property
    def mask(self) -> int:
        return ((1 << self.width) - 1) << self.lsb

    def to_json(self) -> dict:
        d = {"id": self.id, "path": self.path, "rw": self.rw,
             "verified": self.verified, "hits": self.hits}
        for k in ("value", "condition", "hit_condition", "field", "addr"):
            if getattr(self, k) is not None:
                d[k] = getattr(self, k)
        if self.message:
            d["message"] = self.message
        return d


def resolve(registers: Mapping[int, dict], path: str
            ) -> Tuple[Optional[int], Optional[str], str]:
    """``(reg_id, field, message)`` for ``path``: a register instance's path,
    or one with a field name after it. No reg_id = not found."""
    by_path = {r["path"]: rid for rid, r in registers.items()}
    if path in by_path:
        return by_path[path], None, ""
    reg, _, field = path.rpartition(".")
    rid = by_path.get(reg)
    if rid is None:
        return None, None, f"no register {path}"
    if not any(f["name"] == field for f in registers[rid]["fields"]):
        return None, None, f"register {reg} has no field {field!r}"
    return rid, field, ""


class WatchSet:
    """The session's register watches (DAP's data breakpoints: each set
    replaces the last), sent to the stub as access watches once their
    component's base is known."""

    def __init__(self, model):
        self.registers: Dict[int, dict] = getattr(model, "registers", None) or {}
        #: component path -> its registers' base address
        self.bases: Dict[str, int] = {}
        self.watches: List[Watch] = []
        self._next_id = 1
        self._stub = None

    def set(self, specs: Sequence[dict]) -> List[Watch]:
        """Replace the watches with ``specs`` (each ``{"path", "rw"?: "r"|"w"|
        "rw", "value"?, "condition"?, "hit_condition"?}``); returns them."""
        self.watches = [self._make(s) for s in specs]
        self._place()
        return self.watches

    def add(self, spec: dict) -> Watch:
        """Add one watch (a :meth:`set` spec), keeping the others."""
        w = self._make(spec)
        self.watches = self.watches + [w]
        self._place()
        return w

    def remove(self, wid: int) -> None:
        """Drop watch ``wid``, keeping the others (and their ids)."""
        self.watches = [w for w in self.watches if w.id != wid]

    def _make(self, s: dict) -> Watch:
        w = Watch(id=self._next_id, path=s["path"], rw=s.get("rw", "w"),
                  value=s.get("value"), condition=s.get("condition") or None,
                  hit_condition=s.get("hit_condition") or None)
        self._next_id += 1
        if w.rw not in ("r", "w", "rw"):
            w.message = f"rw {w.rw!r}: expected r, w or rw"
            return w
        if w.hit_condition:
            try:
                parse_hit_condition(w.hit_condition)
            except ValueError as e:
                w.message = str(e)
                return w
        w.reg_id, w.field, w.message = resolve(self.registers, w.path)
        if w.reg_id is None:
            return w
        reg = self.registers[w.reg_id]
        w.reg = reg["path"]
        f = next((f for f in reg["fields"] if f["name"] == w.field), None)
        w.lsb, w.width = (f["lsb"], f["width"]) if f else (0, reg["width"])
        return w

    def _place(self) -> None:
        """Give each resolved watch its address, where its base is known."""
        for w in self.watches:
            if w.reg_id is None:
                continue
            reg = self.registers[w.reg_id]
            base = self.bases.get(reg["comp"])
            w.addr = None if base is None else base + reg["offset"]
            w.verified = w.addr is not None
            w.message = "" if w.verified else (
                f"{w.reg}: the address is known after the first access to a "
                f"register of {reg['comp'] or 'the root'}")

    def learn(self, comp: str, base: int) -> None:
        """Component ``comp``'s registers are at ``base``; re-sends the
        watches to the stub ``apply`` last sent them to when one moves."""
        if self.bases.get(comp) == base:
            return
        self.bases[comp] = base
        self._place()
        if self._stub is not None and any(
                self.registers[w.reg_id]["comp"] == comp
                for w in self.watches if w.reg_id is not None):
            self.apply(self._stub)

    def access_watches(self) -> List[dict]:
        """The stub's ``set_access_watch`` argument."""
        out = []
        for w in self.watches:
            if not w.verified:
                continue
            reg = self.registers[w.reg_id]
            d = {"id": w.id, "addr": w.addr, "size": reg["width"] // 8, "rw": w.rw}
            if w.value is not None:
                d["value"], d["mask"] = (w.value << w.lsb) & w.mask, w.mask
            out.append(d)
        if any(w.reg_id is not None and not w.verified for w in self.watches):
            out.append(dict(INERT))
        return out

    def apply(self, stub) -> dict:
        """Send the access watches to ``stub`` (``request(msg) -> reply``);
        later bases are sent to it too."""
        self._stub = stub
        return stub.request({"cmd": "set_access_watch",
                             "args": {"watches": self.access_watches()}})

    def by_id(self, wid: int) -> Optional[Watch]:
        return next((w for w in self.watches if w.id == wid), None)

    def on_stop(self, hit_ids: Sequence[int], detail: dict,
                evaluate: Optional[Evaluate] = None) -> Tuple[List[int], List[dict]]:
        """A ``watch`` stop with the stub's ``hits`` and ``detail`` (``rw``,
        ``width``, ``addr``, ``data``): ``(ids that stop, what each saw)``,
        each ``{"id", "path", "rw", "data", "value"?}`` (``value``: the
        field's or register's, when the access covers it). No ids means
        continue."""
        stop, seen = [], []
        for wid in dict.fromkeys(hit_ids):
            w = self.by_id(wid)
            if w is None or w.addr is None:
                continue
            v = self._value(w, detail)
            if w.value is not None and v is not None and v != w.value:
                continue
            if w.condition:
                try:
                    if evaluate is None:
                        raise RuntimeError("no evaluator")
                    if not evaluate(w.condition):
                        continue
                except Exception as e:
                    w.message = f"condition {w.condition!r}: {e}"
                    stop.append(wid)
                    continue
            w.hits += 1
            if w.hit_condition and not parse_hit_condition(w.hit_condition)(w.hits):
                continue
            stop.append(wid)
            d = {"id": wid, "path": w.path, "rw": detail.get("rw"),
                 "data": detail.get("data")}
            if v is not None:
                d["value"] = v
            seen.append(d)
        return stop, seen

    def _value(self, w: Watch, detail: dict) -> Optional[int]:
        """The watched bits' value in the access, or None when the access
        does not hold the register (another width or address)."""
        reg = self.registers[w.reg_id]
        if detail.get("addr") != w.addr or detail.get("width") != reg["width"]:
            return None
        return (detail["data"] & w.mask) >> w.lsb


class BaseLearner:
    """A platform memory wrapper that learns register bases from the
    accesses the VM notes (``note_access``) and tells ``on_base(comp,
    base)``; the note goes on to the memory when it takes notes too (a
    :class:`~pssc.testing.fake.RegisterFake`)."""

    def __init__(self, mem, registers: Mapping[int, dict],
                 on_base: Callable[[str, int], None]):
        self._mem, self._registers, self._on_base = mem, registers, on_base
        self._note = None

    def note_access(self, reg_id, src_ref) -> None:
        self._note = reg_id
        note = getattr(self._mem, "note_access", None)
        if note is not None:
            note(reg_id, src_ref)

    def _learn(self, addr: int) -> None:
        rid, self._note = self._note, None
        reg = self._registers.get(rid) if rid is not None else None
        if reg is not None:
            self._on_base(reg["comp"], addr - reg["offset"])

    def read(self, addr, n):
        self._learn(addr)
        return self._mem.read(addr, n)

    def write(self, addr, n, data):
        self._learn(addr)
        self._mem.write(addr, n, data)

    def __getattr__(self, name):
        return getattr(self._mem, name)


def learn_bases(vm, watches: WatchSet) -> BaseLearner:
    """Put a :class:`BaseLearner` for ``watches`` on ``vm``'s memory (before
    the stub's ``set_access_watch`` wraps it); returns it."""
    vm.memory = BaseLearner(vm.memory, watches.registers, watches.learn)
    return vm.memory




def paths(registers: Mapping[int, dict], prefix: str = "") -> List[str]:
    """Every register and field path under ``prefix``, for completion."""
    out = []
    for r in registers.values():
        if not r["path"].startswith(prefix):
            continue
        out.append(r["path"])
        out += [r["path"] + "." + f["name"] for f in r["fields"]]
    return sorted(out)
