"""T1.21: the WB DMA suite (examples/op_model/tests) passes on the fake in
under 1 s, and each seeded bug (wrong offset, wrong field, missing completion)
fails at least one of its tests."""
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
EXAMPLE = REPO / "examples" / "op_model"

# name -> (file under examples/op_model, text, replacement)
SEEDED = {
    "offset_int_msk_a": ("pss/wb_dma_regs_pkg.pss",
                         '["int_msk_a"]: return 0x4;', '["int_msk_a"]: return 0x8;'),
    "offset_bank": ("pss/wb_dma_regs_pkg.pss",
                    "0x20 + index*0x20", "0x24 + index*0x20"),
    "driver_wrong_register": ("pss/wb_dma_c/functions/configure_interrupt_routing.pss",
                              "[WB_DMA_INT_A]: regs.int_msk_a.write(vec);",
                              "[WB_DMA_INT_A]: regs.int_msk_b.write(vec);"),
    "field_done_err_swapped": ("pss/wb_dma_regs_pkg.pss",
                               "        bit done; // [11] sw=r hw=w\n"
                               "        bit err; // [12] sw=r hw=w\n",
                               "        bit err; // [12] sw=r hw=w\n"
                               "        bit done; // [11] sw=r hw=w\n"),
    "field_prio_moved": ("pss/wb_dma_regs_pkg.pss",
                         "        bit[3] prio; // [15:13] sw=rw hw=r reset=0x0\n"
                         "        bit rest_en; // [16] sw=rw hw=r reset=0x0\n",
                         "        bit rest_en; // [16] sw=rw hw=r reset=0x0\n"
                         "        bit[3] prio; // [15:13] sw=rw hw=r reset=0x0\n"),
    "driver_wrong_field": ("pss/wb_dma_ch_c/functions/probe_status.pss",
                           "if (csr.done == 1)", "if (csr.busy == 1)"),
    "completion_not_awaited": ("pss/wb_dma_ch_c/functions/blocking_ops.pss",
                               "transfer_single_start(cfg);\n      return wait_completion();",
                               "transfer_single_start(cfg);\n      return WB_DMA_DONE;"),
    "completion_not_polled": ("pss/wb_dma_ch_c/functions/probe_status.pss",
                              "if (csr.done == 1)", "if (1 == 1)"),
}


def _run(tests_dir: pathlib.Path, *more: str):
    env = dict(os.environ, PSSC_NO_DAEMON="1",
               PYTHONPATH=os.pathsep.join([str(REPO / "src"), os.environ.get("PYTHONPATH", "")]))
    proc = subprocess.run(
        [sys.executable, "-m", "pssc", "test", "@model.f", ".", "--platform", "fake",
         "--targets", "none", "--no-ctrf", "--json", *more],
        cwd=tests_dir, env=env, capture_output=True, text=True, timeout=120)
    events = [json.loads(l) for l in proc.stdout.splitlines() if l.startswith("{")]
    assert events, proc.stderr[-2000:]
    return proc.returncode, events


def _copy(tmp_path: pathlib.Path) -> pathlib.Path:
    for sub in ("pss", "tests"):
        shutil.copytree(EXAMPLE / sub, tmp_path / sub,
                        ignore=shutil.ignore_patterns(".pssunit"))
    return tmp_path / "tests"


def test_the_suite_passes_on_the_fake_in_under_a_second(tmp_path):
    rc, events = _run(_copy(tmp_path))
    end = events[-1]
    assert end["event"] == "run.end"
    s = end["summary"]
    assert rc == 0 and s["failed"] == 0 and s["error"] == 0, end
    assert s["passed"] >= 20
    assert end["duration_ms"] < 1000


@pytest.mark.parametrize("bug", sorted(SEEDED))
def test_a_seeded_bug_fails_a_test(tmp_path, bug):
    tests = _copy(tmp_path)
    rel, old, new = SEEDED[bug]
    path = tmp_path / rel
    text = path.read_text()
    assert old in text, f"{bug}: the model no longer has the text to seed"
    path.write_text(text.replace(old, new, 1))
    rc, events = _run(tests)
    failed = [e["id"] for e in events
              if e.get("event") == "test.end" and e["status"] in ("fail", "error")]
    assert rc != 0 and failed, f"{bug}: no test caught it"


def _covsight(cwd: pathlib.Path, *args: str):
    proc = subprocess.run([sys.executable, "-m", "covsight", *args], cwd=cwd,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc


def test_cov_cdb_runs_merge_in_covsight(tmp_path):
    """T2.8: the suite and a -k run, each with --cov x.cdb, merge in covsight."""
    tests = _copy(tmp_path)
    assert _run(tests, "--cov", "all.cdb")[0] == 0
    assert _run(tests, "-k", "prio", "--cov", "prio.cdb")[0] == 0
    merge = _covsight(tests, "merge", "all.cdb", "prio.cdb", "-o", "merged.cdb")
    assert "schema" not in merge.stderr     # the same points in both: no slow path

    def show(what, db):
        return json.loads(_covsight(tests, "show", what, db).stdout)
    merged, prio = show("summary", "merged.cdb"), show("summary", "prio.cdb")
    code, code_prio = merged["coverage_by_type"]["code"], prio["coverage_by_type"]["code"]
    assert code["total"] == code_prio["total"]
    assert code["covered"] > code_prio["covered"] > 0
    names = [t["name"] for t in show("tests", "merged.cdb")["tests"]]
    n_prio = sum("prio" in n for n in names) // 2
    assert n_prio > 0 and len(names) == show("summary", "all.cdb")["tests"]["total_tests"] + n_prio
    assert all(t["passed"] for t in show("tests", "merged.cdb")["tests"])


def _pssc(cwd: pathlib.Path, *args: str):
    env = dict(os.environ, PSSC_NO_DAEMON="1",
               PYTHONPATH=os.pathsep.join([str(REPO / "src"), os.environ.get("PYTHONPATH", "")]))
    proc = subprocess.run([sys.executable, "-m", "pssc", *args], cwd=cwd, env=env,
                          capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    return proc


@pytest.fixture(scope="module")
def cov_run(tmp_path_factory):
    """The suite run once with --cov x.json and once with --cov x.cdb."""
    tests = _copy(tmp_path_factory.mktemp("cov"))
    assert _run(tests, "--cov", "c.json")[0] == 0
    assert _run(tests, "--cov", "c.cdb")[0] == 0
    return tests


def test_cov_gaps_resolve_their_sources(cov_run):
    """T2.9: gaps on the WB DMA report find the model's files and show context."""
    doc = json.loads(_pssc(cov_run, "cov", "gaps", "c.json", "--json",
                           "--source-root", ".").stdout)
    assert doc["schema"] == "pssc.cov-gaps/1"
    gs = doc["gaps"]
    assert doc["summary"]["statement"] > 0 and len(gs) == sum(doc["summary"].values())
    located = [g for g in gs if g.get("file")]
    assert located and all((cov_run / g["file"]).is_file() for g in located)
    assert any(c["text"].strip() for g in located for c in g.get("context", ()))


def test_cov_report_is_the_same_from_json_and_cdb(cov_run):
    def summary(report):
        doc = json.loads(_pssc(cov_run, "cov", "report", report, "--json").stdout)
        assert doc["schema"] == "pssc.cov-report/1"
        return doc["summary"]
    s, c = summary("c.json"), summary("c.cdb")
    assert s == c
    assert 0 < s["statements"]["hit"] <= s["statements"]["total"]


def test_cov_lcov_names_files_that_exist(cov_run):
    _pssc(cov_run, "cov", "lcov", "c.json", "-o", "c.info", "--source-root", ".")
    files = [l[3:] for l in (cov_run / "c.info").read_text().splitlines()
             if l.startswith("SF:")]
    assert files and all((cov_run / f).is_file() for f in files)


@pytest.mark.parametrize("ext", ["json", "cdb"])
def test_cov_merge_of_a_run_with_itself_doubles_hits(cov_run, ext):
    from pssc.devenv import cov_cmd
    _pssc(cov_run, "cov", "merge", f"c.{ext}", f"c.{ext}", "-o", f"m.{ext}")

    def hits(name):
        rep = cov_cmd.load(str(cov_run / name))
        return {k: p["hits"] for t in ("statements", "arms")
                for k, p in rep[t].items()}
    one = hits(f"c.{ext}")
    assert any(one.values())
    assert hits(f"m.{ext}") == {k: 2 * n for k, n in one.items()}


def test_trace_fail_keeps_only_the_failures_trace(tmp_path):
    """T2.10: with a seeded bug, ``--trace fail`` keeps one trace per failing
    test and none for the rest; ``pssc trace show --at`` maps the failing
    check's (or sanitizer's) index to its source line."""
    tests = _copy(tmp_path)
    rel, old, new = SEEDED["driver_wrong_field"]
    path = tmp_path / rel
    path.write_text(path.read_text().replace(old, new, 1))
    rc, events = _run(tests, "--trace", "fail")
    ends = [e for e in events if e.get("event") == "test.end"]
    failed = [e for e in ends if e["status"] in ("fail", "error")]
    assert rc != 0 and failed and len(failed) < len(ends)
    assert all(e.get("trace_file") for e in failed)
    assert not any(e.get("trace_file") for e in ends if e not in failed)
    traces = tests / ".pssunit" / "traces"
    assert sorted(p.name for p in traces.iterdir()) == sorted(
        e["id"].replace("::", ".") + ".ndjson" for e in failed)

    trace = os.path.relpath(failed[0]["trace_file"], tests) if os.path.isabs(
        failed[0]["trace_file"]) else failed[0]["trace_file"]
    checks = json.loads(_pssc(tests, "trace", "show", trace, "--kind", "check",
                              "--kind", "sanitizer", "--json").stdout)["events"]
    bad = next(e for e in checks if e["detail"].get("verdict") == "fail")
    assert bad["file"] and bad["text"]
    doc = json.loads(_pssc(tests, "trace", "show", trace, "--at",
                           str(bad["index"]), "--json").stdout)
    assert (doc["file"], doc["line"], doc["from"]) == (bad["file"], bad["line"], bad["index"])
    (here,) = [c for c in doc["context"] if c["line"] == bad["line"]]
    assert here["text"].strip() == bad["text"]


def test_changed_reruns_only_the_edited_operations_tests_through_the_daemon(tmp_path):
    """T2.13: through the workspace daemon, ``--changed`` reuses every cached
    result when nothing changed, and an edit to one operation re-runs only the
    tests whose slice (catalog ``calls.units``) contains it."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'ws'\n")
    tests = _copy(tmp_path)
    run = tempfile.mkdtemp(prefix="pssd")   # a socket path is limited to ~107 bytes
    env = dict(os.environ, PSSC_DAEMON_DIR=run,
               PYTHONPATH=os.pathsep.join([str(REPO / "src"), os.environ.get("PYTHONPATH", "")]))
    env.pop("PSSC_NO_DAEMON", None)

    def pssc(*argv):
        return subprocess.run([sys.executable, "-m", "pssc", *argv], cwd=tests, env=env,
                              capture_output=True, text=True, timeout=180)

    def changed():
        p = pssc("test", "@model.f", ".", "--platform", "fake", "--targets", "none",
                 "--no-ctrf", "--json", "--changed")
        evs = [json.loads(l) for l in p.stdout.splitlines() if l.startswith("{")]
        assert p.returncode == 0, p.stderr[-2000:]
        return {e["id"] for e in evs if e.get("event") == "test.end"}

    try:
        cat = json.loads(pssc("test", "@model.f", ".", "--platform", "fake", "--targets",
                              "none", "--collect-only", "--json").stdout)
        op = "wb_dma_ch_c::check_completion"
        uses = {t["id"] for t in cat["tests"] if op in t["calls"]["units"]}
        assert 0 < len(uses) < len(cat["tests"])

        assert changed() == {t["id"] for t in cat["tests"]}
        assert changed() == set()
        st = json.loads(pssc("daemon", "status", "--json").stdout)
        assert st["root"] == str(tmp_path.resolve())

        src = tmp_path / "pss" / "wb_dma_ch_c" / "functions" / "check_completion.pss"
        text = src.read_text()
        old = "wb_dma_status_e status;"
        assert old in text
        src.write_text(text.replace(old, "wb_dma_status_e status = WB_DMA_PENDING;", 1))
        assert changed() == uses
        assert changed() == set()
    finally:
        p = pssc("daemon", "status", "--json")
        pid = (json.loads(p.stdout or "null") or {}).get("pid")
        pssc("daemon", "stop")
        if pid:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        shutil.rmtree(run, ignore_errors=True)
