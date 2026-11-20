"""T2.2 (breakpoints): ``file:line`` -> pc breaks on a debug-lowered image,
and conditions, hit counts and logpoints decided over the engine's stub."""

from __future__ import annotations

import dataclasses
import types

import pytest

from pssc import driver
from pssc.devenv.debug.breakpoints import (BreakpointSet, LineIndex, interpolate,
                                           parse_hit_condition)
from pssc.testing import bc_session

MODEL = """\
component dev_c {
    function int sum(int n) {
        int acc;
        acc = 0;

        // add the even ones
        repeat (i : n) {
            if (i % 2 == 0) {
                acc += i;
            }
        }
        return acc;
    }

    function int twice(int n) {
        return sum(n) + sum(n);
    }
}
"""


@pytest.fixture(scope="module")
def model(tmp_path_factory):
    p = tmp_path_factory.mktemp("bp") / "dev.pss"
    p.write_text(MODEL)
    c = driver.translate([str(p)])
    assert not c.errors
    return bc_session.lower(c, "dev_c", functions=["*"], debug=True)


def _sum(model):
    return model.entries["dev_c::sum"].coro


@pytest.mark.parametrize("line, at", [
    (3, 3), (4, 4), (7, 7), (8, 8), (9, 9), (12, 12),
    (5, 7), (6, 7),             # blank, comment: the next statement
    (10, 12), (11, 12),         # closing braces
])
def test_a_line_resolves_to_the_next_statement(model, line, at):
    [bp] = BreakpointSet(model).set_file("dev.pss", [{"line": line}])
    assert bp.verified and bp.resolved_line == at
    assert [c for c, _ in bp.locations] == [_sum(model)]


@pytest.mark.parametrize("line", [1, 2, 13, 14, 17, 18, 40])
def test_a_line_outside_every_coroutine_is_unverified(model, line):
    [bp] = BreakpointSet(model).set_file("dev.pss", [{"line": line}])
    assert not bp.verified and not bp.locations
    assert f"dev.pss:{line}" in bp.message


def test_a_file_not_in_the_image_is_unverified(model):
    [bp] = BreakpointSet(model).set_file("other.pss", [{"line": 3}])
    assert not bp.verified and "no statements from other.pss" in bp.message


def test_the_file_matches_by_absolute_path_or_suffix(model):
    path = next(iter(LineIndex(model).starts))
    for name in (path, "dev.pss", "bp0/dev.pss".replace("bp0", path.split("/")[-2])):
        assert BreakpointSet(model).set_file(name, [{"line": 4}])[0].verified, name
    assert not BreakpointSet(model).set_file("x/dev.pss", [{"line": 4}])[0].verified


def test_each_statement_location_is_a_marker(model):
    bs = BreakpointSet(model)
    bs.set_file("dev.pss", [{"line": ln} for ln in (3, 4, 7, 8, 9, 12, 16)])
    marks = {(ci, pc) for ci, c in enumerate(model.coros) for pc, _ in c.stmts}
    got = {(b["coro"], b["pc"]) for b in bs.pc_breaks()}
    assert got == marks


def test_set_file_replaces_that_files_set(model):
    bs = BreakpointSet(model)
    bs.set_file("dev.pss", [{"line": 4}, {"line": 9}])
    [bp] = bs.set_file("dev.pss", [{"line": 12}])
    assert [b.line for b in bs.all()] == [12]
    assert [b["id"] for b in bs.pc_breaks()] == [bp.id]


def _fake_model(stmts_by_coro):
    """A model-shaped object: coroutine i has statements ``[(pc, file, line)]``."""
    entries = [types.SimpleNamespace(file="", line=0, line_end=0)]
    coros = []
    for stmts in stmts_by_coro:
        cs = []
        for pc, f, ln in stmts:
            entries.append(types.SimpleNamespace(file=f, line=ln, line_end=ln))
            cs.append((pc, len(entries) - 1))
        coros.append(types.SimpleNamespace(stmts=cs))
    return types.SimpleNamespace(prov=types.SimpleNamespace(entries=entries), coros=coros)


def test_an_inlined_line_breaks_in_every_coroutine():
    # f's lines 3..4 inlined into two actions' coroutines
    m = _fake_model([[(0, "a.pss", 10), (5, "f.pss", 3), (9, "f.pss", 4)],
                     [(0, "a.pss", 20), (7, "f.pss", 3), (12, "f.pss", 4)]])
    [bp] = BreakpointSet(m).set_file("f.pss", [{"line": 4}])
    assert bp.locations == [(0, 9), (1, 12)]


@pytest.mark.parametrize("text, stops", [
    ("3", [3]), ("== 3", [3]), (">=4", [4, 5, 6]), ("> 4", [5, 6]),
    ("<= 2", [1, 2]), ("<2", [1]), ("% 2", [2, 4, 6]),
])
def test_hit_conditions(text, stops):
    f = parse_hit_condition(text)
    assert [h for h in range(1, 7) if f(h)] == stops


@pytest.mark.parametrize("text", ["", "x", ">= ", "% 0", "!= 2"])
def test_a_bad_hit_condition_is_refused(model, text):
    with pytest.raises(ValueError):
        parse_hit_condition(text)
    if text:
        [bp] = BreakpointSet(model).set_file("dev.pss", [{"line": 4, "hit_condition": text}])
        assert not bp.verified and "hit condition" in bp.message


def test_interpolate():
    vals = {"i": 4, "acc": 6}

    def ev(e):
        if e not in vals:
            raise NameError(f"unknown identifier '{e}'")
        return vals[e]
    assert interpolate("i={i} acc={ acc }", ev) == "i=4 acc=6"
    assert interpolate("{{i}} {nope}", ev) == "{i} <error: unknown identifier 'nope'>"
    assert interpolate("{i}", None) == "<error: no evaluator>"


# -- over the engine's stub ---------------------------------------------------

def _start(model, n):
    """A stub attached to ``twice(n)``."""
    from zuspec.be.bc.interp.debug import Stub
    from zuspec.be.bc.interp.vm import VM
    m = dataclasses.replace(model, entry_coro=model.entries["dev_c::twice"].coro)
    vm = VM(m)
    root = vm.root_frame(m.entry_coro, seed=0)
    root.args = [n]
    if root.cobj is not None:
        vm.construct_components()
    stub = Stub(vm, root)
    assert stub.request({"cmd": "attach", "args": {"version": 1}})["ok"]
    return stub


def _locals(stub, frame):
    rep = stub.request({"cmd": "read_frame", "args": {"frame": frame}})
    return {v["name"]: v["value"] for v in rep["body"]["locals"]}


def _run(stub, bs):
    """Continue to the end; ``(stops, logs)``: per user stop, ``(ids, line,
    locals)``. The evaluator here reads a local by name (eval.py is P2.2e)."""
    assert bs.apply(stub)["ok"]
    stops, logs = [], []
    while True:
        assert stub.request({"cmd": "continue"})["ok"]
        ev = stub.next_event()
        if ev["event"] == "exited":
            assert ev["body"]["status"] == "ok"
            return stops, logs
        body = ev["body"]
        assert body["reason"] == "breakpoint"
        local = _locals(stub, body["frame"])
        ids, out = bs.on_stop(body["hits"], lambda e: local[e])
        logs += out
        if ids:
            line = next(b.resolved_line for b in bs.all() if b.id in ids)
            stops.append((ids, line, local))


def test_a_breakpoint_stops_at_every_pass(model):
    stub = _start(model, 3)
    bs = BreakpointSet(model)
    [bp] = bs.set_file("dev.pss", [{"line": 9}])
    stops, _ = _run(stub, bs)
    # i = 0, 2 in each of the two calls
    assert [s[2]["i"] for s in stops] == [0, 2, 0, 2]
    assert all(s[:2] == ([bp.id], 9) for s in stops) and bp.hits == 4


def test_a_condition_filters_stops(model):
    stub = _start(model, 5)
    bs = BreakpointSet(model)
    [bp] = bs.set_file("dev.pss", [{"line": 8, "condition": "i"}])
    stops, _ = _run(stub, bs)
    assert [s[2]["i"] for s in stops] == [1, 2, 3, 4] * 2
    assert bp.hits == 8


def test_a_failing_condition_stops_and_says_why(model):
    stub = _start(model, 1)
    bs = BreakpointSet(model)
    [bp] = bs.set_file("dev.pss", [{"line": 12, "condition": "nope"}])
    stops, _ = _run(stub, bs)
    assert len(stops) == 2 and "condition 'nope'" in bp.message and bp.hits == 0


def test_a_hit_condition_counts_passes(model):
    stub = _start(model, 4)
    bs = BreakpointSet(model)
    bs.set_file("dev.pss", [{"line": 8, "hit_condition": "% 3"}])
    stops, _ = _run(stub, bs)
    # 8 passes over line 8 (i = 0..3, twice): the 3rd and 6th
    assert [s[2]["i"] for s in stops] == [2, 1]


def test_a_logpoint_logs_and_goes_on(model):
    stub = _start(model, 3)
    bs = BreakpointSet(model)
    bs.set_file("dev.pss", [{"line": 12, "log_message": "acc={acc} n={n} {z}"},
                            {"line": 4}])
    stops, logs = _run(stub, bs)
    assert [s[1] for s in stops] == [4, 4]
    assert logs == ["acc=2 n=3 <error: 'z'>"] * 2
