"""P3.5a1: server-mode control channel, daemon end + testbench end (no sim).

A daemon serves in a thread on a short /tmp socket; a ``Server`` (what the
pssvm shim uses inside a simulator) registers and answers on a poll thread.
"""
import json
import os
import shutil
import tempfile
import threading
import time

import pytest

from pssc.devenv import daemon, sim_cmd
from pssc.devenv.sim import registry, serve, sim_path
from pssc.devenv.sim.registry import REGISTRY, SimError


@pytest.fixture
def pssd():
    d = tempfile.mkdtemp(prefix="pssd", dir="/tmp")    # AF_UNIX path limit
    dm = daemon.Daemon(d, os.path.join(d, "w.sock"), idle=60)
    t = threading.Thread(target=dm.serve, daemon=True)
    t.start()
    end = time.time() + 5
    while not os.path.exists(dm.sim_path) and time.time() < end:
        time.sleep(0.01)
    yield dm
    dm._stop = True
    t.join(5)
    shutil.rmtree(d, ignore_errors=True)


def _wait(cond, timeout=5.0):
    end = time.time() + timeout
    while not cond():
        assert time.time() < end, "timed out"
        time.sleep(0.01)


class _Poller:
    """The simulator's thread: answers requests until the channel ends."""

    def __init__(self, srv):
        self.srv = srv
        self.t = threading.Thread(target=self._run, daemon=True)
        self.t.start()

    def _run(self):
        while not self.srv.closed and not self.srv.ended:
            self.srv.poll(0.05)


class _FakeStub:
    """The P2.1 stub's shape: request(msg) -> reply, events via next_event."""

    def __init__(self):
        self.events = []

    def request(self, msg):
        if msg["cmd"] == "pause":
            self.events.append({"event": "stopped", "body": {"reason": "pause"}})
            return {"seq": msg["seq"], "ok": True, "body": {}}
        return {"seq": msg["seq"], "ok": False,
                "error": {"code": "bad_request", "message": msg["cmd"]}}

    def next_event(self, timeout):
        if self.events:
            return self.events.pop(0)
        time.sleep(timeout)
        return None


def test_sim_path():
    assert sim_path("/r/abc.sock") == "/r/abc.sim"


def test_register_list_request_and_disconnect(pssd):
    srv = serve.Server("tb0", info={"top": "tb", "image": "x.zbc"},
                       handlers={"echo": lambda a: {"echo": a}})
    sid = srv.connect(path=pssd.sim_path)
    _wait(lambda: len(REGISTRY) == 1)
    (row,) = REGISTRY.list()
    assert row["id"] == sid and row["name"] == "tb0" and row["top"] == "tb"
    assert row["pid"] == os.getpid() and row["image"] == "x.zbc"
    s = REGISTRY.get("tb0")
    assert REGISTRY.get() is s and REGISTRY.get(sid) is s
    _Poller(srv)
    assert s.request("ping")["body"] == {"pid": os.getpid()}
    assert s.request("info")["body"]["name"] == "tb0"
    assert s.request("echo", {"a": 1}) == {"seq": 3, "ok": True,
                                           "body": {"echo": {"a": 1}}}
    r = s.request("step")                  # no stub attached
    assert not r["ok"] and r["error"]["code"] == "unsupported"
    srv.event("log", {"text": "hi"})
    assert s.next_event(5) == {"event": "log", "body": {"text": "hi"}}
    srv.close()
    _wait(lambda: len(REGISTRY) == 0)
    with pytest.raises(SimError):
        s.request("ping", timeout=1)
    with pytest.raises(SimError):
        REGISTRY.get("tb0")


def test_event_time(pssd):
    """A server with a ``clock`` stamps every event with the sim time (fs)
    as ``t``, stub events too (P3.7c1)."""
    now = [5000]
    srv = serve.Server("tbT", clock=lambda: now[0])
    srv.connect(path=pssd.sim_path)
    _wait(lambda: len(REGISTRY) == 1)
    s = REGISTRY.get("tbT")
    srv.event("log", {"text": "hi"})
    assert s.next_event(5) == {"event": "log", "body": {"text": "hi", "t": 5000}}
    now[0] = 7000
    srv._send({"event": "stopped", "body": {"reason": "step"}})
    assert s.next_event(5)["body"] == {"reason": "step", "t": 7000}
    srv.close()
    _wait(lambda: len(REGISTRY) == 0)


def test_stub_messages_are_carried(pssd):
    srv = serve.Server("tb1")
    srv.connect(path=pssd.sim_path)
    srv.attach_stub(_FakeStub())
    _Poller(srv)
    _wait(lambda: len(REGISTRY) == 1)
    s = REGISTRY.get("tb1")
    assert s.request("pause") == {"seq": 1, "ok": True, "body": {}}
    assert s.next_event(5) == {"event": "stopped", "body": {"reason": "pause"}}
    r = s.request("bogus")
    assert r["error"] == {"code": "bad_request", "message": "bogus"}
    assert s.request("end")["ok"] and srv.ended   # session cmds stay local
    srv.close()
    _wait(lambda: len(REGISTRY) == 0)


def test_handler_error_is_a_reply(pssd):
    def boom(args):
        raise ValueError("no image")
    srv = serve.Server("tb2", handlers={"load": boom})
    srv.connect(path=pssd.sim_path)
    _Poller(srv)
    _wait(lambda: len(REGISTRY) == 1)
    r = REGISTRY.get().request("load", {"path": "y"})
    assert r == {"seq": 1, "ok": False,
                 "error": {"code": "failed", "message": "no image"}}
    srv.close()
    _wait(lambda: len(REGISTRY) == 0)


def test_bad_registration_and_version(pssd):
    import socket
    from pssc.devenv.sim import dumps
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.connect(pssd.sim_path)
    c.sendall(dumps({"seq": 0, "cmd": "register", "args": {"protocol": 99}}))
    r = c.makefile("rb").readline()
    assert b'"code":"version"' in r
    c.close()
    with pytest.raises(serve.ServeError):
        bad = serve.Server("tb3")
        bad.info["protocol"] = 99
        bad.connect(path=pssd.sim_path)
    assert len(REGISTRY) == 0


def test_daemon_keeps_alive_with_session_and_ends_it(pssd):
    srv = serve.Server("tb4")
    srv.connect(path=pssd.sim_path)
    _wait(lambda: len(REGISTRY) == 1)
    assert daemon._sim_sessions() == 1
    pssd._stop = True                      # daemon exit drops the channel
    _wait(lambda: srv.closed)
    assert not srv.poll(0.1)
    _wait(lambda: registry.REGISTRY.list() == [])


# -- P3.5a2: a Verilator testbench in server mode (`+pssvm_serve=NAME`) ------ #

def _shim_tests():
    import sys
    sys.path.insert(0, os.path.dirname(__file__))
    import test_pssvm_shim
    return test_pssvm_shim


def test_verilator_serve(pssd, tmp_path, capsys):
    import subprocess
    st = _shim_tests()
    if st.VERILATOR is None or st.hdl_if is None:
        pytest.skip("verilator or pyhdl-if not found")
    img = tmp_path / "wb.zbc"
    env = st._env()
    r = subprocess.run(
        [os.sys.executable, "-m", "pssc", "build", "@model.f", ".", "--root",
         "wb_dma_fx_c", "--image", str(img)],
        cwd=st.WB_TESTS, capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    st._build(tmp_path, st.RUNNER_TB)
    env["PSSC_SIM_SOCKET"] = pssd.sim_path
    p = subprocess.Popen([str(tmp_path / "obj" / "simv"), f"+img={img}",
                          "+pssvm_serve=tbX"], cwd=tmp_path, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait(lambda: len(REGISTRY) == 1 or p.poll() is not None, 60)
        assert p.poll() is None, p.stdout.read()
        (row,) = REGISTRY.list()
        assert row["name"] == "tbX" and row["pid"] == p.pid
        assert row["image"] == str(img)
        s = REGISTRY.get("tbX")
        assert s.request("ping")["body"] == {"pid": p.pid}
        assert s.request("info")["body"]["name"] == "tbX"
        assert s.request("load")["error"]["code"] == "unsupported"
        # T3.5: through `pssc sim`; two runs in the one simulation
        assert _sim(capsys, pssd, "list")["sessions"][0]["name"] == "tbX"
        r = _sim(capsys, pssd, "run", "*prio*", "--timeout", "60")
        assert r["passed"] == 1 and r["failed"] == 0, r
        (t1,) = r["tests"]
        assert (t1["name"], t1["status"]) == ("wb_dma_fx_c::t_csr_prio_bits",
                                              "pass"), r
        assert t1["t"] > 0, r                     # sim time (fs), P3.7c1
        assert sim_cmd.main(["run", "t_csr_*", "nope"], path=pssd.path) == 0
        out = capsys.readouterr().out
        assert out.count("PASS  ") == 2, out
        assert out.rstrip().endswith(": 2 passed, 0 failed"), out
        ev = _sim(capsys, pssd, "log")["events"]
        assert [(e["n"], e["event"], e["body"]["status"]) for e in ev] == [
            (1, "test", "pass"), (2, "test", "pass"), (3, "test", "pass")], ev
        assert _sim(capsys, pssd, "end")["ok"]
        out, _ = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    assert f"PSSVM-SERVE: {row['id']}" in out and "PSSVM-SERVE: end" in out, out
    assert out.count("PSSVM-TEST: PASS") == 3 and "FAILED=0" in out, out
    _wait(lambda: len(REGISTRY) == 0)



def test_shim_engine_serve_and_poll(pssd, monkeypatch):
    shim = _shim_tests().shim
    monkeypatch.setenv("PSSC_SIM_SOCKET", pssd.sim_path)
    eng = shim.Engine()
    assert eng.poll(0) == -1                  # not serving
    sid = eng.serve("tbE", "top")
    assert sid.startswith("sim"), eng.error(0)
    assert eng.poll(0) == 0
    s = REGISTRY.get("tbE")
    assert REGISTRY.list()[0]["top"] == "top"
    res = {}
    t = threading.Thread(target=lambda: res.update(r=s.request("ping")))
    t.start()
    assert eng.poll(5000) == 1
    t.join(5)
    assert res["r"]["ok"]
    # a run is deferred: poll says 2 until the testbench answers it
    t = threading.Thread(target=lambda: res.update(
        r=s.request("run", {"sel": "a*"})))
    t.start()
    assert eng.poll(5000) == 2
    assert eng.pending_sel() == "a*"
    assert eng.poll(0) == 2
    eng.set_time(42)
    eng.serve_test("c::a1", 0)
    eng.serve_done(0, 1)
    t.join(5)
    assert res["r"]["body"] == {"passed": 0, "failed": 1}
    assert s.next_event(5)["body"] == {"name": "c::a1", "status": "fail",
                                       "t": 42}
    assert eng.poll(0) == 0 and eng.pending_sel() == ""
    t = threading.Thread(target=lambda: res.update(r=s.request("end")))
    t.start()
    assert eng.poll(5000) == -1               # ended
    t.join(5)
    assert eng.poll(0) == -1
    _wait(lambda: len(REGISTRY) == 0)


# -- P3.5b2: `pssc sim list|run|log|end` through the daemon (no sim) -------- #

def _sim(capsys, pssd, *argv):
    """``pssc sim ARGV --json`` against *pssd*; its JSON object."""
    import json
    capsys.readouterr()
    sim_cmd.main(list(argv) + ["--json"], path=pssd.path)
    return json.loads(capsys.readouterr().out)


def _fake_tb(pssd, name, results):
    """A testbench that answers ``run`` as pssvm_runner does: an event per
    test, then the counts."""
    srv = serve.Server(name)

    def run(args):
        sel = args.get("sel") or []
        for t, ok in results:
            if not sel or any(s in t for s in sel):
                srv.event("test", {"name": t, "status": "pass" if ok else "fail"})
        ran = [ok for t, ok in results if not sel or any(s in t for s in sel)]
        return {"passed": sum(ran), "failed": len(ran) - sum(ran)}
    srv.handlers["run"] = run
    srv.connect(path=pssd.sim_path)
    _Poller(srv)
    return srv


def test_sim_cli(pssd, capsys):
    assert _sim(capsys, pssd, "list") == {"ok": True, "sessions": []}
    assert sim_cmd.main(["list"], path=pssd.path) == 0
    assert capsys.readouterr().out == "no sim sessions\n"
    srv = _fake_tb(pssd, "tbA", [("t_a", True), ("t_b", False)])
    _wait(lambda: len(REGISTRY) == 1)
    (row,) = _sim(capsys, pssd, "list")["sessions"]
    assert row["name"] == "tbA" and row["pid"] == os.getpid()
    r = _sim(capsys, pssd, "run", "t_a")
    assert r == {"ok": True, "session": row["id"], "passed": 1, "failed": 0,
                 "tests": [{"name": "t_a", "status": "pass"}]}, r
    assert sim_cmd.main(["run", "--session", "tbA"], path=pssd.path) == 1
    out = capsys.readouterr().out
    assert out.splitlines() == ["PASS  t_a", "FAIL  t_b",
                                f"{row['id']}: 1 passed, 1 failed"], out
    ev = _sim(capsys, pssd, "log", "--since", "1")["events"]
    assert [(e["n"], e["body"]["name"]) for e in ev] == [(2, "t_a"), (3, "t_b")]
    sim_cmd.main(["log"], path=pssd.path)
    assert capsys.readouterr().out.splitlines()[0] == "[1] test  PASS  t_a"
    assert _sim(capsys, pssd, "run", "--session", "nope") == {
        "ok": False, "error": "no sim session 'nope'"}
    assert _sim(capsys, pssd, "end") == {"ok": True, "session": row["id"]}
    _wait(lambda: srv.ended and len(REGISTRY) == 0)
    assert sim_cmd.main(["end"], path=pssd.path) == 1
    assert "no sim session" in capsys.readouterr().err


def test_sim_run_does_not_block_daemon(pssd, capsys):
    """A long ``sim run`` is answered on its own thread."""
    import json
    gate = threading.Event()
    srv = serve.Server("slow")

    def run(args):
        gate.wait(10)
        return {"passed": 0, "failed": 0}
    srv.handlers["run"] = run
    srv.connect(path=pssd.sim_path)
    _Poller(srv)
    _wait(lambda: len(REGISTRY) == 1)
    res = {}
    t = threading.Thread(target=lambda: res.update(
        r=sim_cmd._request({"op": "sim", "cmd": "run", "session": None},
                           pssd.path)))
    t.start()
    time.sleep(0.2)
    assert t.is_alive()
    assert _sim(capsys, pssd, "list")["sessions"][0]["name"] == "slow"
    gate.set()
    t.join(10)
    assert res["r"]["ok"] and res["r"]["passed"] == 0
    srv.close()
    _wait(lambda: len(REGISTRY) == 0)


# --- P3.6c: `pssc sim reload` (T3.6, sim part) -----------------------------

RL_DUT = """\
component dut_c {
    function bit[32] v() { return %(v)s; }
}
"""

RL_TESTS = """\
import pssunit_pkg::*;
component tests_c {
    dut_c dut;
    %(more)s
    rt_c rt;
    @pssunit_pkg::test
    function void t_v() { rt.check_eq_int(dut.v(), 5, "v"); }
}
"""


def _rl_image(d, v, more=False):
    """Write the reload project in *d* (``dut.v()`` returns *v*; *more*: a
    second ``dut_c``, a change to the tree) and build ``d/rl.zbc``."""
    from pssc import cli
    (d / "dut.pss").write_text(RL_DUT % {"v": v})
    (d / "t.pss").write_text(RL_TESTS % {"more": "dut_c dut2;" if more else ""})
    img = d / "rl.zbc"
    assert cli.main(["build", str(d / "dut.pss"), str(d / "t.pss"), "--root",
                     "tests_c", "--image", str(img)]) == 0
    return img


def _in_thread(fn):
    res = {}
    t = threading.Thread(target=lambda: res.update(r=fn()))
    t.start()
    return t, res


def test_shim_reload(pssd, tmp_path, capsys, monkeypatch):
    """The engine answers ``reload`` between runs: the rebuilt image is its
    next generation, the test that failed passes, the tests are the new
    image's; a tree change under ``--strict`` is refused and changes
    nothing."""
    st = _shim_tests()
    monkeypatch.setenv("PSSC_SIM_SOCKET", pssd.sim_path)
    img = _rl_image(tmp_path, 4)
    eng = st.shim.Engine()
    assert eng.load(str(img), "", "fake") == 0, eng.error(0)
    assert eng.serve("tbR", "top")
    st._call(eng, "tests_c::t_v", {})
    assert eng.checks_failed() == 1
    _rl_image(tmp_path, 5)
    capsys.readouterr()
    t, res = _in_thread(lambda: _sim(capsys, pssd, "reload"))
    _wait(lambda: eng.poll(50) == 1 or not t.is_alive(), 10)
    t.join(10)
    r = res["r"]
    assert r["ok"] and (r["old"], r["new"]) == (1, 2), r
    assert r["image"] == str(img) and r["session"].startswith("sim"), r
    st._call(eng, "tests_c::t_v", {})
    assert eng.checks_failed() == 1                  # no new failure
    assert eng.test_name(0) == "tests_c::t_v"
    _rl_image(tmp_path, 5, more=True)
    t, res = _in_thread(lambda: _sim(capsys, pssd, "reload", str(img),
                                     "--strict"))
    _wait(lambda: eng.poll(50) == 1 or not t.is_alive(), 10)
    t.join(10)
    r = res["r"]
    assert not r["ok"] and r["refused"]["reason"] == "structure", r
    assert r["error"].startswith("reload refused (structure)"), r
    assert eng.session.generation == 2
    t, res = _in_thread(lambda: _sim(capsys, pssd, "end"))
    _wait(lambda: eng.poll(50) == -1, 10)
    t.join(10)


def test_verilator_reload(pssd, tmp_path, capsys):
    """T3.6 under Verilator: a failing test, a fix, ``pssc sim reload``, the
    same test passing; the simulation process is the same."""
    import subprocess
    st = _shim_tests()
    if st.VERILATOR is None or st.hdl_if is None:
        pytest.skip("verilator or pyhdl-if not found")
    img = _rl_image(tmp_path, 4)
    st._build(tmp_path, st.RUNNER_TB)
    env = st._env()
    env["PSSC_SIM_SOCKET"] = pssd.sim_path
    p = subprocess.Popen([str(tmp_path / "obj" / "simv"), f"+img={img}",
                          "+pssvm_serve=tbV"], cwd=tmp_path, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait(lambda: len(REGISTRY) == 1 or p.poll() is not None, 60)
        assert p.poll() is None, p.stdout.read()
        r = _sim(capsys, pssd, "run", "t_v", "--timeout", "60")
        assert (r["passed"], r["failed"]) == (0, 1), r
        _rl_image(tmp_path, 5)
        assert sim_cmd.main(["reload", "--timeout", "60"], path=pssd.path) == 0
        out = capsys.readouterr().out
        assert "generation 1 -> 2" in out, out
        r = _sim(capsys, pssd, "run", "t_v", "--timeout", "60")
        assert (r["passed"], r["failed"]) == (1, 0), r
        assert _sim(capsys, pssd, "list")["sessions"][0]["pid"] == p.pid
        assert _sim(capsys, pssd, "end")["ok"]
        out, _ = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    assert "PSSVM-TEST: FAIL tests_c::t_v" in out, out
    assert "PSSVM-TEST: PASS tests_c::t_v" in out, out


# --- P3.6c2: `pssc test [--watch] --platform sim:<s>` ----------------------

def _serve_runs(st, eng, alive):
    """The testbench's loop (pssvm_runner's): answer requests; a pending run
    calls each selected test and reports it -- until *alive()* is false."""
    import fnmatch
    while alive():
        if eng.poll(50) != 2:
            continue
        sel = [s for s in eng.pending_sel().split(",") if s]
        n = [0, 0]
        for i in range(len(eng.tests)):
            name = eng.test_name(i)
            if sel and not any(fnmatch.fnmatchcase(name, s) for s in sel):
                continue
            before = eng.checks_failed()
            st._call(eng, name, {})
            ok = eng.checks_failed() == before
            eng.serve_test(name, int(ok))
            n[0 if ok else 1] += 1
        eng.serve_done(*n)


def _sim_engine(st, img, name):
    eng = st.shim.Engine()
    assert eng.load(str(img), "", "fake") == 0, eng.error(0)
    assert eng.serve(name, "top")
    return eng


def test_sim_platform(pssd, tmp_path, capsys, monkeypatch):
    """``pssc test --platform sim:S``: the sources' image is reloaded into
    the session and its tests run there; an unchanged image is not
    reloaded again."""
    from pssc import cli
    from pssc.testing import sim_platform
    st = _shim_tests()
    monkeypatch.setenv("PSSC_SIM_SOCKET", pssd.sim_path)
    monkeypatch.setattr(sim_platform, "DAEMON_PATH", pssd.path)
    monkeypatch.setattr(sim_platform, "_SENT", {})
    monkeypatch.chdir(tmp_path)
    eng = _sim_engine(st, _rl_image(tmp_path, 4), "tbP")
    argv = ["test", "dut.pss", "t.pss", "--platform", "sim:tbP", "--no-ctrf"]

    def round_():
        capsys.readouterr()
        t, res = _in_thread(lambda: cli.main(argv))
        _serve_runs(st, eng, t.is_alive)
        t.join(10)
        return res["r"], capsys.readouterr()
    rc, cap = round_()
    assert rc == 1, cap
    assert "generation 1 -> 2" in cap.out and "FAIL  tests_c::t_v" in cap.out, cap
    assert "0 passed, 1 failed (sim session sim" in cap.out, cap
    (tmp_path / "dut.pss").write_text(RL_DUT % {"v": 5})
    rc, cap = round_()
    assert rc == 0 and "generation 2 -> 3" in cap.out, cap
    assert "1 passed, 0 failed" in cap.out, cap
    rc, cap = round_()                            # same image: no reload
    assert rc == 0 and "generation" not in cap.out, cap
    assert eng.session.generation == 3
    assert (tmp_path / ".pssunit" / "sim" / "tbP.zbc").is_file()
    assert cli.main(argv + ["--cov", "c.json"]) == 5
    assert "--cov: not with --platform sim:tbP" in capsys.readouterr().err
    t, res = _in_thread(lambda: _sim(capsys, pssd, "end"))
    _wait(lambda: eng.poll(50) == -1, 10)
    t.join(10)


def test_sim_platform_watch(pssd, tmp_path, capsys, monkeypatch):
    """T3.6: ``pssc test --watch --platform sim:S`` -- a failing test, a save
    that fixes it, the next round passes in the same simulation."""
    from pssc import _entry
    from pssc.testing import sim_platform, watch
    st = _shim_tests()
    monkeypatch.setenv("PSSC_SIM_SOCKET", pssd.sim_path)
    monkeypatch.setattr(sim_platform, "DAEMON_PATH", pssd.path)
    monkeypatch.setattr(sim_platform, "_SENT", {})
    monkeypatch.chdir(tmp_path)
    eng = _sim_engine(st, _rl_image(tmp_path, 4), "tbW")
    sleeps = []

    def sleep(_):
        sleeps.append(1)
        if len(sleeps) == 1:                      # the save
            (tmp_path / "dut.pss").write_text(RL_DUT % {"v": "(5)"})
        else:
            raise KeyboardInterrupt
    monkeypatch.setattr(watch, "_sleep", sleep)
    capsys.readouterr()
    t, res = _in_thread(lambda: _entry.main(
        ["test", "dut.pss", "t.pss", "--watch", "--platform=sim:tbW",
         "--no-ctrf"]))
    _serve_runs(st, eng, t.is_alive)
    t.join(10)
    out = capsys.readouterr().out
    assert res["r"] == 0, out
    assert out.count("generation") == 2 and "generation 2 -> 3" in out, out
    assert "FAIL  tests_c::t_v" in out and "1 passed, 0 failed" in out, out
    t, res = _in_thread(lambda: _sim(capsys, pssd, "end"))
    _wait(lambda: eng.poll(50) == -1, 10)
    t.join(10)


# --- P3.7a1: a debug stub on the sim's VM ----------------------------------

def _next_stub_event(s, name, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        ev = s.next_event(0.2)
        if ev is not None and ev.get("event") == name:
            return ev
    raise AssertionError(f"no {name} event")


def test_shim_debug_stops_inside_a_call(pssd, tmp_path, capsys, monkeypatch):
    """``debug`` puts a stub on the sim's VM, attached with ``entry:
    false``: no stop until a breakpoint, which stops the testbench's
    call (it does not return while stopped); the stub answers while the
    polling thread is blocked; ``continue`` lets the run end."""
    st = _shim_tests()
    monkeypatch.setenv("PSSC_SIM_SOCKET", pssd.sim_path)
    eng = _sim_engine(st, _rl_image(tmp_path, 4), "tbD")
    alive = [True]
    tb = threading.Thread(target=_serve_runs, args=(st, eng, lambda: alive[0]),
                          daemon=True)
    tb.start()
    try:
        _wait(lambda: len(REGISTRY) == 1)
        s = REGISTRY.get("tbD")
        assert s.request("debug", {"on": True})["body"]["image"].endswith("rl.zbc")
        r = s.request("attach", {"version": 1, "entry": False})
        assert r["ok"], r
        e = eng.session.model.entries["tests_c::t_v"]
        r = s.request("set_pc_breaks", {"breaks": [
            {"id": 1, "coro": e.coro, "pc": 0}]})
        assert r["body"]["breaks"][0]["verified"], r
        t, res = _in_thread(lambda: _sim(capsys, pssd, "run", "tests_c::t_v"))
        ev = _next_stub_event(s, "stopped")
        assert ev["body"]["reason"] == "breakpoint", ev
        time.sleep(0.3)
        assert t.is_alive()                      # the call is held
        th = s.request("threads")
        assert th["ok"] and th["body"]["threads"], th
        assert s.request("continue")["ok"]
        t.join(10)
        r = res["r"]
        assert (r["passed"], r["failed"]) == (0, 1), r
        assert s.request("debug", {"on": False})["ok"]
        r = _sim(capsys, pssd, "run", "tests_c::t_v")  # no stub: runs straight
        assert (r["passed"], r["failed"]) == (0, 1), r
    finally:
        alive[0] = False
        tb.join(5)


# --- P3.7a2: `pssc sim attach` = a `pssc dbg` session on the sim -------------

def _dbg(capsys, *argv):
    from pssc import cli
    capsys.readouterr()
    rc = cli.main(["dbg", *argv, "--json"])
    out = capsys.readouterr().out
    assert rc == 0, out
    return json.loads(out)


def test_sim_attach_dbg(pssd, tmp_path, capsys, monkeypatch):
    """``pssc sim attach`` makes a ``pssc dbg`` session (not stopped) on the
    sim's VM; a source breakpoint stops the testbench's call, ``dbg
    bt``/``eval`` work there, ``dbg continue`` lets it end; ``dbg end``
    takes the stub off and the sim runs on."""
    from pssc.devenv.debug.sessions import REGISTRY as DBG
    st = _shim_tests()
    monkeypatch.setenv("PSSC_SIM_SOCKET", pssd.sim_path)
    eng = _sim_engine(st, _rl_image(tmp_path, 4), "tbA")
    alive = [True]
    tb = threading.Thread(target=_serve_runs, args=(st, eng, lambda: alive[0]),
                          daemon=True)
    tb.start()
    try:
        _wait(lambda: len(REGISTRY) == 1)
        r = _sim(capsys, pssd, "attach")
        assert r["ok"] and r["image"].endswith("rl.zbc"), r
        sid = r["dbg"]
        lst = _dbg(capsys, "list")["sessions"]
        assert [(x["session"], x["test"], x["state"]) for x in lst] == \
            [(sid, "sim:tbA", "running")], lst
        bp = _dbg(capsys, "break", "add", f"{tmp_path / 'dut.pss'}:2",
                  "--session", sid)["breakpoint"]
        assert bp["verified"], bp
        t, res = _in_thread(lambda: _sim(capsys, pssd, "run", "tests_c::t_v"))
        stop = _dbg(capsys, "continue", "--session", sid, "--timeout", "10")
        assert (stop["event"], stop["reason"]) == ("stopped", "breakpoint"), stop
        time.sleep(0.3)
        assert t.is_alive()                      # the call is held
        assert _dbg(capsys, "bt", "--session", sid)["frames"]
        r = _dbg(capsys, "continue", "--session", sid, "--timeout", "1")
        assert r["event"] == "running", r        # the call returned; no stop
        t.join(10)
        assert (res["r"]["passed"], res["r"]["failed"]) == (0, 1), res
        assert _dbg(capsys, "end", "--session", sid)["ended"]
        assert not DBG.sessions
        r = _sim(capsys, pssd, "run", "tests_c::t_v")  # no stub: runs straight
        assert (r["passed"], r["failed"]) == (0, 1), r
    finally:
        DBG.end_all()
        alive[0] = False
        tb.join(5)


def test_verilator_attach(pssd, tmp_path, capsys):
    """T3.7 under Verilator: ``pssc sim attach``, a breakpoint in the test's
    call holds the simulation; sim time does not move across the stop (the
    runner's ``PSSVM-TEST`` line: same start and end ``$time``)."""
    import re
    import subprocess
    from pssc.devenv.debug.sessions import REGISTRY as DBG
    st = _shim_tests()
    if st.VERILATOR is None or st.hdl_if is None:
        pytest.skip("verilator or pyhdl-if not found")
    img = _rl_image(tmp_path, 4)
    st._build(tmp_path, st.RUNNER_TB)
    env = st._env()
    env["PSSC_SIM_SOCKET"] = pssd.sim_path
    p = subprocess.Popen([str(tmp_path / "obj" / "simv"), f"+img={img}",
                          "+pssvm_serve=tbV"], cwd=tmp_path, env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        _wait(lambda: len(REGISTRY) == 1 or p.poll() is not None, 60)
        assert p.poll() is None, p.stdout.read()
        sid = _sim(capsys, pssd, "attach")["dbg"]
        assert _dbg(capsys, "break", "add", f"{tmp_path / 'dut.pss'}:2",
                    "--session", sid)["breakpoint"]["verified"]
        t, res = _in_thread(lambda: _sim(capsys, pssd, "run", "t_v",
                                         "--timeout", "60"))
        stop = _dbg(capsys, "continue", "--session", sid, "--timeout", "30")
        assert (stop["event"], stop["reason"]) == ("stopped", "breakpoint"), stop
        time.sleep(0.5)                          # held: the sim waits
        assert t.is_alive() and p.poll() is None
        assert _dbg(capsys, "eval", "1 + 1", "--session", sid)["value"] == 2
        r = _dbg(capsys, "continue", "--session", sid, "--timeout", "1")
        assert r["event"] == "running", r
        t.join(60)
        assert (res["r"]["passed"], res["r"]["failed"]) == (0, 1), res
        assert _dbg(capsys, "end", "--session", sid)["ended"]
        assert _sim(capsys, pssd, "end")["ok"]
        out, _ = p.communicate(timeout=60)
    finally:
        DBG.end_all()
        if p.poll() is None:
            p.kill()
    assert p.returncode == 0, out
    m = re.search(r"PSSVM-TEST: FAIL tests_c::t_v @(\d+)\.\.(\d+)", out)
    assert m and m.group(1) == m.group(2), out


# --- P3.7b: `+pssvm_dbg_on_fail`, `pssc sim resume` --------------------------

def test_verilator_dbg_on_fail(pssd, tmp_path, capsys):
    """A failing test holds a simulation that is not serving: it registers
    (`held` event), sim time stands still, a ``run`` is refused while held;
    a fix, ``pssc sim reload`` and ``pssc sim resume --rerun`` run it again,
    and only that run counts."""
    import re
    import subprocess
    st = _shim_tests()
    if st.VERILATOR is None or st.hdl_if is None:
        pytest.skip("verilator or pyhdl-if not found")
    img = _rl_image(tmp_path, 4)
    st._build(tmp_path, st.RUNNER_TB)
    env = st._env()
    env["PSSC_SIM_SOCKET"] = pssd.sim_path
    p = subprocess.Popen([str(tmp_path / "obj" / "simv"), f"+img={img}",
                          "+pssunit=t_v", "+pssvm_dbg_on_fail"], cwd=tmp_path,
                         env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True)
    try:
        _wait(lambda: len(REGISTRY) == 1 or p.poll() is not None, 60)
        assert p.poll() is None, p.stdout.read()
        _wait(lambda: any(e["event"] == "held" for e in
                          _sim(capsys, pssd, "log")["events"]), 30)
        (ev,) = [e for e in _sim(capsys, pssd, "log")["events"]
                 if e["event"] == "held"]
        r = _sim(capsys, pssd, "run", "t_v", "--timeout", "10")
        assert not r["ok"] and "held" in r["error"], r
        _rl_image(tmp_path, 5)
        assert _sim(capsys, pssd, "reload", "--timeout", "60")["new"] == 2
        r = _sim(capsys, pssd, "resume", "--rerun")
        assert r["ok"] and r["rerun"] and r["why"] == "tests_c::t_v", r
        out, _ = p.communicate(timeout=60)
    finally:
        if p.poll() is None:
            p.kill()
    assert "PSSVM-DBG: held tests_c::t_v" in out, out
    m = re.search(r"PSSVM-TEST: FAIL tests_c::t_v @(\d+)\.\.(\d+)", out)
    assert m, out
    held = re.search(r"PSSVM-DBG: held tests_c::t_v @(\d+)", out)
    m2 = re.search(r"PSSVM-TEST: PASS tests_c::t_v @(\d+)", out)
    assert held and m2 and held.group(1) == m2.group(1) == m.group(2), out
    assert ev["body"]["t"] == int(held.group(1)) * 10**6, ev   # 1ns -> fs
    assert "PSSVM-SUMMARY: 1 passed, 0 failed" in out, out
    assert sim_cmd.main(["resume"], path=pssd.path) == 1   # no session left


def test_shim_trace(tmp_path):
    """``Engine.trace`` writes a test's events to ``DIR/<test-id>.ndjson``,
    each with ``t`` (fs, the last ``set_time``); "" ends it, and later
    calls are not traced."""
    import json
    from zuspec.be.bc.trace import schema
    st = _shim_tests()
    img = _rl_image(tmp_path, 5)
    eng = st.shim.Engine()
    assert eng.load(str(img), "", "fake") == 0, eng.error(0)
    eng.set_time(7000000)
    f = eng.trace(str(tmp_path / "tr"), "tests_c::t_v")
    assert f == str(tmp_path / "tr" / "tests_c.t_v.ndjson"), eng.error(0)
    st._call(eng, "tests_c::t_v", {})
    assert eng.trace(str(tmp_path / "tr"), "") == ""
    n = len(open(f).read().splitlines())
    st._call(eng, "tests_c::t_v", {})
    text = open(f).read()
    lines = text.splitlines()
    assert len(lines) == n > 2
    assert json.loads(lines[0])["test"] == "tests_c::t_v"
    _, events = schema.parse(text)
    assert events and all(json.loads(x)["t"] == 7000000 for x in lines[1:])
    assert any(e.kind == "check" for e in events), [e.kind for e in events]


def test_verilator_trace(tmp_path, capsys):
    """``+pssvm_trace=DIR``: each test run writes its trace (``PSSVM-TRACE:
    file``), each event with its sim time; ``pssc trace show --at`` the
    test's end time gives its last event."""
    import json
    import re
    import subprocess
    from pssc.cli import main
    st = _shim_tests()
    if st.VERILATOR is None or st.hdl_if is None:
        pytest.skip("verilator or pyhdl-if not found")
    img = _rl_image(tmp_path, 5)
    st._build(tmp_path, st.RUNNER_TB)
    p = subprocess.run([str(tmp_path / "obj" / "simv"), f"+img={img}",
                        "+pssvm_trace=tr"], cwd=tmp_path, env=st._env(),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, timeout=120)
    out = p.stdout
    m = re.search(r"PSSVM-TRACE: (\S+)", out)
    assert m and "PSSVM-SUMMARY: 1 passed, 0 failed" in out, out
    f = tmp_path / m.group(1)
    assert f == tmp_path / "tr" / "tests_c.t_v.ndjson", out
    t1 = int(re.search(r"PSSVM-TEST: PASS tests_c::t_v @\d+\.\.(\d+)",
                       out).group(1))
    lines = f.read_text().splitlines()
    ev = [json.loads(x) for x in lines[1:]]
    assert ev and all(e["t"] <= t1 * 10**6 for e in ev), ev   # 1ns -> fs
    capsys.readouterr()
    assert main(["trace", "show", str(f), "--at", f"{t1}ns", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["at"] == ev[-1]["seq"] and doc["events"][0]["t"] == ev[-1]["t"]


DBGSIG_PSS = """\
import pssunit_pkg::*;
function bit[32] hw_v();
import target function hw_v;
component tests_c {
    rt_c rt;
    @pssunit_pkg::test
    function void t_v() {
        bit[32] x = hw_v();
        rt.check_eq_int(x, 0, "x");
    }
}
"""


def test_verilator_dbgsig(tmp_path):
    """``+pssvm_dbgsig``: module ``pssvm_dbg`` shows the function a call
    runs and the line it last stopped at; without the plusarg it stays
    still."""
    import re
    import subprocess
    from pssc import cli
    st = _shim_tests()
    if st.VERILATOR is None or st.hdl_if is None:
        pytest.skip("verilator or pyhdl-if not found")
    # a target import: the call yields to SV there (a check does not)
    (tmp_path / "d.pss").write_text(DBGSIG_PSS)
    img = tmp_path / "d.zbc"
    assert cli.main(["build", str(tmp_path / "d.pss"), "--root", "tests_c",
                     "--image", str(img)]) == 0
    tb = st.RUNNER_TB.replace("  endclass", (
        "    virtual task call_import(pssvm_req req, output longint unsigned ret);\n"
        "      #5; ret = 0;\n"
        "    endtask\n"
        "  endclass"), 1)
    tb = tb.replace("  initial begin", (
        "  pssvm_dbg u_dbg();\n"
        "  always @(u_dbg.seq) $display(\"PSSVM-DBGSIG: %0d %s %s:%0d\",\n"
        "    u_dbg.seq, u_dbg.cur_op, u_dbg.cur_file, u_dbg.cur_line);\n"
        "  initial begin"), 1)
    st._build(tmp_path, tb)

    def sim(*plusargs):
        p = subprocess.run([str(tmp_path / "obj" / "simv"), f"+img={img}",
                            *plusargs], cwd=tmp_path, env=st._env(),
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=120)
        assert "PSSVM-SUMMARY: 1 passed, 0 failed" in p.stdout, p.stdout
        return re.findall(r"PSSVM-DBGSIG: (\d+) (\S+) (\S*):(\d+)", p.stdout)

    assert sim() == []
    rows = sim("+pssvm_dbgsig")
    # t_v's `hw_v()` is d.pss line 8
    assert rows and all(op == "tests_c::t_v" for _, op, _, _ in rows), rows
    assert any(f.endswith("d.pss") and ln == "8" for _, _, f, ln in rows), rows
