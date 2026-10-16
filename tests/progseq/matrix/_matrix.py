"""The construct matrix: PSS constructs held to one trace on every op-model
target.

Each case is a small model and the memory-access trace its ``run()`` must
produce, worked out by hand from the LRM -- never taken from a backend, since
an address a backend computed proves only that the backends agree. Every case
runs on op-model-c, -cpp, -py and -sv (`..trace_harness`).

A case a target does not handle yet is a STRICT xfail naming its defect, so a
fix that makes it pass has to remove the marker too. A target that refuses a
case BY DESIGN names the refusal in ``refused``; a refusal is checked, not
skipped. The defect list these markers name is in
docs/design/construct-test-audit/00-plan.md §2 (W, E, I) and
docs/design/op-model-field-defects-plan.md (D1-D9).

``covers`` names the constructs a case exercises, by the IDs of
docs/design/construct-test-audit/taxonomy.md (``ST-MATCH``) and the
interaction patterns of 01-lrm-inventory.md §8 (``IP-1``). It is what the
coverage accounting reads; a case that covers nothing new needs a reason to
exist.

Integer expressions are evaluated at their PSS bit width (LRM 8.7), not at the
host language's: a call argument and an assignment are assignment-like
contexts (8.7.2) whose target width propagates into the expression, and
otherwise operands are propagated per Table 22.

Each ``test_m_<area>.py`` defines ``CASES`` and calls `define_tests`. Its
C, C++ and SV builds are made together, one process per core, before the
first of its cases runs (`conftest.matrix_results`).
"""
from __future__ import annotations

import re
from typing import Dict, NamedTuple, Optional, Sequence

import pytest

from pssc.driver import CompileError

from .. import trace_harness as th
from ..conftest import diagnostic_text

#: Register groups the cases share. `ga_c` puts CTL at 0x0 and STS at 0x4;
#: `gb_c` puts CTL at 0x10, so a group bound at the wrong base shows up as a
#: wrong address rather than a coincidentally right one.
REGS = """
struct ctl_s : packed_s<> { bit[1] en; bit[11] rsvd; bit[20] base_lo; }
pure component ga_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  reg_c<bit[32], READWRITE, 32> STS;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["CTL"]: return 0x0; ["STS"]: return 0x4; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
pure component gb_c : reg_group_c {
  reg_c<bit[32], READWRITE, 32> CTL;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["CTL"]: return 0x10; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
pure component gs_c : reg_group_c {
  reg_c<ctl_s, READWRITE, 32> CTL;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["CTL"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
pure component g64_c : reg_group_c {
  reg_c<bit[64], READWRITE, 64> W;
  function bit[64] get_offset_of_instance(string name) {
    match (name) { ["W"]: return 0x0; }
    return 0xFFFFFFFFFFFFFFFF;
  }
}
"""

#: The root binds one `ga_c` at its one handle; most cases need no more.
ONE = """
  ga_c a;
  solve function void initialize(addr_handle_t base) { a.set_handle(base); }
"""


class Refused(NamedTuple):
    """The model is refused as a user error whose text matches every pattern."""
    patterns: Sequence[str]


class Case(NamedTuple):
    pss: str
    want: object                         # trace lines, or Refused
    args: Sequence[int] = (0x1000,)
    mem: Optional[Dict[int, int]] = None
    xfail: Dict[str, str] = {}           # target -> defect it is waiting on
    refused: Dict[str, Refused] = {}     # target -> how it refuses the model
    covers: Sequence[str] = ()           # construct / pattern IDs


ALL = ("c", "cpp", "py", "sv")


def on(targets, why):
    return {t: why for t in targets}


#: Python builds a sub-component whose type has a constructor AT its
#: `initialize` call (`_sub_storage`), and it is `None` until then.
PY_LAZY_SUB = "py: a sub-component with a constructor is None until called"


#: In an expected trace, the model reporting a run-time error: `error()`, an
#: unmatched `match` (LRM 20.7.10 g; ruling R2). A target reports one as a
#: trace line ``error: <text>``; the text is the target's own. `error()` may
#: or may not stop the run (LRM 21.3), so nothing after it is compared, and
#: ERROR is the last line of an expectation.
ERROR = "<error>"

#: `fatal()`: like ERROR, but the run must stop there, so it is also the last
#: line of what the target printed (``fatal: <text>``).
FATAL = "<fatal>"


#: How every line of a refusal begins (plan P5.2): the place in the model,
#: as the gate (`pssc.diag.at`) and pssparser give it, or as ast2ir does.
LOCATED = re.compile(r"\S+:\d+:\d+: |line \d+: ")


def trace_matches(got, want):
    """``got`` (a target's lines) against ``want`` (an expectation, which may
    end in ERROR or FATAL)."""
    if want and want[-1] in (ERROR, FATAL):
        head, n = list(want[:-1]), len(want) - 1
        if list(got[:n]) != head or len(got) <= n:
            return False
        if want[-1] == FATAL:
            return len(got) == n + 1 and got[n].startswith("fatal: ")
        return got[n].startswith("error: ")
    return list(got) == list(want)


def expected(case, target):
    """What ``case`` must give on ``target``: trace lines, or a `Refused`."""
    return case.refused.get(target, case.want)


def case_params(cases):
    for name in sorted(cases):
        case = cases[name]
        for tgt in ALL:
            marks = []
            why = th.available(tgt)
            if why:
                marks.append(pytest.mark.skip(reason=why))
            if tgt in ("c", "cpp"):
                marks.append(pytest.mark.c_toolchain)
            if tgt == "sv":
                marks.append(pytest.mark.sim)
            if tgt in case.xfail:
                marks.append(pytest.mark.xfail(strict=True,
                                               reason=case.xfail[tgt]))
            yield pytest.param(name, tgt, marks=marks, id=f"{tgt}-{name}")


def check(cases, name, target, tmp_path, results):
    """Hold case ``name`` to its expectation on ``target``. ``results`` is
    what `conftest.matrix_results` built and ran."""
    case = cases[name]
    want = expected(case, target)
    if isinstance(want, Refused):
        with pytest.raises(CompileError) as ei:
            th.compile_model(tmp_path, REGS + case.pss, target)
        text = diagnostic_text(ei)
        for pat in want.patterns:
            assert re.search(pat, text), (pat, text)
        unlocated = [e for e in ei.value.errors or [str(ei.value)]
                     if not LOCATED.match(e)]
        assert not unlocated, unlocated
        return
    got = results[(name, target)]
    if isinstance(got, BaseException):
        raise got
    assert trace_matches(got, case.want), (got, case.want)


def define_tests(cases):
    """The test function for a module's ``CASES``."""
    @pytest.mark.parametrize("name, target", list(case_params(cases)))
    def test_construct(tmp_path, name, target, matrix_results):
        check(cases, name, target, tmp_path, matrix_results)
    return test_construct
