"""P5.1a: ``pssc test --platform replay:<trace>`` runs a test against a run
``--trace`` recorded: reads and user imports are answered from the trace,
and the first access or import that is not the recorded one fails the test.
"""
from __future__ import annotations

import sys

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.cli import main
from pssc.testing import cmd
from pssc.testing.replay import ReplayPlatform

SUITE = """\
import addr_reg_pkg::*;
import pssunit_pkg::*;
import function bit[32] plat_id();

package r_pkg {
    import addr_reg_pkg::*;
    pure component rw_c : reg_c<bit[32], READWRITE, 32> {}
    pure component grp_c : reg_group_c {
        rw_c st;
        rw_c id;
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
        regs.st.write_val(plat_id());
        rt.check_eq_int(regs.st.read_val(), 0x5A, "rw keeps");
    }
}
"""

# The recorded run: plat_id() is 0x5A and the register keeps what is written.
BOARD = """\
import pssc.testing.vm as pv

@pv.implements("plat_id")
def plat_id(ctx):
    return 0x5A
"""

TRACE = ".pssunit/traces/r_f.t_rw.ndjson"


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "t.pss").write_text(SUITE)
    (tmp_path / "p51a_board.py").write_text(BOARD)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(cmd, "LAST_FAILED", str(tmp_path / "lastfailed"))
    assert main(["test", "t.pss", "--no-ctrf", "--trace", "all",
                 "--platform", "py:p51a_board"]) == 0
    assert (tmp_path / TRACE).is_file()
    return tmp_path


def _replay(capsys, src="t.pss", trace=".pssunit/traces"):
    capsys.readouterr()
    rc = main(["test", src, "--no-ctrf", "--platform", "replay:" + trace])
    return rc, capsys.readouterr().out


def test_trace_steps_are_accesses_and_user_imports(project):
    p = ReplayPlatform(TRACE, "r_f::t_rw")
    assert [(s["kind"], s.get("rw"), s.get("addr"), s.get("name"))
            for s in p.steps] == [("import", None, None, "plat_id"),
                                  ("access", "w", 0x1000, None),
                                  ("access", "r", 0x1000, None)]


def test_replay_of_the_same_model_passes(project, capsys):
    # no doubles: plat_id() and the read come from the trace
    rc, out = _replay(capsys)
    assert rc == 0, out
    rc, out = _replay(capsys, trace=TRACE)
    assert rc == 0, out


def test_a_moved_register_is_the_first_divergent_access(project, capsys):
    (project / "t2.pss").write_text(
        SUITE.replace('["st"]: return 0x0;', '["st"]: return 0x4;'))
    rc, out = _replay(capsys, "t2.pss")
    assert rc == 1, out
    assert ("t2.pss:31:9: first divergent access (step 2 of 3 recorded): "
            "expected write32 0x1000 = 0x5a (t.pss:31), got write32 0x1004 "
            "= 0x5a") in out


def test_a_run_that_stops_early_diverges(project, capsys):
    (project / "t3.pss").write_text(SUITE.replace(
        'rt.check_eq_int(regs.st.read_val(), 0x5A, "rw keeps");', ''))
    rc, out = _replay(capsys, "t3.pss")
    assert rc == 1, out
    assert ("first divergent access (step 3 of 3 recorded): expected "
            "read32 0x1000 (t.pss:32), got the end of the test") in out


def test_a_missing_trace(project, capsys):
    rc = main(["test", "t.pss", "--no-ctrf", "--platform", "replay:nope"])
    assert rc == 3
    (project / "other").mkdir()
    rc, out = _replay(capsys, trace="other")
    assert rc != 0 and "no recorded trace for r_f::t_rw" in out


def _header():
    import json
    with open(TRACE) as f:
        return json.loads(f.readline())


def test_a_replay_defaults_to_the_recorded_seed(project):
    from pssc.testing import vm
    seed = _header()["seed"]
    assert isinstance(seed, int)
    assert vm.load_platform("replay:" + TRACE).recorded_seed() == seed
    h = vm.load_platform("replay:.pssunit/traces")
    assert h.recorded_seed() == seed
    assert h.recorded_seed("r_f::t_rw") == seed
    assert vm.load_platform("fake").recorded_seed() is None


def test_a_replay_runs_under_the_debugger(project):
    # `pssc dbg --platform replay:<dir>`: the session's test picks its trace
    from pssc.devenv.debug import sessions
    reg = sessions.Registry()
    try:
        live = reg.start(["t.pss"], "r_f::t_rw",
                         platform="replay:.pssunit/traces")
        assert live.seed == _header()["seed"]
        assert live.debug.last["reason"] == "entry"
        end = live.debug.cont()
        assert end["event"] == "exited", end
        assert live.error is None and live.checks[1] == 0, live.error
        (project / "t2.pss").write_text(
            SUITE.replace('["st"]: return 0x0;', '["st"]: return 0x4;'))
        live = reg.start(["t2.pss"], "r_f::t_rw",
                         platform="replay:.pssunit/traces")
        end = live.debug.cont()
        assert "first divergent access (step 2 of 3 recorded)" in str(
            (end, live.error)), (end, live.error)
    finally:
        reg.end_all()


# the fixture of test_pssunit_irq.py: kick() raises irq 2, whose handler
# wakes t_wait
IRQ = """\
import sync_pkg::*;
import pssunit_pkg::*;

import function void kick();

component i_f : fixture_c {
    rt_c rt;
    channel_c<bit, 1> wake;
    bit[32] seen;
    function void notify_irq() {
        seen = seen + 1;
        wake.try_put(1);
    }
    @pssunit_pkg::test
    function void t_wait() {
        kick();
        wake.get();
        rt.check_eq_int(seen, 1, "handler ran once");
    }
}
"""


def test_a_recorded_interrupt_is_raised_again(tmp_path, monkeypatch, capsys):
    # the recorded double of kick() raised irq 2; the replay has no double,
    # so the handler that wakes t_wait runs only if the replay raises it
    (tmp_path / "irq.pss").write_text(IRQ)
    (tmp_path / "p51b_irq.py").write_text(
        "import pssc.testing.vm as pv\n\n"
        "@pv.implements(\"kick\")\ndef kick(ctx):\n    ctx.raise_irq(2)\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(cmd, "LAST_FAILED", str(tmp_path / "lastfailed"))
    assert main(["test", "irq.pss", "--no-ctrf", "--trace", "all",
                 "--platform", "py:p51b_irq"]) == 0
    p = ReplayPlatform(".pssunit/traces/i_f.t_wait.ndjson", "i_f::t_wait")
    assert [(s["name"], s.get("irqs")) for s in p.steps] == [("kick", [2])]
    rc, out = _replay(capsys, src="irq.pss")
    assert rc == 0, out
    assert "t_wait" in out and "1/1 checks" in out


def _to_binary(project, keep_lines=True):
    """The recorded trace in the binary form, as the C engine would write it
    (``keep_lines=False``: its events have no ``file``/``line``)."""
    import json

    from zuspec.be.bc.trace import binary
    src = project / TRACE
    text = src.read_text()
    if not keep_lines:
        rows = [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        for r in rows[1:]:
            r.pop("file", None)
            r.pop("line", None)
        text = "\n".join(json.dumps(r, sort_keys=True) for r in rows) + "\n"
    src.unlink()
    out = src.with_suffix(".pstrace")
    out.write_bytes(binary.from_ndjson(text))
    return out


def test_a_binary_trace_replays(project, capsys):
    from pssc.testing.replay import recorded_seed
    path = _to_binary(project)
    p = ReplayPlatform(str(path), "r_f::t_rw")
    assert [s["kind"] for s in p.steps] == ["import", "access", "access"]
    assert recorded_seed(".pssunit/traces") == p.header.get("seed")
    rc, out = _replay(capsys)                       # the directory form
    assert rc == 0, out


def test_a_binary_trace_without_lines_reports_the_divergence(project, capsys):
    _to_binary(project, keep_lines=False)
    (project / "t2.pss").write_text(
        SUITE.replace('["st"]: return 0x0;', '["st"]: return 0x4;'))
    rc, out = _replay(capsys, "t2.pss")
    assert rc == 1, out
    assert ("first divergent access (step 2 of 3 recorded): expected "
            "write32 0x1000 = 0x5a, got write32 0x1004 = 0x5a") in out


def test_dbg_start_trace_replays_and_steps_back(project, capsys):
    # P5.2b: `pssc dbg start --trace <dir>`; `back` runs the replay again
    # from its start (a fresh replay platform), so the run still passes
    import json
    from unittest import mock
    from pssc.devenv.debug import sessions
    reg = sessions.Registry()

    def dbg(*argv):
        rc = main(["dbg", *argv, "--json"])
        cap = capsys.readouterr()
        assert cap.out.strip(), (rc, cap.err)
        return rc, json.loads(cap.out)
    try:
        with mock.patch.object(sessions, "REGISTRY", reg):
            capsys.readouterr()
            rc, doc = dbg("start", "t.pss", "r_f::t_rw", "--trace", ".pssunit/traces")
            assert rc == 0 and doc["seed"] == _header()["seed"], doc
            seen = []
            for _ in range(3):
                rc, doc = dbg("step")
                assert rc == 0 and doc["event"] == "stopped", doc
                seen.append(doc)
            rc, doc = dbg("back")
            assert rc == 0, doc
            assert (doc["tick"], doc["source"]) == (seen[1]["tick"], seen[1]["source"])
            rc, doc = dbg("continue")
            assert doc["event"] == "exited" and doc["checks"]["failed"] == 0, doc
    finally:
        reg.end_all()
