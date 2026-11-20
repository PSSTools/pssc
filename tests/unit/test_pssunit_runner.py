"""Runner behaviour on the ZBC path.

These cover the parts of the migration that are the runner's own judgement --
how it classifies a failure, how it scopes a lowering error, and what it puts on
the object -- as opposed to whether the backend works, which the end-to-end
tests cover.
"""

import pytest

from pssc.testing.runner import (
    Kind, Result, Status, _action_fields, _build_image, _classify, run_one,
    _Image)


class _Item:
    """Minimal stand-in for TestItem (the runner only reads three attributes)."""

    __test__ = False

    def __init__(self, qname="c::a", name="a", comp="c"):
        self.qname, self.name, self.comp = qname, name, comp
        self.loc = None

    def __str__(self):
        return self.qname


# --- classification ---------------------------------------------------------

def test_an_unsatisfiable_model_is_a_failure_not_an_error():
    """FAIL means the model said something untrue; ERROR means the test never
    got far enough to say anything. Different people read the two."""
    sc = pytest.importorskip("zuspec.be.bc.interp.solve_cache")
    assert _classify(sc.SolveUnsatError("unsat")) == (Status.FAIL, Kind.UNSAT)


def test_a_solver_limitation_is_an_error_not_a_failure():
    """A solve that exhausts its budget is reported separately from "no
    solution exists". Collapsing them would blame the design for the tool's
    gap and send the reader hunting a constraint bug that is not there.
    """
    sc = pytest.importorskip("zuspec.be.bc.interp.solve_cache")
    assert _classify(sc.SolveBudgetError("budget")) == (
        Status.ERROR, Kind.UNSUPPORTED)


def test_an_unsupported_construct_is_an_error():
    v = pytest.importorskip("zuspec.ir.core.xf.validate")
    status, kind = _classify(v.UnsupportedConstructError("nope"))
    assert (status, kind) == (Status.ERROR, Kind.UNSUPPORTED)


def test_anything_else_is_an_exception_error():
    assert _classify(ValueError("boom")) == (Status.ERROR, Kind.EXCEPTION)


# --- the object the test runs against ---------------------------------------

def test_the_object_carries_the_action_fields():
    """SOLVE writes results back **by name** and skips a name the object does
    not have -- so an object built without them would solve and then silently
    discard every value produced."""

    class _F:
        def __init__(self, name):
            self.name = name

    class _DT:
        fields = [_F("a"), _F("b")]

    class _Ctx:
        type_map = {"c::a": _DT()}

    assert _action_fields(_Ctx(), "c::a") == ["a", "b"]


def test_an_unknown_action_yields_no_fields_rather_than_raising():
    class _Ctx:
        type_map = {}

    assert _action_fields(_Ctx(), "c::missing") == []


# --- lowering failures are scoped to their component ------------------------

def test_a_lowering_failure_is_reported_per_test_not_raised():
    """One unsupported construct must not abort the run and hide the tests
    that would have passed."""
    image = _Image(error=ValueError("cannot lower"))
    r = run_one(image, None, _Item(), seed=1)
    assert r.status is Status.ERROR
    assert "cannot lower" in r.detail
    assert r.trace                      # the traceback is kept for debugging


def test_a_test_with_no_matching_entry_reports_what_was_available():
    """A silent skip here would read as a pass. The message names the entries
    that *did* lower, which is what tells you whether the name is wrong or the
    lowering dropped it."""
    image = _Image(model=object(), entries={"other_a": 0})
    r = run_one(image, None, _Item(name="missing_a"), seed=1)
    assert r.status is Status.ERROR
    assert "other_a" in r.detail


def test_the_seed_is_recorded_on_a_failure_result():
    """The replay line depends on it."""
    r = run_one(_Image(error=ValueError("x")), None, _Item(), seed=4242)
    assert r.seed == 4242


# --- Result invariants the reports rely on ----------------------------------

@pytest.mark.parametrize("status,ok", [
    (Status.PASS, True), (Status.SKIP, True),
    (Status.FAIL, False), (Status.ERROR, False),
])
def test_ok_covers_pass_and_skip_only(status, ok):
    assert Result(item=_Item(), status=status).ok is ok
