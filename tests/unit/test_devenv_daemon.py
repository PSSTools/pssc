"""T1.18 / T-G6: the workspace daemon (`pssd`) and the thin `pssc` client.

Every test runs `python -m pssc` in a subprocess with ``PSSC_NO_DAEMON``
unset and ``PSSC_DAEMON_DIR`` in a private directory, and stops whatever
daemons it started.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

SRC = Path(__file__).resolve().parents[2] / "src"

DUT = """\
component dut_c {
    target function int one() {
        return 1;
    }
}
"""

TESTS = """\
import pssunit_pkg::*;
component tests_c {
    dut_c dut;
    rt_c rt;
    @pssunit_pkg::test
    function void t_a() {
        rt.check_eq_int(1, 1, "one");
    }
}
"""


def _workspace(base: Path, name: str) -> Path:
    ws = base / name
    (ws / "src").mkdir(parents=True)
    (ws / "tests").mkdir()
    (ws / "pyproject.toml").write_text("[project]\nname = 'ws'\n")
    (ws / "src" / "dut.pss").write_text(DUT)
    (ws / "tests" / "t.pss").write_text(TESTS)
    return ws


@pytest.fixture
def env():
    # A short private directory: a Unix socket path is limited to ~107 bytes.
    run = tempfile.mkdtemp(prefix="pssd")
    e = dict(os.environ)
    e.pop("PSSC_NO_DAEMON", None)
    e.update(PSSC_DAEMON_DIR=run, PYTHONPATH=str(SRC) + os.pathsep
             + e.get("PYTHONPATH", ""))
    yield e
    for sock in Path(run).glob("*.sock"):
        _stop_pid(_status(e, None, sock=str(sock)))
    shutil.rmtree(run, ignore_errors=True)


def _pssc(env, cwd, *argv, **kw):
    return subprocess.run([sys.executable, "-m", "pssc", *argv], cwd=cwd,
                          env=dict(env, **kw), capture_output=True, text=True,
                          timeout=120)


def _status(env, cwd, sock=None):
    if sock is not None:     # teardown: ask the socket directly
        code = ("import json,sys; from pssc.devenv import client; "
                "r = client.request(sys.argv[1], {'op': 'status'}); "
                "client.stop(sys.argv[1]); print(json.dumps(r))")
        p = subprocess.run([sys.executable, "-c", code, sock], env=env,
                           capture_output=True, text=True, timeout=60)
        return json.loads(p.stdout or "null")
    return json.loads(_pssc(env, cwd, "daemon", "status", "--json").stdout)


def _stop_pid(st):
    pid = (st or {}).get("pid")
    if pid:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A zombie child of nobody we know: check /proc where we can.
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


def _wait(pred, timeout=10.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return pred()


def _mask(text: str) -> str:
    return re.sub(r"\d+\.\d\ds", "T", text)


def test_autostart_serves_identical_output_and_stops(tmp_path, env):
    ws = _workspace(tmp_path, "ws")
    assert _status(env, ws)["running"] is False
    argv = ("test", ".", "--no-ctrf", "--seed", "7")
    served = _pssc(env, ws, *argv)
    assert served.returncode == 0, served.stderr
    assert "1 passed" in served.stdout
    st = _status(env, ws)
    assert st["running"] and st["requests"] == 1 and st["current"]
    assert st["root"] == str(ws.resolve())
    local = _pssc(env, ws, *argv, PSSC_NO_DAEMON="1")
    assert (_mask(served.stdout), served.stderr) == \
        (_mask(local.stdout), local.stderr)
    # `pssc model` is served too, byte for byte.
    margv = ("model", "api", "src", "--root", "dut_c", "--json")
    m1 = _pssc(env, ws, *margv)
    m2 = _pssc(env, ws, *margv, PSSC_NO_DAEMON="1")
    assert m1.returncode == m2.returncode == 0, m1.stderr
    assert m1.stdout == m2.stdout and "dut_c" in m1.stdout
    assert _status(env, ws)["requests"] == 2
    # A served failure keeps its exit code and stderr.
    bad = _pssc(env, ws, "test", "nowhere.pss", "--no-ctrf")
    assert bad.returncode == _pssc(env, ws, "test", "nowhere.pss", "--no-ctrf",
                                   PSSC_NO_DAEMON="1").returncode != 0
    assert "stopped" in _pssc(env, ws, "daemon", "stop").stdout
    assert _wait(lambda: not _alive(st["pid"]))
    assert _status(env, ws)["running"] is False


def test_daemon_start_command(tmp_path, env):
    # A native simulator holding for a debugger cannot start pssd itself.
    ws = _workspace(tmp_path, "ws")
    first = json.loads(_pssc(env, ws, "daemon", "start", "--json").stdout)
    assert first["running"] is True and first["started"] is True
    pid = _status(env, ws)["pid"]
    again = _pssc(env, ws, "daemon", "start")
    assert again.returncode == 0 and "pssd: running" in again.stdout
    assert _status(env, ws)["pid"] == pid           # not restarted
    assert "stopped" in _pssc(env, ws, "daemon", "stop").stdout
    assert _wait(lambda: not _alive(pid))


def test_no_daemon_env_starts_nothing(tmp_path, env):
    ws = _workspace(tmp_path, "ws")
    r = _pssc(env, ws, "test", ".", "--no-ctrf", PSSC_NO_DAEMON="1")
    assert r.returncode == 0, r.stderr
    assert _status(env, ws)["running"] is False


def test_idle_stop(tmp_path, env):
    ws = _workspace(tmp_path, "ws")
    r = _pssc(env, ws, "test", ".", "--no-ctrf", PSSC_DAEMON_IDLE="1")
    assert r.returncode == 0, r.stderr
    st = _status(env, ws)
    assert st["running"] and st["idle"] == 1.0
    assert _wait(lambda: not _alive(st["pid"]), timeout=15)
    assert _status(env, ws)["running"] is False


def test_version_mismatch_restarts(tmp_path, env):
    ws = _workspace(tmp_path, "ws")
    sock = _socket(env, ws)
    code = ("import sys; from pssc.devenv import protocol, daemon; "
            "protocol.VERSION = '0.0-old'; "
            "sys.exit(daemon.main(['--root', sys.argv[1], '--socket', sys.argv[2]]))")
    old = subprocess.Popen([sys.executable, "-c", code, str(ws.resolve()), sock],
                           env=env, start_new_session=True)
    try:
        assert _wait(lambda: os.path.exists(sock), timeout=30)
        st = _status(env, ws)
        assert st["running"] and not st["current"] and st["pid"] == old.pid
        r = _pssc(env, ws, "test", ".", "--no-ctrf")
        assert r.returncode == 0 and "1 passed" in r.stdout, r.stderr
        assert old.wait(timeout=10) == 0          # stopped, not killed
        st = _status(env, ws)
        assert st["current"] and st["pid"] != old.pid
    finally:
        if old.poll() is None:
            old.kill()


def test_two_workspaces_two_daemons(tmp_path, env):
    a, b = _workspace(tmp_path, "a"), _workspace(tmp_path, "b")
    for ws in (a, b):
        assert _pssc(env, ws, "test", ".", "--no-ctrf").returncode == 0
    sa, sb = _status(env, a), _status(env, b)
    assert sa["running"] and sb["running"]
    assert sa["pid"] != sb["pid"] and sa["socket"] != sb["socket"]
    # A subdirectory belongs to its workspace's daemon.
    assert _status(env, a / "tests")["pid"] == sa["pid"]


def test_crashed_daemon_is_restarted(tmp_path, env):
    ws = _workspace(tmp_path, "ws")
    assert _pssc(env, ws, "test", ".", "--no-ctrf").returncode == 0
    st = _status(env, ws)
    os.kill(st["pid"], signal.SIGKILL)
    assert _wait(lambda: not _alive(st["pid"]))
    assert os.path.exists(st["socket"])           # left behind
    r = _pssc(env, ws, "test", ".", "--no-ctrf")
    assert r.returncode == 0 and "1 passed" in r.stdout, r.stderr
    st2 = _status(env, ws)
    assert st2["running"] and st2["pid"] != st["pid"]


def test_source_hash_cache(tmp_path, env):
    """T1.18: no edit -> no rebuild; an edit -> one rebuild; a test-file edit
    does not re-run the gate, a device-file edit does."""
    ws = _workspace(tmp_path, "ws")
    argv = ("test", ".", "--no-ctrf", "--seed", "7")
    first = _pssc(env, ws, *argv)
    second = _pssc(env, ws, *argv)
    assert first.returncode == second.returncode == 0, second.stderr
    assert _mask(first.stdout) == _mask(second.stdout)
    c = _status(env, ws)["cache"]
    assert (c["builds"], c["lowered"], c["gated"]) == (1, 1, 1) and c["hits"] >= 3
    # --collect-only reuses the context too.
    assert _pssc(env, ws, "test", ".", "--collect-only").returncode == 0
    assert _status(env, ws)["cache"]["builds"] == 1
    with open(ws / "tests" / "t.pss", "a") as f:
        f.write("// edited\n")
    assert "1 passed" in _pssc(env, ws, *argv).stdout
    c = _status(env, ws)["cache"]
    assert (c["builds"], c["lowered"], c["gated"]) == (2, 2, 1)
    with open(ws / "src" / "dut.pss", "a") as f:
        f.write("// edited\n")
    third = _pssc(env, ws, *argv)
    assert "1 passed" in third.stdout
    c = _status(env, ws)["cache"]
    assert (c["builds"], c["lowered"], c["gated"]) == (3, 3, 2)
    local = _pssc(env, ws, *argv, PSSC_NO_DAEMON="1")
    assert _mask(third.stdout) == _mask(local.stdout)


def _socket(env, ws) -> str:
    code = ("import sys; from pssc.devenv import protocol; "
            "print(protocol.socket_path(protocol.find_root(sys.argv[1])))")
    return subprocess.run([sys.executable, "-c", code, str(ws)], env=env,
                          capture_output=True, text=True,
                          check=True).stdout.strip()


# -- T-G6: the thin client imports nothing heavy ------------------------------

THIN = ["_entry.py", "devenv/client.py", "devenv/protocol.py", "__init__.py",
        "__version__.py"]
HEAVY = ("pssparser", "zuspec", "pssc.ast2ir", "pssc.frontend", "pssc.driver",
         "pssc.targets", "pssc.cli", "pssc.runtime", "pssc.ir", "pssc.testing")


def _module_scope_imports(path: Path):
    tree = ast.parse(path.read_text())
    pkg = "pssc." + ".".join(path.relative_to(SRC / "pssc").parts[:-1])
    pkg = pkg.rstrip(".")
    for node in tree.body:          # module scope only, not function bodies
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg.split(".")[: len(pkg.split(".")) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            yield mod
            for a in node.names:
                yield f"{mod}.{a.name}"


def test_thin_client_imports_nothing_heavy_structural():
    for rel in THIN:
        for mod in _module_scope_imports(SRC / "pssc" / rel):
            assert not mod.startswith(HEAVY), f"{rel} imports {mod}"


def test_thin_client_imports_nothing_heavy_measured():
    code = ("import sys, time; t = time.perf_counter(); "
            "import pssc._entry, pssc.devenv.client; "
            "ms = (time.perf_counter() - t) * 1e3; "
            "print(ms); print(','.join(m for m in sys.modules "
            "if m.startswith(%r)))" % (HEAVY,))
    env = dict(os.environ, PYTHONPATH=str(SRC))
    p = subprocess.run([sys.executable, "-c", code], env=env,
                       capture_output=True, text=True, check=True)
    ms, heavy = p.stdout.splitlines()[0], p.stdout.splitlines()[1:]
    assert heavy in ([], [""]), heavy
    assert float(ms) < 50.0          # ~5 ms measured; the pipeline is ~160 ms
