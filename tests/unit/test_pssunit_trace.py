"""P2.10: test traces -- ``TraceWriter`` writes a run's events as be-bc
NDJSON v2 with source lines, and shares the session with ``--cov`` (a);
``pssc test --trace all [--trace-dir]`` writes one file per test (b);
``--trace fail`` re-runs a failure, traced, and keeps only that (c)."""
from __future__ import annotations

import io
import json

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from zuspec.be.bc.trace import schema

from pssc.cli import main
from pssc.testing.cmd import collect
from pssc.testing.coverage import Coverage
from pssc.testing.runner import Status, _build_image, run_one
from pssc.testing.trace import TraceWriter

SUITE = """\
import pssunit_pkg::*;

component t_f : fixture_c {
    rt_c rt;

    function int twice(int v) {
        return v + v;
    }

    @pssunit_pkg::test
    function void t_ok() {
        int x;
        x = twice(3);
        if (x == 6) {
            x = x + 1;
        }
        rt.check_eq_int(x, 7, "twice");
    }

    @pssunit_pkg::test
    function void t_bad() {
        rt.check_eq_int(twice(2), 5, "bad");
    }
}
"""


@pytest.fixture(scope="module")
def suite(tmp_path_factory):
    d = tmp_path_factory.mktemp("trace")
    (d / "t.pss").write_text(SUITE)
    coll = collect([str(d / "t.pss")])
    (item,) = [i for i in coll.items if i.name == "t_ok"]
    image = _build_image(coll.ctx, item.comp, (item.decl,), debug=True)
    assert image.error is None, image.error
    return coll, item, image


def _run(suite, cov=None):
    coll, item, image = suite
    buf = io.StringIO()
    w = TraceWriter(buf, image.model, test=item.qname, seed=4)
    r = run_one(image, coll.ctx, item, 4, cov=cov, trace=w)
    assert r.status is Status.PASS, r.detail
    return w, buf.getvalue()


def test_trace_is_bebc_ndjson_with_lines(suite):
    w, text = _run(suite)
    lines = text.splitlines()
    head = json.loads(lines[0])
    assert head["zbc_trace"] == schema.TRACE_SCHEMA_VERSION == 2
    assert head["test"].endswith("t_ok") and head["seed"] == 4
    # be-bc reads it back
    version, events = schema.parse(text)
    assert version == 2 and len(events) == w.count == len(lines) - 1
    kinds = {e.kind for e in events}
    assert {"stmt", "call", "return", "test.start", "test.end"} <= kinds
    recs = [json.loads(l) for l in lines[1:]]
    stmts = [d for d in recs if d["kind"] == "stmt"]
    assert stmts and all(d.get("file") and d.get("line") for d in stmts)
    # (pssunit_pkg's statements are in the trace too)
    src = SUITE.splitlines()
    ran = {src[d["line"] - 1].strip() for d in stmts
           if d["file"].endswith("t.pss")}
    assert "x = twice(3);" in ran and "x = x + 1;" in ran
    assert [d["seq"] for d in recs] == list(range(len(recs)))


def test_trace_and_coverage_share_the_session(suite):
    _, item, image = suite
    cov = Coverage(image.model)
    w, text = _run(suite, cov=cov)
    hit = [p for p in cov.stmts.values() if p["hits"]]
    assert hit, "coverage counted nothing"
    # the trace saw every statement coverage counted, and more kinds
    events = schema.parse(text)[1]
    assert sum(1 for e in events if e.kind == "stmt") >= sum(
        p["hits"] for p in cov.stmts.values())
    assert any(e.kind == "call" for e in events)


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "t.pss").write_text(SUITE)
    monkeypatch.chdir(tmp_path)
    return tmp_path


ARGV = ["test", "t.pss", "--targets", "none", "--no-ctrf", "--seed", "5"]


def test_trace_all_writes_a_file_per_test(project, capsys):
    d = project / "tr"
    assert main(ARGV + ["--json", "--trace", "all",
                        "--trace-dir", str(d)]) == 1
    ends = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    ends = {e["id"]: e for e in ends if e["event"] == "test.end"}
    assert len(ends) == 2
    for tid, e in ends.items():
        path = e["trace_file"]
        assert path.startswith(str(d)) and path.endswith(".ndjson")
        text = open(path).read()
        head = json.loads(text.splitlines()[0])
        assert head["test"] == tid and head["seed"] == 5
        version, events = schema.parse(text)
        assert version == 2 and any(ev.kind == "stmt" for ev in events)
    assert sorted(p.name for p in d.iterdir()) == sorted(
        tid.replace("::", ".") + ".ndjson" for tid in ends)


def test_trace_default_dir_and_failure_card(project, capsys):
    assert main(ARGV + ["--trace", "all"]) == 1
    out = capsys.readouterr().out
    files = sorted((project / ".pssunit" / "traces").iterdir())
    assert len(files) == 2
    bad = [f for f in files if "t_bad" in f.name][0]
    assert "  trace: %s" % bad.relative_to(project) in out


def test_trace_off_by_default_and_options_checked(project, capsys):
    assert main(ARGV) == 1
    assert not (project / ".pssunit" / "traces").exists()
    capsys.readouterr()
    assert main(ARGV + ["--trace", "all", "--no-debug"]) != 1
    assert "--trace needs the debug image" in capsys.readouterr().err
    assert main(ARGV + ["--trace-dir", "x"]) != 1
    assert "--trace-dir needs --trace" in capsys.readouterr().err
    assert not (project / "x").exists()


@pytest.mark.parametrize("jobs", ["1", "2"])
def test_trace_fail_keeps_only_the_failure(project, capsys, jobs):
    assert main(ARGV + ["--json", "--trace", "fail", "-j", jobs]) == 1
    ends = [json.loads(l) for l in capsys.readouterr().out.splitlines()]
    ends = {e["id"].split("::")[-1]: e for e in ends
            if e["event"] == "test.end"}
    assert "trace_file" not in ends["t_ok"]
    bad = ends["t_bad"]
    assert bad["status"] == "fail" and "trace_flaky" not in bad
    files = list((project / ".pssunit" / "traces").iterdir())
    assert [str(f.relative_to(project)) for f in files] == [bad["trace_file"]]
    version, events = schema.parse(files[0].read_text())
    assert any(e.kind == "check" or e.kind == "stmt" for e in events)
    assert json.loads(files[0].read_text().splitlines()[0])["seed"] == 5


def test_trace_fail_rerun_that_passes_is_flaky(project, capsys, monkeypatch):
    from pssc.testing import runner
    real = runner.run_one

    def run_one(*a, trace=None, **kw):
        r = real(*a, trace=trace, **kw)
        if trace is not None:      # the traced re-run "passes"
            r.status = Status.PASS
        return r

    monkeypatch.setattr(runner, "run_one", run_one)
    assert main(ARGV + ["--trace", "fail", "-k", "t_bad"]) == 1
    out = capsys.readouterr().out
    assert "FAIL" in out and "flaky" in out
    assert "  trace: .pssunit/traces/t_f.t_bad.ndjson" in out
