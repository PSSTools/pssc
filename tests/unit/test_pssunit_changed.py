"""P2.13a: a test's slice, and ``pssc test --changed`` (T2.13).

An edit to one operation re-runs only the tests whose slice reaches it; a
test that passed with nothing in its slice changed is not run again.
"""
from __future__ import annotations

import json

from pssc.cli import main
from pssc.testing import cmd

SUITE = """\
import pssunit_pkg::*;

component t_f : fixture_c {
    function int op_a(int x) {
        return x + 1;
    }
    function int op_b(int x) {
        return x + 2;
    }
    function int op_ab(int x) {
        return op_a(x) + 1;
    }
    @pssunit_pkg::test
    function void t_a() {
        check_eq(op_a(1), 2, "a");
    }
    @pssunit_pkg::test
    function void t_b() {
        check_eq(op_b(1), 3, "b");
    }
    @pssunit_pkg::test
    function void t_ab() {
        check_eq(op_ab(1), 3, "ab");
    }
}
"""


def _ran(capsys, *extra):
    rc = main(["test", "t.pss", "--targets", "none", "--no-ctrf", "--json",
               *extra])
    cap = capsys.readouterr()
    out = cap.out
    evs = [json.loads(line) for line in out.splitlines() if line.startswith("{")]
    return rc, {e["id"].split("::")[-1]: e["status"]
                for e in evs if e["event"] == "test.end"}, cap.err


def _project(tmp_path, monkeypatch, text=SUITE):
    (tmp_path / "t.pss").write_text(text)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cmd, "LAST_FAILED", str(tmp_path / "rec" / "lastfailed"))


def test_catalog_lists_each_tests_slice(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    assert main(["test", "t.pss", "--collect-only", "--json"]) == 0
    cat = json.loads(capsys.readouterr().out)
    calls = {t["name"]: t["calls"] for t in cat["tests"]}
    assert calls["t_a"]["units"] == ["t_f::op_a"]
    assert calls["t_b"]["units"] == ["t_f::op_b"]
    assert calls["t_ab"]["units"] == ["t_f::op_a", "t_f::op_ab"]
    assert len({c["slice"] for c in calls.values()}) == 3


def test_changed_reruns_only_what_an_edit_reaches(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    rc, ran, _ = _ran(capsys, "--changed")
    assert rc == 0 and set(ran) == {"t_a", "t_b", "t_ab"}
    assert (tmp_path / "rec" / "results.json").is_file()

    # Nothing changed: every result is reused.
    rc, ran, out = _ran(capsys, "--changed")
    assert rc == 0 and ran == {}
    assert "nothing to run" in out

    # An edit inside op_a (lines shift too) re-runs t_a and t_ab, not t_b.
    (tmp_path / "t.pss").write_text(SUITE.replace(
        "return x + 1;", "// one more line\n        return x + 0 + 1;"))
    rc, ran, _ = _ran(capsys, "--changed")
    assert rc == 0 and set(ran) == {"t_a", "t_ab"}

    # Without --changed everything runs (and, the record existing, is
    # recorded); a failure is never reused.
    (tmp_path / "t.pss").write_text(SUITE.replace("return x + 2;", "return x;"))
    rc, ran, _ = _ran(capsys)
    assert rc == 1 and ran == {"t_a": "pass", "t_b": "fail", "t_ab": "pass"}
    rc, ran, _ = _ran(capsys, "--changed")
    assert rc == 1 and ran == {"t_b": "fail"}


def test_changed_respects_seed_and_options(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    _ran(capsys, "--changed", "--seed", "5")
    assert _ran(capsys, "--changed", "--seed", "5")[1] == {}
    assert set(_ran(capsys, "--changed", "--seed", "6")[1]) == {"t_a", "t_b", "t_ab"}
    # an option that changes verdicts is part of the key
    assert set(_ran(capsys, "--changed", "--strict")[1]) == {"t_a", "t_b", "t_ab"}


def test_edit_outside_every_unit_reruns_all(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    _ran(capsys, "--changed")
    (tmp_path / "t.pss").write_text(SUITE.replace(
        "component t_f : fixture_c {", "component t_f : fixture_c {\n    int k;"))
    assert set(_ran(capsys, "--changed")[1]) == {"t_a", "t_b", "t_ab"}


def _git(tmp_path, *argv):
    import subprocess
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *argv],
                   cwd=tmp_path, check=True, capture_output=True)


def test_changed_since_a_git_revision(tmp_path, monkeypatch, capsys):
    """P2.13b: ``--changed --base REV`` compares slices with the sources at REV."""
    _project(tmp_path, monkeypatch)
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "t.pss")
    _git(tmp_path, "commit", "-q", "-m", "suite")

    rc, ran, out = _ran(capsys, "--changed", "--base", "HEAD")
    assert rc == 0 and ran == {} and "unchanged since HEAD" in out

    (tmp_path / "t.pss").write_text(SUITE.replace(
        "return x + 1;", "// one more line\n        return x + 0 + 1;"))
    rc, ran, _ = _ran(capsys, "--changed", "--base", "HEAD")
    assert rc == 0 and set(ran) == {"t_a", "t_ab"}
    # no result record is read or started
    assert not (tmp_path / "rec" / "results.json").exists()

    # A test absent at REV runs. (Adding one also adds an annotation line
    # outside every unit, which re-runs them all; a rename does not.)
    (tmp_path / "t.pss").write_text(SUITE.replace("void t_b()", "void t_b2()"))
    assert set(_ran(capsys, "--changed", "--base", "HEAD")[1]) == {"t_b2"}


def test_base_errors(tmp_path, monkeypatch, capsys):
    _project(tmp_path, monkeypatch)
    rc, _, err = _ran(capsys, "--base", "HEAD")
    assert rc == 5 and "--base needs --changed" in err
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path.parent))
    rc, _, err = _ran(capsys, "--changed", "--base", "HEAD")
    assert rc == 5 and "not in a git work tree" in err
    _git(tmp_path, "init", "-q")
    rc, _, err = _ran(capsys, "--changed", "--base", "nope")
    assert rc == 5 and "no such git revision" in err


def test_watch_reruns_what_a_save_changes(tmp_path, monkeypatch, capsys):
    """P2.13c: ``--watch`` runs, then re-runs ``--changed`` after each save;
    Ctrl-C ends it with the last round's exit code."""
    from pssc.testing import watch
    _project(tmp_path, monkeypatch)
    edits = [SUITE.replace("return x + 2;", "return x + 1 + 1;"),
             SUITE.replace("return x + 2;", "return x;")]
    polls = []

    def fake_sleep(_s):
        polls.append(_s)
        if len(polls) in (2, 4):            # the user saves after a while
            # (each edit changes the size: an mtime may not move this fast)
            (tmp_path / "t.pss").write_text(edits.pop(0))
        elif len(polls) > 5:
            raise KeyboardInterrupt
    monkeypatch.setattr(watch, "_sleep", fake_sleep)
    rc = main(["test", "t.pss", "--targets", "none", "--no-ctrf", "--json",
               "--watch"])
    cap = capsys.readouterr()
    rounds, cur = [], None
    for line in cap.out.splitlines():
        ev = json.loads(line)
        if ev["event"] == "run.start":
            cur = {}
            rounds.append(cur)
        elif ev["event"] == "test.end":
            cur[ev["id"].split("::")[-1]] = ev["status"]
    assert rounds == [{"t_a": "pass", "t_b": "pass", "t_ab": "pass"},
                      {"t_b": "pass"}, {"t_b": "fail"}]
    assert rc == 1


def test_watch_round_argv():
    from pssc.testing.watch import round_argv
    assert round_argv(["test", ".", "--watch", "-k", "a"]) == \
        ["test", ".", "-k", "a", "--changed"]
    assert round_argv(["test", "--watch", "--changed", "--base", "HEAD"]) == \
        ["test", "--changed", "--base", "HEAD"]


def test_watch_from_the_console_entry(tmp_path, monkeypatch, capsys):
    """The entry loops itself; with no daemon each round runs in-process."""
    from pssc import _entry
    from pssc.testing import watch
    _project(tmp_path, monkeypatch)

    def stop(_s):
        raise KeyboardInterrupt
    monkeypatch.setattr(watch, "_sleep", stop)
    rc = _entry.main(["test", "t.pss", "--targets", "none", "--no-ctrf",
                      "--watch"])
    cap = capsys.readouterr()
    assert rc == 0 and "3 passed" in cap.out
    assert "--watch: waiting for a change" in cap.err
    assert (tmp_path / "rec" / "results.json").is_file()    # rounds are --changed
