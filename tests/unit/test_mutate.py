"""T5.12 (P5.12a): mutants of a model's PSS source -- operator, constant,
offset and field -- with comments and strings left alone; on the WB DMA model
they include the hand-seeded bugs of test_wb_dma_suite.py."""
import pathlib
import shutil

import pytest

from pssc.testing.mutate import Mutant, apply, mask, model_files, mutants
from pssc.testing import mutate
from pssc.testing.sources import resolve_sources

REPO = pathlib.Path(__file__).resolve().parents[1].parent
EXAMPLE = REPO / "examples" / "op_model"

from .test_wb_dma_suite import SEEDED  # noqa: E402


def _of(tmp_path, text, kind=None):
    p = tmp_path / "m.pss"
    p.write_text(text)
    ms = mutants([str(p)])
    return [m for m in ms if kind is None or m.kind == kind]


def test_mask_blanks_comments_and_strings_in_place():
    text = 'a == 1; // x == 2\n/* 3 +\n 4 */ s = "5 + 6";\n'
    m = mask(text)
    assert len(m) == len(text) and m.count("\n") == text.count("\n")
    assert "2" not in m and "3" not in m and "5" not in m and "a == 1" in m


def test_operators_flip_and_skip_templates_and_unary(tmp_path):
    ms = _of(tmp_path, "struct s : packed_s<LE> { bit a; }\n"
                       "function int f(int x) { if (x == -1 && x < 3) return x + 2; return x; }\n",
             "operator")
    assert [(m.old, m.new) for m in ms] == [("==", "!="), ("&&", "||"), ("<", "<="), ("+", "-")]
    assert ms[0].line == 2


def test_constants_and_offsets(tmp_path):
    ms = _of(tmp_path, "function bit[64] f() { return 0x1C + 1 + 7; } // 9\n")
    got = {(m.kind, m.old, m.new) for m in ms if m.kind in ("constant", "offset")}
    assert got == {("constant", "0x1C", "0x1D"), ("offset", "0x1C", "0x20"),
                   ("constant", "1", "0"), ("constant", "7", "8")}


def test_fields_swap_lines_and_references(tmp_path):
    text = ("struct r_s : packed_s<LE> {\n"
            "    bit a; // [0]\n"
            "    bit[2] b; // [2:1]\n"
            "    bit[29] rsvd_3;\n"
            "}\n"
            "function bool f(r_s v) { return v.b == 1; }\n")
    ms = _of(tmp_path, text, "field")
    swaps = [m for m in ms if "\n" in m.old]
    assert swaps[0].old == "    bit a; // [0]\n    bit[2] b; // [2:1]\n"
    assert swaps[0].new == "    bit[2] b; // [2:1]\n    bit a; // [0]\n"
    refs = [(m.old, m.new) for m in ms if "\n" not in m.old]
    assert refs == [("b", "a")]                     # rsvd_3 is never a target
    m = [m for m in ms if "\n" not in m.old][0]
    assert "return v.a == 1;" in apply(m, text)


def test_apply_refuses_stale_text(tmp_path):
    m = Mutant("constant", str(tmp_path / "x"), 1, 1, "1", "0", 0)
    with pytest.raises(ValueError):
        apply(m, "2")


def test_model_files_drop_files_that_declare_tests():
    class Loc:
        file = "t.pss"

    class Item:
        loc = Loc()
    assert model_files(["a.pss", "t.pss"], [Item()]) == ["a.pss"]


@pytest.fixture(scope="module")
def op_model():
    paths = [str(p) for p in resolve_sources(["@" + str(EXAMPLE / "tests" / "model.f")])]
    return paths, mutants(paths)


@pytest.mark.parametrize("bug", ["offset_int_msk_a", "offset_bank",
                                 "field_done_err_swapped", "driver_wrong_field"])
def test_op_model_mutants_include_the_seeded_bug(op_model, bug):
    paths, ms = op_model
    rel, old, new = SEEDED[bug]
    path = (EXAMPLE / rel).resolve()
    text = path.read_text()
    want = text.replace(old, new, 1)
    mine = [m for m in ms if pathlib.Path(m.file).resolve() == path]
    assert any(apply(m, text) == want for m in mine), bug


def test_op_model_mutants_cover_every_kind(op_model):
    _, ms = op_model
    assert {m.kind for m in ms} == {"operator", "constant", "offset", "field"}
    assert all(apply(m) != pathlib.Path(m.file).read_text() for m in ms[:50])


# --- T5.12: `pssc test --mutate` on a copy of op_model ----------------------

RUN_SEEDED = ("offset_int_msk_a", "offset_bank", "field_done_err_swapped",
              "driver_wrong_field")


def _mutate(tmp_path, monkeypatch, capsys, *more, bug=None):
    """``pssc test --mutate`` in a copy of op_model (with seeded ``bug``), on
    the mutants equal to the seeded bugs in ``RUN_SEEDED`` only (the full set
    takes minutes)."""
    from pssc import cli
    from pssc.testing import cmd, mutate
    for sub in ("pss", "tests"):
        shutil.copytree(EXAMPLE / sub, tmp_path / sub,
                        ignore=shutil.ignore_patterns(".pssunit"))
    if bug:
        rel, old, new = SEEDED[bug]
        p = tmp_path / rel
        p.write_text(p.read_text().replace(old, new, 1))
    want = set()
    for name in RUN_SEEDED:
        rel, old, new = SEEDED[name]
        path = (tmp_path / rel).resolve()
        want.add((str(path), path.read_text().replace(old, new, 1)))
    every = mutate.mutants
    monkeypatch.setattr(mutate, "mutants", lambda *a, **k: [
        m for m in every(*a, **k)
        if (str(pathlib.Path(m.file).resolve()), apply(m)) in want])
    monkeypatch.setattr(cmd, "LAST_FAILED", str(tmp_path / "lastfailed"))
    monkeypatch.chdir(tmp_path / "tests")
    capsys.readouterr()
    rc = cli.main(["test", "@model.f", ".", "--platform", "fake", "--targets",
                   "none", "--no-ctrf", "--seed", "1", "-q", "--mutate", "-v",
                   "-j", "4", *more])
    return rc, capsys.readouterr().out


def test_mutate_kills_the_seeded_bugs(tmp_path, monkeypatch, capsys):
    rc, out = _mutate(tmp_path, monkeypatch, capsys)
    assert rc == 0, out
    assert out.count("killed: ") == len(RUN_SEEDED), out
    assert "mutation score 100.0%: 4 killed, 0 survived" in out, out


def test_mutate_lists_what_one_test_misses(tmp_path, monkeypatch, capsys):
    rc, out = _mutate(tmp_path, monkeypatch, capsys,
                      "wb_dma_fx_c::t_csr_prio_bits")
    assert rc == 1, out
    # probe_status is not reached by that test: its mutant is not run
    assert "NOT COVERED: " in out and "probe_status.pss" in out, out
    assert "SURVIVED: " in out, out


def test_mutate_refuses_a_red_baseline(tmp_path, monkeypatch, capsys):
    rc, out = _mutate(tmp_path, monkeypatch, capsys, bug="completion_not_polled")
    assert rc == 2 and "mutation score" not in out, out


def test_mutate_json_events(tmp_path, monkeypatch, capsys):
    import json
    import jsonschema
    rc, out = _mutate(tmp_path, monkeypatch, capsys, "--json")
    assert rc == 0, out
    evs = [json.loads(l) for l in out.splitlines() if l.startswith("{")]
    schema = json.loads(
        (REPO / "docs" / "schemas" / "pssunit.events-1.json").read_text())
    for ev in evs:
        jsonschema.validate(ev, schema)
    names = [e["event"] for e in evs]
    assert names.index("run.end") < names.index("mutant.end"), names
    ends = [e for e in evs if e["event"] == "mutant.end"]
    assert len(ends) == len(RUN_SEEDED)
    assert all(e["status"] == "killed" and e["by"] for e in ends), ends
    assert evs[-1] == {"event": "mutate.end", "killed": 4, "survived": 0,
                       "uncovered": 0, "invalid": 0, "score": 100.0,
                       "exit": 0}


def test_mutate_kinds_filter(tmp_path, monkeypatch, capsys):
    rc, out = _mutate(tmp_path, monkeypatch, capsys, "--mutate-kinds", "offset")
    assert "of 2\n" in out and " field " not in out, out


def test_mutate_max_samples(tmp_path, monkeypatch, capsys):
    rc, out = _mutate(tmp_path, monkeypatch, capsys, "--mutate-max", "1")
    assert rc == 0 and "1 killed, 0 survived" in out and "of 1\n" in out, out


def test_mutate_kinds_rejects_unknown(capsys):
    from pssc import cli
    with pytest.raises(SystemExit):
        cli.build_parser().parse_args(["test", ".", "--mutate-kinds", "bogus"])
    assert "choose from operator" in capsys.readouterr().err


def test_sample_is_stable_and_ordered():
    ms = [mutate.Mutant("constant", "f", i + 1, 1, "1", "2", i)
          for i in range(20)]
    a = mutate.sample(ms, 5, 3)
    assert a == mutate.sample(ms, 5, 3) and len(a) == 5
    assert a == sorted(a, key=lambda m: m.pos)
    assert mutate.sample(ms, None) == ms and mutate.sample(ms, 50, 1) == ms


def test_str_shows_first_line_only():
    m = mutate.Mutant("field", "f.pss", 3, 5, "bit a;\n  bit b;", "bit b;\n  bit a;", 0)
    assert "\\n" not in str(m) and "'bit a; ...' -> 'bit b; ...'" in str(m)
