"""`pssc.Check` -- elaborate-only, and the file-order-independence check.

The interesting test is `test_order_check_detects_dependence`: it holds the
exit status constant and shows the check still fires. That is the whole point
of comparing IR counts instead of statuses -- see `pssc/dvflow/check.py`.
"""
from pathlib import Path

import pytest

from .conftest import requires_dfm, run_task

GOOD = """
package p_pkg {
    struct s_s { bit[4] f; }
}
component c_c {
    function void go() { }
}
"""

BAD = """
component c_c {
    function void go() {
"""


@requires_dfm
def test_check_elaborates_and_generates_nothing(tmp_path):
    """It passes its inputs through and adds nothing of its own.

    That is what lets `Check` be dropped in ahead of a build task without
    rewiring anything downstream.
    """
    status, output, errors = run_task(tmp_path, "pssc.Check", pss_text=GOOD)

    assert status == 0, errors
    assert [fs.filetype for fs in output] == ["pssSource"]
    assert [fs.src for fs in output] == ["pss_src"]


@requires_dfm
def test_check_reports_a_broken_model(tmp_path):
    status, _, errors = run_task(tmp_path, "pssc.Check", pss_text=BAD)

    assert status != 0
    assert errors


@requires_dfm
def test_check_requires_pss_input(tmp_path):
    status, _, errors = run_task(tmp_path, "pssc.Check")

    assert status != 0
    assert any("no 'pssSource' inputs" in e for e in errors)


# --- order independence ----------------------------------------------------

#: Two files with nothing to resolve across them: no file order can change
#: what elaborates, so this is the check's negative control.
INDEP_A = """
package a_pkg {
    struct a_s { bit[8] mode; }
}
"""

INDEP_B = """
package b_pkg {
    struct b_s { bit[4] kind; }
}
"""


def _two_files(tmp_path, a=INDEP_A, b=INDEP_B):
    d = Path(tmp_path) / "src"
    d.mkdir(parents=True, exist_ok=True)
    (d / "a_defs.pss").write_text(a)
    (d / "b_use.pss").write_text(b)
    return [str(d / "a_defs.pss"), str(d / "b_use.pss")]


@requires_dfm
def test_order_check_passes_on_an_order_independent_model(tmp_path):
    status, _, errors = run_task(tmp_path, "pssc.Check",
                                 pss_files=_two_files(tmp_path),
                                 order_check=True)

    assert status == 0, errors


def test_order_check_detects_dependence(tmp_path, monkeypatch):
    """A front end that drops references in one order is caught -- with the
    exit status held constant at "no errors" in both runs.

    The real defect (pssparser D3) is in a compiled dependency and cannot be
    switched on and off from a test, so the order-dependence is injected here:
    the second (reversed) link drops a type. What the test pins is that the
    verdict comes from comparing the two IRs, not from either run's error
    list -- both of which stay empty. The task runs `evaluate` in a child
    process, out of the injection's reach, so this calls `evaluate` itself.
    """
    import pssc.dvflow.check as check

    real_link = check._link
    calls = {"n": 0}

    # `prelude` is the `target_cfg_pkg` source unit Check injects ahead of the
    # sources; accepted here so the double matches the real signature.
    def flaky_link(sources, prelude=()):
        context, errors = real_link(sources, prelude)
        calls["n"] += 1
        if calls["n"] == 2:            # the reversed-order link
            # Drop a user type, as an unresolved reference would.
            for name in list(context.type_m):
                if name.startswith("a_pkg::"):
                    del context.type_m[name]
        assert errors == [], "the injected failure must not add errors"
        return context, errors

    monkeypatch.setattr(check, "_link", flaky_link)

    res = check.evaluate(_two_files(tmp_path), order_check=True)
    status = res["status"]
    errors = [msg for sev, msg in res["markers"] if sev == "error"]

    assert calls["n"] == 2, "order_check must link the model twice"
    assert status != 0
    assert any("DEPENDS ON FILE ORDER" in e for e in errors), errors
    assert any("_types:" in e for e in errors), \
        "the message must carry the size of the difference"


def test_order_check_off_by_default(tmp_path, monkeypatch):
    import pssc.dvflow.check as check

    real_link = check._link
    calls = {"n": 0}

    def counting_link(sources, prelude=()):
        calls["n"] += 1
        return real_link(sources, prelude)

    monkeypatch.setattr(check, "_link", counting_link)

    res = check.evaluate(_two_files(tmp_path))

    assert res["status"] == 0, res["markers"]
    assert calls["n"] == 1


@requires_dfm
def test_check_runs_out_of_process(tmp_path, monkeypatch):
    """The check links in a child process, never in the DFM one. Breaking the
    in-process link proves the task doesn't use it."""
    import pssc.dvflow.check as check

    def _boom(*a, **kw):
        raise AssertionError("pssc.Check linked in the DFM process")
    monkeypatch.setattr(check, "_link", _boom)

    status, _, errors = run_task(tmp_path, "pssc.Check",
                                 pss_files=_two_files(tmp_path),
                                 order_check=True)

    assert status == 0, errors
    log = Path(tmp_path) / "rundir" / "task" / "pssc_check.log"
    assert log.exists()
