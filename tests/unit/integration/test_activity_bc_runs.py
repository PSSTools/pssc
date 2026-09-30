"""Activities run on bc: what a traversal RUNS, observed as a trace.

P0 of docs/design/activity-flow-resource-bc-design.md. Every test here runs
the whole pipeline (PSS -> ast2ir -> PSSToScenarioPass -> lower_module ->
the interpreter) and compares what the exec bodies print, because the
defects this guards against produced a well-formed program that ran the
WRONG action: a traversal whose target the lowering did not know ran
coroutine 0.
"""
import os
import tempfile
from typing import List

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir.core.xf import PSSToScenarioPass
from zuspec.ir.core.xf.validate import UnsupportedConstructError
from zuspec.be.bc.interp import NativeBlobBackend, run_model
from zuspec.be.bc.lower import lower_module
from zuspec.be.bc.lower.errors import LoweringError


def _lower(src: str, root: str = "pss_top::T"):
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        ctx = AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)
    assert not ctx.errors, ctx.errors
    comp, _, action = root.rpartition("::")
    module = PSSToScenarioPass(root=comp, exports=[action]).lower(ctx)
    return lower_module(module, entry_action=action, solve_unconstrained=True)


def trace(src: str, root: str = "pss_top::T", seed: int = 0) -> List[str]:
    """Run *root* and return the lines its exec bodies print."""
    lines: List[str] = []
    run_model(_lower(src, root), seed=seed, solve_backend=NativeBlobBackend(),
              out=lines.append)
    return [ln.strip() for ln in lines if ln.strip()]


# A is declared first, so it is coroutine 0: a traversal that falls back to
# the default coroutine prints "A".
_ACTIONS = """\
import std_pkg::*;
component pss_top {
    action A { exec body { message(NONE, "A"); } }
    action B { exec body { message(NONE, "B"); } }
    action C { exec body { message(NONE, "C"); } }
    %s
}
"""


def test_handle_traversal_runs_its_type():
    assert trace(_ACTIONS % "action T { B b1; activity { b1; } }") == ["B"]


def test_two_handles_same_type():
    assert trace(_ACTIONS % "action T { B b1, b2; activity { b1; b2; } }") \
        == ["B", "B"]


def test_handles_of_different_types_in_order():
    assert trace(_ACTIONS % "action T { C c1; B b1; activity { c1; b1; c1; } }") \
        == ["C", "B", "C"]


def test_qualified_type_traversal():
    assert trace(_ACTIONS % "action T { activity { do pss_top::B; do C; } }") \
        == ["B", "C"]


def test_bodiless_traversal_runs_nothing():
    """A bodiless action contributes no line, and does not shift the next
    traversal onto another action."""
    src = _ACTIONS % "action Z { } action T { activity { do Z; do B; } }"
    assert trace(src) == ["B"]


def test_unresolved_target_is_an_error():
    """A hand-built INVOKE to a name nothing lowered is refused -- it does not
    run coroutine 0."""
    from zuspec.ir.core import scenario as SC
    src = _ACTIONS % "action T { activity { do B; } }"
    fd, fname = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, src.encode())
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([fname])
        ctx = AstToIrTranslator().translate(parser.link())
    finally:
        os.unlink(fname)
    module = PSSToScenarioPass(root="pss_top", exports=["T"]).lower(ctx)
    t = module.coroutines["T"]
    invokes = [s for s in _walk(t.body) if isinstance(s, SC.ScInvoke)]
    assert invokes, "expected an INVOKE of B"
    invokes[0].target = "no_such_action"
    with pytest.raises(LoweringError, match="no_such_action"):
        lower_module(module, entry_action="T", solve_unconstrained=True)


def test_handle_of_other_component_is_rejected():
    """Actions of a sub-component are not lowered until P1: refused, with the
    target named -- not run as coroutine 0."""
    src = """\
import std_pkg::*;
component sub_c { action S { exec body { message(NONE, "S"); } } }
component pss_top {
    sub_c sub;
    action A { exec body { message(NONE, "A"); } }
    action T { sub_c::S s; activity { s; } }
}
"""
    with pytest.raises(UnsupportedConstructError, match="S"):
        _lower(src)


def _walk(stmts):
    for s in stmts or []:
        yield s
        for attr in ("body", "branches", "then_body", "else_body"):
            v = getattr(s, attr, None)
            if isinstance(v, list):
                for x in v:
                    if isinstance(x, list):
                        yield from _walk(x)
                    else:
                        yield from _walk([x])


def test_p0_trace_probe():
    """The design doc's probe: a handle, a qualified type and an atomic body.
    Before P0 this ran `A A` -- coroutine 0 twice, and the atomic body
    dropped."""
    src = _ACTIONS % ("action T { B b1; "
                      "activity { b1; do pss_top::B; atomic { do B; } } }")
    assert trace(src) == ["B", "B", "B"]


def test_single_statement_loop_bodies_run():
    src = _ACTIONS % ("action T { activity { repeat (2) do B; "
                      "if (1 > 0) do C; else do A; } }")
    assert trace(src) == ["B", "B", "C"]


# Join specs reach bc as JoinKind (F7): before, ast2ir spelled them as
# strings, which bc's dispatch on JoinKind did not recognise.
def test_parallel_join_first_runs():
    src = _ACTIONS % "action T { activity { parallel join_first(1) { do B; do C; } } }"
    assert sorted(trace(src)) == ["B", "C"]


def test_parallel_join_none_runs():
    src = _ACTIONS % ("action T { activity { parallel join_none { do B; do C; } "
                      "do A; } }")
    assert sorted(trace(src)) == ["A", "B", "C"]


@pytest.mark.parametrize("spec", ["join_select(1)", "join_branch(L1)"])
def test_parallel_join_select_and_branch_refused(spec):
    src = _ACTIONS % ("action T { activity { parallel %s { L1: do B; do C; } } }"
                      % spec)
    with pytest.raises(LoweringError, match="deferred"):
        trace(src)


# --- P0.23: `repeat ... while` and `replicate` ---------------------------------

def test_repeat_while_runs_body_first():
    """`repeat {...} while (c)` is do-while: the body runs before the first
    test, so a false condition still runs it once."""
    src = _ACTIONS % ("action T { rand bit[2] n; constraint n == 3; "
                      "activity { repeat do B; while (n < 2); } }")
    assert trace(src) == ["B"]


def test_replicate_runs_count_times():
    src = _ACTIONS % "action T { activity { replicate (3) do B; } }"
    assert trace(src) == ["B", "B", "B"]


def test_labeled_replicate_rejected():
    """`R[]` names each iteration's instances; nothing carries them before P1."""
    src = _ACTIONS % "action T { activity { replicate (2) R[]: do B; } }"
    with pytest.raises(UnsupportedConstructError, match="replicate"):
        _lower(src)


# --- P0.24: a compound action's own pre_solve / post_solve ---------------------

def test_compound_pre_post_solve_run():
    src = _ACTIONS % ("""action T {
        exec pre_solve { message(NONE, "T.pre"); }
        exec post_solve { message(NONE, "T.post"); }
        activity { do B; }
    }""")
    assert trace(src) == ["T.pre", "T.post", "B"]


def test_pre_solve_value_reaches_the_activity():
    src = _ACTIONS % ("""action T {
        int k;
        exec pre_solve { k = 1; }
        activity { if (k == 1) do B; else do C; }
    }""")
    assert trace(src) == ["B"]


# --- P0.25: `schedule` (D4) ----------------------------------------------------

def test_schedule_noninteracting_runs_all():
    src = _ACTIONS % "action T { activity { schedule { do B; do C; } } }"
    assert sorted(trace(src)) == ["B", "C"]


_FLOW = """\
import std_pkg::*;
component pss_top {
    buffer d_b { rand bit[4] v; }
    resource r_r { }
    pool d_b dp; bind dp *;
    pool [1] r_r rp; bind rp *;
    action P { output d_b o; exec body { message(NONE, "P"); } }
    action L { lock r_r r; exec body { message(NONE, "L"); } }
    action W { activity { do L; } }
    action B { exec body { message(NONE, "B"); } }
    %s
}
"""


@pytest.mark.parametrize("member", ["do P;", "do L;", "do W;"])
def test_schedule_with_interacting_member_rejected(member):
    """A member with a flow-object reference or a resource claim -- directly,
    or through a compound action -- interacts: schedule may not pick an
    order for it until P3/P4 (design D4)."""
    src = _FLOW % ("action T { activity { schedule { %s do B; } } }" % member)
    with pytest.raises(UnsupportedConstructError, match="schedule"):
        _lower(src)


def test_schedule_with_scheduling_constraint_rejected():
    src = _ACTIONS % ("action T { B b1, b2; activity { schedule { b1; b2; "
                      "constraint sequence {b1, b2}; } } }")
    with pytest.raises(UnsupportedConstructError, match="schedule"):
        _lower(src)


@pytest.mark.parametrize("kind", ["parallel", "schedule"])
def test_replicate_directly_in_parallel_rejected(kind):
    """There `replicate` expands into branches (LRM 11.5.1); a loop would be
    one branch, which changes what a join waits for."""
    src = _ACTIONS % ("action T { activity { %s { replicate (2) do B; do C; } } }"
                      % kind)
    with pytest.raises(UnsupportedConstructError, match="replicate"):
        _lower(src)
