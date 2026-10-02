"""bc on a scenario that grows: a pssc-owned job pipeline (``model/jobs.pss``).

The model holds the constructs a real IO model uses -- a compound of four
stages chained by bound buffers, a state prerequisite, per-component pools
with locks, a write->read flow through a pool, and a ``parallel`` of
streams -- so it is what tells a change that makes bc slower than linear,
or wrong, at scale (nvme-bench plan B6d, D-B1).

Every run is checked against the model's own rules (``_check_jobs``,
``_check_rw``). The timing tests are marked ``perf`` and run only when asked
(``pytest -m perf tests/perf``): a generous bound, and time growing linearly
with the size.
"""
import os
import re
import tempfile
import time
from collections import defaultdict

import pytest

import pssc
from pssc.ast2ir import AstToIrTranslator
from zuspec.ir.core.xf import PSSToScenarioPass
from zuspec.be.bc.lower import lower_module
from zuspec.be.bc.interp import NativeBlobBackend, run_model

pytestmark = pytest.mark.filterwarnings("ignore::UserWarning")

_MODEL = os.path.join(os.path.dirname(__file__), "model", "jobs.pss")
_FIELD = re.compile(r"(\w+)=(\d+)")


def _model(export, n):
    """The export compiled with ``JOBS_N = n``, set by a file parsed first."""
    fd, consts = tempfile.mkstemp(suffix=".pss")
    try:
        os.write(fd, b"const int JOBS_N = %d;\n" % n)
        os.close(fd)
        parser = pssc.Parser()
        parser.parse([consts, _MODEL])
        linked = parser.link()
        ctx = AstToIrTranslator().translate(linked, files=parser.file_map)
    finally:
        os.unlink(consts)
    assert not ctx.errors, ctx.errors
    module = PSSToScenarioPass(root="pss_top", exports=[export]).lower(ctx)
    return lower_module(module, entry_action=export, solve_unconstrained=True)


def _run(model, seed):
    lines = []
    t = time.perf_counter()
    run_model(model, seed=seed, solve_backend=NativeBlobBackend(), out=lines.append)
    return lines, time.perf_counter() - t


def _events(lines):
    for ln in lines:
        words = ln.split()
        if words:
            yield words[0], words[1], {k: int(v) for k, v in _FIELD.findall(ln)}


def _check_jobs(lines, expect, parallel):
    """Every job passes its four stages in order, its values obey job_s, and
    no slot or DMA engine is used by two streams of a parallel."""
    ev = list(_events(lines))
    assert ev and ev[0][0] == "LINK" and ev[0][2]["speed"] in (1, 2, 4)
    stages = defaultdict(list)
    jobs = {}
    for kind, what, f in ev[1:]:
        assert kind == "JOB"
        stage = what.split("=")[1]
        stages[f["id"]].append(stage)
        if stage == "accept":
            jobs[f["id"]] = f
        elif stage == "move":
            jobs[f["id"]]["dma"] = f["dma"]
    assert len(jobs) == expect
    for jid, seq in stages.items():
        assert seq == ["accept", "move", "finish", "retire"], (jid, seq)
    users = defaultdict(set)
    for jid, j in jobs.items():
        assert j["port"] < 4 and j["slot"] < 8 and j["lane"] == j["slot"] // 2
        assert j["unit"] in (64, 256) and 1 <= j["len"] <= 128
        assert j["bytes"] == j["len"] * j["unit"] <= 16384
        assert j["kind"] != 2 or j["len"] == 1
        assert j["kind"] != 0 or j["bytes"] <= 1024
        assert j["kind"] != 1 or j["bytes"] > 1024
        assert j["dma"] < 2
        if parallel:
            users[("slot", j["port"], j["slot"])].add(jid >> 16)
            users[("dma", j["port"], j["dma"])].add(jid >> 16)
    for key, streams in users.items():
        assert len(streams) == 1, (key, streams)
    return jobs


def _check_rw(lines, n):
    """Each read holds the object a write left in the pool of the port it
    runs on; while unread writes remain, none is read twice."""
    writes, reads = {}, []
    for kind, what, f in _events(lines):
        assert kind == "DATA"
        if what == "write":
            writes[f["id"]] = (f["port"], f["len"])
        else:
            reads.append(f)
    assert len(writes) == len(reads) == n // 2
    for r in reads:
        assert writes[r["id"]] == (r["port"], r["len"])
        assert r["comp"] == r["port"] and r["off"] < r["len"]
    assert len({r["id"] for r in reads}) == len(reads)


@pytest.mark.parametrize("seed", range(1, 6))
def test_jobs_seq(seed):
    lines, _ = _run(_model("jobs_seq", 40), seed)
    jobs = _check_jobs(lines, 40, parallel=False)
    assert sorted(jobs) == list(range(1, 41))


@pytest.mark.parametrize("seed", range(1, 6))
def test_jobs_par(seed):
    lines, _ = _run(_model("jobs_par", 40), seed)
    _check_jobs(lines, 40, parallel=True)


@pytest.mark.parametrize("seed", range(1, 6))
def test_jobs_rw(seed):
    lines, _ = _run(_model("jobs_rw", 40), seed)
    _check_rw(lines, 40)


# -- timing -----------------------------------------------------------------------

#: generous: about 30x what a run takes on a laptop core today
_BOUND_S = 10.0


@pytest.mark.perf
@pytest.mark.parametrize("export", ["jobs_seq", "jobs_par", "jobs_rw"])
def test_time_grows_linearly(export):
    """At 4x the size, a run takes at most 8x the time (linear is 4x; a
    quadratic step would show as 16x), and N = 200 is within the bound."""
    times = {}
    for n in (200, 800):
        model = _model(export, n)
        _run(model, 1)                       # warm: imports, first compiles
        times[n] = min(_run(model, s)[1] for s in (2, 3))
    assert times[200] < _BOUND_S, times
    assert times[800] < 8 * times[200], times
