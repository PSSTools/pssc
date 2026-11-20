"""T1.5: a run that stops reports where, as ``file:line:col`` (part a); a
debug image keeps what that needs, and the PSS sources, through write and
read (part b).

A ``VMError`` out of the bytecode VM (a divide by zero, say) and a PSS
``error()``/``fatal()`` carry the ``src_ref`` of the statement that raised
them, resolved through the image's provenance table; ``pssc test`` puts the
location in front of the failure's message.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc import driver
from pssc.testing import bc_session, sources

MODEL = """\
import std_pkg::*;
component dev_c {
    function int div(int a, int b) {
        int q;
        q = a / b;
        return q;
    }
    function void boom(int v) {
        error("boom %d", v);
    }
    function void doom() {
        fatal(2, "doom");
    }
}
"""


@pytest.fixture(scope="module")
def path(tmp_path_factory):
    p = tmp_path_factory.mktemp("loc") / "loc.pss"
    p.write_text(MODEL)
    return p


@pytest.fixture(scope="module")
def ctx(path):
    c = driver.translate([str(x) for x in sources.all_sources([str(path)])])
    assert not c.errors
    return c


def _raise(ctx, debug, fn, *args, reread=False):
    from zuspec.be.bc.interp import VMError, oracle
    model = bc_session.lower(ctx, "dev_c", functions=["*"], debug=debug)
    if reread:
        model = oracle.roundtrip(model)
    s = bc_session.Session.load(model)
    with pytest.raises(VMError) as ei:
        s.call(s.construct(), fn, *args)
    return ei.value


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("fn,args,line,col,msg", [
    ("div", (1, 0), 5, 9, "division by zero"),
    ("boom", (7,), 9, 9, "error: boom 7"),
    ("doom", (), 12, 9, "fatal (status 2): doom"),
])
def test_a_run_error_is_located(ctx, path, debug, fn, args, line, col, msg):
    e = _raise(ctx, debug, fn, *args)
    assert str(e) == msg
    assert e.location == f"{path}:{line}:{col}"


@pytest.mark.parametrize("fn,args,line", [("div", (1, 0), 5), ("boom", (7,), 9)])
def test_a_read_back_debug_image_still_locates(ctx, path, fn, args, line):
    e = _raise(ctx, True, fn, *args, reread=True)
    assert e.location == f"{path}:{line}:9"


def test_a_debug_image_carries_the_sources(ctx, path):
    from zuspec.be.bc.model import ZbcModel
    model = bc_session.lower(ctx, "dev_c", functions=["*"], debug=True)
    assert model.profile == "debug"
    back = ZbcModel.from_bytes(model.to_bytes())
    assert back.profile == "debug"
    assert back.sources[str(path)] == MODEL
    assert back.prov == model.prov
    assert [c.stmts for c in back.coros] == [c.stmts for c in model.coros]


def test_a_release_image_has_no_sources(ctx):
    from zuspec.be.bc.model import ZbcModel
    model = bc_session.lower(ctx, "dev_c", functions=["*"])
    assert model.profile == "codegen" and not model.sources
    assert not ZbcModel.from_bytes(model.to_bytes()).sources


SUITE = """\
import std_pkg::*;
import pssunit_pkg::*;
component t_f : fixture_c {
    rt_c rt;
    @pssunit_pkg::test
    function void t_div() {
        int z;
        z = 0;
        rt.check_eq_int(4 / z, 0, "div");
    }
    @pssunit_pkg::test
    function void t_err() {
        error("stopped at %d", 3);
    }
}
"""


def test_pssc_test_prints_the_location(capsys, tmp_path):
    from pssc.cli import main
    p = tmp_path / "suite.pss"
    p.write_text(SUITE)
    rc = main(["test", str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert f"{p}:9:" in out and "division by zero" in out
    assert f"{p}:13:9: error: stopped at 3" in out


def test_a_debug_image_carries_its_identity(ctx):
    from zuspec.be.bc.model import ZbcModel
    model = bc_session.lower(ctx, "dev_c", functions=["*"], debug=True, generation=3)
    assert model.api_hash
    back = ZbcModel.from_bytes(model.to_bytes())
    assert (back.api_hash, back.generation) == (model.api_hash, 3)
    again = bc_session.lower(ctx, "dev_c", functions=["*"], debug=True)
    assert again.api_hash == model.api_hash
    release = bc_session.lower(ctx, "dev_c", functions=["*"])
    assert release.api_hash == model.api_hash
    assert ZbcModel.from_bytes(release.to_bytes()).api_hash == 0


def test_the_api_hash_follows_signatures(ctx, tmp_path):
    def lowered(text):
        p = tmp_path / f"m{abs(hash(text))}.pss"
        p.write_text(text)
        c = driver.translate([str(x) for x in sources.all_sources([str(p)])])
        assert not c.errors
        return bc_session.lower(c, "dev_c", functions=["*"], debug=True).api_hash

    base = bc_session.lower(ctx, "dev_c", functions=["*"], debug=True).api_hash
    body = MODEL.replace("q = a / b;", "q = a / b + 0;")
    assert lowered(body) == base
    sig = MODEL.replace("int div(int a, int b)", "int div(int a, bit[8] b)")
    assert lowered(sig) != base
