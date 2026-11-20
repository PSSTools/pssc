"""The static test catalog (P1.2, T1.2), against real IR.

``test_pssunit_discover.py`` covers the base-type rule on synthetic contexts;
this file covers what the catalog adds -- fixtures and inherited tests, test
functions, location spans and ``FILE:LINE`` selection, the annotations it
records, and the ``pssunit.catalog/1`` JSON -- by translating small suites.
"""
from __future__ import annotations

import json
import pathlib
import shutil

import pytest

from pssc import driver
from pssc.testing import sources
from pssc.testing.catalog import build, select_lines, to_json
from pssc.testing.discover import DiscoveryError

pytest.importorskip("pssparser")

REPO = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = REPO / "docs" / "schemas" / "pssunit.catalog-1.json"

SUITE = """\
import pssunit_pkg::*;
component base_f : fixture_c {
    @pssunit_pkg::tag {.name = "smoke"}
    @pssunit_pkg::property {.runs = 7}
    @pssunit_pkg::platform {.min = "sim"}
    action t1 : test_a {
        exec body {
            int x = 1;
        }
    }
    abstract action t_abs : test_a { }
    @pssunit_pkg::test {.group = "fg", .tags = "a,b"}
    @pssunit_pkg::expect_error {.text = "boom"}
    function void tf() {
        int y = 2;
    }
    function void helper() { }
}
component derived_f : base_f {
    action t2 : t_abs { }
}
"""
#: Line numbers in SUITE, by what is on them.
T1_BODY, BETWEEN, TF_BODY, T2 = 8, 11, 15, 20


def _ctx(path):
    ctx = driver.translate([str(p) for p in sources.all_sources([str(path)])])
    assert not ctx.errors
    return ctx


@pytest.fixture
def suite(tmp_path):
    p = tmp_path / "suite.pss"
    p.write_text(SUITE)
    return p


@pytest.fixture
def items(suite):
    return build(_ctx(suite))


def _ids(items):
    return [it.qname for it in items]


def test_concrete_tests_are_collected_and_abstract_ones_are_not(items):
    assert "base_f::t_abs" not in _ids(items)
    assert "base_f::t1" in _ids(items)
    assert "derived_f::t2" in _ids(items)          # via the abstract base


def test_inherited_tests_get_the_derived_fixtures_id(items):
    assert _ids(items) == ["base_f::t1", "base_f::tf",
                           "derived_f::t1", "derived_f::tf", "derived_f::t2"]
    inh = next(it for it in items if it.qname == "derived_f::t1")
    assert (inh.comp, inh.decl, inh.type_name) == (
        "derived_f", "base_f::t1", "base_f::t1")
    assert inh.is_fixture


def test_test_functions_are_tests_and_plain_functions_are_not(items):
    tf = next(it for it in items if it.qname == "base_f::tf")
    assert (tf.kind, tf.meta.group) == ("function", "fg")
    assert not any(it.name == "helper" for it in items)


def test_the_catalog_records_the_annotations(items):
    t1 = items[0]
    assert (t1.tags, t1.property_runs, t1.platform_min) == (
        ("smoke",), 7, "sim")
    assert t1.bases == ("pssunit_pkg::test_a",)
    tf = items[1]
    assert (tf.tags, tf.expect_error) == (("a", "b"), "boom")
    t2 = items[-1]
    assert t2.bases == ("base_f::t_abs", "pssunit_pkg::test_a")


@pytest.mark.parametrize("line,want", [
    (T1_BODY, ["base_f::t1", "derived_f::t1"]),
    (TF_BODY, ["base_f::tf", "derived_f::tf"]),
    (T2, ["derived_f::t2"]),
    (BETWEEN, []),
])
def test_a_line_selects_the_tests_whose_body_spans_it(items, suite, line, want):
    assert _ids(select_lines(items, [(str(suite), line)])) == want


def test_moving_the_file_keeps_the_ids(items, suite, tmp_path):
    moved = tmp_path / "elsewhere" / "renamed.pss"
    moved.parent.mkdir()
    shutil.copy(suite, moved)
    assert _ids(build(_ctx(moved))) == _ids(items)


def test_the_json_matches_its_schema(items):
    jsonschema = pytest.importorskip("jsonschema")
    doc = to_json(items)
    jsonschema.validate(doc, json.loads(SCHEMA.read_text()))
    assert doc["schema"] == "pssunit.catalog/1"
    assert [f["name"] for f in doc["fixtures"]] == ["base_f", "derived_f"]


def test_the_catalog_locates_a_base_it_cannot_resolve():
    """Reached only if the front end lets an unknown base through."""
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


# ---- through the command ----------------------------------------------------

def _run(capsys, *argv):
    from pssc.cli import main
    rc = main(["test", *argv])
    return rc, capsys.readouterr()


def test_collect_only_json(capsys, suite):
    rc, out = _run(capsys, str(suite), "--collect-only", "--json")
    assert rc == 0
    assert json.loads(out.out)["tests"][2]["id"] == "derived_f::t1"


def test_a_line_outside_every_test_exits_5(capsys, suite):
    rc, out = _run(capsys, f"{suite}:{BETWEEN}", "--collect-only")
    assert rc == 5
    assert f"{suite}:{BETWEEN}" in out.err


def test_a_line_inside_a_test_selects_it(capsys, suite):
    rc, out = _run(capsys, f"{suite}:{T2}", "--collect-only")
    assert rc == 0
    assert "1 test collected" in out.out


def test_an_unresolvable_base_exits_2_located(capsys, tmp_path):
    p = tmp_path / "bad.pss"
    p.write_text(SUITE.replace("action t2 : t_abs", "action t2 : t_absx"))
    rc, out = _run(capsys, str(p), "--collect-only")
    assert rc == 2
    assert f"{p}:{T2}:" in out.err


def test_an_inherited_test_runs_under_the_derived_fixture(capsys, tmp_path):
    p = tmp_path / "run.pss"
    p.write_text(SUITE.replace("@pssunit_pkg::test {", "@pssunit_pkg::tag {")
                 .replace('.group = "fg", .tags = "a,b"', '.name = "x"'))
    rc, out = _run(capsys, str(p), "--no-ctrf", "-k", "derived_f::t1")
    assert "1 passed" in out.out, out.out
    assert rc == 0
