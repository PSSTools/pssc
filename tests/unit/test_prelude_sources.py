"""A prelude sees the compile's sources, duplicates are dropped by content,
and the `pssc.preludes` group contributes to every target.

`opts.sources` is what lets a target leave out a unit the user passed as well; the driver drops a unit byte-identical to a source or to an earlier
unit whether or not the target looked; and an installed contributor
puts its PSS in scope on every target. `prelude_units` is the one
place all three are decided, and `compile`, `pssc.testing` and `pssc.Check`
each ask it -- the tests at the end hold each caller to that.
"""
from __future__ import annotations

import argparse

import pytest

from pssc import cli, driver, preludes, targets
from pssc.targets import Target

#: The library a model is annotated against: a package of annotation types.
LIB = """\
package ann_lib_pkg {
    annotation api_s { string prefix = ""; }
    annotation reviewed_s { }
}
"""

#: A model that needs it. The `import` is what makes a missing package an
#: error rather than a disregarded annotation.
MODEL = """\
import ann_lib_pkg::*;

@ann_lib_pkg::api_s {.prefix = "dev"}
component dev_c {
    @ann_lib_pkg::reviewed_s
    function bit[32] twice(bit[32] x) { return x * 2; }
    export target function twice;
}
"""


class _Recorder(Target):
    """A target that records what its prelude was asked with, and puts
    ``units`` in it."""
    name = "x-prelude-recorder"
    description = "records opts.sources (test_prelude_sources)"
    units: list = []

    def __init__(self):
        self.seen = []

    def prelude(self, opts):
        self.seen.append(getattr(opts, "sources", None))
        return list(self.units)

    def run(self, ctx, opts):
        return []


@pytest.fixture
def recorder():
    tgt = _Recorder()
    tgt.units = []
    saved = dict(targets._REGISTRY)
    targets.register(tgt)
    yield tgt
    targets._REGISTRY.clear()
    targets._REGISTRY.update(saved)


@pytest.fixture
def contributors(monkeypatch):
    """The contributor registry, emptied and latched so `prelude_units` does
    not rediscover over what a test registers; restored afterwards."""
    saved = (list(preludes._CONTRIBUTORS), list(preludes._LOAD_ERRORS),
             list(preludes._CALL_ERRORS), preludes._discovered)
    preludes._CONTRIBUTORS.clear()
    preludes._LOAD_ERRORS.clear()
    preludes._CALL_ERRORS.clear()
    preludes._discovered = True
    yield preludes
    preludes._CONTRIBUTORS[:] = saved[0]
    preludes._LOAD_ERRORS[:] = saved[1]
    preludes._CALL_ERRORS[:] = saved[2]
    preludes._discovered = saved[3]


def _files(tmp_path, **texts):
    out = {}
    for name, text in texts.items():
        p = tmp_path / f"{name}.pss"
        p.write_text(text)
        out[name] = str(p)
    return out


# -- the prelude sees the sources ------------------------------------

def test_the_prelude_sees_a_list_of_sources(recorder, contributors, tmp_path):
    f = _files(tmp_path, lib=LIB, m=MODEL)
    driver.compile([f["lib"], f["m"]], target=recorder.name)
    assert recorder.seen == [[f["lib"], f["m"]]]


def test_one_path_is_a_list_of_one(recorder, contributors, tmp_path):
    f = _files(tmp_path, lib=LIB)
    driver.compile(tmp_path / "lib.pss", target=recorder.name)
    assert recorder.seen == [[f["lib"]]]


def test_the_cli_gives_the_prelude_the_sources(recorder, contributors,
                                               tmp_path):
    f = _files(tmp_path, lib=LIB, m=MODEL)
    rc = cli.main(["compile", "-t", recorder.name, "-q",
                   "-o", str(tmp_path / "out"), f["lib"], f["m"]])
    assert rc == 0
    assert recorder.seen == [[f["lib"], f["m"]]]


# -- dedup by content -------------------------------------------------

def test_a_unit_identical_to_a_source_is_one_package(recorder, contributors,
                                                     tmp_path):
    f = _files(tmp_path, lib=LIB, m=MODEL)
    recorder.units = [("ann_lib_pkg.pss", LIB)]
    res = driver.compile([f["lib"], f["m"]], target=recorder.name)
    assert res.ok


def test_a_modified_copy_is_still_a_duplicate(recorder, contributors,
                                              tmp_path):
    """By content, never by name: a different text under the same name is a
    different package, and the parser says so."""
    f = _files(tmp_path, lib=LIB, m=MODEL)
    recorder.units = [("lib.pss", LIB.replace("{ }", "{ int x; }"))]
    with pytest.raises(driver.CompileError) as e:
        driver.compile([f["lib"], f["m"]], target=recorder.name)
    assert any("duplicate declaration" in str(m) for m in e.value.errors)


def test_a_unit_repeated_in_the_prelude_is_kept_once(contributors):
    tgt = _Recorder()
    tgt.units = [("a.pss", LIB), ("b.pss", LIB), ("c.pss", "// other\n")]
    units = driver.prelude_units(tgt, argparse.Namespace(sources=[]))
    assert units == [("a.pss", LIB), ("c.pss", "// other\n")]


def test_a_missing_source_is_left_to_the_parser(contributors, tmp_path):
    tgt = _Recorder()
    tgt.units = [("a.pss", LIB)]
    ns = argparse.Namespace(sources=[str(tmp_path / "absent.pss")])
    assert driver.prelude_units(tgt, ns) == [("a.pss", LIB)]


# -- contributions ----------------------------------------------------

def test_without_the_library_the_model_does_not_compile(contributors,
                                                        tmp_path):
    f = _files(tmp_path, m=MODEL)
    with pytest.raises(driver.CompileError) as e:
        driver.compile([f["m"]], target="op-model-c", progseq_root="dev_c",
                       output_dir=str(tmp_path / "out"))
    assert any("unknown type 'ann_lib_pkg'" in str(m) for m in e.value.errors)


@pytest.mark.parametrize("target", ["op-model-c", "op-model-sv"])
def test_a_contribution_is_in_scope_on_every_target(contributors, tmp_path,
                                                    target):
    seen = []

    def contribute(target_name, opts):
        seen.append((target_name, list(opts.sources)))
        return [("ann_lib_pkg.pss", LIB)]

    contributors.register("lib", contribute)
    f = _files(tmp_path, m=MODEL)
    res = driver.compile([f["m"]], target=target, progseq_root="dev_c",
                         output_dir=str(tmp_path / "out"))
    assert res.ok and res.outputs
    assert seen == [(target, [f["m"]])]


def test_parse_sees_a_contribution(contributors, tmp_path):
    """`pssc parse` names no target, but a model that compiles must parse:
    it takes the contributions too."""
    f = _files(tmp_path, m=MODEL)
    assert cli.main(["parse", f["m"]]) != 0
    contributors.register("lib", lambda t, o: [("ann_lib_pkg.pss", LIB)])
    assert cli.main(["parse", f["m"]]) == 0


def test_a_contribution_also_passed_explicitly_is_one_package(contributors,
                                                              tmp_path):
    contributors.register("lib", lambda t, o: [("ann_lib_pkg.pss", LIB)])
    f = _files(tmp_path, lib=LIB, m=MODEL)
    res = driver.compile([f["lib"], f["m"]], target="op-model-c",
                         progseq_root="dev_c", output_dir=str(tmp_path / "o"))
    assert res.ok


def test_contributions_follow_the_targets_own_prelude(contributors):
    contributors.register("b", lambda t, o: [("b.pss", "// b\n")])
    contributors.register("a", lambda t, o: [("a.pss", "// a\n")])
    tgt = _Recorder()
    tgt.units = [("own.pss", "// own\n")]
    names = [n for n, _ in driver.prelude_units(tgt, argparse.Namespace())]
    assert names == ["own.pss", "a.pss", "b.pss"]     # then by entry-point name


@pytest.mark.parametrize("bad", [
    lambda t, o: 1 / 0,
    lambda t, o: "package p { }",
    lambda t, o: [("ok.pss", "// ok\n"), ("half",)],
])
def test_a_failing_contributor_is_reported_and_the_others_still_run(
        contributors, bad):
    contributors.register("bad", bad, dist="bad-dist 1.0")
    contributors.register("good", lambda t, o: [("good.pss", "// good\n")])
    units = driver.prelude_units(None, argparse.Namespace())
    assert units == [("good.pss", "// good\n")]     # all or nothing for `bad`
    errs = contributors.prelude_errors()
    assert [e.entry_point for e in errs] == ["bad"]
    report = "\n".join(contributors.prelude_error_report())
    assert "bad (from bad-dist 1.0)" in report and "PSSC_NO_PLUGINS" in report


def test_the_cli_warns_of_a_failing_contributor(recorder, contributors,
                                                tmp_path, capsys):
    contributors.register("bad", lambda t, o: 1 / 0)
    f = _files(tmp_path, m="component c { }\n")
    assert cli.main(["compile", "-t", recorder.name, "-q",
                     "-o", str(tmp_path / "out"), f["m"]]) == 0
    err = capsys.readouterr().err
    assert "pssc: warning: 1 prelude contributor failed" in err
    assert "ZeroDivisionError" in err


def test_a_contributed_units_name_reaches_a_diagnostic(contributors,
                                                       tmp_path):
    """`_parse_error` maps the unit's name to its text, so a diagnostic
    pointing into a contribution shows its source line."""
    contributors.register(
        "lib", lambda t, o: [("contrib_lib.pss", "package q { int x = ; }\n")])
    f = _files(tmp_path, m="component c { }\n")
    with pytest.raises(driver.CompileError) as e:
        driver.compile([f["m"]], target="op-model-c", progseq_root="c",
                       output_dir=str(tmp_path / "o"))
    text = "\n".join(str(m) for m in e.value.errors)
    assert "contrib_lib.pss:1:" in text
    assert "package q { int x = ; }" in text


# -- discovery ----------------------------------------------------------------

class _EP:
    def __init__(self, name, obj):
        self.name, self._obj, self.dist = name, obj, None

    def load(self):
        if isinstance(self._obj, BaseException):
            raise self._obj
        return self._obj


def test_discovery_loads_in_name_order_and_records_failures(contributors,
                                                            monkeypatch):
    def fn(t, o):
        return []
    monkeypatch.delenv(targets.NO_PLUGINS_ENV, raising=False)
    monkeypatch.setattr(preludes, "_entry_points", lambda: [
        _EP("zz", fn), _EP("broken", ImportError("no module 'sdk'")),
        _EP("aa", fn), _EP("notcallable", 42)])
    preludes.discover(force=True)
    assert [c.name for c in preludes.contributors()] == ["aa", "zz"]
    assert sorted(e.entry_point for e in preludes.prelude_errors()) == [
        "broken", "notcallable"]


def test_no_plugins_skips_discovery(contributors, monkeypatch):
    monkeypatch.setenv(targets.NO_PLUGINS_ENV, "1")
    monkeypatch.setattr(preludes, "_entry_points",
                        lambda: [_EP("aa", lambda t, o: [])])
    preludes.discover(force=True)
    assert preludes.contributors() == () and preludes.prelude_errors() == ()


# -- every caller asks `prelude_units` ----------------------------------------

def test_the_testing_kit_sees_a_contribution(contributors, tmp_path):
    from pssc import testing
    contributors.register("lib", lambda t, o: [("ann_lib_pkg.pss", LIB)])
    f = _files(tmp_path, m=MODEL)
    sections = testing.generated_sections("op-model-c", sources=[f["m"]],
                                          root="dev_c")
    assert any("twice" in text for text in sections.values())


def test_the_check_sees_a_contribution(contributors, tmp_path):
    from pssc.dvflow import check
    f = _files(tmp_path, m=MODEL)
    assert check.evaluate([f["m"]])["status"] == 1
    contributors.register("lib", lambda t, o: [("ann_lib_pkg.pss", LIB)])
    seen = []
    contributors.register("spy", lambda t, o: seen.append(t) or [])
    res = check.evaluate([f["m"]], order_check=True)
    assert res["status"] == 0, res
    assert seen and set(seen) == {None}           # a check names no target
