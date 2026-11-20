"""Functions declared in an action, lowered by bc (P2.11c).

A call ``f()`` in an action's exec body or function runs the action's
function ``f`` -- its own or inherited, the derived action's when it
overrides -- on the action's object: ``self.x`` in it is the action's
attribute. ``ast2ir`` marks them ``action_function`` so pss_lower does not
take them for constraint blocks; bc inlines them (no called form).
"""
from __future__ import annotations

import pytest

from pssc.testing.cmd import collect
from pssc.testing.runner import Status, run

#: The template method: the base's exec body calls arrange() and act(); the
#: derived action defines act().
TEMPLATE = """\
import pssunit_pkg::*;
component pss_top : fixture_c {
    rt_c rt;
    abstract action base_a : test_a {
        rand bit[8] ch;
        int seen;
        function void arrange() { seen = 10; }
        function void act();
        function int twice(int v) { return v + v; }
        exec body {
            arrange();
            act();
            comp.rt.check_eq_int(seen, %(want)d, "act ran after arrange");
            comp.rt.check_eq_int(twice(ch), ch * 2, "value function");
        }
    }
    action d_a : base_a {
        function void act() { seen = seen + 1; }
    }
}
"""


def _run(tmp_path, text, debug=True):
    src = tmp_path / "t.pss"
    src.write_text(text)
    return {r.item.name: r for r in run(collect([str(src)]), seed=3,
                                        debug=debug, regressions=None)}


@pytest.mark.parametrize("debug", [True, False])
def test_template_method_runs(tmp_path, debug):
    (r,) = _run(tmp_path, TEMPLATE % {"want": 11}, debug=debug).values()
    assert r.status is Status.PASS, r.detail
    assert r.checks_passed == 2 and r.checks_failed == 0


def test_a_wrong_value_fails(tmp_path):
    # the functions really ran: a wrong expectation is a failed check
    (r,) = _run(tmp_path, TEMPLATE % {"want": 12}).values()
    assert r.status is Status.FAIL
    assert r.checks_passed == 1 and r.checks_failed == 1


SUPER = """\
import pssunit_pkg::*;
component pss_top : fixture_c {
    rt_c rt;
    abstract action base_a : test_a {
        int seen;
        function void arrange() { seen = 10; }
        function void check(int want) {
            comp.rt.check_eq_int(seen, want, "seen");
        }
        exec body { arrange(); check(15); }
    }
    action d_a : base_a {
        function void arrange() { super.arrange(); seen = seen + 5; }
    }
}
"""


def test_super_call_and_component_call_from_an_action_function(tmp_path):
    (r,) = _run(tmp_path, SUPER).values()
    assert r.status is Status.PASS, r.detail
    assert r.checks_passed == 1


RECURSIVE = """\
import pssunit_pkg::*;
component pss_top : fixture_c {
    rt_c rt;
    action r_a : test_a {
        function int down(int n) { if (n == 0) { return 0; } return down(n - 1); }
        exec body { comp.rt.check_eq_int(down(2), 0, "down"); }
    }
}
"""


def test_recursion_is_refused(tmp_path):
    (r,) = _run(tmp_path, RECURSIVE).values()
    assert r.status is Status.ERROR
    assert "recursion in an action's function" in r.detail


def test_runs_under_pssc_test(tmp_path, monkeypatch, capsys):
    from pssc.cli import main
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "t.pss").write_text(TEMPLATE % {"want": 11})
    monkeypatch.chdir(tmp_path)
    assert main(["test", "tests", "--no-ctrf"]) == 0
    assert "d_a" in capsys.readouterr().out
