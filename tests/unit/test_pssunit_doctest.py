"""P5.10: examples in doc comments run as tests on the fake (T5.10).

A ```` ```pss-example ```` block in a function's doc comment is the body of a
test in a fixture holding the documented component as ``dut``.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pssparser")

from pssc.testing import doctest


DEV = """\
package m_pkg {
    component calc_c {
        /**
         * Adds two numbers.
         * ```pss-example
         * check_eq(dut.add(1, 2), 3, "1 + 2");
         * ```
         * Not an example:
         * ```pss
         * dut.add(0, 0);
         * ```
         */
        function int add(int a, int b) { return a + b; }

        /// Subtracts, wrongly.
        /// ```pss-example
        /// check_eq(dut.sub(3, 1), 2, "3 - 1");
        /// ```
        function int sub(int a, int b) { return a + b; }
    }
}
"""


def test_blocks_only_pss_example_fences():
    doc = "x\n```pss-example\na();\nb();\n```\n```pss\nc();\n```"
    assert doctest.blocks(doc) == ["a();\nb();"]
    assert doctest.blocks(None) == []


def test_examples_and_wrappers(tmp_path):
    from pssc import driver
    (tmp_path / "dev.pss").write_text(DEV)
    ctx = driver.translate([str(tmp_path / "dev.pss")])
    exs = doctest.examples(ctx)
    assert [(e.comp, e.function, e.index) for e in exs] == [
        ("m_pkg::calc_c", "add", 1), ("m_pkg::calc_c", "sub", 1)]
    src = doctest.synth(exs)
    assert "component doc_m_pkg_calc_c_add_1 : pssunit_pkg::fixture_c {" in src
    assert "    m_pkg::calc_c dut;" in src
    assert 'check_eq(dut.add(1, 2), 3, "1 + 2");' in src


@pytest.mark.parametrize("engine", ["py", "c"])
def test_examples_run_as_tests(tmp_path, monkeypatch, capsys, engine):
    from pssc.cli import main
    if engine == "c":
        pytest.importorskip("zuspec.rt.eng.host")
    (tmp_path / "dev.pss").write_text(DEV)
    monkeypatch.chdir(tmp_path)
    rc = main(["test", "dev.pss", "--targets", "none", "--no-ctrf",
               "--platform", "fake", "--engine", engine])
    out = capsys.readouterr().out
    assert rc != 0, out
    assert "1 passed" in out and "1 failed" in out, out
    assert "doc_m_pkg_calc_c_sub_1" in out, out
    assert (tmp_path / ".pssunit" / doctest.FILE).is_file()
    # the card points at the example's line in the doc comment, not the wrapper
    assert "FAIL dev.pss:17:13" in out, out


def test_example_located_in_doc_comment(tmp_path, monkeypatch, capsys):
    """The test's place is the example's fences: ``FILE:LINE`` selects it."""
    from pssc.cli import main
    from pssc.testing.cmd import collect
    (tmp_path / "dev.pss").write_text(DEV)
    monkeypatch.chdir(tmp_path)
    coll = collect(["dev.pss"])
    where = {it.comp: (it.loc.file, it.loc.line, it.end_line)
             for it in coll.items}
    assert where == {"doc_m_pkg_calc_c_add_1": ("dev.pss", 5, 7),
                     "doc_m_pkg_calc_c_sub_1": ("dev.pss", 16, 18)}
    rc = main(["test", "dev.pss:17", "--targets", "none", "--no-ctrf",
               "--platform", "fake"])
    out = capsys.readouterr().out
    assert rc != 0 and "0 passed, 1 failed" in out, out


def test_no_doctest(tmp_path, monkeypatch, capsys):
    from pssc.cli import main
    (tmp_path / "dev.pss").write_text(DEV)
    monkeypatch.chdir(tmp_path)
    rc = main(["test", "dev.pss", "--targets", "none", "--no-ctrf",
               "--platform", "fake", "--no-doctest"])
    capsys.readouterr()
    assert rc == 5          # nothing to run
    assert not (tmp_path / ".pssunit" / doctest.FILE).exists()


def test_source_outside_wrappers():
    assert doctest.source("dev.pss", 3, 1) is None
    assert doctest.item_loc(None) is None


def test_no_examples_no_file(tmp_path, monkeypatch, capsys):
    """A model without examples is translated once, and nothing is written."""
    from pssc.cli import main
    (tmp_path / "dev.pss").write_text(
        "component plain_c { function int one() { return 1; } }\n")
    monkeypatch.chdir(tmp_path)
    main(["test", "dev.pss", "--targets", "none", "--no-ctrf",
          "--platform", "fake"])
    capsys.readouterr()
    assert not (tmp_path / ".pssunit" / doctest.FILE).exists()
