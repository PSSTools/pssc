"""T-G4: every kind of failure names its place as ``file:line:col``.

One case per kind, each through the path a user meets it on (``pssc test``
where it can be reached there): a check and a require (the failure card), the
three sanitizers, std_pkg ``error`` and ``fatal``, a VM error, an import the
platform does not implement, a gate refusal, a parse error and a discovery
error.
"""
from __future__ import annotations

import contextlib
import io

import pytest

pytest.importorskip("pssparser")
pytest.importorskip("zuspec.be.bc")

from pssc.cli import main

from . import test_pssunit_gate as gate_t
from . import test_pssunit_sanitizers as san_t

KINDS = """\
import std_pkg::*;
import pssunit_pkg::*;
function int ext_fn(int x);
import target function ext_fn;
component k_f : fixture_c {
    rt_c rt;
    @pssunit_pkg::test
    function void t_check() {
        check_eq(1 + 1, 3, "sum");
    }
    @pssunit_pkg::test
    function void t_require() {
        require(1 == 2, "pre");
        check(true);
    }
    @pssunit_pkg::test
    function void t_error() {
        error("err %d", 1);
    }
    @pssunit_pkg::test
    function void t_fatal() {
        fatal(2, "fat %d", 2);
    }
    @pssunit_pkg::test
    function void t_vm() {
        int z;
        z = 0;
        rt.check_eq_int(4 / z, 0, "div");
    }
    @pssunit_pkg::test
    function void t_import() {
        rt.check_eq_int(ext_fn(1), 0, "imp");
    }
}
"""

PARSE = """\
import pssunit_pkg::*;
component p_f : fixture_c {
    @pssunit_pkg::test
    function void t_a() {
        int x = ;
    }
}
"""


def _at(src: str, text: str, offset: int = 0) -> str:
    """``line:col`` (1-based) of the first ``text`` in ``src``, plus
    ``offset`` columns."""
    i = src.index(text) + offset
    return "%d:%d" % (src[:i].count("\n") + 1, i - src.rfind("\n", 0, i))


def _run(*argv, lastfailed):
    # Module-scoped fixtures run before conftest's autouse redirect of the
    # ``--lf`` record, so every run redirects it itself.
    from pssc.testing import cmd
    out, err = io.StringIO(), io.StringIO()
    with pytest.MonkeyPatch.context() as mp, \
            contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        mp.setattr(cmd, "LAST_FAILED", str(lastfailed))
        rc = main(["test", *argv, "--no-ctrf"])
    return rc, out.getvalue(), err.getvalue()


def _section(out: str, test: str) -> str:
    """The report's block for ``test`` (from its FAIL/ERROR header on)."""
    for head in ("FAIL ", "ERROR "):
        i = out.find("\n" + head + test + "\n")
        if i >= 0:
            j = out.find("\n\n", i + 1)
            return out[i:j if j >= 0 else None]
    raise AssertionError(f"no failure block for {test}:\n{out}")


@pytest.fixture(scope="module")
def kinds(tmp_path_factory):
    p = tmp_path_factory.mktemp("kinds") / "kinds.pss"
    p.write_text(KINDS)
    rc, out, _ = _run(str(p), "--targets", "none",
                     lastfailed=p.parent / "lastfailed")
    assert rc == 1
    return p, out


@pytest.mark.parametrize("test,anchor,says", [
    ("t_check", 'check_eq(1 + 1', "FAIL {loc}  k_f::t_check"),
    ("t_require", 'require(1 == 2', "FAIL {loc}  k_f::t_require"),
    ("t_error", 'error("err', "{loc}: error: err 1"),
    ("t_fatal", 'fatal(2,', "{loc}: fatal (status 2): fat 2"),
    ("t_vm", 'rt.check_eq_int(4 / z', "{loc}: division by zero"),
])
def test_a_run_failure_is_located(kinds, test, anchor, says):
    p, out = kinds
    loc = f"{p}:{_at(KINDS, anchor)}"
    assert says.format(loc=loc) in _section(out, "k_f::" + test)


def test_an_unimplemented_import_is_located(kinds):
    # ``pssc test`` gives unimplemented imports 0, so the test passes there;
    # a platform that does not stand in for them stops at the call.
    import pssc.testing.vm as pv
    from zuspec.be.bc.interp import VMError
    p, out = kinds
    assert "t_import ." in out and "k_f::t_import\n" not in out
    s = pv.load([p], root="k_f", platform=pv.Platform())
    with pytest.raises(VMError) as e:
        s.run_test("k_f::t_import")
    assert e.value.location == f"{p}:{_at(KINDS, 'rt.check_eq_int(ext_fn')}"
    assert "does not implement import ext_fn" in str(e.value)


@pytest.fixture(scope="module")
def sanitized(tmp_path_factory):
    p = tmp_path_factory.mktemp("san") / "san.pss"
    p.write_text(san_t.SUITE)
    rc, out, _ = _run(str(p), "--targets", "none", "--platform", "fake",
                     lastfailed=p.parent / "lastfailed")
    assert rc == 1
    return p, out


@pytest.mark.parametrize("test,name,anchor", [
    ("t_spin", "poll-budget", "while (regs.st"),
    ("t_rules", "access-rule", "regs.id.write_val(1);"),
    ("t_hole", "unmapped", "write32(make_handle_from_handle(0, 0x1008)"),
])
def test_a_sanitizer_finding_is_located(sanitized, test, name, anchor):
    p, out = sanitized
    loc = f"{p}:{_at(san_t.SUITE, anchor)}"
    assert f"sanitizer {name} at {loc}:" in _section(out, "s_f::" + test)


def test_a_gate_refusal_is_located(tmp_path):
    d = gate_t._project(tmp_path, gate_t.DUT_REFUSED, gate_t.TESTS)
    rc, _, err = _run(d, lastfailed=tmp_path / "lastfailed")
    assert rc == 2
    at = _at(gate_t.DUT_REFUSED, "target function int sum")
    assert f"{tmp_path}/src/dut.pss:{at}:" in err


def test_a_parse_error_is_located(tmp_path):
    p = tmp_path / "parse.pss"
    p.write_text(PARSE)
    rc, _, err = _run(str(p), "--targets", "none",
                      lastfailed=tmp_path / "lastfailed")
    assert rc == 2
    at = _at(PARSE, "int x = ;", offset=len("int x = "))
    assert f"{p}:{at}: error: unexpected ';'" in err


def test_a_discovery_error_is_located():
    # The front end rejects an unknown base before discovery sees it, so the
    # model is built by hand (as test_pssunit_catalog does).
    from pssc.testing.catalog import build
    from pssc.testing.discover import DiscoveryError

    class Loc:
        file, line, pos = "x.pss", 4, 5

    class T:
        def __init__(self, sup, loc=None):
            self.super = type("R", (), {"ref_name": sup})() if sup else None
            self.loc, self.is_abstract = loc, False

    ctx = type("C", (), {})()
    ctx.type_map = {"c_c": T(None), "c_c::t_a": T("tset_a", Loc())}
    ctx.parent_comp_names = {"c_c::t_a": "c_c"}
    with pytest.raises(DiscoveryError) as e:
        build(ctx)
    assert str(e.value).startswith("x.pss:4:5: cannot resolve type 'tset_a'")
