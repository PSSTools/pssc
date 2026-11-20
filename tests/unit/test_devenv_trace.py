"""P2.10d: ``pssc trace show`` -- a trace's events with their source lines,
``--at IDX`` to an event's line in context, ``--line FILE:N`` to the events
that ran it, ``--json`` (pssc.trace-show/1)."""
from __future__ import annotations

import json
import pathlib

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.cli import main

from .test_pssunit_trace import SUITE

REPO = pathlib.Path(__file__).resolve().parents[2]
BAD = ".pssunit/traces/t_f.t_bad.ndjson"


@pytest.fixture(scope="module")
def project(tmp_path_factory):
    import os
    from pssc.testing import cmd
    d = tmp_path_factory.mktemp("trshow")
    (d / "sub").mkdir()
    (d / "sub" / "t.pss").write_text(SUITE)
    old = os.getcwd()
    mp = pytest.MonkeyPatch()
    mp.setattr(cmd, "LAST_FAILED", str(d / "lastfailed"))
    os.chdir(d)
    try:
        assert main(["test", "sub", "--targets", "none", "--no-ctrf",
                     "--seed", "5", "--trace", "all", "-q"]) == 1
    finally:
        os.chdir(old)
        mp.undo()
    return d


@pytest.fixture
def at(project, monkeypatch):
    monkeypatch.chdir(project)
    return project


def _show(capsys, *args):
    rc = main(["trace", "show", BAD, *args])
    out = capsys.readouterr()
    return rc, out


def _json(capsys, *args):
    rc, out = _show(capsys, *args, "--json")
    assert rc == 0, out.err
    doc = json.loads(out.out)
    assert doc["schema"] == "pssc.trace-show/1"
    return doc


def test_lists_events_with_source_lines(at, capsys):
    rc, out = _show(capsys)
    assert rc == 0
    head, *rows = out.out.splitlines()
    assert "t_f::t_bad" in head and "seed 5" in head
    check = [r for r in rows if " check " in r]
    assert len(check) == 1 and "sub/t.pss:22" in check[0]
    assert check[0].endswith('| rt.check_eq_int(twice(2), 5, "bad");')
    assert any("| return v + v;" in r for r in rows)


def test_json_events_and_filters(at, capsys):
    doc = _json(capsys)
    assert doc["test"] == "t_f::t_bad" and doc["seed"] == 5
    assert len(doc["events"]) == doc["count"] > 5
    assert [e["index"] for e in doc["events"]] == list(range(doc["count"]))
    calls = _json(capsys, "--kind", "call", "--kind", "return")["events"]
    assert calls and {e["kind"] for e in calls} == {"call", "return"}
    assert all(e["file"] is None and e["text"] is None for e in calls)
    c = calls[0]["coro"]
    assert {e["coro"] for e in _json(capsys, "--coro", str(c))["events"]} == {c}


def test_at_maps_an_index_to_its_line(at, capsys):
    events = _json(capsys)["events"]
    chk = next(e for e in events if e["kind"] == "check")
    doc = _json(capsys, "--at", str(chk["index"]), "--context", "1")
    assert doc["at"] == doc["from"] == chk["index"]
    assert (doc["file"], doc["line"]) == ("sub/t.pss", 22)
    assert [c["line"] for c in doc["context"]] == [21, 22, 23]
    # an event without a location maps to the last located one before it
    call = next(e for e in events if e["kind"] == "call")
    doc = _json(capsys, "--at", str(call["index"]))
    assert doc["from"] < call["index"] and doc["line"] == 22
    rc, out = _show(capsys, "--at", str(chk["index"]))
    assert rc == 0 and '  >   22 |         rt.check_eq_int(twice(2), 5, "bad");' in out.out


def test_line_lists_every_index_that_ran_it(at, capsys):
    doc = _json(capsys, "--line", "t.pss:22")
    assert len(doc["indexes"]) >= 2
    assert all(e["line"] == 22 for e in doc["events"])
    assert _json(capsys, "--line", "t.pss:2")["indexes"] == []
    rc, out = _show(capsys, "--line", "sub/t.pss:7")
    assert rc == 0 and "sub/t.pss:7: run at " in out.out


def test_sources_found_from_another_cwd(project, capsys, monkeypatch):
    monkeypatch.chdir(project.parent)
    rc = main(["trace", "show", str(project / BAD), "--kind", "check"])
    assert rc == 0
    assert capsys.readouterr().out.rstrip().endswith('"bad");')


def test_errors(at, capsys, tmp_path):
    rc, out = _show(capsys, "--at", "9999")
    assert rc == 2 and "no event 9999" in out.err
    (tmp_path / "x.ndjson").write_text('{"a": 1}\n')
    assert main(["trace", "show", str(tmp_path / "x.ndjson")]) == 2
    assert "not a zbc trace" in capsys.readouterr().err
    assert main(["trace", "show", "nope.ndjson"]) == 2
    with pytest.raises(SystemExit):
        main(["trace", "show", BAD, "--line", "t.pss"])
    with pytest.raises(SystemExit):
        main(["trace", "show", BAD, "--at", "1", "--line", "t.pss:1"])


def test_help_output_and_schema(capsys):
    assert main(["help", "trace", "show", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert "pssc.trace-show/1" in json.dumps(doc)
    s = json.loads((REPO / "docs/schemas/pssc.trace-show-1.json").read_text())
    assert s["$id"] == "pssc.trace-show/1"


SIM_TRACE = [
    {"zbc_trace": 2, "test": "tests_c::t_v", "seed": 0},
    {"seq": 0, "kind": "call", "coro": 0, "detail": {"fn": 1}, "t": 1000},
    {"seq": 1, "kind": "access", "coro": 0, "detail": {}, "t": 1234000000},
    {"seq": 2, "kind": "return", "coro": 0, "detail": {}, "t": 2000000000},
]


def test_at_a_time_in_a_sim_trace(tmp_path, capsys):
    """A simulation trace's events have ``t`` (fs): ``--at`` takes a time
    with a unit, the last event at or before it; rows show the time."""
    f = tmp_path / "s.ndjson"
    f.write_text("".join(json.dumps(d) + "\n" for d in SIM_TRACE))

    def at(t):
        assert main(["trace", "show", str(f), "--at", t, "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    assert at("1.234us")["at"] == 1 and at("1.234us")["at_time"] == 1234000000
    assert at("1233999.999ps")["at"] == 0
    assert at("2us")["at"] == 2 and at("1s")["at"] == 2
    assert at("1ps")["events"][0]["t"] == 1000
    assert at("1")["at"] == 1 and "at_time" not in at("1")
    assert main(["trace", "show", str(f), "--at", "999fs"]) == 2
    assert "no event at or before 999fs" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["trace", "show", str(f), "--at", "3 parsecs"])
    capsys.readouterr()
    assert main(["trace", "show", str(f)]) == 0
    out = capsys.readouterr().out
    assert "@1.234us" in out and "@1ps" in out and "@2us" in out, out


def test_at_a_time_needs_a_sim_trace(at, capsys):
    rc, out = _show(capsys, "--at", "1ns")
    assert rc == 2 and "no event times" in out.err


def test_a_binary_trace_shows_as_its_ndjson(at, capsys):
    """The binary form (``.pstrace``) of a trace shows exactly as the NDJSON
    trace it was made from."""
    from zuspec.be.bc.trace import binary
    pst = at / "bad.pstrace"
    pst.write_bytes(binary.from_ndjson((at / BAD).read_text()))
    for args in ((), ("--at", "3"), ("--kind", "check")):
        assert main(["trace", "show", BAD, *args, "--json"]) == 0
        want = json.loads(capsys.readouterr().out)
        assert main(["trace", "show", str(pst), *args, "--json"]) == 0
        got = json.loads(capsys.readouterr().out)
        assert got.pop("trace") == str(pst) and want.pop("trace") == BAD
        assert got == want


def test_a_c_trace_gets_its_lines_from_the_image(tmp_path, capsys):
    """A C engine's events carry only ``src_ref``: ``--image`` (or the
    header's ``image``) gives them their file and line."""
    import io
    from zuspec.be.bc.model import Prov, ZbcModel
    from zuspec.be.bc.trace import binary
    (tmp_path / "m.pss").write_text("a\nb\nc;\n")
    m = ZbcModel()
    m.profile = "codegen"
    m.prov.add(Prov(file="m.pss", line=3))
    (tmp_path / "m.zbc").write_bytes(m.to_bytes())

    def trace(name, **header):
        buf = io.BytesIO()
        w = binary.Writer(buf, test="t", **header)
        w.write({"seq": 0, "kind": "stmt", "coro": 0, "src_ref": 1,
                 "detail": {}})
        (tmp_path / name).write_bytes(buf.getvalue())
        return str(tmp_path / name)

    def show(*args):
        assert main(["trace", "show", *args, "--json"]) == 0
        return json.loads(capsys.readouterr().out)["events"][0]

    bare = trace("c.pstrace")
    assert show(bare)["file"] is None
    e = show(bare, "--image", str(tmp_path / "m.zbc"))
    assert (e["file"], e["line"], e["text"]) == ("m.pss", 3, "c;")
    assert show(trace("h.pstrace", image="m.zbc"))["line"] == 3
    assert main(["trace", "show", bare, "--image", str(tmp_path / "no.zbc")]) == 2
    assert "cannot read image" in capsys.readouterr().err
