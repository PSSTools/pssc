"""Evaluate a PSS expression in a stopped frame (``evaluate``, conditional
breakpoints and watches, log messages).

An expression is parsed here, its names resolved against the frame's debug
tables, and compiled to bytecode the engine runs with ``exec_snippet``
(read-only, on a fresh register file over the frame):

- a **local** in scope at the frame's pc (the coroutine's LOCAL table; a
  struct leaf is ``cfg.src``) is ``LD_LOCAL``; a frame without debug locals
  has ``arg0``... (``LD_ARG``);
- a **field of the frame's component** (``chan``, ``caps.ars``, relative to
  ``this``) is ``LD_COMP``; another instance's field (``ch[0].chan``) is
  read with ``read_comp`` and becomes a constant;
- an **action attribute** (pyvm in-process only, as in variables.py) is
  ``LD_FIELD``;
- a **register** (``regs.csr``, ``ch[1].regs.csr.ch_en``; relative to the
  frame's component first) is read through the **back door**
  (``backdoor_read``) when the expression is compiled and becomes a
  constant: no bus access, no bus-log entry, no read side effect.

Operators: ``! ~ -`` (unary), ``* / % + - << >> < <= > >= == != & ^ |
&& || ?:``, parentheses; literals ``42``, ``0x2a``, ``0b101``, ``8'h2a``,
``true``/``false``. Arithmetic is 64-bit; an operation is signed when either
operand is (a debugger's convenience: ``prio - 10`` is -5, not 2**64-5).
``&&``/``||``/``?:`` evaluate both sides (no side effects to skip).

Why not the PSS front end (spike S2's synthetic ``extend`` unit): the
wrapper must redeclare the frame's locals with their PSS types, and the
LOCAL table has widths, not type names (``cfg`` is a struct parameter);
it also needs the sources at hand. Not supported yet: enum literals
(``e_t::A``), function calls, struct values, strings.
"""

from __future__ import annotations

import re
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from .watches import resolve as resolve_register

#: (width, signed) of a compiled value
Type = Tuple[int, bool]
_M64 = (1 << 64) - 1


class EvalError(Exception):
    """An expression that cannot be compiled or run; ``col`` is 1-based
    (0: no position)."""

    def __init__(self, message: str, col: int = 0):
        super().__init__(f"{message} (col {col})" if col else message)
        self.col = col


# -- lexer / parser ----------------------------------------------------------

_TOK = re.compile(r"""\s*(?:
    (?P<num>\d*'[sS]?[hH][0-9a-fA-F_]+|\d*'[sS]?[dD][0-9_]+|\d*'[sS]?[bB][01_]+|\d*'[sS]?[oO][0-7_]+
       |0[xX][0-9a-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|\d[\d_]*)
   |(?P<id>[A-Za-z_]\w*)
   |(?P<op>&&|\|\||<<|>>|<=|>=|==|!=|[-+*/%&|^~!<>()?:.\[\]])
)""", re.X)

#: binary operators by precedence, lowest first
_BIN = [["||"], ["&&"], ["|"], ["^"], ["&"], ["==", "!="],
        ["<", "<=", ">", ">="], ["<<", ">>"], ["+", "-"], ["*", "/", "%"]]


def _number(text: str) -> int:
    t = text.replace("_", "")
    if "'" in t:
        base = t.split("'", 1)[1].lstrip("sS")
        return int(base[1:], {"h": 16, "d": 10, "b": 2, "o": 8}[base[0].lower()])
    return int(t, 0) if t[:2].lower() in ("0x", "0b", "0o") else int(t)


def tokenize(text: str) -> List[Tuple[str, str, int]]:
    """``[(kind, text, col)]``: kind ``num``, ``id``, ``op``, then ``end``."""
    out, pos = [], 0
    while pos < len(text):
        if not text[pos:].strip():
            break
        m = _TOK.match(text, pos)
        if m is None or m.end() == pos:
            col = pos + len(text[pos:]) - len(text[pos:].lstrip()) + 1
            raise EvalError(f"unexpected {text[col - 1]!r}", col)
        kind = m.lastgroup
        out.append((kind, m.group(kind), m.start(kind) + 1))
        pos = m.end()
    out.append(("end", "", len(text) + 1))
    return out


class _Parser:
    """Expression -> tuple tree: ``("num", v)``, ``("name", path, col)``,
    ``("un", op, e)``, ``("bin", op, a, b, col)``, ``("cond", c, a, b)``."""

    def __init__(self, text: str):
        self.toks, self.i = tokenize(text), 0

    def peek(self) -> Tuple[str, str, int]:
        return self.toks[self.i]

    def take(self, text: Optional[str] = None) -> Tuple[str, str, int]:
        tok = self.toks[self.i]
        if text is not None and tok[1] != text:
            raise EvalError(f"expected {text!r}, found {tok[1] or 'end'!r}", tok[2])
        self.i += 1
        return tok

    def parse(self):
        e = self.cond()
        if self.peek()[0] != "end":
            tok = self.peek()
            raise EvalError(f"unexpected {tok[1]!r}", tok[2])
        return e

    def cond(self):
        c = self.binary(0)
        if self.peek()[1] == "?":
            self.take()
            a = self.cond()
            self.take(":")
            return ("cond", c, a, self.cond())
        return c

    def binary(self, level: int):
        if level == len(_BIN):
            return self.unary()
        e = self.binary(level + 1)
        while self.peek()[0] == "op" and self.peek()[1] in _BIN[level]:
            _, op, col = self.take()
            e = ("bin", op, e, self.binary(level + 1), col)
        return e

    def unary(self):
        kind, text, col = self.peek()
        if kind == "op" and text in ("!", "~", "-", "+"):
            self.take()
            e = self.unary()
            return e if text == "+" else ("un", text, e)
        return self.primary()

    def primary(self):
        kind, text, col = self.take()
        if kind == "num":
            return ("num", _number(text))
        if kind == "id" and text in ("true", "false"):
            return ("num", int(text == "true"))
        if kind == "op" and text == "(":
            e = self.cond()
            self.take(")")
            return e
        if kind != "id":
            raise EvalError(f"unexpected {text or 'end'!r}", col)
        path = text
        while self.peek()[1] in (".", "["):
            if self.take()[1] == ".":
                k, t, c = self.take()
                if k != "id":
                    raise EvalError(f"expected a name after '.', found {t!r}", c)
                path += "." + t
            else:
                k, t, c = self.take()
                if k != "num":
                    raise EvalError("an index must be an integer literal", c)
                self.take("]")
                path += f"[{_number(t)}]"
        return ("name", path, col)


def parse(text: str):
    """The expression tree of ``text`` (raises :class:`EvalError`)."""
    return _Parser(text).parse()


# -- compiler ----------------------------------------------------------------

_ARITH = {"+": "ADD", "-": "SUB", "*": "MUL", "&": "AND", "|": "OR",
          "^": "XOR", "<<": "SHL", ">>": "SHR"}
_CMP = {"==": "CMP_EQ", "!=": "CMP_NE", "<": "CMP_LT", "<=": "CMP_LE",
        ">": "CMP_GT", ">=": "CMP_GE"}
_BOOL: Type = (1, False)


class Scope:
    """What names mean in one stopped frame: ``locals`` name -> (slot,
    type), ``args`` (count, no debug locals), ``comp`` name -> slot from
    the frame's instance, ``attrs`` name -> object slot; ``lookup(path)``
    is called for any other name and returns a constant (a register, a field
    of another instance) or None."""

    def __init__(self, locals_: Mapping[str, Tuple[int, Type]] = (),
                 nargs: int = 0, comp: Mapping[str, int] = (),
                 attrs: Mapping[str, int] = (),
                 lookup: Optional[Callable[[str], Optional[Tuple[int, Type]]]] = None):
        self.locals, self.nargs = dict(locals_), nargs
        self.comp, self.attrs, self.lookup = dict(comp), dict(attrs), lookup


class _Code:
    def __init__(self):
        self.code: List[dict] = []
        self.nregs = 0

    def emit(self, op: str, *args, imm: int = 0) -> int:
        """Emit ``op`` writing a new register (the first arg); that register."""
        rd = self.nregs
        self.nregs += 1
        self.code.append({"op": op, "args": [rd, *args], "imm": imm & _M64})
        return rd

    def const(self, v: int) -> int:
        return self.emit("CONST", imm=v)


def _sext(c: _Code, r: int, t: Type) -> int:
    """``r`` as a 64-bit value: a signed value narrower than 64 bits is
    sign-extended."""
    w, signed = t
    if not signed or w >= 64 or w <= 0:
        return r
    m = c.emit("AND", r, c.const((1 << w) - 1))
    s = c.const(1 << (w - 1))
    return c.emit("SUB", c.emit("XOR", m, s), s)


def _truth(c: _Code, r: int) -> int:
    return c.emit("CMP_NE", r, c.const(0))


def _cond_neg(c: _Code, r: int, neg: int) -> int:
    """``-r`` where ``neg`` is all ones, ``r`` where it is 0."""
    return c.emit("SUB", c.emit("XOR", r, neg), neg)


def _signed_divmod(c: _Code, a: int, b: int, is_mod: bool) -> int:
    na = c.emit("NEG", c.emit("SHR", a, c.const(63)))
    nb = c.emit("NEG", c.emit("SHR", b, c.const(63)))
    q = c.emit("MOD" if is_mod else "DIV", _cond_neg(c, a, na), _cond_neg(c, b, nb))
    return _cond_neg(c, q, na if is_mod else c.emit("XOR", na, nb))


def _compile(c: _Code, e, scope: Scope) -> Tuple[int, Type]:
    kind = e[0]
    if kind == "num":
        return c.const(e[1]), (64, True)
    if kind == "name":
        return _name(c, e[1], e[2], scope)
    if kind == "un":
        r, t = _compile(c, e[2], scope)
        r = _sext(c, r, t)
        if e[1] == "!":
            return c.emit("CMP_EQ", r, c.const(0)), _BOOL
        return c.emit("NEG" if e[1] == "-" else "NOT", r), (64, t[1] or e[1] == "-")
    if kind == "cond":
        k = c.emit("NEG", _truth(c, _sext(c, *_compile(c, e[1], scope))))
        a, ta = _compile(c, e[2], scope)
        b, tb = _compile(c, e[3], scope)
        a, b = _sext(c, a, ta), _sext(c, b, tb)
        r = c.emit("OR", c.emit("AND", a, k), c.emit("AND", b, c.emit("NOT", k)))
        return r, (max(ta[0], tb[0]), ta[1] or tb[1])
    _, op, ea, eb, col = e
    a, ta = _compile(c, ea, scope)
    b, tb = _compile(c, eb, scope)
    signed = ta[1] or tb[1]
    a, b = _sext(c, a, ta), _sext(c, b, tb)
    if op in ("&&", "||"):
        return c.emit("AND" if op == "&&" else "OR", _truth(c, a), _truth(c, b)), _BOOL
    if op in _CMP:
        if signed and op not in ("==", "!="):
            bias = c.const(1 << 63)
            a, b = c.emit("XOR", a, bias), c.emit("XOR", b, bias)
        return c.emit(_CMP[op], a, b), _BOOL
    if op in ("/", "%"):
        if signed:
            return _signed_divmod(c, a, b, op == "%"), (64, True)
        return c.emit("DIV" if op == "/" else "MOD", a, b), (64, False)
    if op == ">>" and ta[1]:
        # arithmetic shift: flip, shift, flip back for a negative value
        n = c.emit("NEG", c.emit("SHR", a, c.const(63)))
        return c.emit("XOR", c.emit("SHR", c.emit("XOR", a, n), b), n), (64, True)
    t = (64, ta[1]) if op in ("<<", ">>") else (64, signed)
    return c.emit(_ARITH[op], a, b), t


def _name(c: _Code, path: str, col: int, scope: Scope) -> Tuple[int, Type]:
    if path in scope.locals:
        slot, t = scope.locals[path]
        return c.emit("LD_LOCAL", slot), t
    m = re.fullmatch(r"arg(\d+)", path)
    if m and not scope.locals and int(m.group(1)) < scope.nargs:
        return c.emit("LD_ARG", int(m.group(1))), (64, False)
    if path in scope.attrs:
        return c.emit("LD_FIELD", scope.attrs[path]), (64, False)
    if path in scope.comp:
        return c.emit("LD_COMP", scope.comp[path]), (64, False)
    if scope.lookup is not None:
        hit = scope.lookup(path)
        if hit is not None:
            return c.const(hit[0]), hit[1]
    raise EvalError(f"unknown name {path!r}", col)


def compile_expr(text: str, scope: Scope) -> Tuple[List[dict], int, Type]:
    """``(code, nregs, type)`` of ``text`` in ``scope``: an ``exec_snippet``
    program whose ``RET`` value is the expression's 64-bit pattern."""
    c = _Code()
    r, t = _compile(c, parse(text), scope)
    r = _sext(c, r, t)
    c.code.append({"op": "RET", "args": [r]})
    return c.code, c.nregs, t


# -- a stopped frame ---------------------------------------------------------


class Evaluator:
    """Expressions in the frames of ``stub`` (``request(msg) -> reply``)
    while it is stopped. ``model``: the lowered image (``coros`` with debug
    LOCAL tables, ``components``, ``registers``); ``bases``: component path
    -> its registers' base (as in :class:`~.variables.Variables`)."""

    def __init__(self, stub, model, bases: Optional[Mapping[str, int]] = None):
        self.stub, self.model = stub, model
        self.registers = getattr(model, "registers", None) or {}
        self.bases = bases if bases is not None else {}

    @classmethod
    def for_session(cls, stub, model, platform=None) -> "Evaluator":
        return cls(stub, model, getattr(platform, "bases", None))

    def _req(self, cmd: str, **args) -> dict:
        rep = self.stub.request({"cmd": cmd, "args": args})
        if not rep.get("ok"):
            err = rep.get("error") or {}
            raise EvalError(f"{cmd}: {err.get('message', err)}")
        return rep["body"]

    def scope(self, frame: int, coro: int, pc: int) -> Scope:
        """The names of frame ``frame`` (running coroutine ``coro`` at
        ``pc``, as ``frames``/``stopped`` report them)."""
        body = self._req("read_frame", frame=frame)
        locs: Dict[str, Tuple[int, Type]] = {}
        coros = getattr(self.model, "coros", None) or []
        lvars = getattr(coros[coro], "locals", None) or () if 0 <= coro < len(coros) else ()
        for v in sorted(lvars, key=lambda v: v.pc_start):   # inner scopes last
            if v.pc_start <= pc < v.pc_end:
                locs[v.name] = (v.slot, (v.width or 64, v.signed))
        comp_path, comp = "", {}
        this = body.get("this")
        comps = getattr(self.model, "components", None)
        if this is not None and comps is not None:
            comp_path = this["path"]
            lo, pre = comps.bases[this["comp"]], comp_path + "."
            # a register's struct fields also have component slots (never
            # written: the value is the device's); the register wins
            regs = tuple(r["path"] + "." for r in self.registers.values())
            for s, n in enumerate(comps.names):
                if n.startswith(pre) and s >= lo and not n.startswith(regs):
                    comp.setdefault(n[len(pre):], s - lo)
        return Scope(locs, len(body.get("args") or ()), comp,
                     self._attrs(frame), lambda p: self._lookup(p, comp_path))

    def _attrs(self, frame: int) -> Dict[str, int]:
        get = getattr(self.stub, "_frame_by_id", None)
        try:
            f = get(frame) if get is not None else None
        except Exception:
            f = None
        obj = getattr(f, "obj", None)
        names = getattr(obj, "field_names", None)
        if not names or obj is getattr(f, "cobj", None):
            return {}
        base = getattr(f, "base", 0)
        return {n: i for i, n in enumerate(names[base:])}

    def _lookup(self, path: str, comp_path: str) -> Optional[Tuple[int, Type]]:
        """A register (back door) or another instance's field, as a constant."""
        for p in ([f"{comp_path}.{path}"] if comp_path else []) + [path]:
            rid, field, _ = resolve_register(self.registers, p)
            if rid is None:
                continue
            reg = self.registers[rid]
            base = self.bases.get(reg["comp"])
            if base is None:
                raise EvalError(f"register {reg['path']}: its address is not known yet")
            v = self._req("backdoor_read", addr=base + reg["offset"],
                          width=reg["width"])["value"]
            if field is None:
                return v, (reg["width"], False)
            f = next(f for f in reg["fields"] if f["name"] == field)
            return (v >> f["lsb"]) & ((1 << f["width"]) - 1), (f["width"], False)
        comps = getattr(self.model, "components", None)
        if comps is not None and path in comps.names:
            inst = max((i for i, b in enumerate(comps.bases)
                        if path.startswith(comps.paths[i] + ".") or not comps.paths[i]),
                       key=lambda i: len(comps.paths[i]), default=None)
            if inst is not None:
                for f in self._req("read_comp", comp=inst)["fields"]:
                    if f["name"] == path:
                        return f["value"], (64, False)
        return None

    def evaluate(self, text: str, frame: int, coro: int, pc: int) -> int:
        """The value of ``text`` in the frame (signed when its type is)."""
        code, nregs, (w, signed) = compile_expr(text, self.scope(frame, coro, pc))
        v = self._req("exec_snippet", frame=frame, code=code, nregs=nregs)["value"]
        if v is None:
            raise EvalError("the snippet returned no value")
        v &= _M64
        return v - (1 << 64) if signed and v >> 63 else v

    def at(self, frame: int, coro: int, pc: int) -> Callable[[str], int]:
        """An ``Evaluate`` (breakpoints.py) bound to one frame."""
        return lambda text: self.evaluate(text, frame, coro, pc)

    def at_stop(self, body: dict) -> Callable[[str], int]:
        """An ``Evaluate`` for the frame a ``stopped`` event's body names."""
        return self.at(body["frame"], body["coro"], body["pc"])
