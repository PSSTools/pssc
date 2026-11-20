"""P1.16b: runs -- ``--platform fake``, ``--seeds N``, ``-j N`` (T1.16)."""
from __future__ import annotations

import pytest

from pssc.cli import main
from pssc.testing.cmd import collect
from pssc.testing.runner import Status, run

SUITE = """\
import addr_reg_pkg::*;
import pssunit_pkg::*;

package r_pkg {
    import addr_reg_pkg::*;
    pure component rw_c : reg_c<bit[32], READWRITE, 32> {}
    pure component ro_c : reg_c<bit[32], READONLY, 32> {}
    pure component grp_c : reg_group_c {
        rw_c st;
        ro_c id;
        pure function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["st"]: return 0x0;
                ["id"]: return 0x4;
                default: return -1;
            }
        }
        pure function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { default: return -1; }
        }
    }
}

component r_f : fixture_c {
    rt_c rt;
    r_pkg::grp_c regs;
    function void place() { regs.set_handle(make_handle_from_handle(0, 0x1000)); }
    @pssunit_pkg::test
    function void t_rw() {
        place();
        regs.st.write_val(7);
        rt.check_eq_int(regs.st.read_val(), 7, "rw keeps");
    }
    @pssunit_pkg::allow {.what = "access-rule"}
    @pssunit_pkg::test
    function void t_ro() {
        place();
        regs.id.write_val(5);
        rt.check_eq_int(regs.id.read_val(), 0, "ro ignores writes");
    }
    @pssunit_pkg::test
    function void t_bad() {
        rt.check_eq_int(1, 2, "bad");
    }
}
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "t.pss").write_text(SUITE)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _verdicts(results):
    return {r.item.qname: (r.status, r.detail, r.seed, r.failing_seed)
            for r in results}


def test_platform_fake_gives_registers_their_kinds(project, capsys):
    argv = ["test", "t.pss", "--targets", "none", "--no-ctrf", "--seed", "3",
            "-v"]
    # Plain memory: the read-only register reads back what was written.
    assert main(argv + ["-k", "t_ro"]) == 1
    assert "ro ignores writes" in capsys.readouterr().out
    assert main(argv + ["-k", "t_r*", "--platform", "fake"]) == 0
    assert "2 passed" in capsys.readouterr().out


def test_unknown_platform_is_exit_3(project, capsys):
    assert main(["test", "t.pss", "--targets", "none", "--no-ctrf",
                 "--platform", "bogus"]) == 3
    assert "expected fake or py:" in capsys.readouterr().out


def test_seeds_runs_every_test_n_times(project):
    coll = collect(["t.pss"])
    res = {r.item.name: r for r in run(coll, seed=11, seeds=4,
                                       regressions=None)}
    assert res["t_rw"].status is Status.PASS and res["t_rw"].runs == 4
    bad = res["t_bad"]
    # The first run uses the run's seed, so it is the one that failed.
    assert bad.status is Status.FAIL and bad.runs == 1
    assert bad.failing_seed == 11 and "run 1 of 4" in bad.detail
    # Only property tests keep regression seeds.
    assert not (project / ".pssunit").exists()


def test_jobs_give_the_verdicts_of_one_job(project):
    coll = collect(["t.pss"])
    one = run(coll, seed=5, regressions=None)
    many = run(coll, seed=5, regressions=None, jobs=3)
    assert [r.item.qname for r in many] == [r.item.qname for r in one]
    assert _verdicts(many) == _verdicts(one)
    # -x stops at the same failure.
    one = run(coll, seed=5, regressions=None, exitfirst=True)
    many = run(coll, seed=5, regressions=None, exitfirst=True, jobs=3)
    assert _verdicts(many) == _verdicts(one)


def test_cli_jobs_and_seeds(project, capsys):
    assert main(["test", "t.pss", "--targets", "none", "--no-ctrf",
                 "-j", "2", "--seeds", "2", "--platform", "fake"]) == 1
    out = capsys.readouterr().out
    assert "2 passed, 1 failed" in out
