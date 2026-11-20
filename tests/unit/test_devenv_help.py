"""`pssc help --json` (P1.18c; T-G5: it lists exactly the registered commands)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pssc import cli
from pssc.devenv import help_cmd

REPO = Path(__file__).resolve().parents[2]


def _help(capsys, *argv):
    assert cli.main(["help", *argv, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def _by_name(cmds):
    return {c["name"]: c for c in cmds}


def test_lists_exactly_the_registered_commands(capsys):
    doc = _help(capsys)
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads((REPO / "docs/schemas/pssc.help-1.json").read_text())
    jsonschema.validate(doc, schema)
    assert doc["schema"] == "pssc.help/1"
    registered = list(help_cmd._subparsers(cli.build_parser()))
    assert [c["name"] for c in doc["commands"]] == registered
    for name in ("compile", "parse", "test", "model", "daemon", "help",
                 "targets"):
        assert name in registered
    model = _by_name(doc["commands"])["model"]
    assert [c["name"] for c in model["commands"]] == ["api", "regs", "explain",
                                                      "tests"]
    assert _by_name(model["commands"])["regs"]["path"] == ["model", "regs"]


def test_test_arguments_and_outputs(capsys):
    test = _by_name(_help(capsys)["commands"])["test"]
    args = {a["name"]: a for a in test["args"]}
    assert args["json"]["flags"] == ["--json"] and args["json"]["type"] == "bool"
    assert args["seed"]["flags"] == ["--seed"] and args["seed"]["type"] == "int"
    assert args["sources"]["positional"] and not args["sources"]["required"]
    assert args["jobs"]["flags"] == ["-j", "--jobs"]
    files = {o["file"] for o in test["outputs"] if "file" in o}
    assert "docs/schemas/pssunit.events-1.json" in files
    for f in files:
        assert (REPO / f).is_file(), f


def test_every_output_names_a_real_command_and_file():
    parser = cli.build_parser()
    for path, outs in help_cmd.OUTPUTS.items():
        p = parser
        for name in path:
            p = help_cmd._subparsers(p)[name]
        for o in outs:
            if "file" in o:
                assert (REPO / o["file"]).is_file(), o["file"]


def test_one_command_and_text(capsys):
    doc = _help(capsys, "model", "explain")
    (cmd,) = doc["commands"]
    assert cmd["path"] == ["model", "explain"]
    assert "what" in {a["name"] for a in cmd["args"]}
    assert cli.main(["help", "test"]) == 0
    assert "--collect-only" in capsys.readouterr().out
    assert cli.main(["help", "nosuch"]) == 2
