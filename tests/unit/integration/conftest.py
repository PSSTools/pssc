"""Every activation table an integration test builds is also written to an
ACT section and read back (P4.10a), so the activation suites check that the
section holds every kind of tree they lower."""

import pytest

from zuspec.be.bc.lower import driver

from . import act_roundtrip, act_sweep


@pytest.fixture(autouse=True)
def _act_sweep(request, monkeypatch):
    """``PSSC_ACT_SWEEP=c``: rerun every run_model on the C engine and
    record how it compares (act_sweep.py, P4.10f1). Otherwise the files in
    ``act_sweep.GUARD`` are swept and a C run that differs fails (P4.10f4)."""
    if act_sweep.enabled():
        act_sweep.patch(monkeypatch)
        yield
    elif request.path.name in act_sweep.GUARD:
        bad = []
        act_sweep.patch(monkeypatch, bad)
        yield
        assert not bad, f"C engine != Python VM: {bad[:3]}"
    else:
        yield


@pytest.fixture(autouse=True)
def _act_roundtrip(monkeypatch):
    build = driver._activation_table

    def checked(coro, tree, types):
        table = build(coro, tree, types)
        act_roundtrip.check({0: table}, act_roundtrip.roundtrip({0: table}))
        return table
    monkeypatch.setattr(driver, "_activation_table", checked)
