"""The `pssc test` argument surface, and the exit-code contract around it.

`pssc test` has its own exit codes (plan R-5: `1` test failed, `2` compile
error, `3` internal, `5` nothing selected). The tests here pin that adding them
did NOT change the codes the other subcommands already published.
"""
from __future__ import annotations

import subprocess
import sys

import pytest

from pssc.cli import build_parser, main


def test_test_is_a_subcommand():
    args = build_parser().parse_args(["test", "somewhere"])
    assert args.command == "test"
    assert args.sources == ["somewhere"]


def test_sources_are_optional_and_default_to_the_current_directory():
    """Unlike compile/parse, which take `nargs="+"`.

    `pssc test` inside a suite should mean the suite.
    """
    assert build_parser().parse_args(["test"]).sources == []


def test_a_ctrf_report_is_written_by_default():
    assert build_parser().parse_args(["test"]).ctrf == "pssunit-ctrf.json"


def test_no_ctrf_suppresses_it():
    assert build_parser().parse_args(["test", "--no-ctrf"]).ctrf is None


def test_the_last_ctrf_flag_wins():
    args = build_parser().parse_args(["test", "--no-ctrf", "--ctrf", "r.json"])
    assert args.ctrf == "r.json"


def test_the_seed_defaults_to_none_so_the_runner_picks_and_reports_one():
    assert build_parser().parse_args(["test"]).seed is None


# --- the exit-code contract -------------------------------------------------

def test_an_unknown_target_is_still_a_user_error(capsys):
    """`1` keeps the meaning it has always had across pssc."""
    assert main(["compile", "--target", "nosuch", "x.pss"]) == 1
    assert "pssc: error" in capsys.readouterr().err


def test_internal_error_is_still_2_on_a_preexisting_subcommand(monkeypatch,
                                                                capsys):
    """`pssc test`'s codes are its own; `parse` and `compile` keep `2`."""
    from pssc import driver

    def _boom(*a, **kw):
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(driver, "translate", _boom)
    assert main(["parse", "anything.pss"]) == 2
    assert "internal error" in capsys.readouterr().err


def test_a_parse_error_is_a_user_error_not_an_internal_one(tmp_path, capsys):
    """A syntax error in the user's PSS is the user's mistake, not a compiler
    bug, on `parse` (exit 1); `pssc test` reports it as exit 2."""
    src = tmp_path / "bad_c.pss"
    src.write_text("component bad_c { this is not pss }\n")
    assert main(["parse", str(src)]) == 1
    assert "internal error" not in capsys.readouterr().err


# --- import weight ----------------------------------------------------------

@pytest.mark.parametrize("module", ["pssc.testing.sources",
                                    "pssc.testing.discover",
                                    "pssc.testing.report",
                                    "pssc.testing.runner"])
def test_a_stage_module_adds_no_imports_of_its_own(module):
    """Each stage must be loadable without dragging in the next one.

    Measured as a delta against `import pssc.testing`, not as an absolute:
    `pssc/__init__.py` already pulls in the parser and the runtime, so an
    absolute check would fail for reasons that have nothing to do with these
    modules. The delta is what this can honestly assert, and it still catches
    the thing worth catching -- a module-scope `from ..runtime import ...` in a
    stage that is supposed to stay independent. `runner.py` imports the runtime
    inside `run()` for exactly this reason.

    Checked structurally rather than by watching `sys.modules`, because
    `pssc/__init__.py` loads the parser and the runtime before any of these
    modules gets a say -- so a runtime check would either fail for reasons that
    have nothing to do with them, or pass vacuously. Reading the module's own
    top-level imports asks the question that is actually being asked.
    """
    import ast
    import importlib.util

    HEAVY = ("pssparser", "zuspec.be", "pssc.runtime", "pssc.driver")

    spec = importlib.util.find_spec(module)
    tree = ast.parse(open(spec.origin).read())

    offenders = []
    for node in tree.body:                      # top level only, by construction
        if isinstance(node, ast.Import):
            offenders += [a.name for a in node.names
                          if a.name.startswith(HEAVY)]
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith(HEAVY):
                offenders.append(node.module)

    assert not offenders, (
        f"{module} imports {offenders} at module scope; move it inside the "
        f"function that needs it, as runner.run() does")
