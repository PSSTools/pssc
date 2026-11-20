"""`pssc build --image` (P3.2a): the debug image file and what it carries."""

from __future__ import annotations

import json

import pytest

from pssc import cli
from pssc.devenv import build_cmd

SUITE = """\
import addr_reg_pkg::*;
import pssunit_pkg::*;

package s_pkg {
    import addr_reg_pkg::*;
    pure component rw_c : reg_c<bit[32], READWRITE, 32> {}
    pure component grp_c : reg_group_c {
        rw_c st;
        pure function bit[64] get_offset_of_instance(string name) {
            match (name) {
                ["st"]: return 0x0;
                default: return -1;
            }
        }
        pure function bit[64] get_offset_of_instance_array(string name, int index) {
            match (name) { default: return -1; }
        }
    }
}

component s_f : fixture_c {
    s_pkg::grp_c regs;
    function void place() { regs.set_handle(make_handle_from_handle(0, 0x1000)); }
    @pssunit_pkg::test
    function void t_wr() {
        place();
        regs.st.write_val(1);
    }
    @pssunit_pkg::allow {.what = "access-rule"}
    @pssunit_pkg::test
    function void t_rd() {
        place();
        check_eq(regs.st.read_val(), 0);
    }
}
"""

OTHER = """\
import pssunit_pkg::*;
component o_f : fixture_c {
    @pssunit_pkg::test
    function void t_o() { }
}
"""


@pytest.fixture
def suite(tmp_path):
    (tmp_path / "s.pss").write_text(SUITE)
    return tmp_path


def _build(*argv):
    return cli.main(["build", *argv])


def test_build_writes_a_debug_image(suite, capsys):
    out = suite / "out" / "s.zbc"
    assert _build(str(suite / "s.pss"), "--image", str(out),
                  "--generation", "3", "--json") == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["schema"] == build_cmd.SCHEMA
    assert doc["root"] == "s_f"
    assert doc["tests"] == ["s_f::t_wr", "s_f::t_rd"]
    assert doc["generation"] == 3

    model, meta = build_cmd.read(str(out))
    assert model.profile == "debug"
    assert model.generation == 3 and model.api_hash != 0
    assert doc["api_hash"] == f"{model.api_hash:016x}"
    # stable-symbol entries for the test functions
    assert {"s_f::t_wr", "s_f::t_rd"} <= set(model.entries)
    assert any(p.endswith("s.pss") for p in model.sources)
    assert model.sources[next(iter(model.sources))] == SUITE
    # the line table survived the file
    assert any(c.stmts for c in model.coros)

    cat = meta[build_cmd.CATALOG]
    assert [t["id"] for t in cat["tests"]] == ["s_f::t_wr", "s_f::t_rd"]
    assert cat["tests"][0]["line"] is not None
    img = meta[build_cmd.IMAGE]
    assert img["root"] == "s_f" and img["schema"] == build_cmd.SCHEMA
    assert "access-rule" in img["allows"]["s_f::t_rd"]
    regs = meta[build_cmd.REGISTERS]
    (reg,) = regs["registers"].values()
    assert reg["width"] == 32
    assert any(c["kind"] == "check_eq" for c in regs["checks"].values())


def test_build_is_reproducible_and_no_sources_drops_them(suite, capsys):
    a, b, c = (str(suite / n) for n in ("a.zbc", "b.zbc", "c.zbc"))
    src = str(suite / "s.pss")
    assert _build(src, "--image", a) == 0
    assert _build(src, "--image", b) == 0
    with open(a, "rb") as fa, open(b, "rb") as fb:
        assert fa.read() == fb.read()
    assert _build(src, "--image", c, "--no-sources") == 0
    model, _ = build_cmd.read(c)
    assert model.sources == {}
    assert "wrote" in capsys.readouterr().out


def test_several_roots_need_root(suite, capsys):
    (suite / "o.pss").write_text(OTHER)
    out = str(suite / "x.zbc")
    assert _build(str(suite), "--image", out) == 2
    err = capsys.readouterr().err
    assert "--root" in err and "s_f" in err and "o_f" in err
    assert _build(str(suite), "--image", out, "--root", "o_f") == 0
    _, meta = build_cmd.read(out)
    assert [t["id"] for t in meta[build_cmd.CATALOG]["tests"]] == ["o_f::t_o"]
    assert _build(str(suite), "--image", out, "--root", "nope") == 2


MIXED = """\
import pssunit_pkg::*;
component m_f : fixture_c {
    action a_t : test_a {
        rand bit[8] v;
        constraint v == 7;
        exec body { check_eq(v, 7); }
    }
    @pssunit_pkg::test
    function void t_bad() { check_eq(1, 2); }
    @pssunit_pkg::test
    function void t_ok() { check_eq(2, 2); }
}
"""


def _verdicts(image, ctx, items):
    from pssc.testing import runner

    return {it.qname: (runner.run_one(image, ctx, it, seed=1).status.name)
            for it in items}


@pytest.mark.parametrize("src", ["suite", "mixed"])
def test_an_image_read_back_runs_without_the_sources(tmp_path, src):
    """P3.2b1: ``build_cmd.load`` gives a runnable image and its test items
    with no IR context; each test's verdict is the in-memory one."""
    from pssc.testing import cmd as _cmd, runner

    (tmp_path / "m.pss").write_text(SUITE if src == "suite" else MIXED)
    coll = _cmd.collect([str(tmp_path / "m.pss")])
    model, root = build_cmd.build(coll)
    out = tmp_path / "m.zbc"
    out.write_bytes(model.to_bytes())

    image, items, meta = build_cmd.load(str(out))
    assert [it.qname for it in items] == \
        [it.qname for it in coll.items if it.comp == root]
    fresh = runner._build_image(
        coll.ctx, root, [it.decl for it in items if it.kind == "function"])
    want = _verdicts(fresh, coll.ctx, items)
    assert _verdicts(image, None, items) == want
    if src == "mixed":
        assert want == {"m_f::a_t": "PASS", "m_f::t_bad": "FAIL",
                        "m_f::t_ok": "PASS"}
        # a_t passes only if its object has field v (SOLVE writes by name)
        assert list(image.fields.values()) == [["v"]]


def test_an_image_from_another_abi_is_refused(suite, monkeypatch):
    from zuspec.be.bc import model as _model

    out = suite / "s.zbc"
    assert _build(str(suite / "s.pss"), "--image", str(out)) == 0
    monkeypatch.setattr(_model, "ABI_ID", _model.ABI_ID + 1)
    with pytest.raises(build_cmd.BuildError, match="rebuild"):
        build_cmd.load(str(out))


@pytest.fixture
def mixed_image(tmp_path):
    (tmp_path / "m.pss").write_text(MIXED)
    out = str(tmp_path / "m.zbc")
    assert _build(str(tmp_path / "m.pss"), "--image", out) == 0
    (tmp_path / "m.pss").unlink()       # the image alone must do
    return out


def _test(*argv):
    return cli.main(["test", *argv, "--no-ctrf"])


def test_pssc_test_runs_an_image_file(mixed_image, capsys):
    """P3.2b2: ``pssc test --image`` runs the image's tests with no sources;
    ``-k`` and test IDs select from them."""
    capsys.readouterr()
    assert _test("--image", mixed_image) == 1
    out = capsys.readouterr().out
    assert "m_f::t_bad" in out
    assert "replay: pssc test m_f::t_bad --image" in out
    assert f"--image {mixed_image}" in out
    assert _test("--image", mixed_image, "-k", "t_ok") == 0
    assert _test("--image", mixed_image, "m_f::a_t", "-j", "2") == 0
    assert _test("--image", mixed_image, "-k", "nope") == 5


def test_pssc_test_image_collect_only(mixed_image, capsys):
    capsys.readouterr()
    assert _test("--image", mixed_image, "--collect-only", "--json") == 0
    doc = json.loads(capsys.readouterr().out)
    assert [t["id"] for t in doc["tests"]] == \
        ["m_f::a_t", "m_f::t_bad", "m_f::t_ok"]
    assert _test("--image", mixed_image, "--collect-only") == 0
    assert "t_ok" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [["--changed"], ["--cov", "c.json"],
                                  ["--targets", "op-model-c"], ["x.pss"]])
def test_pssc_test_image_refuses_what_needs_sources(mixed_image, capsys, argv):
    capsys.readouterr()
    assert _test("--image", mixed_image, *argv) == 2
    assert "--image" in capsys.readouterr().err


def test_pssc_test_image_missing_file(tmp_path, capsys):
    assert _test("--image", str(tmp_path / "none.zbc")) == 2
