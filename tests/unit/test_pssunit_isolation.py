"""P1.13a: isolation, set-up and tear-down (T1.13).

Each test runs on a freshly built component tree; ``@pssunit_pkg::setup``
functions run on it before the test and ``@pssunit_pkg::teardown`` functions
after, whatever the test did.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.testing import catalog
from pssc.testing.cmd import collect
from pssc.testing.runner import Status, run

SUITE = """\
import std_pkg::*;
import pssunit_pkg::*;
component base_f : fixture_c {
    rt_c rt;
    int cnt;
    int ready;
    @pssunit_pkg::setup
    function void arm() { ready = 1; }
    @pssunit_pkg::teardown
    function void disarm() {
        message(LOW, "teardown base");
        rt.check_eq_int(ready, 2, "derived set-up ran after the base's");
    }
}
component s_f : base_f {
    @pssunit_pkg::setup
    function void arm2() { ready = ready + 1; }
    @pssunit_pkg::teardown
    function void disarm2() { message(LOW, "teardown derived"); }
    @pssunit_pkg::test
    function void t_a() {
        cnt = cnt + 1;
        rt.check_eq_int(cnt, 1, "fresh tree");
    }
    @pssunit_pkg::test
    function void t_b() {
        cnt = cnt + 1;
        rt.check_eq_int(cnt, 1, "fresh tree");
    }
    @pssunit_pkg::test
    function void t_fails() {
        rt.check_eq_int(cnt, 7, "wrong");
    }
    @pssunit_pkg::test
    function void t_errors() {
        error("boom");
    }
}
"""


@pytest.fixture(scope="module")
def coll(tmp_path_factory):
    src = tmp_path_factory.mktemp("iso") / "suite.pss"
    src.write_text(SUITE)
    return collect([str(src)])


@pytest.fixture(scope="module")
def results(coll):
    return {r.item.name: r for r in run(coll, seed=7)}


def test_hooks_run_base_first_and_tear_down_derived_first(coll):
    assert catalog.hooks(coll.ctx, "s_f") == (
        ["base_f::arm", "s_f::arm2"], ["s_f::disarm2", "base_f::disarm"])


def test_set_up_and_tear_down_are_not_tests(coll):
    assert sorted(it.name for it in coll.items) == [
        "t_a", "t_b", "t_errors", "t_fails"]


def test_each_test_gets_a_fresh_tree_after_set_up(results):
    # set-up made `ready` 2 for the teardown's check; `cnt` starts at 0 twice
    for name in ("t_a", "t_b"):
        r = results[name]
        assert r.status is Status.PASS, r.detail
        assert (r.checks_passed, r.checks_failed) == (2, 0)


def test_tear_down_runs_after_a_failed_check(results):
    r = results["t_fails"]
    assert r.status is Status.FAIL
    assert (r.checks_passed, r.checks_failed) == (1, 1)


def test_tear_down_runs_after_an_error_and_keeps_the_error(results):
    r = results["t_errors"]
    assert r.status is Status.ERROR
    assert "boom" in r.detail
    assert r.log.index("teardown derived") < r.log.index("teardown base")


# ---- P1.13b: property runs and regression seeds ---------------------------

PROPERTY = """\
import std_pkg::*;
import pssunit_pkg::*;
component p_c : fixture_c {
    rt_c rt;
    @pssunit_pkg::property {.runs=20}
    action prop_fail_a : test_a {
        rand bit[8] x;
        exec body { comp.rt.check_true(x < 128, "x < 128"); }
    }
    @pssunit_pkg::property {.runs=12}
    action prop_ok_a : test_a {
        rand bit[8] x;
        constraint x < 100;
        exec body { comp.rt.check_true(x < 100, "x < 100"); }
    }
    action plain_a : test_a {
        rand bit[8] x;
        exec body { comp.rt.check_true(true, "t"); }
    }
}
"""


@pytest.fixture(scope="module")
def pcoll(tmp_path_factory):
    src = tmp_path_factory.mktemp("prop") / "suite.pss"
    src.write_text(PROPERTY)
    return collect([str(src)])


def _by_name(results):
    return {r.item.name: r for r in results}


def test_property_runs_each_seed_and_sums_checks(pcoll, tmp_path):
    r = _by_name(run(pcoll, seed=7, regressions=str(tmp_path)))
    assert r["prop_ok_a"].status is Status.PASS
    assert (r["prop_ok_a"].runs, r["prop_ok_a"].checks_passed) == (12, 12)
    assert (r["plain_a"].runs, r["plain_a"].failing_seed) == (0, None)


def test_run_seeds_are_reproducible_and_start_with_the_run_seed(pcoll):
    from pssc.testing.runner import run_seeds
    item = next(i for i in pcoll.items if i.name == "prop_ok_a")
    seeds = run_seeds(7, item, 12)
    assert seeds[0] == 7 and len(set(seeds)) == 12
    assert run_seeds(7, item, 12) == seeds
    other = next(i for i in pcoll.items if i.name == "prop_fail_a")
    assert run_seeds(7, other, 12)[1:] != seeds[1:]


def test_failing_run_is_recorded_and_replays(pcoll, tmp_path):
    from pssc.testing.report import replay_command
    from pssc.testing.runner import read_regressions, regressions_file
    fail = _by_name(run(pcoll, seed=7, regressions=str(tmp_path)))["prop_fail_a"]
    assert fail.status is Status.FAIL and fail.seed == 7
    bad = fail.failing_seed
    assert bad is not None and f"(seed {bad}, shrunk: " in fail.detail
    assert replay_command(fail).endswith(f"--seed {bad}")
    path = regressions_file(str(tmp_path), fail.item)
    assert path.endswith("p_c.prop_fail_a.seeds")
    assert read_regressions(path) == [bad]

    # --seed <failing seed> fails on the first run, with no file at all
    again = _by_name(run(pcoll, seed=bad, regressions=None))["prop_fail_a"]
    assert (again.runs, again.failing_seed) == (1, bad)
    # and a recorded seed runs first whatever the run seed
    first = _by_name(run(pcoll, seed=11, regressions=str(tmp_path)))["prop_fail_a"]
    assert (first.runs, first.failing_seed) == (1, bad)
    assert read_regressions(path) == [bad]


def test_failing_run_shrinks_to_the_smallest_failing_value(pcoll):
    # P5.5b: prop_fail_a fails for x >= 128: the shrink finds 128
    fail = _by_name(run(pcoll, seed=7, regressions=None))["prop_fail_a"]
    assert fail.status is Status.FAIL and fail.shrunk == {"x": 128}
    assert "shrunk: x=128 (from " in fail.detail
    ok = _by_name(run(pcoll, seed=7, regressions=None))["prop_ok_a"]
    assert ok.shrunk is None


# ---- P1.13c: expect_error --------------------------------------------------

EXPECT = """\
import std_pkg::*;
import pssunit_pkg::*;
component e_c : fixture_c {
    rt_c rt;
    @pssunit_pkg::test
    @pssunit_pkg::expect_error {.text="overflow"}
    function void e_match() { error("counter overflow at %d", 3); }
    @pssunit_pkg::test
    @pssunit_pkg::expect_error {.text="overflow"}
    function void e_other() { error("underflow"); }
    @pssunit_pkg::test
    @pssunit_pkg::expect_error {.text="overflow"}
    function void e_passes() { rt.check_true(true, "fine"); }
    @pssunit_pkg::test
    @pssunit_pkg::expect_error {.text="overflow"}
    function void e_check() { rt.check_true(false, "wrong"); }
}
"""


@pytest.fixture(scope="module")
def eresults(tmp_path_factory):
    src = tmp_path_factory.mktemp("expect") / "suite.pss"
    src.write_text(EXPECT)
    c = collect([str(src)])
    return {r.item.name: r for r in run(c, seed=3, regressions=None)}


def test_expect_error_passes_on_a_matching_located_error(eresults):
    r = eresults["e_match"]
    assert r.status is Status.PASS, r.detail
    assert r.detail.startswith("expected error: ")
    assert "suite.pss:7:" in r.detail and "counter overflow at 3" in r.detail


def test_expect_error_fails_on_other_text_a_pass_or_a_failed_check(eresults):
    assert eresults["e_other"].status is Status.FAIL
    assert "got: " in eresults["e_other"].detail
    assert "underflow" in eresults["e_other"].detail
    assert eresults["e_passes"].status is Status.FAIL
    assert "ran to completion" in eresults["e_passes"].detail
    assert eresults["e_check"].status is Status.FAIL
    assert "expected an error containing 'overflow'" in eresults["e_check"].detail


# ---- P1.13d: `pssc test` runs through vm.Session ---------------------------

PKG = """\
import pssunit_pkg::*;
component k_c : fixture_c {
    int n;
    @pssunit_pkg::test
    function void k_ok() {
        n = 1;
        check_eq(n + 1, 2, "one more");
    }
    @pssunit_pkg::test
    function void k_bad() {
        n = 1;
        check_eq(n + 1, 3, "off by one");
        check(n == 1, "still one");
    }
}
"""


def test_cli_counts_pkg_checks_and_prints_a_failure_card(tmp_path, capsys):
    from pssc.cli import main
    (tmp_path / "suite.pss").write_text(PKG)
    assert main(["test", str(tmp_path), "--seed", "5", "--no-ctrf"]) == 1
    out = capsys.readouterr().out
    assert "1 passed" in out and "1 failed" in out, out
    assert "1 of 2 check(s) failed" in out
    # the card: located, the call as written, each operand's value
    assert "FAIL %s:%d:" % (tmp_path / "suite.pss", 12) in out, out
    assert 'check_eq(n + 1, 3, "off by one")' in out


def test_pkg_checks_count_in_the_result(tmp_path):
    (tmp_path / "suite.pss").write_text(PKG)
    rs = {r.item.name: r for r in
          run(collect([str(tmp_path / "suite.pss")]), seed=5, regressions=None)}
    assert (rs["k_ok"].status, rs["k_ok"].checks_passed) == (Status.PASS, 1)
    bad = rs["k_bad"]
    assert bad.status is Status.FAIL
    assert (bad.checks_passed, bad.checks_failed) == (1, 1)


# ---- P3.8a: @pssunit_pkg::session -----------------------------------------

SESSION = """\
import std_pkg::*;
import pssunit_pkg::*;
component boot_f : fixture_c {
    rt_c rt;
    int booted;
    int ready;
    @pssunit_pkg::session
    function void boot() { booted = booted + 1; }
}
component k_f : boot_f {
    @pssunit_pkg::setup
    function void arm() { ready = booted; }
    @pssunit_pkg::test
    function void k_a() { rt.check_eq_int(ready, 1, "booted before set-up"); }
    @pssunit_pkg::test
    function void k_b() { rt.check_eq_int(booted, 1, "booted once per tree"); }
}
"""


def test_session_runs_before_set_up_on_every_fresh_tree(tmp_path):
    (tmp_path / "suite.pss").write_text(SESSION)
    coll = collect([str(tmp_path / "suite.pss")])
    assert sorted(it.name for it in coll.items) == ["k_a", "k_b"]
    assert catalog.session_hooks(coll.ctx, "k_f") == ["boot_f::boot"]
    assert catalog.hooks(coll.ctx, "k_f") == (
        ["boot_f::boot", "k_f::arm"], [])
    rs = {r.item.name: r for r in run(coll, seed=5, regressions=None)}
    for name in ("k_a", "k_b"):
        assert rs[name].status is Status.PASS, rs[name].detail


def test_an_image_keeps_session_apart_from_set_up(tmp_path):
    """The image lists session functions on their own (a simulation runs
    them once); read back for pyvm, they lead the set-up again."""
    from pssc.devenv import build_cmd
    (tmp_path / "suite.pss").write_text(SESSION)
    coll = collect([str(tmp_path / "suite.pss")])
    model, root = build_cmd.build(coll)
    out = tmp_path / "m.zbc"
    out.write_bytes(model.to_bytes())
    image, _, meta = build_cmd.load(str(out))
    doc = meta[build_cmd.IMAGE]
    assert (doc["session"], doc["setup"]) == (["boot_f::boot"], ["k_f::arm"])
    assert image.setup == [image.entries["boot_f::boot"],
                           image.entries["k_f::arm"]]
