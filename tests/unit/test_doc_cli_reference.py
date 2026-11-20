"""``docs/cli-reference.rst`` is generated from ``pssc help --json`` (D4, T-D2).

The checked-in page must be exactly what ``pssc help --rst`` prints now, so a
new command or option cannot be missing from it; on a mismatch, regenerate
with ``help_cmd.REGENERATE``.
"""
from __future__ import annotations

from pathlib import Path

from pssc import cli
from pssc.devenv import help_cmd

_ROOT = Path(__file__).resolve().parents[2]
_DOCS = _ROOT / "docs"
_PAGE = _DOCS / "cli-reference.rst"


def _generated() -> str:
    return help_cmd.render_rst(help_cmd.describe(cli.build_parser()))


def test_the_page_matches_pssc_help():
    assert _PAGE.read_text() == _generated(), (
        "docs/cli-reference.rst is stale; regenerate it from the repo root: "
        + help_cmd.REGENERATE)


def test_help_rst_prints_the_page(capsys):
    assert cli.main(["help", "--rst"]) == 0
    assert capsys.readouterr().out == _generated()


def test_every_command_has_a_section():
    page = _PAGE.read_text()

    def walk(cmds):
        for c in cmds:
            yield c["path"]
            yield from walk(c.get("commands", []))

    paths = list(walk(help_cmd.describe(cli.build_parser())["commands"]))
    assert ["test"] in paths and ["model", "regs"] in paths
    for path in paths:
        assert "``pssc %s``\n" % " ".join(path) in page


def test_every_option_and_output_schema_is_listed():
    page = _PAGE.read_text()
    test = next(c for c in help_cmd.describe(cli.build_parser())["commands"]
                if c["name"] == "test")
    for a in test["args"]:
        for f in a["flags"]:
            assert f in page
    for o in help_cmd.OUTPUTS[("test",)]:
        assert o["schema"] in page


def test_the_page_leaves_the_version_out():
    from pssc.__version__ import version
    assert str(version) not in _PAGE.read_text()


def test_the_page_is_in_the_toctree_and_linked():
    index = (_DOCS / "index.rst").read_text()
    toctree = index.split(".. toctree::", 1)[1].split("\n\n", 2)[1]
    assert "   cli-reference" in toctree.splitlines()
    assert "cli-reference.rst" in (_DOCS / "cli.md").read_text()
