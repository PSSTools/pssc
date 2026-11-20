"""The runner's implementations of ``pssunit_pkg``'s import functions.

Every check and every platform-double call in ``pssunit_pkg`` is an ``import
target function``, implemented here rather than in PSS. That is what lets a
test run on generated code as well as on an engine: the runner supplies the
same functions to whichever one runs it.

An implementation takes the run's :class:`RunState` and the call's arguments
as Python values (``int``, ``bool``, ``str``), already converted from the
engine's representation; the engine adapter does that conversion, not this
module. ::

    st = RunState()
    IMPORTS["pssunit_pkg::check_eq"](st, 3, 4, "")
    st.ledger.n_fail            # 1

A new import in ``pssunit_pkg.pss`` without an implementation here is caught
by :func:`missing`, which the unit tier runs against the shipped package: an
import nothing implements would otherwise read as 0 and pass silently.

The platform-double calls forward to ``RunState.double``, which has the
:class:`Double` shape; a run with no double fails such a call by name.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol

__all__ = ["IMPORTS", "PACKAGE", "Ledger", "RunState", "Double",
           "RequireFailed", "NoDouble", "implements", "declared", "missing"]

PACKAGE = "pssunit_pkg"

#: qualified import name -> implementation(state, *args)
IMPORTS: Dict[str, Callable[..., Any]] = {}


class RequireFailed(Exception):
    """A ``require`` or ``fail`` ended the test; the ledger already has it."""


class NoDouble(Exception):
    """A platform-double call in a run that has no double."""


@dataclass
class Ledger:
    """What a test asserted: the counts, and each failure's text in order."""

    n_pass: int = 0
    n_fail: int = 0
    failures: List[str] = field(default_factory=list)

    def record(self, ok: bool, what: str) -> None:
        if ok:
            self.n_pass += 1
        else:
            self.n_fail += 1
            self.failures.append(what)


class Double(Protocol):
    """What the import calls need of a platform double.

    Register paths are relative to the fixture ("dma.ch[0].regs.csr").
    ``peek_*`` and ``poke_*`` are the back door: no access, no log entry.
    """

    def base(self, name: str) -> int: ...
    def peek_reg(self, reg: str) -> int: ...
    def peek_field(self, reg: str, field: str) -> int: ...
    def poke_reg(self, reg: str, v: int) -> None: ...
    def poke_field(self, reg: str, field: str, v: int) -> None: ...
    def mem_poke(self, addr: int, data: int, size: int) -> None: ...
    def mem_peek(self, addr: int, size: int) -> int: ...
    def last_written(self, reg: str, field: str) -> Optional[int]: ...
    def accessed(self, reg: str) -> bool: ...
    def complete_after(self, reg: str, field: str, reads: int) -> None: ...
    def status_sequence(self, reg: str, field: str, v: int) -> None: ...
    def fail_on_access(self, reg: str) -> None: ...
    def poke_field_at(self, index: int, reg: str, field: str, v: int) -> None: ...


@dataclass
class RunState:
    """One test run's state, as the import calls see it."""

    ledger: Ledger = field(default_factory=Ledger)
    double: Optional[Double] = None
    #: check id -> site (the model's ``checks``, from ``check_rewrite``)
    checks: Dict[int, Any] = field(default_factory=dict)
    #: operands captured for the check about to be recorded
    operands: List[int] = field(default_factory=list)
    #: each rewritten check recorded, in order: ``(check id, ok, operands)``
    captured: List[Any] = field(default_factory=list)
    #: a mock's state (``state_get``/``state_set``), for this test
    state: Dict[str, int] = field(default_factory=dict)
    #: ``raise_irq(irq)``: the session's (``vm.Ctx.raise_irq``)
    irq: Optional[Callable[[int], None]] = None
    #: ``schedule_after(delay, name, arg)``: the session's
    #: (``vm.Ctx.schedule_after``)
    schedule: Optional[Callable[[int, str, int], None]] = None
    #: ``expect_write``/``expect_read`` not yet checked: ``(op, reg, field, v)``
    expected: List[Any] = field(default_factory=list)
    #: the bus log index ``check_seq`` matches from: after the last one
    seq_mark: int = 0
    #: check id -> why a ``check_seq`` there failed (its failure card shows it)
    reasons: Dict[int, str] = field(default_factory=dict)

    def need_double(self, call: str) -> Double:
        if self.double is None:
            raise NoDouble("%s::%s needs a platform double, and this run has none"
                           % (PACKAGE, call))
        return self.double


def implements(name: str) -> Callable:
    """Register the decorated function as the implementation of ``name``."""
    def deco(fn):
        IMPORTS["%s::%s" % (PACKAGE, name)] = fn
        return fn
    return deco


def declared(ctx) -> Dict[str, Any]:
    """The ``pssunit_pkg`` import functions a translated context declares,
    by qualified name."""
    out = {}
    for fn in getattr(ctx, "import_functions", None) or []:
        qname = (fn.metadata or {}).get("qualified_name", fn.name)
        if qname.startswith(PACKAGE + "::"):
            out[qname] = fn
    return out


def missing(ctx) -> List[str]:
    """Declared ``pssunit_pkg`` imports with no implementation, sorted."""
    return sorted(q for q in declared(ctx) if q not in IMPORTS)


def _what(what: str, default: str) -> str:
    return what or default


# ---- checks ------------------------------------------------------------------

@implements("check")
def _check(st: RunState, e: bool, what: str = "") -> None:
    st.ledger.record(bool(e), _what(what, "check"))


@implements("require")
def _require(st: RunState, e: bool, what: str = "") -> None:
    st.ledger.record(bool(e), _what(what, "require"))
    if not e:
        raise RequireFailed(_what(what, "require"))


def _compare(name: str, op: str, test: Callable[[int, int], bool]) -> None:
    def impl(st: RunState, a: int, b: int, what: str = "") -> None:
        st.ledger.record(test(a, b), _what(what, "%s: %d %s %d" % (name, a, op, b)))
    implements(name)(impl)


_compare("check_eq", "==", lambda a, b: a == b)
_compare("check_ne", "!=", lambda a, b: a != b)
_compare("check_lt", "<", lambda a, b: a < b)
_compare("check_le", "<=", lambda a, b: a <= b)
_compare("check_gt", ">", lambda a, b: a > b)
_compare("check_ge", ">=", lambda a, b: a >= b)


@implements("check_reg")
def _check_reg(st: RunState, reg: str, v: int, what: str = "") -> None:
    got = st.need_double("check_reg").peek_reg(reg)
    st.ledger.record(got == v, _what(what, "check_reg: %s == %#x (is %#x)"
                                     % (reg, v, got)))


@implements("check_field")
def _check_field(st: RunState, reg: str, fld: str, v: int, what: str = "") -> None:
    got = st.need_double("check_field").peek_field(reg, fld)
    st.ledger.record(got == v, _what(what, "check_field: %s.%s == %#x (is %#x)"
                                     % (reg, fld, v, got)))


@implements("check_written")
def _check_written(st: RunState, reg: str, fld: str, v: int, what: str = "") -> None:
    got = st.need_double("check_written").last_written(reg, fld)
    shown = "never written" if got is None else "last wrote %#x" % got
    st.ledger.record(got == v, _what(what, "check_written: %s.%s == %#x (%s)"
                                     % (reg, fld, v, shown)))


@implements("check_not_accessed")
def _check_not_accessed(st: RunState, reg: str, what: str = "") -> None:
    hit = st.need_double("check_not_accessed").accessed(reg)
    st.ledger.record(not hit, _what(what, "check_not_accessed: %s" % reg))


# ---- expected sequences (opt-in) ---------------------------------------------

@implements("expect_write")
def _expect_write(st: RunState, reg: str, fld: str, v: int) -> None:
    d = st.need_double("expect_write")
    d._field(reg, fld) if fld else d._rid(reg)        # a bad name fails here
    st.expected.append(("write", reg, fld, v))


@implements("expect_read")
def _expect_read(st: RunState, reg: str) -> None:
    st.need_double("expect_read")._rid(reg)
    st.expected.append(("read", reg, "", None))


def _shown(e) -> str:
    op, reg, fld, v = e
    if op == "read":
        return "read %s" % reg
    return "write %s%s == %#x" % (reg, "." + fld if fld else "", v)


def _matches(d, e, acc) -> bool:
    op, reg, fld, v = e
    if acc["op"] != op or acc["reg"] != reg:
        return False
    if op == "read":
        return True
    if not fld:
        return acc["data"] == v
    f = d._field(reg, fld)
    return (acc["data"] >> f["lsb"]) & ((1 << f["width"]) - 1) == v


def _seq(st: RunState, call: str, strict: bool, what: str) -> None:
    d = st.need_double(call)
    want, st.expected = st.expected, []
    log, st.seq_mark = d.bus[st.seq_mark:], len(d.bus)
    i, k, extra = 0, 0, None
    while i < len(want) and k < len(log):
        if _matches(d, want[i], log[k]):
            i += 1
        elif strict and extra is None:
            extra = k
        k += 1
    if strict and extra is None and k < len(log):
        extra = k
    seen = ", ".join("%s %s" % (a["op"], a["reg"] or "%#x" % a["addr"])
                     for a in log) or "none"
    if i < len(want):
        st.ledger.record(False, _what(what, "%s: expected access %d (%s) did "
                                      "not happen; accesses: %s"
                                      % (call, i + 1, _shown(want[i]), seen)))
    elif extra is not None:
        st.ledger.record(False, _what(what, "%s: unexpected access %d (%s %s); "
                                      "accesses: %s" % (call, extra + 1,
                                      log[extra]["op"], log[extra]["reg"], seen)))
    else:
        st.ledger.record(True, _what(what, call))


@implements("check_seq")
def _check_seq(st: RunState, what: str = "") -> None:
    _seq(st, "check_seq", False, what)


@implements("check_seq_strict")
def _check_seq_strict(st: RunState, what: str = "") -> None:
    _seq(st, "check_seq_strict", True, what)


def unchecked(st: RunState) -> None:
    """At the end of a test: expectations no ``check_seq`` checked fail."""
    if st.expected:
        st.ledger.record(False, "%d expected access(es) never checked, from "
                         "%s: call check_seq" % (len(st.expected),
                                                 _shown(st.expected[0])))
        st.expected = []


@implements("fail")
def _fail(st: RunState, msg: str) -> None:
    st.ledger.record(False, msg)
    raise RequireFailed(msg)


# ---- captured checks (check_rewrite) -----------------------------------------

@implements("check_operand")
def _check_operand(st: RunState, v: int) -> None:
    st.operands.append(v)


_SITE_CALL = {"check_reg": "check_reg", "check_field": "check_field",
              "check_written": "check_written",
              "check_not_accessed": "check_not_accessed",
              "check_seq": "check_seq", "check_seq_strict": "check_seq_strict"}


@implements("check_capture")
def _check_capture(st: RunState, cid: int, ok: bool) -> None:
    """Record rewritten check ``cid``: its operands came first. A register
    check is decided here, from the double; the others by the model."""
    ops, st.operands = st.operands, []
    site = st.checks.get(cid)
    if site is None:
        st.captured.append((cid, bool(ok), ops))
        st.ledger.record(bool(ok), "check #%d" % cid)
        return
    if site.kind in _SITE_CALL:
        n_fail = st.ledger.n_fail
        try:
            IMPORTS["%s::%s" % (PACKAGE, site.kind)](
                st, *site.strings, *ops[:1], site.what)
        finally:
            ok = st.ledger.n_fail == n_fail
            st.captured.append((cid, ok, ops))
            if not ok and site.kind.startswith("check_seq"):
                st.reasons[cid] = st.ledger.failures[-1]
        return
    st.captured.append((cid, bool(ok) and site.kind != "fail", ops))
    if site.kind == "fail":
        _fail(st, site.what)
    what = site.what or "%s: %s" % (site.kind, " ".join(
        "%s=%d" % (o.text, v) for o, v in zip(site.operands, ops)) or site.text)
    st.ledger.record(bool(ok), what)
    if site.stops and not ok:
        raise RequireFailed(what)


# ---- the platform double -----------------------------------------------------

def _forward(name: str) -> None:
    def impl(st: RunState, *args):
        return getattr(st.need_double(name), name)(*args)
    implements(name)(impl)


for _name in ("base", "poke_reg", "poke_field", "mem_poke", "mem_peek",
              "complete_after", "status_sequence", "fail_on_access",
              "poke_field_at"):
    _forward(_name)
del _name


@implements("double_call")
def _double_call(st: RunState, cid: int, v: int = 0) -> int:
    """A rewritten platform-double call (check_rewrite ``DOUBLES``): its
    register names are in site ``cid``."""
    site = st.checks[cid]
    has_v = site.kind not in ("base", "fail_on_access", "state_get",
                              "expect_read")
    from .check_rewrite import LEADS

    # a LEADS call: its leading scalar came first, as an operand
    lead, st.operands = (st.operands[:1], []) if site.kind in LEADS \
        else ([], st.operands)
    r = IMPORTS["%s::%s" % (PACKAGE, site.kind)](
        st, *lead, *site.strings, *([v] if has_v else []))
    return int(r or 0)


@implements("state_get")
def _state_get(st: RunState, key: str) -> int:
    return st.state.get(key, 0)


@implements("state_set")
def _state_set(st: RunState, key: str, v: int) -> None:
    st.state[key] = v


@implements("raise_irq")
def _raise_irq(st: RunState, irq: int = 0) -> None:
    if st.irq is None:
        raise NoDouble("%s::raise_irq needs a session to raise it in" % PACKAGE)
    st.irq(irq)


@implements("schedule_after")
def _schedule_after(st: RunState, delay: int, name: str, arg: int) -> None:
    if st.schedule is None:
        raise NoDouble("%s::schedule_after needs a session to run hooks in"
                       % PACKAGE)
    st.schedule(delay, name, arg)


@implements("double_handle")
def _double_handle(st: RunState, cid: int) -> int:
    return _double_call(st, cid)
