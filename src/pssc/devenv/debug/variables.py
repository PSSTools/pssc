"""The variable model of a stopped run: DAP's scopes and variables.

A frame of a stopped stub has these scopes, each a list of variables:

- **Locals**: the PSS locals in scope at the frame's pc (``read_frame``); a
  struct's leaves (``cfg.src``) are grouped under the struct (``cfg``). A
  frame without debug locals shows its arguments (``arg0``...).
- **Action**: the attributes of the action the frame runs, when it runs one.
  The stub protocol has no request for them yet (``read_frame`` gives none),
  so they are read from the in-process pyvm frame's object; a stub whose
  ``read_frame`` reply carries ``attrs`` (``[Var]``) is used first.
- **Component**: the scalar fields of the frame's component (``read_comp``),
  named relative to it.
- **Registers**: every register instance under the frame's component, its
  value read through the **back door** (``backdoor_read``: no bus access, no
  watch, no read side effect) and decoded by field (``model.registers``). A
  register whose component's base is not known yet shows no value.
- **Channels**: what each channel did, from the ``chan`` events the run
  raised (:class:`ChannelLog`, the session's events sink).
- **Bus**: the last N bus accesses of the platform's bus log (a
  :class:`~pssc.testing.fake.RegisterFake`'s ``bus``), newest first.

A variable is ``{"name", "value": str, "ref": int}`` (``ref`` > 0: it has
children, fetched with :meth:`Variables.variables`) plus ``"int"`` when it
has an integer value. References last until :meth:`Variables.invalidate`
(the run resumes).
"""

from __future__ import annotations

from typing import Callable, Dict, List, Mapping, Optional

#: how many bus accesses the Bus scope shows by default
BUS_N = 16


class ChannelLog:
    """An events sink that keeps what each channel did (``chan`` events:
    ``{"channel", "op", "ok"?, "value"?}``): the values put and got, and
    the last operation. Pass it as the session's ``events`` with
    ``watch=EV_CHAN``; ``sink`` gets every event too."""

    def __init__(self, sink=None):
        self.sink = sink
        #: channel name -> {"puts", "gets", "failed", "last": detail}
        self.channels: Dict[str, dict] = {}

    def event(self, kind, coro=0, src_ref=0, detail=None):
        if kind == "chan" and isinstance(detail, dict):
            c = self.channels.setdefault(detail.get("channel", "?"), {
                "puts": 0, "gets": 0, "failed": 0, "last": None})
            if detail.get("ok", True):
                c["puts" if "put" in detail.get("op", "") else "gets"] += 1
            else:
                c["failed"] += 1
            c["last"] = dict(detail)
        if self.sink is not None:
            return self.sink.event(kind, coro=coro, src_ref=src_ref, detail=detail)

    def __getattr__(self, name):
        if self.sink is None:
            raise AttributeError(name)
        return getattr(self.sink, name)


def _var(name: str, v: Optional[int], ref: int = 0, hex_: bool = False) -> dict:
    d = {"name": name, "ref": ref}
    if v is None:
        d["value"] = ""
    else:
        d["value"], d["int"] = (hex(v) if hex_ else str(v)), v
    return d


def _signed(v: int, width: int, signed: bool) -> int:
    if signed and width and v >> (width - 1) & 1:
        return v - (1 << width)
    return v


def _within(path: str, comp: str) -> bool:
    return not comp or path == comp or path.startswith(comp + ".") \
        or path.startswith(comp + "[")


class Variables:
    """Scopes and variables of ``stub`` (``request(msg) -> reply``) while it
    is stopped. ``registers``: ``model.registers``; ``bases``: component
    path -> its registers' base (a mapping read when asked; a
    :class:`~pssc.devenv.debug.watches.WatchSet`'s or a fake's ``bases``);
    ``bus``: the platform's bus log (a list, oldest first)."""

    def __init__(self, stub, registers: Optional[Mapping[int, dict]] = None,
                 bases: Optional[Mapping[str, int]] = None,
                 bus: Optional[List[dict]] = None,
                 channels: Optional[ChannelLog] = None, bus_n: int = BUS_N):
        self.stub, self.registers = stub, registers or {}
        self.bases = bases if bases is not None else {}
        self.bus, self.channels, self.bus_n = bus, channels, bus_n
        self._refs: Dict[int, Callable[[], List[dict]]] = {}
        #: reference -> the path its register children's names are under
        self._reg_pre: Dict[int, str] = {}

    @classmethod
    def for_session(cls, stub, model, platform=None, channels=None,
                    bus_n: int = BUS_N) -> "Variables":
        """The variables of a ``pssc.testing.vm`` session's run: registers
        from ``model``, bases and bus log from ``platform`` (a fake)."""
        return cls(stub, getattr(model, "registers", None),
                   getattr(platform, "bases", None),
                   getattr(platform, "bus", None), channels, bus_n)

    # -- references -------------------------------------------------------

    def invalidate(self) -> None:
        """Forget every reference (the run moved on)."""
        self._refs.clear()
        self._reg_pre.clear()

    def _ref(self, fetch: Callable[[], List[dict]]) -> int:
        ref = len(self._refs) + 1
        self._refs[ref] = fetch
        return ref

    def variables(self, ref: int) -> List[dict]:
        """The children of reference ``ref`` (a scope or a variable)."""
        fetch = self._refs.get(ref)
        if fetch is None:
            raise KeyError(f"no variables reference {ref}")
        return fetch()

    def register_path(self, ref: int, name: str) -> str:
        """The full path of ``name`` as shown under reference ``ref``: a
        register under a Registers scope (named relative to its component)
        or a field under its register; ``name`` itself elsewhere."""
        return self._reg_pre.get(ref, "") + name

    def _req(self, cmd: str, **args) -> dict:
        rep = self.stub.request({"cmd": cmd, "args": args})
        if not rep.get("ok"):
            err = rep.get("error") or {}
            raise RuntimeError(f"{cmd}: {err.get('message', err)}")
        return rep["body"]

    # -- scopes -----------------------------------------------------------

    def scopes(self, frame: int) -> List[dict]:
        """``[{"name", "ref"}]`` for frame ``frame`` (a ``frames`` id)."""
        body = self._req("read_frame", frame=frame)
        out = [{"name": "Locals", "ref": self._ref(lambda: self._locals(body))}]
        attrs = self._attrs(frame, body)
        if attrs:
            out.append({"name": "Action", "ref": self._ref(
                lambda: [self._leaf(a) for a in attrs])})
        this = body.get("this")
        if this is not None:
            out.append({"name": "Component", "ref": self._ref(
                lambda: self._comp(this["comp"]))})
        comp = this["path"] if this is not None else ""
        if self.registers:
            ref = self._ref(lambda: self._regs(comp))
            self._reg_pre[ref] = comp + "." if comp else ""
            out.append({"name": "Registers", "ref": ref})
        if self.channels is not None:
            out.append({"name": "Channels", "ref": self._ref(self._chans)})
        if self.bus is not None:
            out.append({"name": "Bus", "ref": self._ref(self._bus)})
        return out

    def _leaf(self, v: dict, name: Optional[str] = None) -> dict:
        return _var(name or v["name"], _signed(v["value"], v.get("width", 0),
                                               v.get("signed", False)))

    def _locals(self, body: dict) -> List[dict]:
        locs = body.get("locals") or []
        if not locs:
            return [_var(f"arg{i}", a) for i, a in enumerate(body.get("args") or ())]
        return self._tree([(v["name"].split("."), v) for v in locs])

    def _tree(self, items) -> List[dict]:
        """Variables for ``[(name parts, Var)]``: a struct's leaves under
        their struct."""
        out, groups = [], {}
        for parts, v in items:
            if len(parts) == 1:
                out.append(self._leaf(v, parts[0]))
                continue
            if parts[0] not in groups:
                groups[parts[0]] = []
                out.append(parts[0])
            groups[parts[0]].append((parts[1:], v))
        return [o if isinstance(o, dict) else _var(
            o, None, self._ref((lambda g: lambda: self._tree(g))(groups[o])))
            for o in out]

    def _attrs(self, frame: int, body: dict) -> List[dict]:
        if "attrs" in body:
            return body["attrs"]
        # pyvm in-process: the frame's action object (not its component's)
        get = getattr(self.stub, "_frame_by_id", None)
        try:
            f = get(frame) if get is not None else None
        except Exception:
            f = None
        obj = getattr(f, "obj", None)
        names = getattr(obj, "field_names", None)
        if not names or obj is getattr(f, "cobj", None):
            return []
        base = getattr(f, "base", 0)
        return [{"name": n, "value": obj.values[base + i], "width": 64,
                 "signed": False}
                for i, n in enumerate(names[base:]) if base + i < len(obj.values)]

    def _comp(self, comp: int) -> List[dict]:
        body = self._req("read_comp", comp=comp)
        pre = body["path"] + "." if body["path"] else ""
        return self._tree([
            ((f["name"][len(pre):] if f["name"].startswith(pre) else f["name"]
              ).split("."), f) for f in body["fields"]])

    def _addr(self, reg: dict) -> Optional[int]:
        base = self.bases.get(reg["comp"])
        return None if base is None else base + reg["offset"]

    def peek(self, rid: int) -> Optional[int]:
        """Register ``rid``'s value through the back door, or None when its
        address is not known yet."""
        reg = self.registers[rid]
        addr = self._addr(reg)
        if addr is None:
            return None
        return self._req("backdoor_read", addr=addr, width=reg["width"])["value"]

    def _regs(self, comp: str) -> List[dict]:
        pre = comp + "." if comp else ""
        out = []
        for rid, reg in sorted(self.registers.items(),
                               key=lambda kv: kv[1]["path"]):
            if not _within(reg["path"], comp):
                continue
            v = self.peek(rid)
            d = _var(reg["path"][len(pre):], v, hex_=True)
            if v is None:
                d["value"] = "<address unknown>"
            elif reg["fields"]:
                d["ref"] = self._ref((lambda r, x: lambda: [
                    _var(f["name"], (x >> f["lsb"]) & ((1 << f["width"]) - 1))
                    for f in r["fields"]])(reg, v))
                self._reg_pre[d["ref"]] = reg["path"] + "."
            out.append(d)
        return out

    def _chans(self) -> List[dict]:
        out = []
        for name, c in sorted(self.channels.channels.items()):
            last = c["last"] or {}
            depth = c["puts"] - c["gets"]
            d = _var(name, None)
            d["value"] = f"{depth} queued, last {last.get('op', '-')}" + (
                f" {last['value']}" if "value" in last else "")
            items = [_var("queued", depth), _var("puts", c["puts"]),
                     _var("gets", c["gets"]), _var("failed", c["failed"])]
            if "value" in last:
                items.append(_var("last_value", last["value"]))
            d["ref"] = self._ref((lambda xs: lambda: xs)(items))
            out.append(d)
        return out

    def _bus(self) -> List[dict]:
        out = []
        n = len(self.bus)
        for i in range(n - 1, max(n - self.bus_n, 0) - 1, -1):
            e = self.bus[i]
            what = e.get("reg") or hex(e["addr"])
            d = _var(f"[{i}]", e["data"], hex_=True)
            d["value"] = f"{e['op']} {what} = {e['data']:#x}" + (
                f"  ({e['src']})" if e.get("src") else "")
            if e.get("fields"):
                d["ref"] = self._ref((lambda fs: lambda: [
                    _var(k, v) for k, v in fs.items()])(e["fields"]))
            out.append(d)
        return out
