"""Tier 0 register fake (P1.10, design §7.1): a ``pv.Platform`` that gives
every register instance of a debug image its own storage::

    fake = RegisterFake()
    s = pv.Session(model, platform=fake)
    root = s.construct()
    s.call(root, "initialize", 0x1000_0000)
    s.call(s.instance("ch[0]"), "configure_channel", ...)
    assert fake.peek_reg("ch[0].regs.csr") & 1

Registers are the image's (``model.registers``, debug lowering). The VM
tells the fake which register each access is for just before it happens
(``note_access``, with the ``access`` event on). One access to a register
gives the base of its component, and so the address of every register of
that component: a later access to one of them without that note (a plain
``write32``) reaches the register. Two register instances at one address (a
component's group bound to another's, ``ch[0].regs`` to ``regs.bank[0]``)
are one register once both addresses are known; the one placed there first
keeps its value. Every other address is the platform's sparse memory.

- A register starts at its reset value: 0, or ``resets[path]``.
- ``READONLY``: writes leave it unchanged. ``WRITEONLY``: reads return 0.
- Per field, its ``kind`` (``bc_session._registers``: the register's access,
  or a ``@pssunit_pkg::field_access`` annotation): ``RO`` keeps its bits on a
  write, ``WO`` reads as 0, ``RC`` is cleared by a read (a write sets it as
  ``RW`` does), ``W1C`` / ``W1S`` clear / set the bits written 1 and keep the
  others. ``VOLATILE`` is stored as ``RW``; only the device changes it, so a
  test sets it with ``poke_reg`` or a hook. Aliased instances share one
  register, with the kinds of all of them.

``bus`` has every access decoded (``log`` keeps the platform's tuples): a
dict with ``op`` (``"read"``/``"write"``), ``width``, ``addr``, ``data``,
``reg`` (instance path, or None for memory), ``fields`` (name -> value, for
a register with fields) and ``src`` (``file:line`` of the access, when the
VM noted it). The back door (``peek_reg``/``poke_reg``, ``peek_field``/
``poke_field``, ``check_reg``, ``mem_peek``/``mem_poke``) has no side
effects and leaves no log entry.

A subclass gives a register behaviour with hooks (design §7.3)::

    class Dma(pv.RegisterFake):
        @pv.on_write("grp_c.csr", field="ch_en")
        def armed(self, ctx, acc):
            self.poke_field(acc.reg, "done", 1)
            ctx.raise_irq()

The register is named as an instance path (``ch[0].regs.csr``), the same
without its indices (``ch.regs.csr``: every channel), or as
``<group type>.<register>`` (the type plain or package-qualified); a name
that matches no register is an error when the session starts. An
``on_write`` hook runs after the write is stored; with ``field`` only when
the data written has that field non-zero. An ``on_read`` hook runs before
the read takes the register's value, so it can set what is read. Both get
the session's ``ctx`` (``pv.Ctx``) and an ``Access``.
"""
from __future__ import annotations

import dataclasses as _dc
import re
from typing import Callable, Dict, List, Optional, Tuple

from .vm import Platform


@_dc.dataclass
class Access:
    """One access a hook sees: ``op``, ``width``, ``addr``, ``data`` (written,
    or for a read the register's value before it), ``reg`` (instance path),
    ``group`` (its register group's path), ``index`` (the first ``[n]`` of the
    path, or None), ``fields`` (``data`` by field), ``src`` (``file:line``,
    or None), and for a write the register's value ``old`` and ``new``."""
    op: str
    width: int
    addr: int
    data: int
    reg: str
    group: str
    index: Optional[int]
    fields: Dict[str, int]
    src: Optional[str]
    old: int = 0
    new: int = 0


def _hook(op: str, reg: str, field: Optional[str]):
    def deco(fn):
        fn.__dict__.setdefault("_pv_hooks", []).append((op, reg, field))
        return fn
    return deco


def on_write(reg: str, field: Optional[str] = None):
    """Make a ``RegisterFake`` method ``fn(self, ctx, acc)`` run after each
    write to register ``reg`` (module doc); with ``field``, only a write
    whose data has that field non-zero."""
    return _hook("write", reg, field)


def on_read(reg: str):
    """Make a ``RegisterFake`` method ``fn(self, ctx, acc)`` run before each
    read of register ``reg`` (module doc)."""
    return _hook("read", reg, None)


class RegisterFake(Platform):
    """Storage per register instance; subclass it to add behaviour.

    ``resets``: register instance path -> reset value (default 0).
    ``registers``: reg-id -> register (``bc_session._registers``), set when a
    session attaches the fake to its model."""

    resets: Dict[str, int] = {}

    def __init__(self):
        super().__init__()
        self.registers: Dict[int, Dict] = {}
        #: reg-id -> current value (by the id its aliases share, ``_cell``)
        self.values: Dict[int, int] = {}
        #: reg-id -> the reg-id it is an alias of
        self.alias: Dict[int, int] = {}
        #: register instance path -> reg-id
        self.by_path: Dict[str, int] = {}
        #: address -> reg-id, learned from the accesses seen
        self.reg_at: Dict[int, int] = {}
        #: component path -> its registers' base address, learned likewise
        self.bases: Dict[str, int] = {}
        #: ``base(name)`` -> the window it gave
        self.windows: Dict[str, int] = {}
        #: reg-id (instance) -> queued (field, value) reads (``status_sequence``)
        self.seq: Dict[int, List[Tuple[Dict, int]]] = {}
        self._by_comp: Dict[str, list] = {}
        #: reg-id -> kind -> bit mask, for registers with fields (by cell)
        self.kinds: Dict[int, Dict[str, int]] = {}
        self._note: Optional[Tuple[Optional[int], int]] = None
        #: every access, decoded (module doc)
        self.bus: List[Dict] = []
        #: a ``coverage.Coverage`` told of each register access (the session
        #: sets it when its events sink is one)
        self.cov = None
        self._prov = None
        self._src_ref = 0
        self._acc: Optional[int] = None
        #: the session's ``pv.Ctx`` (set by ``pv.Session``), for hooks
        self.ctx = None
        #: (op, field or None, the reg-ids it is for, bound method); op is
        #: ``write``/``read`` (after the access), or for the library hooks
        #: ``access`` (before it) and ``value`` (maps the value read)
        self._hooks: List[Tuple[str, Optional[str], frozenset, Callable]] = []
        #: the library hooks in ``_hooks``, set by this test (``reset`` drops them)
        self._scripts: List[Tuple] = []

    def attach(self, model) -> None:
        """Take the registers of ``model`` (a debug image) and reset them."""
        regs = getattr(model, "registers", None)
        if regs is None:
            raise ValueError("a register fake needs a debug image "
                             "(bc_session.lower(..., debug=True))")
        self.registers = dict(regs)
        self._prov = getattr(model, "prov", None)
        self.by_path = {r["path"]: i for i, r in self.registers.items()}
        self._by_comp = {}
        for i, r in self.registers.items():
            self._by_comp.setdefault(r["comp"], []).append(i)
        unknown = sorted(set(self.resets) - set(self.by_path))
        if unknown:
            raise ValueError(f"{type(self).__name__}.resets names no register: "
                             + ", ".join(unknown))
        self._hooks = self._find_hooks(model)
        self.reset()

    def _find_hooks(self, model) -> List[Tuple]:
        """This class's ``on_write``/``on_read`` methods, each with the
        registers it names (an error if none)."""
        from .hooks import reg_names
        names = self._names = reg_names(self.registers,
                                        getattr(model, "components", None))
        out = []
        for attr in dir(type(self)):
            fn = getattr(type(self), attr, None)
            for op, reg, field in getattr(fn, "_pv_hooks", ()):
                rids = names.get(reg)
                if not rids:
                    raise ValueError(f"{type(self).__name__}.{attr}: "
                                     f"on_{op}({reg!r}) names no register")
                if field is not None:
                    for rid in rids:
                        self._field(self.registers[rid]["path"], field)
                out.append((op, field, frozenset(rids), getattr(self, attr)))
        return out

    def _run_hooks(self, op: str, acc: Optional["Access"]):
        """Run the ``op`` hooks of the register accessed; a ``value`` hook
        maps ``acc`` (the value read) and the last one's result is returned."""
        cell = self._cell(self._acc)
        for hop, field, rids, fn in list(self._hooks):
            if hop != op or not any(self._cell(r) == cell for r in rids):
                continue
            if field is not None:
                f = self._field(self.registers[next(iter(rids))]["path"], field)
                if not (acc.data >> f["lsb"]) & _mask(f["width"]):
                    continue
            r = fn(self.ctx, acc)
            if op == "value":
                acc = r
        return acc

    def _script(self, op: str, path: str, fn: Callable) -> Tuple:
        """Add library hook ``fn`` on register ``path`` for this test."""
        h = (op, None, frozenset([self._inst(path)]), fn)
        self._hooks.append(h)
        self._scripts.append(h)
        return h

    def _unscript(self, h: Tuple) -> None:
        self._hooks = [x for x in self._hooks if x is not h]
        self._scripts = [x for x in self._scripts if x is not h]

    def _access(self, op: str, width: int, addr: int, data: int) -> "Access":
        reg = self.registers[self._acc]
        m = re.search(r"\[(\d+)\]", reg["path"])
        return Access(op, width, addr, data, reg["path"],
                      reg["path"].rpartition(".")[0],
                      int(m.group(1)) if m else None,
                      _fields(reg, data) if reg["fields"] else {},
                      self.where(self._src_ref))

    def reset(self) -> None:
        """Every register back to its reset value; memory is kept."""
        self.values = {i: self.resets.get(r["path"], 0) & _mask(r["width"])
                       for i, r in self.registers.items()}
        self.kinds = {i: _kind_masks(r) for i, r in self.registers.items()
                      if r["fields"]}
        self.alias.clear()
        self.reg_at.clear()
        self.bases.clear()
        self.windows.clear()
        self.seq.clear()
        drop = {id(h) for h in self._scripts}
        self._hooks = [h for h in self._hooks if id(h) not in drop]
        self._scripts = []

    #: first ``base(name)`` window, and the distance between two
    WINDOW0, WINDOW = 0x1000_0000, 0x0100_0000

    def base(self, name: str) -> int:
        """The base address of the device ``name`` (the ``base`` import): a
        window of its own, the same for each call until ``reset``. Its
        registers are placed there on their first access (``_place``)."""
        if name not in self.windows:
            self.windows[name] = self.WINDOW0 + len(self.windows) * self.WINDOW
        return self.windows[name]

    def _cell(self, rid: int) -> int:
        while rid in self.alias:
            rid = self.alias[rid]
        return rid

    def note_access(self, reg_id: Optional[int], src_ref: int) -> None:
        self._note = (reg_id, src_ref)

    def _reg(self, addr: int) -> Optional[int]:
        note, self._note = self._note, None
        rid = note[0] if note is not None else None
        self._src_ref = note[1] if note is not None else 0
        if rid is not None and rid in self.registers:
            self._acc = rid     # the instance accessed, not its alias's cell
            reg = self.registers[rid]
            if self.bases.get(reg["comp"]) != addr - reg["offset"]:
                self._place(reg["comp"], addr - reg["offset"])
            if self._scripts:
                self._run_hooks("access", None)
            return self._cell(rid)
        seen = self.reg_at.get(addr)
        self._acc = seen
        return None if seen is None else self._cell(seen)

    def _place(self, comp: str, base: int) -> None:
        """Component ``comp``'s registers are at ``base``: map each one's
        address, merging it with a register already there."""
        self.bases[comp] = base
        for rid in self._by_comp.get(comp, ()):
            seen = self.reg_at.setdefault(base + self.registers[rid]["offset"], rid)
            a, b = self._cell(seen), self._cell(rid)
            if a != b:
                self.alias[b] = a
                if b in self.kinds:
                    self.kinds[a] = _merged(self.kinds.get(a, {}),
                                            self.kinds.pop(b))

    def peek(self, addr: int, n: int) -> int:
        """``n`` bytes at ``addr`` through the back door: a placed
        register's value (no read side effect, no log entry), else memory."""
        rid = self.reg_at.get(addr)
        if rid is None:
            return super().peek(addr, n)
        return self.values[self._cell(rid)] & ((1 << (8 * n)) - 1)

    def _read(self, width: int, addr: int) -> int:
        rid = self._reg(addr)
        if rid is None:
            v = super()._read(width, addr)
            self._decoded("read", width, addr, v, None)
            return v
        if self._hooks:
            self._run_hooks("read", self._access("read", width, addr,
                                                 self.values[rid]))
        reg = self.registers[rid]
        kinds = self.kinds.get(rid)
        if kinds is None:
            v = 0 if reg["access"] == "WRITEONLY" else self.values[rid]
        else:
            v = self.values[rid] & ~kinds.get("WO", 0)
            cleared = self.values[rid] & kinds.get("RC", 0)
            self.values[rid] &= ~kinds.get("RC", 0)
        if self._scripts:
            v = self._run_hooks("value", v)
        v &= _mask(width)
        self.log.append(("read", width, addr, v))
        self._decoded("read", width, addr, v, rid,
                      cleared if kinds is not None else 0)
        return v

    def _write(self, width: int, addr: int, data: int) -> None:
        rid = self._reg(addr)
        if rid is None:
            super()._write(width, addr, data)
            self._decoded("write", width, addr, self.log[-1][3], None)
            return
        data &= _mask(width)
        reg = self.registers[rid]
        kinds = self.kinds.get(rid)
        old = self.values[rid]
        if kinds is not None:
            self.values[rid] = _written(self.values[rid], data, kinds) \
                & _mask(reg["width"])
        elif reg["access"] != "READONLY":
            self.values[rid] = data & _mask(reg["width"])
        self.log.append(("write", width, addr, data))
        acc = self._access("write", width, addr, data) if self._hooks else None
        self._decoded("write", width, addr, data, rid,
                      old & data & kinds.get("W1C", 0) if kinds is not None else 0)
        if acc is not None:
            acc.old, acc.new = old, self.values[rid]
            self._run_hooks("write", acc)

    def _decoded(self, op: str, width: int, addr: int, data: int,
                 rid: Optional[int], cleared: int = 0) -> None:
        """Log an access; ``cleared``: the bits its RC/W1C side effect
        cleared (for ``cov``)."""
        e = {"op": op, "width": width, "addr": addr, "data": data,
             "reg": None, "src": self.where(self._src_ref)}
        if rid is not None:
            reg = self.registers[self._acc]
            e["reg"] = reg["path"]
            if reg["fields"]:
                e["fields"] = _fields(reg, data)
            if self.cov is not None:
                self.cov.register_access(reg, op, data, cleared)
        self._src_ref = 0
        self.bus.append(e)

    def where(self, src_ref: int) -> Optional[str]:
        """``file:line:col`` of provenance entry ``src_ref`` (``file:line``
        when the column is unknown; None for none)."""
        entries = getattr(self._prov, "entries", None) or ()
        if not 0 < src_ref < len(entries) or not entries[src_ref].file:
            return None
        p = entries[src_ref]
        col = getattr(p, "col", 0)
        return f"{p.file}:{p.line}:{col}" if col else f"{p.file}:{p.line}"

    # -- by path, no side effects and no log entry -------------------------

    def _rid(self, path: str) -> int:
        rid = self.by_path.get(path)
        if rid is None:
            raise KeyError(f"no register {path!r}")
        return self._cell(rid)

    def peek_reg(self, path: str) -> int:
        """The value of register ``path`` (instance path, e.g.
        ``ch[0].regs.csr``)."""
        return self.values[self._rid(path)]

    def decoded(self, path: str) -> Dict:
        """Register ``path`` now, as a ``bus`` entry decodes one: ``reg``,
        ``width``, ``data`` and ``fields`` (for a register with fields)."""
        rid = self._rid(path)
        reg = self.registers[self.by_path[path]]
        e = {"reg": path, "width": reg["width"], "data": self.values[rid]}
        if reg["fields"]:
            e["fields"] = _fields(reg, self.values[rid])
        return e

    def poke_reg(self, path: str, value: int) -> None:
        """Set register ``path``, whatever its access."""
        rid = self._rid(path)
        self.values[rid] = value & _mask(self.registers[rid]["width"])

    def _field(self, path: str, name: str) -> Dict:
        self._rid(path)
        for f in self.registers[self.by_path[path]]["fields"]:
            if f["name"] == name:
                return f
        raise KeyError(f"register {path!r} has no field {name!r}")

    def peek_field(self, path: str, field: str) -> int:
        """The value of ``field`` of register ``path``."""
        f = self._field(path, field)
        return (self.peek_reg(path) >> f["lsb"]) & _mask(f["width"])

    def poke_field(self, path: str, field: str, value: int) -> None:
        """Set ``field`` of register ``path``, whatever its kind; the other
        fields are kept."""
        f = self._field(path, field)
        m = _mask(f["width"]) << f["lsb"]
        self.poke_reg(path, (self.peek_reg(path) & ~m)
                      | ((value << f["lsb"]) & m))

    def poke_field_at(self, index: int, reg: str, field: str,
                      value: int) -> None:
        """`poke_field` of the register ``reg`` names (as a hook names one:
        ``hooks.reg_names``) whose first ``[n]`` is ``index``; of the only
        one, when it has no index and ``index`` is 0. Paths that alias one
        register (``ch[2].regs.csr`` and ``regs.bank[2].csr``) are one, and
        a register the driver has placed is chosen over one it has not."""
        rids = getattr(self, "_names", {}).get(reg) or ()
        paths = sorted(self.registers[r]["path"] for r in rids)
        at = [p for p in paths
              if (m := re.search(r"\[(\d+)\]", p)) and int(m.group(1)) == index]
        if not at and index == 0 and len(paths) == 1 and "[" not in paths[0]:
            at = paths
        placed = [p for p in at
                  if self.registers[self.by_path[p]]["comp"] in self.bases]
        at = placed or at
        if len({self._rid(p) for p in at}) != 1:
            raise KeyError(f"no register {reg!r} at index {index}")
        self.poke_field(at[0], field, value)

    def check_reg(self, path: str, value: Optional[int] = None,
                  **fields: int) -> None:
        """Assert register ``path`` holds ``value`` and/or each field the
        given value; the message names every mismatch."""
        bad = []
        if value is not None and self.peek_reg(path) != value:
            bad.append(f"value: expected {value:#x}, got {self.peek_reg(path):#x}")
        for name, want in fields.items():
            got = self.peek_field(path, name)
            if got != want:
                bad.append(f"{name}: expected {want:#x}, got {got:#x}")
        if bad:
            raise AssertionError(f"register {path}: " + "; ".join(bad))

    # -- scripted behaviour (the pssunit_pkg double imports) ----------------
    # Library hooks: each call adds a hook on the register for this test,
    # run by the same dispatch as the mocks' hooks (``_run_hooks``).

    def status_sequence(self, path: str, field: str, value: int) -> None:
        """Queue ``value`` as the next value ``field`` reads as; when the
        queue is empty the field reads as stored."""
        f = self._field(path, field)
        inst = self._inst(path)
        if inst not in self.seq:
            q = self.seq[inst] = []

            def next_read(ctx, v):
                if not q:
                    return v
                f, want = q.pop(0)
                m = _mask(f["width"]) << f["lsb"]
                return (v & ~m) | ((want << f["lsb"]) & m)
            self._script("value", path, next_read)
        self.seq[inst].append((f, value))

    def complete_after(self, path: str, field: str, reads: int) -> None:
        """Clear ``field`` once register ``path`` has been read ``reads``
        times."""
        f = self._field(path, field)
        m = _mask(f["width"]) << f["lsb"]
        left = [reads]

        def count(ctx, v):
            left[0] -= 1
            if left[0] == 0:
                self.values[self._cell(self._acc)] &= ~m
                self._unscript(h)
            return v
        h = self._script("value", path, count)

    def fail_on_access(self, path: str) -> None:
        """Any access to register ``path`` is an error."""
        def fail(ctx, acc):
            raise AssertionError(f"register {self.registers[self._acc]['path']}"
                                 " accessed (fail_on_access)")
        self._script("access", path, fail)

    def last_written(self, path: str, field: str) -> Optional[int]:
        """``field`` of the last value written to ``path`` (None: never)."""
        f = self._field(path, field)
        for e in reversed(self.bus):
            if e["op"] == "write" and e["reg"] == path:
                return (e["data"] >> f["lsb"]) & _mask(f["width"])
        return None

    def accessed(self, path: str) -> bool:
        """Whether the model read or wrote register ``path``."""
        self._rid(path)
        return any(e["reg"] == path for e in self.bus)

    def _inst(self, path: str) -> int:
        """The reg-id of instance ``path``. Scripts are kept by instance, not
        by cell: aliases are learned only when the model accesses them."""
        self._rid(path)
        return self.by_path[path]

    def mem_peek(self, addr: int, width: int = 32) -> int:
        """``width`` bits of sparse memory at ``addr`` (little-endian)."""
        return sum(self.mem.get((addr + i) & _mask(64), 0) << (8 * i)
                   for i in range(width // 8))

    def mem_poke(self, addr: int, value: int, width: int = 32) -> None:
        """Set ``width`` bits of sparse memory at ``addr`` (little-endian)."""
        for i in range(width // 8):
            self.mem[(addr + i) & _mask(64)] = (value >> (8 * i)) & 0xFF


def _mask(width: int) -> int:
    return (1 << width) - 1


def _fields(reg: Dict, data: int) -> Dict[str, int]:
    return {f["name"]: (data >> f["lsb"]) & _mask(f["width"])
            for f in reg["fields"]}


def _kind_masks(reg: Dict) -> Dict[str, int]:
    """kind -> the bits of ``reg``'s fields of that kind."""
    out: Dict[str, int] = {}
    for f in reg["fields"]:
        k = f.get("kind", "RW")
        out[k] = out.get(k, 0) | (_mask(f["width"]) << f["lsb"])
    return out


def _merged(a: Dict[str, int], b: Dict[str, int]) -> Dict[str, int]:
    """The kinds of two aliased instances: a bit takes a kind other than
    ``RW`` from either."""
    out = {k: a.get(k, 0) | b.get(k, 0) for k in set(a) | set(b) if k != "RW"}
    other = 0
    for m in out.values():
        other |= m
    rw = (a.get("RW", 0) | b.get("RW", 0)) & ~other
    if rw:
        out["RW"] = rw
    return out


def _written(old: int, data: int, kinds: Dict[str, int]) -> int:
    """A register's value after ``data`` is written to it (`_kind_masks`).
    Bits of no field (none, in a packed struct) take ``data``."""
    keep = kinds.get("RO", 0)
    w1c, w1s = kinds.get("W1C", 0), kinds.get("W1S", 0)
    new = data & ~(keep | w1c | w1s)
    return (new | (old & keep) | (old & w1c & ~data)
            | ((old | data) & w1s))
